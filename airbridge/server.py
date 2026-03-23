"""AirBridge HTTP and WebSocket server.

Provides:
- Static file serving for the PWA web interface
- REST API for device info, file listing, authentication
- WebSocket endpoint for chunked file transfer with progress
- QR code endpoint for easy mobile connection
"""

from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import uuid
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from airbridge.auth import AuthManager
from airbridge.config import Config
from airbridge.crypto import compute_checksum
from airbridge.discovery import ServiceDiscovery, get_local_ip
from airbridge.transfer import TransferManager, TransferState

logger = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).parent.parent / "webapp"

# Register additional MIME types for formats not always in the default database.
_EXTRA_MIME_TYPES: dict[str, str] = {
    ".mkv": "video/x-matroska",
    ".mk3d": "video/x-matroska-3d",
    ".mka": "audio/x-matroska",
    ".jfif": "image/jpeg",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".m4v": "video/mp4",
    ".3gp": "video/3gpp",
    ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv",
    ".ts": "video/mp2t",
    ".mts": "video/mp2t",
    ".m2ts": "video/mp2t",
    ".mjs": "application/javascript",
}
for _ext, _mime in _EXTRA_MIME_TYPES.items():
    mimetypes.add_type(_mime, _ext)


def _guess_content_type(file_path: Path) -> str:
    """Guess the MIME content type for a file, with fallback to extended types."""
    ct, _ = mimetypes.guess_type(file_path.name)
    if ct:
        return ct
    ext = file_path.suffix.lower()
    return _EXTRA_MIME_TYPES.get(ext, "application/octet-stream")


def create_app(config: Config) -> web.Application:
    """Create and configure the aiohttp application.

    Args:
        config: Server configuration.

    Returns:
        Configured aiohttp Application.
    """
    app = web.Application(client_max_size=config.max_file_size)
    app["config"] = config
    app["auth"] = AuthManager(pin_length=config.pin_length)
    app["transfer_manager"] = TransferManager(
        downloads_dir=config.downloads_dir,
        chunk_size=config.chunk_size,
    )
    app["discovery"] = ServiceDiscovery(
        service_name=config.service_name,
        service_type=config.mdns_type,
        port=config.port,
    )

    # Register routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/info", handle_info)
    app.router.add_post("/api/auth", handle_auth)
    app.router.add_get("/api/qr", handle_qr)
    app.router.add_get("/api/files", handle_files)
    app.router.add_get("/api/files/{filename}", handle_download_file)
    app.router.add_get("/api/transfers", handle_transfers)
    app.router.add_get("/ws", handle_websocket)

    # Serve static webapp files
    if WEBAPP_DIR.is_dir():
        app.router.add_static("/static", WEBAPP_DIR, show_index=False)
        # Serve specific webapp files at root level
        for sub in ("manifest.json", "sw.js"):
            sub_path = WEBAPP_DIR / sub
            if sub_path.exists():
                app.router.add_get(f"/{sub}", _make_file_handler(sub_path))

    # Lifecycle hooks
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # CORS middleware
    app.middlewares.append(cors_middleware)

    return app


def _make_file_handler(file_path: Path):  # type: ignore[no-untyped-def]
    """Create a handler that serves a specific file."""

    async def handler(request: web.Request) -> web.FileResponse:
        return web.FileResponse(file_path)

    return handler


