"""AirBridge HTTP and WebSocket server.

Provides:
- Static file serving for the PWA web interface
- REST API for device info, file listing, authentication
- WebSocket endpoint for chunked file transfer with progress
- QR code and certificate endpoints for easy mobile pairing
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import mimetypes
import ssl
import sys
import uuid
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from airbridge import __version__
from airbridge.auth import AuthManager
from airbridge.config import Config
from airbridge.discovery import ServiceDiscovery, get_local_ip
from airbridge.tls import (
    CertificatePaths,
    build_ssl_context,
    certificate_der,
    certificate_fingerprint,
    ensure_certificates,
)
from airbridge.transfer import TransferManager, TransferState

logger = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).parent / "webapp"

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
    content_type, _ = mimetypes.guess_type(file_path.name)
    if content_type:
        return content_type
    return _EXTRA_MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")


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
        scheme=config.scheme,
    )
    app["certificates"] = None

    # Register routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/info", handle_info)
    app.router.add_post("/api/auth", handle_auth)
    app.router.add_get("/api/qr", handle_qr)
    app.router.add_get("/api/files", handle_files)
    app.router.add_get("/api/files/{filename}", handle_download_file)
    app.router.add_get("/api/transfers", handle_transfers)
    app.router.add_get("/ca.crt", handle_ca_certificate)
    app.router.add_get("/ws", handle_websocket)

    # Serve static webapp files
    if WEBAPP_DIR.is_dir():
        app.router.add_static("/static", WEBAPP_DIR, show_index=False)
        # Serve specific webapp files at root level
        for sub in ("manifest.json", "sw.js"):
            sub_path = WEBAPP_DIR / sub
            if sub_path.exists():
                app.router.add_get(f"/{sub}", _make_file_handler(sub_path))
        # Browsers request /favicon.ico unprompted; answer it rather than
        # logging a 404 on every page load.
        icon_path = WEBAPP_DIR / "icons" / "icon-192.png"
        if icon_path.exists():
            app.router.add_get("/favicon.ico", _make_file_handler(icon_path))

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
        ip = await discovery.register()
        logger.info("AirBridge available at %s", app["config"].url_for(ip))
    except Exception:
        logger.warning("mDNS registration failed — manual IP entry required", exc_info=True)


async def on_shutdown(app: web.Application) -> None:
    """Unregister mDNS and release open files on shutdown."""
    discovery: ServiceDiscovery = app["discovery"]
    try:
        await discovery.unregister()
    except Exception:
        logger.debug("mDNS teardown failed", exc_info=True)
    # Partial uploads stay on disk so they can be continued later.
    transfer_mgr: TransferManager = app["transfer_manager"]
    transfer_mgr.close_all()


# --- HTTP Handlers ---


async def handle_index(request: web.Request) -> web.StreamResponse:
    """Serve the main PWA page."""
    index_path = WEBAPP_DIR / "index.html"
    if index_path.is_file():
        return web.FileResponse(index_path)
    return web.Response(
        text="AirBridge server is running. Web UI not found.",
        content_type="text/plain",
    )


async def handle_info(request: web.Request) -> web.Response:
    """Return server information, including every address it answers on."""
    config: Config = request.app["config"]
    ip = get_local_ip()
    return web.json_response({
        "service": config.service_name,
        "version": __version__,
        "ip": ip,
        "port": config.port,
        "url": config.url_for(ip),
        "scheme": config.scheme,
        "encrypted": config.use_tls,
        "endpoints": config.endpoints_for(ip),
        "chunk_size": config.chunk_size,
        "max_file_size": config.max_file_size,
    })


async def handle_auth(request: web.Request) -> web.Response:
    """Authenticate a client session with PIN."""
    auth: AuthManager = request.app["auth"]
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    pin = body.get("pin", "")
    session_id = body.get("session_id", "") or uuid.uuid4().hex
    client = _client_key(request)

    locked_for = auth.lockout_remaining(client)
    if locked_for > 0:
        return web.json_response(
            {"authenticated": False, "error": "Too many attempts", "locked_for": round(locked_for)},
            status=429,
        )

    if auth.authenticate_session(session_id, pin, client):
        return web.json_response({"authenticated": True, "session_id": session_id})
    return web.json_response({"authenticated": False, "error": "Invalid PIN"}, status=401)


async def handle_qr(request: web.Request) -> web.Response:
    """Return QR code as base64-encoded PNG."""
    auth: AuthManager = request.app["auth"]
    config: Config = request.app["config"]
    base_url = config.url_for(get_local_ip())
    return web.json_response({
        "qr": auth.generate_qr_base64(base_url),
        "url": base_url,
        "pairing_url": auth.pairing_url(base_url),
        "pin": auth.pin,
    })


async def handle_ca_certificate(request: web.Request) -> web.StreamResponse:
    """Serve the local authority certificate for installation on a phone.

    iOS only offers to install a certificate when it arrives in DER form
    under the x509 CA content type; handed PEM text it downloads a file
    nobody can act on.
    """
    certificates: CertificatePaths | None = request.app["certificates"]
    if certificates is None:
        return web.json_response({"error": "Server is running without TLS"}, status=404)
    return web.Response(
        body=certificate_der(certificates.ca_cert),
        content_type="application/x-x509-ca-cert",
        headers={"Content-Disposition": 'attachment; filename="airbridge-ca.crt"'},
    )


async def handle_files(request: web.Request) -> web.Response:
    """List received files."""
    if not _check_auth(request):
        return web.json_response({"error": "Not authenticated"}, status=401)
    tm: TransferManager = request.app["transfer_manager"]
    return web.json_response({"files": tm.list_received_files()})


async def handle_download_file(request: web.Request) -> web.StreamResponse:
    """Download a specific file from the server."""
    if not _check_auth(request):
        return web.json_response({"error": "Not authenticated"}, status=401)

    config: Config = request.app["config"]
    # Sanitize: only allow a bare filename, no path traversal
    safe_name = Path(request.match_info["filename"]).name
    file_path = config.downloads_dir / safe_name

    if not file_path.is_file():
        return web.json_response({"error": "File not found"}, status=404)

    return web.FileResponse(
        file_path,
        headers={"Content-Type": _guess_content_type(file_path)},
    )


async def handle_transfers(request: web.Request) -> web.Response:
    """Return status of all active transfers, and anything left half-sent."""
    if not _check_auth(request):
        return web.json_response({"error": "Not authenticated"}, status=401)
    tm: TransferManager = request.app["transfer_manager"]
    return web.json_response({
        "transfers": {tid: info.to_dict() for tid, info in tm.active_transfers.items()},
        "partial": tm.list_partial_files(),
    })


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
        {"type": "upload_ready", "transfer_id": "...", "resume_from": N, ...}
        {"type": "chunk_ack", "transfer_id": "...", "progress": 50.0, ...}
        {"type": "upload_complete", "transfer_id": "...", "checksum": "..."}
        {"type": "download_start", "transfer_id": "...", "filename": "...", ...}
        {"type": "download_chunk", "transfer_id": "..."}  (followed by binary frame)
        {"type": "download_complete", "transfer_id": "...", "checksum": "..."}
        {"type": "error", "message": "..."}
    """
    ws = web.WebSocketResponse(max_msg_size=0)  # No message size limit
    await ws.prepare(request)

    auth: AuthManager = request.app["auth"]
    tm: TransferManager = request.app["transfer_manager"]
    config: Config = request.app["config"]
    client = _client_key(request)

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
                    session_id = data.get("session_id") or uuid.uuid4().hex
                    locked_for = auth.lockout_remaining(client)
                    if locked_for > 0:
                        await ws.send_json({
                            "type": "auth_result",
                            "authenticated": False,
                            "session_id": session_id,
                            "locked_for": round(locked_for),
                        })
                        continue
                    authenticated = auth.authenticate_session(
                        session_id, data.get("pin", ""), client
                    )
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
                    file_size = data.get("size", 0)
                    if file_size > config.max_file_size:
                        await ws.send_json({
                            "type": "error",
                            "message": f"File too large. Max: {config.max_file_size} bytes",
                        })
                        continue

                    info = tm.create_upload(
                        data.get("filename", "unnamed"),
                        file_size,
                        data.get("mime_type", "application/octet-stream"),
                    )
                    current_transfer = info.transfer_id
                    await ws.send_json({
                        "type": "upload_ready",
                        "transfer_id": info.transfer_id,
                        "total_chunks": info.total_chunks,
                        "chunk_size": config.chunk_size,
                        "resume_from": info.resume_from,
                    })

                elif msg_type == "upload_chunk":
                    # The bytes arrive in the binary frame that follows.
                    current_transfer = data.get("transfer_id", current_transfer or "")

                elif msg_type == "upload_cancel":
                    transfer_id = data.get("transfer_id", current_transfer or "")
                    tm.abandon_transfer(transfer_id)
                    current_transfer = None
                    await ws.send_json({
                        "type": "upload_cancelled",
                        "transfer_id": transfer_id,
                    })

                elif msg_type == "download_request":
                    await _stream_download(ws, tm, config, data.get("filename", ""))

                elif msg_type == "ping":
                    await ws.send_json({"type": "pong"})

                else:
                    await ws.send_json({
                        "type": "error",
                        "message": f"Unknown message type: {msg_type}",
                    })

            elif msg.type == WSMsgType.BINARY:
                if not authenticated:
                    await ws.send_json({"type": "error", "message": "Not authenticated"})
                    continue

                if not current_transfer:
                    await ws.send_json({
                        "type": "error",
                        "message": "No active transfer for binary data",
                    })
                    continue

                try:
                    info = tm.write_chunk(current_transfer, msg.data)
                    response: dict[str, Any] = {"type": "chunk_ack", **info.to_dict()}
                    if info.state == TransferState.COMPLETED:
                        response["type"] = "upload_complete"
                        current_transfer = None
                    await ws.send_json(response)
                except (KeyError, RuntimeError) as exc:
                    await ws.send_json({"type": "error", "message": str(exc)})

            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break

    except Exception:
        logger.exception("WebSocket error")
    finally:
        if current_transfer:
            pending = tm.get_transfer(current_transfer)
            if pending is not None and pending.state == TransferState.IN_PROGRESS:
                # Keep the partial file: reconnecting resumes from it.
                tm.cancel_transfer(current_transfer)
                logger.info(
                    "Connection lost during transfer %s (%.1f%% kept for resume)",
                    current_transfer[:8],
                    pending.progress,
                )
        if session_id:
            auth.revoke_session(session_id)
        logger.info("WebSocket connection closed")

    return ws


