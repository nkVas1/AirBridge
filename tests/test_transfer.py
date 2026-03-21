"""Tests for AirBridge transfer module."""

from __future__ import annotations

from pathlib import Path

import pytest

from airbridge.transfer import TransferInfo, TransferManager, TransferState


@pytest.fixture
def tmp_downloads(tmp_path: Path) -> Path:
    """Create temporary downloads directory."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    return downloads


@pytest.fixture
def manager(tmp_downloads: Path) -> TransferManager:
    """Create a TransferManager with temporary directory."""
    return TransferManager(downloads_dir=tmp_downloads, chunk_size=1024)


class TestTransferInfo:
    def test_progress_calculation(self) -> None:
        info = TransferInfo(total_chunks=10, received_chunks=5)
        assert info.progress == 50.0

    def test_progress_zero_chunks(self) -> None:
        info = TransferInfo(total_chunks=0, received_chunks=0)
        assert info.progress == 0.0

    def test_to_dict(self) -> None:
        info = TransferInfo(
            filename="test.txt",
            file_size=1024,
            total_chunks=2,
            received_chunks=1,
        )
        d = info.to_dict()
        assert d["filename"] == "test.txt"
        assert d["file_size"] == 1024
        assert d["progress"] == 50.0
        assert d["state"] == "pending"


class TestTransferManager:
    def test_create_upload(self, manager: TransferManager) -> None:
        info = manager.create_upload("test.txt", 2048)
        assert info.filename == "test.txt"
        assert info.file_size == 2048
        assert info.total_chunks == 2  # 2048 / 1024 = 2
        assert info.state == TransferState.PENDING

    def test_write_chunks(self, manager: TransferManager) -> None:
        info = manager.create_upload("test.txt", 2048)
        chunk1 = b"x" * 1024
        chunk2 = b"y" * 1024

        info = manager.write_chunk(info.transfer_id, chunk1)
        assert info.received_chunks == 1
        assert info.state == TransferState.IN_PROGRESS

        info = manager.write_chunk(info.transfer_id, chunk2)
        assert info.received_chunks == 2
        assert info.state == TransferState.COMPLETED

    def test_file_written_correctly(
        self, manager: TransferManager, tmp_downloads: Path
    ) -> None:
        data = b"Hello, AirBridge!" + b"\x00" * (1024 - 17)
        info = manager.create_upload("hello.txt", len(data))
        manager.write_chunk(info.transfer_id, data)

        assert info.state == TransferState.COMPLETED
        saved_file = tmp_downloads / "hello.txt"
        assert saved_file.exists()
        assert saved_file.read_bytes() == data

    def test_filename_sanitization(self, manager: TransferManager) -> None:
        info = manager.create_upload("../../etc/passwd", 100)
        assert info.filename == "passwd"
        assert ".." not in str(info.save_path)

    def test_duplicate_filename(
        self, manager: TransferManager, tmp_downloads: Path
    ) -> None:
        # Create first file
        (tmp_downloads / "test.txt").write_text("existing")
        info = manager.create_upload("test.txt", 1024)
        assert info.filename == "test.txt"
        # Save path should be test_1.txt
        assert info.save_path is not None
        assert "test_1.txt" in str(info.save_path)

    def test_cancel_transfer(
        self, manager: TransferManager, tmp_downloads: Path
    ) -> None:
        info = manager.create_upload("cancel_me.txt", 2048)
        manager.write_chunk(info.transfer_id, b"x" * 1024)
        manager.cancel_transfer(info.transfer_id)

        transfer = manager.get_transfer(info.transfer_id)
        assert transfer is not None
        assert transfer.state == TransferState.CANCELLED

    def test_write_to_nonexistent_transfer(self, manager: TransferManager) -> None:
        with pytest.raises(KeyError):
            manager.write_chunk("nonexistent", b"data")

    def test_write_to_completed_transfer(self, manager: TransferManager) -> None:
        info = manager.create_upload("small.txt", 100)
        manager.write_chunk(info.transfer_id, b"x" * 100)
        assert info.state == TransferState.COMPLETED

        with pytest.raises(RuntimeError):
            manager.write_chunk(info.transfer_id, b"more data")

    def test_create_download(
        self, manager: TransferManager, tmp_downloads: Path
    ) -> None:
        test_file = tmp_downloads / "download_test.txt"
        test_file.write_text("download content")
        info = manager.create_download(test_file)
        assert info.filename == "download_test.txt"
        assert info.state == TransferState.IN_PROGRESS

    def test_read_chunks(
        self, manager: TransferManager, tmp_downloads: Path
    ) -> None:
        test_file = tmp_downloads / "read_test.txt"
        content = b"A" * 2048
        test_file.write_bytes(content)

        info = manager.create_download(test_file)
        chunks = []
        while True:
            chunk = manager.read_chunk(info.transfer_id)
            if chunk is None:
                break
            chunks.append(chunk)

        assert b"".join(chunks) == content
        assert info.state == TransferState.COMPLETED

    def test_download_nonexistent_file(self, manager: TransferManager) -> None:
        with pytest.raises(FileNotFoundError):
            manager.create_download(Path("/nonexistent/file.txt"))

    def test_cleanup_completed(self, manager: TransferManager) -> None:
        info = manager.create_upload("cleanup.txt", 100)
        manager.write_chunk(info.transfer_id, b"x" * 100)
        assert info.state == TransferState.COMPLETED

        removed = manager.cleanup_completed()
        assert removed == 1
        assert manager.get_transfer(info.transfer_id) is None

    def test_list_received_files(
        self, manager: TransferManager, tmp_downloads: Path
    ) -> None:
        (tmp_downloads / "file1.txt").write_text("content1")
        (tmp_downloads / "file2.pdf").write_text("content2")

        files = manager.list_received_files()
        assert len(files) == 2
        names = [f["name"] for f in files]
        assert "file1.txt" in names
        assert "file2.pdf" in names

    def test_checksum_on_completion(self, manager: TransferManager) -> None:
        data = b"checksum test data"
        info = manager.create_upload("checksum.txt", len(data))
        manager.write_chunk(info.transfer_id, data)
        assert info.state == TransferState.COMPLETED
        assert len(info.checksum) == 64  # SHA-256 hex
