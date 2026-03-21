"""Chunked file transfer manager with progress tracking and integrity verification."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TransferState(str, Enum):
    """Possible states of a file transfer."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TransferInfo:
    """Metadata and state for a single file transfer."""

    transfer_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    filename: str = ""
    file_size: int = 0
    mime_type: str = "application/octet-stream"
    chunk_size: int = 64 * 1024
    total_chunks: int = 0
    received_chunks: int = 0
    state: TransferState = TransferState.PENDING
    checksum: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    save_path: Path | None = None

    @property
    def progress(self) -> float:
        """Transfer progress as a percentage (0-100)."""
        if self.total_chunks == 0:
            return 0.0
        return round((self.received_chunks / self.total_chunks) * 100, 2)

    @property
    def bytes_received(self) -> int:
        """Estimated bytes received based on chunk count."""
        if self.received_chunks >= self.total_chunks and self.file_size > 0:
            return self.file_size
        return self.received_chunks * self.chunk_size

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed time since transfer started."""
        if self.started_at == 0:
            return 0.0
        end = self.completed_at if self.completed_at > 0 else time.time()
        return end - self.started_at

    @property
    def speed_bps(self) -> float:
        """Current transfer speed in bytes per second."""
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return 0.0
        return self.bytes_received / elapsed

    @property
    def eta_seconds(self) -> float:
        """Estimated time remaining in seconds."""
        speed = self.speed_bps
        if speed <= 0:
            return 0.0
        remaining_bytes = self.file_size - self.bytes_received
        return max(0.0, remaining_bytes / speed)

    def to_dict(self) -> dict[str, Any]:
        """Serialize transfer info to dictionary for JSON responses."""
        return {
            "transfer_id": self.transfer_id,
            "filename": self.filename,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "total_chunks": self.total_chunks,
            "received_chunks": self.received_chunks,
            "state": self.state.value,
            "progress": self.progress,
            "speed_bps": round(self.speed_bps, 2),
            "eta_seconds": round(self.eta_seconds, 2),
            "checksum": self.checksum,
        }


class TransferManager:
    """Manages concurrent file transfers with chunked upload/download."""

    def __init__(self, downloads_dir: Path, chunk_size: int = 64 * 1024) -> None:
        self._downloads_dir = downloads_dir
        self._chunk_size = chunk_size
        self._active: dict[str, TransferInfo] = {}
        self._file_handles: dict[str, Any] = {}
        self._hashers: dict[str, Any] = {}

    @property
    def active_transfers(self) -> dict[str, TransferInfo]:
        """Get all active transfers."""
        return dict(self._active)

    def create_upload(
        self,
        filename: str,
        file_size: int,
        mime_type: str = "application/octet-stream",
    ) -> TransferInfo:
        """Initialize a new upload transfer.

        Args:
            filename: Original filename.
            file_size: Total file size in bytes.
            mime_type: MIME type of the file.

        Returns:
            TransferInfo with allocated transfer_id.
        """
        total_chunks = (file_size + self._chunk_size - 1) // self._chunk_size if file_size > 0 else 1

        # Sanitize filename to prevent path traversal
        safe_name = Path(filename).name
        if not safe_name:
            safe_name = "unnamed_file"

        save_path = self._downloads_dir / safe_name
        # Avoid overwriting: append counter if file exists
        counter = 1
        original_stem = save_path.stem
        original_suffix = save_path.suffix
        while save_path.exists():
            save_path = self._downloads_dir / f"{original_stem}_{counter}{original_suffix}"
            counter += 1

        info = TransferInfo(
            filename=safe_name,
            file_size=file_size,
            mime_type=mime_type,
            chunk_size=self._chunk_size,
            total_chunks=total_chunks,
            save_path=save_path,
        )
        self._active[info.transfer_id] = info
        self._hashers[info.transfer_id] = hashlib.sha256()

        logger.info(
            "Upload initialized: %s (%d bytes, %d chunks) -> %s",
            safe_name,
            file_size,
            total_chunks,
            save_path,
        )
        return info

    def write_chunk(self, transfer_id: str, chunk_data: bytes) -> TransferInfo:
        """Write a chunk of data to the file.

        Args:
            transfer_id: The transfer identifier.
            chunk_data: Raw chunk bytes.

        Returns:
            Updated TransferInfo.

        Raises:
            KeyError: If transfer_id is not found.
            RuntimeError: If transfer is not in a writable state.
        """
        info = self._active.get(transfer_id)
        if info is None:
            raise KeyError(f"Transfer not found: {transfer_id}")

        if info.state not in (TransferState.PENDING, TransferState.IN_PROGRESS):
            raise RuntimeError(f"Transfer {transfer_id} is in state {info.state}, cannot write")

        if info.state == TransferState.PENDING:
            info.state = TransferState.IN_PROGRESS
            info.started_at = time.time()

        # Open file handle on first write
        if transfer_id not in self._file_handles:
            self._file_handles[transfer_id] = open(info.save_path, "wb")  # noqa: SIM115

        self._file_handles[transfer_id].write(chunk_data)
        self._hashers[transfer_id].update(chunk_data)
        info.received_chunks += 1

        # Check if transfer is complete
        if info.received_chunks >= info.total_chunks:
            self._finalize_upload(transfer_id)

        return info

    def create_download(self, file_path: Path) -> TransferInfo:
        """Initialize a download transfer for sending a file from server to client.

        Args:
            file_path: Path to the file to send.

        Returns:
            TransferInfo for the download.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = file_path.stat().st_size
        total_chunks = (file_size + self._chunk_size - 1) // self._chunk_size if file_size > 0 else 1

        info = TransferInfo(
            filename=file_path.name,
            file_size=file_size,
            chunk_size=self._chunk_size,
            total_chunks=total_chunks,
            save_path=file_path,
            state=TransferState.IN_PROGRESS,
            started_at=time.time(),
        )
        self._active[info.transfer_id] = info
        return info

    def read_chunk(self, transfer_id: str) -> bytes | None:
        """Read the next chunk from a download transfer.

        Args:
            transfer_id: The transfer identifier.

        Returns:
            Chunk bytes, or None if transfer is complete.
        """
        info = self._active.get(transfer_id)
        if info is None:
            raise KeyError(f"Transfer not found: {transfer_id}")

        if info.save_path is None:
            raise RuntimeError("No file path for download")

        if transfer_id not in self._file_handles:
            self._file_handles[transfer_id] = open(info.save_path, "rb")  # noqa: SIM115

        data = self._file_handles[transfer_id].read(self._chunk_size)
        if not data:
            info.state = TransferState.COMPLETED
            info.completed_at = time.time()
            self._cleanup_handle(transfer_id)
            return None

        info.received_chunks += 1
        return data

    def cancel_transfer(self, transfer_id: str) -> None:
        """Cancel an active transfer and clean up resources."""
        info = self._active.get(transfer_id)
        if info is None:
            return

        info.state = TransferState.CANCELLED
        self._cleanup_handle(transfer_id)

        # Remove partially uploaded file
        if info.save_path and info.save_path.exists() and info.received_chunks < info.total_chunks:
            info.save_path.unlink(missing_ok=True)
            logger.info("Cancelled transfer %s, removed partial file", transfer_id[:8])

    def get_transfer(self, transfer_id: str) -> TransferInfo | None:
        """Get transfer info by ID."""
        return self._active.get(transfer_id)

    def cleanup_completed(self) -> int:
        """Remove completed/cancelled/failed transfers from tracking.

        Returns:
            Number of transfers cleaned up.
        """
        terminal_states = {TransferState.COMPLETED, TransferState.FAILED, TransferState.CANCELLED}
        to_remove = [
            tid for tid, info in self._active.items() if info.state in terminal_states
        ]
        for tid in to_remove:
            self._cleanup_handle(tid)
            del self._active[tid]
            self._hashers.pop(tid, None)
        return len(to_remove)

    def list_received_files(self) -> list[dict[str, Any]]:
        """List files in the downloads directory.

        Returns:
            List of file info dictionaries.
        """
        files: list[dict[str, Any]] = []
        if not self._downloads_dir.exists():
            return files
        for p in sorted(self._downloads_dir.iterdir()):
            if p.is_file():
                stat = p.stat()
                files.append({
                    "name": p.name,
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "path": str(p),
                })
        return files

    def _finalize_upload(self, transfer_id: str) -> None:
        """Finalize a completed upload."""
        info = self._active[transfer_id]
        info.state = TransferState.COMPLETED
        info.completed_at = time.time()
        info.checksum = self._hashers[transfer_id].hexdigest()
        self._cleanup_handle(transfer_id)
        logger.info(
            "Upload complete: %s (%d bytes in %.1fs, checksum=%s)",
            info.filename,
            info.file_size,
            info.elapsed_seconds,
            info.checksum[:12],
        )

    def _cleanup_handle(self, transfer_id: str) -> None:
        """Close and remove file handle for a transfer."""
        handle = self._file_handles.pop(transfer_id, None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