async def _stream_download(
    ws: web.WebSocketResponse,
    tm: TransferManager,
    config: Config,
    filename: str,
) -> None:
    """Send a file from the downloads directory to the client, chunk by chunk."""
    safe_name = Path(filename).name
    file_path = config.downloads_dir / safe_name

    if not file_path.is_file():
        await ws.send_json({"type": "error", "message": f"File not found: {safe_name}"})
        return

    info = tm.create_download(file_path)
    await ws.send_json({"type": "download_start", **info.to_dict()})

    hasher = hashlib.sha256()
    with open(file_path, "rb") as handle:
        chunk_index = 0
        while True:
            chunk = handle.read(config.chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
            await ws.send_json({
                "type": "download_chunk",
                "transfer_id": info.transfer_id,
                "chunk_index": chunk_index,
            })
            await ws.send_bytes(chunk)
            chunk_index += 1

    info.state = TransferState.COMPLETED
    info.checksum = hasher.hexdigest()
    await ws.send_json({
        "type": "download_complete",
        "transfer_id": info.transfer_id,
        "checksum": info.checksum,
    })


def _client_key(request: web.Request) -> str:
    """Identify the caller for throttling purposes."""
    return request.remote or "unknown"


def _check_auth(request: web.Request) -> bool:
    """Check if request has a valid authenticated session."""
    auth: AuthManager = request.app["auth"]
    session_id = request.headers.get("X-Session-ID") or request.query.get("session_id") or ""
    return auth.is_authenticated(session_id)


# --- Startup ---


async def run_server(config: Config) -> None:
    """Start the AirBridge server on every address it is configured for."""
    app = create_app(config)
    ip = get_local_ip()

    ssl_context: ssl.SSLContext | None = None
    certificates: CertificatePaths | None = None
    if config.use_tls:
        certificates = ensure_certificates(config.cert_dir, ip)
        ssl_context = build_ssl_context(certificates)
        app["certificates"] = certificates

    runner = web.AppRunner(app)
    await runner.setup()

    sites = [web.TCPSite(runner, config.host, config.port, ssl_context=ssl_context)]
    if config.use_tls and config.http_fallback:
        # Safari refuses to open a WebSocket to a certificate it does not
        # trust, even after the user has waved the page's warning through.
        # A plain endpoint alongside the encrypted one means that phone
        # still has a way to transfer rather than a dead screen.
        sites.append(web.TCPSite(runner, config.host, config.fallback_port))

    for site in sites:
        await site.start()

    _print_banner(config, app["auth"], ip, certificates)

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await runner.cleanup()


def _print_banner(
    config: Config,
    auth_mgr: AuthManager,
    ip: str,
    certificates: CertificatePaths | None,
) -> None:
    """Print connection details, including a QR code the phone camera can read."""
    url = config.url_for(ip)
    rule = "=" * 64

    lines = [
        "",
        rule,
        "  AirBridge - Wireless File Transfer",
        rule,
        "",
        f"  Open on the phone:  {url}",
        f"  Connection PIN:     {auth_mgr.pin}",
        f"  Downloads folder:   {config.downloads_dir}",
        "",
    ]
    lines += _qr_section(auth_mgr, url)

    if certificates is not None:
        lines += [
            "  First time on this phone, to avoid warnings and make sure",
            "  transfers can be encrypted, install the AirBridge",
            f"  certificate from  {config.url_for(ip)}/ca.crt",
            "    iOS: Settings > Profile Downloaded > Install, then",
            "         Settings > General > About > Certificate Trust Settings",
            f"  Authority SHA-256: {certificate_fingerprint(certificates.ca_cert)}",
            "",
        ]
        if config.http_fallback:
            lines += [
                "  If the phone will not connect over HTTPS, this address",
                "  always works, without encryption:",
                f"    {config.fallback_url_for(ip)}",
                "",
            ]
    else:
        lines += [
            "  TLS is off (--no-tls): traffic is readable by anyone on",
            "  this network, and the browser disables offline caching.",
            "",
        ]

    lines += [rule, ""]
    _write_lines(lines)


def _qr_section(auth_mgr: AuthManager, url: str) -> list[str]:
    """Build the QR block, or an explanation of why it was left out.

    A Windows console on a legacy code page cannot draw the block
    characters the code is made of. Printing them anyway used to raise
    UnicodeEncodeError and kill the server before it was ever usable;
    printing replacement marks instead would produce an unreadable
    square. Naming the address is more useful than either.
    """
    code = auth_mgr.generate_qr_ascii(url).rstrip()
    if _console_can_render(code):
        return [
            "  Point the phone camera at this code:",
            "",
            code,
            "",
        ]
    return [
        "  Open the address above on the phone and enter the PIN.",
        "  (This console cannot draw the pairing QR code. `chcp 65001`",
        "   switches it to UTF-8, or open /api/qr in a browser.)",
        "",
    ]


def _console_can_render(text: str) -> bool:
    """Report whether stdout's encoding covers every character in `text`."""
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return False
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _write_lines(lines: list[str]) -> None:
    """Write banner lines, replacing anything the console cannot encode."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    for line in lines:
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode(encoding, errors="replace").decode(encoding))
    # Redirected output is block-buffered, and the server then runs
    # forever without filling the buffer: without this the address and
    # PIN never appear for anyone launching through a wrapper script.
    with contextlib.suppress(ValueError, OSError):
        sys.stdout.flush()
