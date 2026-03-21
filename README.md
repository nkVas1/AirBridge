# ✈ AirBridge

**Wireless file transfer between Windows PC and iPhone — no internet required.**

AirBridge lets you send and receive any file between your Windows computer and iPhone over a local Wi-Fi network. It works completely offline — even via iPhone Personal Hotspot with no internet connection.

---

## How It Works

```
┌──────────────┐     Wi-Fi / Hotspot     ┌──────────────┐
│  Windows PC  │ ◄──────────────────────► │    iPhone     │
│  (Python     │    WebSocket chunks      │  (Safari PWA) │
│   Server)    │    AES-256-GCM           │               │
└──────────────┘    mDNS discovery        └──────────────┘
```

1. **Run AirBridge** on your Windows PC — it starts a local server
2. **Open Safari** on your iPhone and navigate to the displayed URL
3. **Enter the PIN** shown on the PC screen
4. **Send files** in either direction — drag & drop or tap to select

No app installation needed on iPhone. No internet needed. No accounts. No cloud.

---

## Features

| Feature | Description |
|---------|-------------|
| 📡 **Zero-configuration** | mDNS/Bonjour auto-discovery — no manual IP entry needed |
| 🔐 **AES-256-GCM encryption** | End-to-end encrypted transfers |
| 📌 **PIN authentication** | 6-digit PIN + QR code for instant pairing |
| ⚡ **WebSocket chunked transfer** | Real-time progress, speed, and ETA |
| 🔄 **Bidirectional** | Send files from phone to PC and PC to phone |
| 📱 **Progressive Web App** | Works in Safari, can be added to Home Screen |
| 🌐 **Fully offline** | Works via Personal Hotspot without internet |
| 📊 **Integrity verification** | SHA-256 checksums on every transfer |
| 🗂️ **Any file type** | Photos, videos, documents, archives — up to 10 GB |
| 🛡️ **Path traversal protection** | Sanitized filenames prevent directory attacks |

---

## Quick Start

### Requirements

- **PC**: Python 3.10+ (Windows, macOS, or Linux)
- **Phone**: Safari on iOS 15+ (or any modern mobile browser)
- **Network**: Same Wi-Fi network, or iPhone Personal Hotspot

### Installation

```bash
# Clone the repository
git clone https://github.com/nkVas1/AirBridge.git
cd AirBridge

# Install dependencies
pip install -r requirements.txt

# Run AirBridge
python -m airbridge
```

### Usage

1. Start AirBridge on your PC:
   ```bash
   python -m airbridge
   ```

2. You'll see:
   ```
   ============================================================
     ✈  AirBridge — Wireless File Transfer
   ============================================================

     Server running at:  http://192.168.1.100:8090
     Connection PIN:     482901
     Downloads folder:   ~/Downloads/AirBridge_Downloads

     On your iPhone:
       1. Connect to the same Wi-Fi network
       2. Open Safari and go to http://192.168.1.100:8090
       3. Enter PIN: 482901

     Or use Personal Hotspot for offline transfer!
   ============================================================
   ```

3. Open the URL in Safari on your iPhone
4. Enter the PIN and start transferring files!

### Command-Line Options

```bash
python -m airbridge --port 9000              # Custom port
python -m airbridge --downloads-dir ~/Files   # Custom download location
python -m airbridge --log-level DEBUG         # Verbose logging
```

---

## Offline Mode (No Internet)

AirBridge works perfectly without any internet connection:

1. **Enable Personal Hotspot** on your iPhone (Settings → Personal Hotspot)
2. **Connect your PC** to the iPhone's hotspot Wi-Fi
3. **Run AirBridge** on your PC — it will detect the network
4. **Open Safari** on your iPhone and navigate to the displayed URL

The connection is entirely local — no data ever leaves your devices.

---

## Architecture

```
AirBridge/
├── airbridge/                 # Python server package
│   ├── __init__.py            # Package metadata
│   ├── __main__.py            # CLI entry point
│   ├── server.py              # HTTP + WebSocket server (aiohttp)
│   ├── config.py              # Configuration management
│   ├── crypto.py              # AES-256-GCM encryption
│   ├── auth.py                # PIN authentication + QR codes
│   ├── transfer.py            # Chunked file transfer engine
│   └── discovery.py           # mDNS/Bonjour service discovery
├── webapp/                    # Progressive Web App (served by Python)
│   ├── index.html             # Main UI
│   ├── manifest.json          # PWA manifest
│   ├── sw.js                  # Service worker for offline caching
│   ├── css/style.css          # Responsive styles
│   ├── js/app.js              # Client-side application logic
│   └── icons/                 # PWA icons
├── tests/                     # Test suite (pytest)
│   ├── test_crypto.py         # Encryption tests
│   ├── test_auth.py           # Authentication tests
│   ├── test_transfer.py       # Transfer engine tests
│   └── test_server.py         # HTTP/WebSocket endpoint tests
├── pyproject.toml             # Project configuration
├── requirements.txt           # Production dependencies
└── requirements-dev.txt       # Development dependencies
```

### Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Server | aiohttp | Async HTTP + WebSocket server |
| Discovery | zeroconf | mDNS/Bonjour zero-configuration |
| Encryption | cryptography | AES-256-GCM authenticated encryption |
| QR Codes | qrcode + Pillow | Connection QR code generation |
| Frontend | Vanilla JS + CSS | PWA with no build step needed |
| Testing | pytest + pytest-asyncio | Full async test coverage |

---

## Development

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
python -m pytest tests/ -v

# Lint
ruff check airbridge/ tests/

# Type check
mypy airbridge/
```

---

## WebSocket Protocol

The transfer protocol uses JSON text frames for metadata and binary frames for file data:

### Client → Server

| Message | Fields | Description |
|---------|--------|-------------|
| `auth` | `pin`, `session_id` | Authenticate with PIN |
| `upload_start` | `filename`, `size`, `mime_type` | Initialize upload |
| `upload_chunk` | `transfer_id` | Signal chunk (binary frame follows) |
| `upload_cancel` | `transfer_id` | Cancel active upload |
| `download_request` | `filename` | Request file download |
| `ping` | — | Keep-alive |

### Server → Client

| Message | Fields | Description |
|---------|--------|-------------|
| `auth_result` | `authenticated`, `session_id` | Auth response |
| `upload_ready` | `transfer_id`, `total_chunks`, `chunk_size` | Upload accepted |
| `chunk_ack` | `transfer_id`, `progress`, `speed_bps`, `eta_seconds` | Chunk received |
| `upload_complete` | `transfer_id`, `checksum` | Upload finished |
| `download_start` | `transfer_id`, `filename`, `file_size` | Download begins |
| `download_chunk` | `transfer_id`, `chunk_index` | Chunk follows (binary) |
| `download_complete` | `transfer_id`, `checksum` | Download finished |
| `error` | `message` | Error description |

---

## License

[MIT](LICENSE)