@web.middleware
async def cors_middleware(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    """Add CORS headers to all responses for cross-origin PWA access."""
    if request.method == "OPTIONS":
        response = web.Response()
    else:
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = exc
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Session-ID"
    return response


async def on_startup(app: web.Application) -> None:
    """Register mDNS service on startup."""
    discovery: ServiceDiscovery = app["discovery"]
    try:
        ip = discovery.register()
        logger.info("AirBridge available at http://%s:%d", ip, app["config"].port)
    except Exception:
        logger.warning("mDNS registration failed — manual IP entry required", exc_info=True)


async def on_shutdown(app: web.Application) -> None:
    """Unregister mDNS service on shutdown."""
    discovery: ServiceDiscovery = app["discovery"]
    discovery.unregister()
    # Clean up active transfers
    transfer_mgr: TransferManager = app["transfer_manager"]
    for tid in list(transfer_mgr.active_transfers.keys()):
        transfer_mgr.cancel_transfer(tid)


# --- HTTP Handlers ---


async def handle_index(request: web.Request) -> web.Response:
    """Serve the main PWA page."""
    index_path = WEBAPP_DIR / "index.html"
    if index_path.is_file():
        return web.FileResponse(index_path)
    return web.Response(
        text="AirBridge server is running. Web UI not found.",
        content_type="text/plain",
    )


async def handle_info(request: web.Request) -> web.Response:
    """Return server information."""
    config: Config = request.app["config"]
    ip = get_local_ip()
    return web.json_response({
        "service": config.service_name,
        "version": "1.0.0",
        "ip": ip,
        "port": config.port,
        "url": f"http://{ip}:{config.port}",
        "chunk_size": config.chunk_size,
        "max_file_size": config.max_file_size,
    })


async def handle_auth(request: web.Request) -> web.Response:
    """Authenticate a client session with PIN."""
    auth: AuthManager = request.app["auth"]
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    pin = body.get("pin", "")
    session_id = body.get("session_id", "")

    if not session_id:
        session_id = uuid.uuid4().hex

    if auth.authenticate_session(session_id, pin):
        return web.json_response({
            "authenticated": True,
            "session_id": session_id,
        })
    return web.json_response({"authenticated": False, "error": "Invalid PIN"}, status=401)


async def handle_qr(request: web.Request) -> web.Response:
    """Return QR code as base64-encoded PNG."""
    auth: AuthManager = request.app["auth"]
    config: Config = request.app["config"]
    ip = get_local_ip()
    qr_b64 = auth.generate_qr_base64(ip, config.port)
    return web.json_response({
        "qr": qr_b64,
        "url": f"http://{ip}:{config.port}",
        "pin": auth.pin,
    })


async def handle_files(request: web.Request) -> web.Response:
    """List received files."""
    if not _check_auth(request):
        return web.json_response({"error": "Not authenticated"}, status=401)
    tm: TransferManager = request.app["transfer_manager"]
    return web.json_response({"files": tm.list_received_files()})


async def handle_download_file(request: web.Request) -> web.Response:
    """Download a specific file from the server."""
    if not _check_auth(request):
        return web.json_response({"error": "Not authenticated"}, status=401)

    filename = request.match_info["filename"]
    config: Config = request.app["config"]
    # Sanitize: only allow filename, no path traversal
    safe_name = Path(filename).name
    file_path = config.downloads_dir / safe_name

    if not file_path.is_file():
        return web.json_response({"error": "File not found"}, status=404)

    content_type = _guess_content_type(file_path)
    return web.FileResponse(
        file_path,
        headers={"Content-Type": content_type},
    )


async def handle_transfers(request: web.Request) -> web.Response:
    """Return status of all active transfers."""
    if not _check_auth(request):
        return web.json_response({"error": "Not authenticated"}, status=401)
    tm: TransferManager = request.app["transfer_manager"]
    transfers = {tid: info.to_dict() for tid, info in tm.active_transfers.items()}
    return web.json_response({"transfers": transfers})


# --- WebSocket Handler ---


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    """Handle WebSocket connections for file transfer.

    Protocol messages (JSON text frames):
      Client -> Server:
        {"type": "auth", "pin": "123456", "session_id": "..."}
        {"type": "upload_start", "filename": "...", "size": 12345, "mime_type": "..."}
        {"type": "upload_chunk", "transfer_id": "..."}  (followed by binary frame)
        {"type": "upload_cancel", "transfer_id": "..."}
        {"type": "download_request", "filename": "..."}

      Server -> Client:
        {"type": "auth_result", "authenticated": true/false, "session_id": "..."}
        {"type": "upload_ready", "transfer_id": "...", "total_chunks": N}
        {"type": "chunk_ack", "transfer_id": "...", "received": N, "progress": 50.0, ...}
        {"type": "upload_complete", "transfer_id": "...", "checksum": "..."}
        {"type": "download_start", "transfer_id": "...", "filename": "...", "size": N, ...}
        {"type": "download_chunk", "transfer_id": "..."}  (followed by binary frame)
        {"type": "download_complete", "transfer_id": "...", "checksum": "..."}
        {"type": "error", "message": "..."}
    """
    ws = web.WebSocketResponse(max_msg_size=0)  # No message size limit
    await ws.prepare(request)

    auth: AuthManager = request.app["auth"]
    tm: TransferManager = request.app["transfer_manager"]
    config: Config = request.app["config"]

    session_id: str = ""
    authenticated = False
    current_transfer: str | None = None

    logger.info("WebSocket connection opened from %s", request.remote)

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "message": "Invalid JSON"})
                    continue

                msg_type = data.get("type", "")

                if msg_type == "auth":
                    session_id = data.get("session_id", uuid.uuid4().hex)
                    pin = data.get("pin", "")
                    authenticated = auth.authenticate_session(session_id, pin)
                    await ws.send_json({
                        "type": "auth_result",
                        "authenticated": authenticated,
                        "session_id": session_id,
                    })

                elif not authenticated:
                    await ws.send_json({
                        "type": "error",
                        "message": "Not authenticated. Send auth message first.",
                    })

                elif msg_type == "upload_start":
                    filename = data.get("filename", "unnamed")
                    file_size = data.get("size", 0)
                    mime_type = data.get("mime_type", "application/octet-stream")

                    if file_size > config.max_file_size:
                        await ws.send_json({
                            "type": "error",
                            "message": f"File too large. Max: {config.max_file_size} bytes",
                        })
                        continue

                    info = tm.create_upload(filename, file_size, mime_type)
                    current_transfer = info.transfer_id
                    await ws.send_json({
                        "type": "upload_ready",
                        "transfer_id": info.transfer_id,
                        "total_chunks": info.total_chunks,
                        "chunk_size": config.chunk_size,
                    })

                elif msg_type == "upload_chunk":
                    transfer_id = data.get("transfer_id", current_transfer or "")
                    current_transfer = transfer_id
                    # The binary data follows in the next message
                    # Client sends: JSON text -> binary data

                elif msg_type == "upload_cancel":
                    transfer_id = data.get("transfer_id", current_transfer or "")
                    tm.cancel_transfer(transfer_id)
                    current_transfer = None
                    await ws.send_json({
                        "type": "upload_cancelled",
                        "transfer_id": transfer_id,
                    })

                elif msg_type == "download_request":
                    filename = data.get("filename", "")
                    safe_name = Path(filename).name
                    file_path = config.downloads_dir / safe_name

                    if not file_path.is_file():
                        await ws.send_json({
                            "type": "error",
                            "message": f"File not found: {safe_name}",
                        })
                        continue

                    info = tm.create_download(file_path)
                    await ws.send_json({
                        "type": "download_start",
                        **info.to_dict(),
                    })

                    # Stream chunks
                    file_hash = ""
                    with open(file_path, "rb") as f:
                        import hashlib as _hashlib

                        hasher = _hashlib.sha256()
                        chunk_idx = 0
                        while True:
                            chunk = f.read(config.chunk_size)
                            if not chunk:
                                break
                            hasher.update(chunk)
                            await ws.send_json({
                                "type": "download_chunk",
                                "transfer_id": info.transfer_id,
                                "chunk_index": chunk_idx,
                            })
                            await ws.send_bytes(chunk)
                            chunk_idx += 1
                        file_hash = hasher.hexdigest()

                    info.state = TransferState.COMPLETED
                    await ws.send_json({
                        "type": "download_complete",
                        "transfer_id": info.transfer_id,
                        "checksum": file_hash,
                    })

                elif msg_type == "ping":
                    await ws.send_json({"type": "pong"})

                else:
                    await ws.send_json({
                        "type": "error",
                        "message": f"Unknown message type: {msg_type}",
                    })

            elif msg.type == WSMsgType.BINARY:
                # Binary frame = file chunk data
                if not authenticated:
                    await ws.send_json({
                        "type": "error",
                        "message": "Not authenticated",
                    })
                    continue

                if not current_transfer:
                    await ws.send_json({
                        "type": "error",
                        "message": "No active transfer for binary data",
                    })
                    continue

                try:
                    info = tm.write_chunk(current_transfer, msg.data)
                    response: dict[str, Any] = {
                        "type": "chunk_ack",
                        **info.to_dict(),
                    }
                    if info.state == TransferState.COMPLETED:
                        response["type"] = "upload_complete"
                        current_transfer = None

                    await ws.send_json(response)
                except (KeyError, RuntimeError) as e:
                    await ws.send_json({
                        "type": "error",
                        "message": str(e),
                    })

            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break

    except Exception:
        logger.exception("WebSocket error")
    finally:
        if current_transfer:
            transfer_info = tm.get_transfer(current_transfer)
            if transfer_info and transfer_info.state == TransferState.IN_PROGRESS:
                # Don't cancel — allow resume
                logger.info(
                    "Connection lost during transfer %s (%.1f%% complete)",
                    current_transfer[:8],
                    transfer_info.progress,
                )
        if session_id:
            auth.revoke_session(session_id)
        logger.info("WebSocket connection closed")

    return ws


