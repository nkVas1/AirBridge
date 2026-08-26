"""Tests for AirBridge transfer module."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from airbridge.transfer import PART_SUFFIX, TransferInfo, TransferManager, TransferState


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
    def test_progress_from_bytes(self) -> None:
        info = TransferInfo(file_size=1000, bytes_received=250)
        assert info.progress == 25.0

    def test_progress_without_a_known_size(self) -> None:
        info = TransferInfo(total_chunks=0, received_chunks=0)
        assert info.progress == 0.0

    def test_progress_never_exceeds_one_hundred(self) -> None:
        info = TransferInfo(file_size=100, bytes_received=140)
        assert info.progress == 100.0

    def test_to_dict(self) -> None:
        info = TransferInfo(filename="test.txt", file_size=1024, bytes_received=512)
        payload = info.to_dict()
        assert payload["filename"] == "test.txt"
        assert payload["file_size"] == 1024
        assert payload["progress"] == 50.0
        assert payload["state"] == "pending"
        assert payload["resume_from"] == 0


class TestUploads:
    def test_create_upload(self, manager: TransferManager) -> None:
        info = manager.create_upload("test.txt", 2048)
        assert info.filename == "test.txt"
        assert info.file_size == 2048
        assert info.total_chunks == 2  # 2048 / 1024
        assert info.state == TransferState.PENDING

    def test_write_chunks(self, manager: TransferManager) -> None:
        info = manager.create_upload("test.txt", 2048)
        info = manager.write_chunk(info.transfer_id, b"x" * 1024)
        assert info.bytes_received == 1024
        assert info.state == TransferState.IN_PROGRESS

        info = manager.write_chunk(info.transfer_id, b"y" * 1024)
        assert info.bytes_received == 2048
        assert info.state == TransferState.COMPLETED

    def test_file_written_correctly(self, manager: TransferManager, tmp_downloads: Path) -> None:
        data = b"Hello, AirBridge!" + b"\x00" * (1024 - 17)
        info = manager.create_upload("hello.txt", len(data))
        manager.write_chunk(info.transfer_id, data)

        assert info.state == TransferState.COMPLETED
        saved = tmp_downloads / "hello.txt"
        assert saved.exists()
        assert saved.read_bytes() == data

    def test_partial_file_is_not_left_behind(
        self, manager: TransferManager, tmp_downloads: Path
    ) -> None:
        info = manager.create_upload("done.txt", 16)
        manager.write_chunk(info.transfer_id, b"0123456789abcdef")
        assert [p.name for p in tmp_downloads.iterdir()] == ["done.txt"]

    def test_filename_sanitization(self, manager: TransferManager) -> None:
        info = manager.create_upload("../../etc/passwd", 100)
        assert info.filename == "passwd"
        assert info.part_path is not None
        assert ".." not in str(info.part_path)

    def test_duplicate_filename(self, manager: TransferManager, tmp_downloads: Path) -> None:
        (tmp_downloads / "test.txt").write_text("existing")
        info = manager.create_upload("test.txt", 4)
        manager.write_chunk(info.transfer_id, b"abcd")
        assert info.save_path is not None
        assert info.save_path.name == "test_1.txt"
        assert (tmp_downloads / "test.txt").read_text() == "existing"

    def test_checksum_matches_the_bytes_written(self, manager: TransferManager) -> None:
        data = b"checksum test data"
        info = manager.create_upload("checksum.txt", len(data))
        manager.write_chunk(info.transfer_id, data)
        assert info.checksum == hashlib.sha256(data).hexdigest()

    def test_write_to_nonexistent_transfer(self, manager: TransferManager) -> None:
        with pytest.raises(KeyError):
            manager.write_chunk("nonexistent", b"data")

    def test_write_to_completed_transfer(self, manager: TransferManager) -> None:
        info = manager.create_upload("small.txt", 100)
        manager.write_chunk(info.transfer_id, b"x" * 100)
        with pytest.raises(RuntimeError):
            manager.write_chunk(info.transfer_id, b"more data")


class TestResume:
    def test_interrupted_upload_resumes_where_it_stopped(
        self, manager: TransferManager, tmp_downloads: Path
    ) -> None:
        payload = bytes(range(256)) * 8  # 2048 bytes
        first = manager.create_upload("big.bin", len(payload))
        manager.write_chunk(first.transfer_id, payload[:1024])
        # The connection drops: the handle closes, the part file stays.
        manager.cancel_transfer(first.transfer_id)

        second = manager.create_upload("big.bin", len(payload))
        assert second.resume_from == 1024
        assert second.progress == 50.0

        manager.write_chunk(second.transfer_id, payload[1024:])
        assert second.state == TransferState.COMPLETED
        assert (tmp_downloads / "big.bin").read_bytes() == payload

    def test_resumed_upload_hashes_the_whole_file(self, manager: TransferManager) -> None:
        payload = b"a" * 1024 + b"b" * 1024
        first = manager.create_upload("hash.bin", len(payload))
        manager.write_chunk(first.transfer_id, payload[:1024])
        manager.cancel_transfer(first.transfer_id)

        second = manager.create_upload("hash.bin", len(payload))
        manager.write_chunk(second.transfer_id, payload[1024:])
        assert second.checksum == hashlib.sha256(payload).hexdigest()

    def test_a_different_size_does_not_resume(self, manager: TransferManager) -> None:
        first = manager.create_upload("same-name.bin", 2048)
        manager.write_chunk(first.transfer_id, b"x" * 1024)
        manager.cancel_transfer(first.transfer_id)

        # Same name, different file: nothing may be carried over.
        other = manager.create_upload("same-name.bin", 4096)
        assert other.resume_from == 0

    def test_abandoning_discards_the_partial_file(
        self, manager: TransferManager, tmp_downloads: Path
    ) -> None:
        info = manager.create_upload("cancel.bin", 2048)
        manager.write_chunk(info.transfer_id, b"x" * 1024)
        manager.abandon_transfer(info.transfer_id)

        assert info.state == TransferState.CANCELLED
        assert list(tmp_downloads.iterdir()) == []
        assert manager.create_upload("cancel.bin", 2048).resume_from == 0

    def test_partial_files_are_listed_separately(self, manager: TransferManager) -> None:
        info = manager.create_upload("half.bin", 2048)
        manager.write_chunk(info.transfer_id, b"x" * 1024)
        manager.cancel_transfer(info.transfer_id)

        assert manager.list_received_files() == []
        partial = manager.list_partial_files()
        assert partial == [{"name": "half.bin", "size": 2048, "received": 1024}]

    def test_part_files_are_hidden_from_the_received_list(
        self, manager: TransferManager, tmp_downloads: Path
    ) -> None:
        (tmp_downloads / f"stray.bin.99{PART_SUFFIX}").write_bytes(b"x")
        (tmp_downloads / "real.txt").write_text("content")
        assert [f["name"] for f in manager.list_received_files()] == ["real.txt"]


class TestDownloads:
    def test_create_download(self, manager: TransferManager, tmp_downloads: Path) -> None:
        source = tmp_downloads / "download_test.txt"
        source.write_text("download content")
        info = manager.create_download(source)
        assert info.filename == "download_test.txt"
        assert info.state == TransferState.IN_PROGRESS
        assert info.file_size == source.stat().st_size

    def test_download_nonexistent_file(self, manager: TransferManager) -> None:
        with pytest.raises(FileNotFoundError):
            manager.create_download(Path("/nonexistent/file.txt"))


class TestListing:
    def test_list_received_files(self, manager: TransferManager, tmp_downloads: Path) -> None:
        (tmp_downloads / "file1.txt").write_text("content1")
        (tmp_downloads / "file2.pdf").write_text("content2")

        names = [f["name"] for f in manager.list_received_files()]
        assert sorted(names) == ["file1.txt", "file2.pdf"]
