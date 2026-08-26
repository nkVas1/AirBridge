"""End-to-end tests of the WebSocket transfer protocol.

These drive a real server through a real socket, because the parts most
likely to break — resuming after a dropped connection, and the digest the
client is asked to trust — only exist in the interaction between the two
sides, not in either one alone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient

from airbridge.config import Config
from airbridge.server import create_app

CHUNK = 1024


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        host="127.0.0.1",
        port=0,
        downloads_dir=tmp_path / "downloads",
        cert_dir=tmp_path / "certs",
        use_tls=False,
        chunk_size=CHUNK,
    )


@pytest.fixture
def app(config: Config):  # type: ignore[no-untyped-def]
    application = create_app(config)
    application.on_startup.clear()  # no mDNS in tests
    application.on_shutdown.clear()
    return application


@pytest.fixture
async def client(app, aiohttp_client):  # type: ignore[no-untyped-def]
    return await aiohttp_client(app)


async def _authenticate(ws, pin: str) -> None:  # type: ignore[no-untyped-def]
    await ws.send_json({"type": "auth", "pin": pin, "session_id": "test-session"})
    result = json.loads((await ws.receive()).data)
    assert result["authenticated"] is True


async def _send(ws, transfer_id: str, payload: bytes) -> dict:  # type: ignore[no-untyped-def]
    """Send one chunk and return the acknowledgement."""
    await ws.send_json({"type": "upload_chunk", "transfer_id": transfer_id})
    await ws.send_bytes(payload)
    return json.loads((await ws.receive()).data)


class TestUploadOverTheWire:
    async def test_a_file_arrives_intact(self, client: TestClient, app, tmp_path: Path) -> None:
        payload = bytes(range(256)) * 12  # 3072 bytes = 3 chunks
        async with client.ws_connect("/ws") as ws:
            await _authenticate(ws, app["auth"].pin)
            await ws.send_json({"type": "upload_start", "filename": "x.bin", "size": len(payload)})
            ready = json.loads((await ws.receive()).data)
            assert ready["resume_from"] == 0

            ack = {}
            for start in range(0, len(payload), CHUNK):
                ack = await _send(ws, ready["transfer_id"], payload[start : start + CHUNK])

        assert ack["type"] == "upload_complete"
        # The digest the client is told to compare against must be the
        # digest of what actually reached the disk.
        assert ack["checksum"] == hashlib.sha256(payload).hexdigest()
        assert (tmp_path / "downloads" / "x.bin").read_bytes() == payload

    async def test_transfer_resumes_after_the_connection_drops(
        self, client: TestClient, app, tmp_path: Path
    ) -> None:
        payload = bytes(range(256)) * 16  # 4096 bytes = 4 chunks

        # First attempt: send half the file, then walk away.
        async with client.ws_connect("/ws") as ws:
            await _authenticate(ws, app["auth"].pin)
            await ws.send_json({"type": "upload_start", "filename": "r.bin", "size": len(payload)})
            first = json.loads((await ws.receive()).data)
            assert first["resume_from"] == 0
            for start in range(0, 2 * CHUNK, CHUNK):
                await _send(ws, first["transfer_id"], payload[start : start + CHUNK])

        assert not (tmp_path / "downloads" / "r.bin").exists()

        # Second attempt: the server should ask only for what is missing.
        async with client.ws_connect("/ws") as ws:
            await _authenticate(ws, app["auth"].pin)
            await ws.send_json({"type": "upload_start", "filename": "r.bin", "size": len(payload)})
            second = json.loads((await ws.receive()).data)
            assert second["resume_from"] == 2 * CHUNK

            ack = {}
            for start in range(2 * CHUNK, len(payload), CHUNK):
                ack = await _send(ws, second["transfer_id"], payload[start : start + CHUNK])

        assert ack["type"] == "upload_complete"
        # The digest has to span the whole file, including the half sent
        # by a connection that no longer exists.
        assert ack["checksum"] == hashlib.sha256(payload).hexdigest()
        assert (tmp_path / "downloads" / "r.bin").read_bytes() == payload

    async def test_cancelling_leaves_nothing_to_resume(
        self, client: TestClient, app, tmp_path: Path
    ) -> None:
        payload = b"z" * (4 * CHUNK)
        async with client.ws_connect("/ws") as ws:
            await _authenticate(ws, app["auth"].pin)
            await ws.send_json({"type": "upload_start", "filename": "c.bin", "size": len(payload)})
            ready = json.loads((await ws.receive()).data)
            await _send(ws, ready["transfer_id"], payload[:CHUNK])

            await ws.send_json({"type": "upload_cancel", "transfer_id": ready["transfer_id"]})
            assert json.loads((await ws.receive()).data)["type"] == "upload_cancelled"

        assert list((tmp_path / "downloads").iterdir()) == []

    async def test_binary_before_authentication_is_refused(self, client: TestClient) -> None:
        async with client.ws_connect("/ws") as ws:
            await ws.send_bytes(b"payload")
            reply = json.loads((await ws.receive()).data)
            assert reply["type"] == "error"


class TestDownloadOverTheWire:
    async def test_a_download_reports_a_matching_digest(
        self, client: TestClient, app, tmp_path: Path
    ) -> None:
        payload = bytes(range(256)) * 10
        downloads = tmp_path / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        (downloads / "out.bin").write_bytes(payload)

        received = bytearray()
        async with client.ws_connect("/ws") as ws:
            await _authenticate(ws, app["auth"].pin)
            await ws.send_json({"type": "download_request", "filename": "out.bin"})

            start = json.loads((await ws.receive()).data)
            assert start["type"] == "download_start"
            assert start["file_size"] == len(payload)

            while True:
                message = await ws.receive()
                if message.type.name == "BINARY":
                    received.extend(message.data)
                    continue
                frame = json.loads(message.data)
                if frame["type"] == "download_complete":
                    assert frame["checksum"] == hashlib.sha256(payload).hexdigest()
                    break
                assert frame["type"] == "download_chunk"

        assert bytes(received) == payload

    async def test_path_traversal_is_refused(self, client: TestClient, app) -> None:
        async with client.ws_connect("/ws") as ws:
            await _authenticate(ws, app["auth"].pin)
            await ws.send_json({"type": "download_request", "filename": "../../secrets.txt"})
            reply = json.loads((await ws.receive()).data)
            assert reply["type"] == "error"
            assert "secrets.txt" in reply["message"]