def _check_auth(request: web.Request) -> bool:
    """Check if request has a valid authenticated session."""
    auth: AuthManager = request.app["auth"]
    session_id = request.headers.get("X-Session-ID", "")
    if not session_id:
        session_id = request.query.get("session_id", "")
    return auth.is_authenticated(session_id)


async def run_server(config: Config) -> None:
    """Start the AirBridge server."""
    app = create_app(config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.host, config.port)
    await site.start()

    ip = get_local_ip()
    auth_mgr: AuthManager = app["auth"]

    print()
    print("=" * 60)
    print("  ✈  AirBridge — Wireless File Transfer")
    print("=" * 60)
    print()
    print(f"  Server running at:  http://{ip}:{config.port}")
    print(f"  Connection PIN:     {auth_mgr.pin}")
    print(f"  Downloads folder:   {config.downloads_dir}")
    print()
    print("  On your iPhone:")
    print(f"    1. Connect to the same Wi-Fi network")
    print(f"    2. Open Safari and go to http://{ip}:{config.port}")
    print(f"    3. Enter PIN: {auth_mgr.pin}")
    print()
    print("  Or use Personal Hotspot for offline transfer!")
    print("=" * 60)
    print()

    # Keep running until interrupted
    try:
        import asyncio

        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await runner.cleanup()
