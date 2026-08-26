"""Chunked file transfer manager with progress tracking and integrity verification."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO

logger = logging.getLogger(__name__)

# An upload lands here first and is renamed once it is whole, so a
# half-written file is never mistaken for a finished one. The declared
# size is part of the name: resuming is only safe when the sender is
# offering the same file it was offering before.
PART_SUFFIX = ".airbridge-part"
_PART_PATTERN = re.compile(r"^(?P<stem>.+)\.(?P<size>\d+)" + re.escape(PART_SUFFIX) + r"$")


def _chunk_count(file_size: int, chunk_size: int) -> int:
    """Number of chunks a file of this size is split into (empty files still take one)."""
    if file_size <= 0:
        return 1
    return (file_size + chunk_size - 1) // chunk_size


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
    bytes_received: int = 0
    resume_from: int = 0
    state: TransferState = TransferState.PENDING
    checksum: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    save_path: Path | None = None
    part_path: Path | None = None

    @property
    def progress(self) -> float:
        """Transfer progress as a percentage (0-100)."""
        if self.file_size > 0:
            return round(min(self.bytes_received / self.file_size, 1.0) * 100, 2)
        if self.total_chunks == 0:
            return 0.0
        return round((self.received_chunks / self.total_chunks) * 100, 2)

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed time since transfer started."""
        if self.started_at == 0:
            return 0.0
        end = self.completed_at if self.completed_at > 0 else time.time()
        return end - self.started_at

    @property
    def speed_bps(self) -> float:
        """Current transfer speed in bytes per second.

        Measured over what this session actually moved, so a resumed
        transfer is not credited with bytes an earlier one delivered.
        """
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return 0.0
        return max(0, self.bytes_received - self.resume_from) / elapsed

    @property
    def eta_seconds(self) -> float:
        """Estimated time remaining in seconds."""
        speed = self.speed_bps
        if speed <= 0:
            return 0.0
        return max(0.0, (self.file_size - self.bytes_received) / speed)

    def to_dict(self) -> dict[str, Any]:
        """Serialize transfer info to dictionary for JSON responses."""
        return {
            "transfer_id": self.transfer_id,
            "filename": self.filename,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "total_chunks": self.total_chunks,
            "received_chunks": self.received_chunks,
            "bytes_received": self.bytes_received,
            "resume_from": self.resume_from,
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
        self._file_handles: dict[str, BinaryIO] = {}
        self._hashers: dict[str, Any] = {}

    @property
    def active_transfers(self) -> dict[str, TransferInfo]:
        """Get all active transfers."""
        return dict(self._active)

    # --- Uploads ---

    def create_upload(
        self,
        filename: str,
        file_size: int,
        mime_type: str = "application/octet-stream",
    ) -> TransferInfo:
        """Initialize an upload, continuing an interrupted one where possible.

        Args:
            filename: Original filename.
            file_size: Total file size in bytes.
            mime_type: MIME type of the file.

        Returns:
            TransferInfo whose `resume_from` says which byte to send next.
        """
        safe_name = Path(filename).name or "unnamed_file"
        part_path = self._part_path(safe_name, file_size)

        resume_from, hasher = self._adopt_partial(part_path, file_size)

        info = TransferInfo(
            filename=safe_name,
            file_size=file_size,
            mime_type=mime_type,
            chunk_size=self._chunk_size,
            total_chunks=_chunk_count(file_size, self._chunk_size),
            bytes_received=resume_from,
            resume_from=resume_from,
            received_chunks=resume_from // self._chunk_size,
            part_path=part_path,
        )
        self._active[info.transfer_id] = info
        self._hashers[info.transfer_id] = hasher

        if resume_from:
            logger.info(
                "Upload resumed: %s at %d/%d bytes (%.1f%%)",
                safe_name,
                resume_from,
                file_size,
                info.progress,
            )
        else:
            logger.info("Upload initialized: %s (%d bytes) -> %s", safe_name, file_size, part_path)
        return info

    def _adopt_partial(self, part_path: Path, file_size: int) -> tuple[int, Any]:
        """Return how many bytes of `part_path` are reusable, and a hasher over them.

        The hash has to be rebuilt from the bytes already on disk,
        because SHA-256 cannot be resumed from a digest alone.
        """
        hasher = hashlib.sha256()
        if not part_path.is_file():
            return 0, hasher

        existing = part_path.stat().st_size
        if existing == 0 or existing > file_size:
            # Nothing worth keeping, or a leftover from a different file
            # that happened to collide on name and declared size.
            part_path.unlink(missing_ok=True)
            return 0, hasher

        with open(part_path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(block)
        return existing, hasher

    def write_chunk(self, transfer_id: str, chunk_data: bytes) -> TransferInfo:
        """Append a chunk of data to the file.

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

        handle = self._file_handles.get(transfer_id)
        if handle is None:
            if info.part_path is None:
                raise RuntimeError(f"Transfer {transfer_id} has no destination path")
            # Append, so a resumed upload builds on what is already there.
            handle = open(info.part_path, "ab")  # noqa: SIM115
            self._file_handles[transfer_id] = handle

        handle.write(chunk_data)
        self._hashers[transfer_id].update(chunk_data)
        info.received_chunks += 1
        info.bytes_received += len(chunk_data)

        if info.bytes_received >= info.file_size:
            self._finalize_upload(transfer_id)

        return info

    def _finalize_upload(self, transfer_id: str) -> None:
        """Close the part file and move it into place under a free name."""
        info = self._active[transfer_id]
        self._cleanup_handle(transfer_id)

        info.checksum = self._hashers[transfer_id].hexdigest()
        info.save_path = self._claim_name(info.filename)
        if info.part_path is not None:
            info.part_path.replace(info.save_path)
        info.filename = info.save_path.name

        info.state = TransferState.COMPLETED
        info.completed_at = time.time()
        logger.info(
            "Upload complete: %s (%d bytes in %.1fs, checksum=%s)",
            info.filename,
            info.file_size,
            info.elapsed_seconds,
            info.checksum[:12],
        )

    def _part_path(self, safe_name: str, file_size: int) -> Path:
        """Path of the scratch file an upload of this name and size writes to."""
        return self._downloads_dir / f"{safe_name}.{file_size}{PART_SUFFIX}"

    def _claim_name(self, safe_name: str) -> Path:
        """Pick a destination that does not overwrite an existing file."""
        candidate = self._downloads_dir / safe_name
        stem, suffix = candidate.stem, candidate.suffix
        counter = 1
        while candidate.exists():
            candidate = self._downloads_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        return candidate

    # --- Downloads ---

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
        info = TransferInfo(
            filename=file_path.name,
            file_size=file_size,
            chunk_size=self._chunk_size,
            total_chunks=_chunk_count(file_size, self._chunk_size),
            save_path=file_path,
            state=TransferState.IN_PROGRESS,
            started_at=time.time(),
        )
        self._active[info.transfer_id] = info
        return info

    # --- Lifecycle ---

    def cancel_transfer(self, transfer_id: str) -> None:
        """Cancel an active transfer, keeping any partial file for a retry."""
        info = self._active.get(transfer_id)
        if info is None:
            return

        info.state = TransferState.CANCELLED
        self._cleanup_handle(transfer_id)
        logger.info("Cancelled transfer %s", transfer_id[:8])

    def abandon_transfer(self, transfer_id: str) -> None:
        """Cancel a transfer and delete whatever it had written.

        Used when the sender explicitly gives up, as opposed to a
        connection dropping — there is nothing to come back for.
        """
        info = self._active.get(transfer_id)
        if info is None:
            return
        self.cancel_transfer(transfer_id)
        if info.part_path is not None:
            info.part_path.unlink(missing_ok=True)
            logger.info("Discarded partial file for %s", info.filename)

    def get_transfer(self, transfer_id: str) -> TransferInfo | None:
        """Get transfer info by ID."""
        return self._active.get(transfer_id)

    def close_all(self) -> None:
        """Release every open file handle, leaving partial files in place."""
        for transfer_id in list(self._file_handles):
            self._cleanup_handle(transfer_id)

    # --- Listing ---

    def list_received_files(self) -> list[dict[str, Any]]:
        """List completed files in the downloads directory.

        Returns:
            List of file info dictionaries. Uploads still in flight are
            omitted: a half-written file is not something to offer back.
        """
        files: list[dict[str, Any]] = []
        if not self._downloads_dir.exists():
            return files
        for path in sorted(self._downloads_dir.iterdir()):
            if not path.is_file() or path.name.endswith(PART_SUFFIX):
                continue
            stat = path.stat()
            files.append({
                "name": path.name,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "path": str(path),
            })
        return files

    def list_partial_files(self) -> list[dict[str, Any]]:
        """List interrupted uploads waiting to be continued."""
        partials: list[dict[str, Any]] = []
        if not self._downloads_dir.exists():
            return partials
        for path in sorted(self._downloads_dir.iterdir()):
            match = _PART_PATTERN.match(path.name)
            if not path.is_file() or match is None:
                continue
            partials.append({
                "name": match.group("stem"),
                "size": int(match.group("size")),
                "received": path.stat().st_size,
            })
        return partials

    def _cleanup_handle(self, transfer_id: str) -> None:
        """Close and remove the file handle for a transfer."""
        handle = self._file_handles.pop(transfer_id, None)
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()
