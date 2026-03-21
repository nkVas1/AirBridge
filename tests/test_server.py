"""Tests for AirBridge server HTTP and WebSocket endpoints."""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import AioHTTPTestCase, TestClient

from airbridge.config import Config
from airbridge.server import create_app


@pytest.fixture
def config(tmp_path):
    """Create test configuration."""
    return Config(
        host="127.0.0.1",
        port=0,  # Let OS pick port
        downloads_dir=tmp_path / "downloads",
        chunk_size=1024,
    )


@pytest.fixture
def app(config):
    """Create test application (without mDNS)."""
    application = create_app(config)
    # Remove lifecycle hooks for testing (mDNS not needed)
    application.on_startup.clear()
    application.on_shutdown.clear()
    return application


@pytest.fixture
async def client(app, aiohttp_client):
    """Create test client."""
    return await aiohttp_client(app)


class TestInfoEndpoint:
    async def test_returns_server_info(self, client: TestClient) -> None:
        resp = await client.get("/api/info")
        assert resp.status == 200
        data = await resp.json()
        assert data["service"] == "AirBridge"
        assert data["version"] == "1.0.0"
        assert "ip" in data
        assert "port" in data


class TestAuthEndpoint:
    async def test_successful_auth(self, client: TestClient, app) -> None:
        pin = app["auth"].pin
        resp = await client.post(
            "/api/auth",
            json={"pin": pin, "session_id": "test-session"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["authenticated"] is True
        assert data["session_id"] == "test-session"

    async def test_failed_auth(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/auth",
            json={"pin": "wrong", "session_id": "test-session"},
        )
        assert resp.status == 401
        data = await resp.json()
        assert data["authenticated"] is False

    async def test_invalid_body(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/auth",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400


class TestQREndpoint:
    async def test_returns_qr_data(self, client: TestClient) -> None:
        resp = await client.get("/api/qr")
        assert resp.status == 200
        data = await resp.json()
        assert "qr" in data
        assert "url" in data
        assert "pin" in data
        assert len(data["qr"]) > 0


class TestFilesEndpoint:
    async def test_unauthenticated_returns_401(self, client: TestClient) -> None:
        resp = await client.get("/api/files")
        assert resp.status == 401

    async def test_authenticated_returns_files(self, client: TestClient, app) -> None:
        # Authenticate first
        pin = app["auth"].pin
        auth_resp = await client.post(
            "/api/auth",
            json={"pin": pin, "session_id": "files-session"},
        )
        assert auth_resp.status == 200

        resp = await client.get(
            "/api/files",
            headers={"X-Session-ID": "files-session"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert "files" in data


class TestWebSocket:
    async def test_auth_flow(self, client: TestClient, app) -> None:
        pin = app["auth"].pin
        async with client.ws_connect("/ws") as ws:
            # Send auth
            await ws.send_json({
                "type": "auth",
                "pin": pin,
                "session_id": "ws-test",
            })
            msg = await ws.receive_json()
            assert msg["type"] == "auth_result"
            assert msg["authenticated"] is True

    async def test_auth_failure(self, client: TestClient) -> None:
        async with client.ws_connect("/ws") as ws:
            await ws.send_json({
                "type": "auth",
                "pin": "wrong",
                "session_id": "ws-test",
            })
            msg = await ws.receive_json()
            assert msg["type"] == "auth_result"
            assert msg["authenticated"] is False

    async def test_unauthenticated_command(self, client: TestClient) -> None:
        async with client.ws_connect("/ws") as ws:
            await ws.send_json({
                "type": "upload_start",
                "filename": "test.txt",
                "size": 100,
            })
            msg = await ws.receive_json()
            assert msg["type"] == "error"
            assert "Not authenticated" in msg["message"]

    async def test_upload_flow(self, client: TestClient, app) -> None:
        pin = app["auth"].pin
        async with client.ws_connect("/ws") as ws:
            # Auth
            await ws.send_json({
                "type": "auth",
                "pin": pin,
                "session_id": "upload-test",
            })
            auth_msg = await ws.receive_json()
            assert auth_msg["authenticated"] is True

            # Start upload
            data = b"Hello AirBridge Test!"
            await ws.send_json({
                "type": "upload_start",
                "filename": "test_upload.txt",
                "size": len(data),
            })
            ready_msg = await ws.receive_json()
            assert ready_msg["type"] == "upload_ready"
            transfer_id = ready_msg["transfer_id"]

            # Send chunk
            await ws.send_json({
                "type": "upload_chunk",
                "transfer_id": transfer_id,
            })
            await ws.send_bytes(data)

            # Receive completion
            result_msg = await ws.receive_json()
            assert result_msg["type"] == "upload_complete"
            assert result_msg["progress"] == 100.0

    async def test_ping_pong(self, client: TestClient, app) -> None:
        pin = app["auth"].pin
        async with client.ws_connect("/ws") as ws:
            await ws.send_json({
                "type": "auth",
                "pin": pin,
                "session_id": "ping-test",
            })
            await ws.receive_json()

            await ws.send_json({"type": "ping"})
            msg = await ws.receive_json()
            assert msg["type"] == "pong"
