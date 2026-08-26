<div align="center">

# AirBridge

**Wireless file transfer between a Windows PC and an iPhone, over your own network.**
*The phone installs nothing. The network needs no internet. Nothing is uploaded anywhere.*

[![Download](https://img.shields.io/github/v/release/nkVas1/AirBridge?label=download&color=00b140)](https://github.com/nkVas1/AirBridge/releases/latest)
[![CI](https://github.com/nkVas1/AirBridge/actions/workflows/ci.yml/badge.svg)](https://github.com/nkVas1/AirBridge/actions/workflows/ci.yml)
[![Platform](https://img.shields.io/badge/platform-Windows_·_iOS_Safari-0078D6?logo=windows)](#requirements)
[![Stack](https://img.shields.io/badge/stack-Python_·_aiohttp_·_PWA-3776AB?logo=python&logoColor=white)](#how-it-works)
[![License](https://img.shields.io/badge/license-source--available_NC-lightgrey)](LICENSE)

<img src="docs/screenshots/connect.png" width="255" alt="Pairing screen with the PIN field" />&nbsp;&nbsp;<img src="docs/screenshots/send.png" width="255" alt="Send tab with a completed transfer" />&nbsp;&nbsp;<img src="docs/screenshots/received.png" width="255" alt="Files received on the PC, with thumbnails" />

</div>

---

## What is this?

Moving a file between a Windows PC and an iPhone is a solved problem
everywhere except between those two devices. AirDrop stops at the edge of
the Apple ecosystem. A cable needs software on the PC and still argues
about file types. Everything else routes a private file through somebody
else's server to travel three metres.

The tools that do solve it each ask for something:

| | on the phone | needs internet | notes |
|---|---|---|---|
| **AirDrop** | built in | no | Apple devices only — no Windows |
| **[LocalSend](https://github.com/localsend/localsend)** | install an app | no | both ends need the app installed |
| **[PairDrop](https://github.com/schlagmichdoch/PairDrop)** / Snapdrop | browser only | **yes** | a signalling server has to introduce the peers |
| **Cloud drives** | install an app | **yes** | the file leaves your network |
| **AirBridge** | **browser only** | **no** | one server, on the PC you already control |

The gap AirBridge fills is the intersection of the last two columns:
nothing to install on the phone, *and* nothing outside the room. You run
a small server on the PC; the phone opens it in Safari. That is the whole
system. It works on a hotel network, and on an iPhone Personal Hotspot
with cellular data switched off.

**It is a personal tool, not a product.** No account, no telemetry, no
update channel, no support commitment. If you want something maintained
by a team, LocalSend is the better answer and is genuinely good.

## How it works

```
   Windows PC                                        iPhone
┌────────────────────┐                        ┌────────────────────┐
│  python -m         │                        │                    │
│    airbridge       │   1. scan the QR code  │  Camera app        │
│                    │ ─────────────────────► │       │            │
│  ┌──────────────┐  │                        │       ▼            │
│  │ aiohttp      │  │   2. TLS 1.3 handshake │  ┌──────────────┐  │
│  │  HTTPS + WSS │◄─┼────────────────────────┼─►│ Safari       │  │
│  │              │  │      AES-256-GCM       │  │  PWA, no     │  │
│  │  PIN auth    │  │                        │  │  install     │  │
│  │  SHA-256     │  │   3. 64 KB chunks,     │  └──────────────┘  │
│  └──────┬───────┘  │      either direction  │                    │
│         │          │                        │                    │
│  ~/Downloads/      │   4. mDNS announce     │                    │
│    AirBridge_...   │ ◄──────────────────────┤  airbridge.local   │
└────────────────────┘                        └────────────────────┘
         └─ your router, or the phone's own hotspot ─┘
                     no path to the internet
```

Metadata travels as JSON text frames, file bytes as binary frames on the
same socket. The server writes each chunk straight to disk and hashes it
as it goes, so memory use does not scale with file size.

### Why HTTPS, for a thing on your own LAN

Not for the padlock. Browsers gate half their capabilities behind a
*secure context*: on a plain `http://192.168.x.x` page, `crypto.subtle`
is undefined and `navigator.serviceWorker` does not exist. Serving over
TLS is what makes offline caching possible at all — and it keeps anyone
else on the same Wi-Fi from reading the file in transit.

No certificate authority will vouch for `192.168.1.5`, so AirBridge signs
its own certificate and re-issues it when the address changes. **Safari
warns once per certificate**: tap *Show Details*, then *visit this
website*. The terminal prints the SHA-256 fingerprint so the certificate
can be checked against the machine that issued it.

`--no-tls` serves plain HTTP instead. Transfers are then readable by
anyone on the network, and offline caching stays off.

## Quick start

```bash
git clone https://github.com/nkVas1/AirBridge
cd AirBridge
pip install -r requirements.txt
python -m airbridge
```

On Windows, `start.bat` does the same and creates a virtual environment
first. On macOS and Linux, `start.sh`.

The terminal prints the address, the PIN, and a QR code:

```
============================================================
  AirBridge - Wireless File Transfer
============================================================

  Server running at:  https://192.168.1.59:8090
  Connection PIN:     482901
  Downloads folder:   C:\Users\you\Downloads\AirBridge_Downloads

  Point your phone camera at this code:

        █████████████████████████████████████
        ████ ▄▄▄▄▄ █▀  ▄▄▄▀▀  █▀██ ▄▄▄▄▄ ████
        ████ █   █ ██ ▄▀█  ▀▄▄▀▀▄█ █   █ ████
        ████ █▄▄▄█ █▄█   ▀▄██▄█▄ █ █▄▄▄█ ████
        ████▄▄▄▄▄▄▄█▄▀ █▄▀ █ █ ▀ █▄▄▄▄▄▄▄████
                        ( ... )
        █████████████████████████████████████

  Or open the address by hand and enter the PIN.

  The certificate is self-signed, so the phone warns once:
    tap "Show Details" -> "visit this website" to continue.
  Certificate SHA-256: 21:25:9A:87:E0:F3:B3:47:...
============================================================
```

Point the iPhone camera at the code and tap the notification. The PIN
travels inside the link, so the app connects on its own and then strips
the PIN back out of the address bar.

### Without any internet

1. Turn on **Personal Hotspot** on the iPhone. Cellular data can stay off.
2. Connect the PC to that hotspot.
3. Run AirBridge and scan the code.

Both devices are then on a network with no route out, which is the point.

### Options

```bash
python -m airbridge --port 9000               # different port
python -m airbridge --downloads-dir D:/Inbox  # where received files land
python -m airbridge --no-tls                  # plain HTTP, no encryption
python -m airbridge --log-level DEBUG         # verbose
```

The same settings exist as `AIRBRIDGE_PORT`, `AIRBRIDGE_DOWNLOADS`,
`AIRBRIDGE_TLS`, `AIRBRIDGE_CERT_DIR` and `AIRBRIDGE_LOG_LEVEL`.

## Requirements

- **PC** — Python 3.10+. Built and used on Windows 11; the code is plain
  cross-platform Python and runs on macOS and Linux, but those get far
  less exercise.
- **Phone** — Safari on iOS 15+, or any current mobile browser.
- **Network** — both devices on the same Wi-Fi, or the PC on the phone's
  hotspot. No internet needed either way.

## Known limitations

Named here rather than discovered later:

- **A dropped transfer does not resume.** The connection is allowed to
  close mid-file and the partial file stays on disk; there is no code to
  pick it back up.
- **The client does not verify the checksum.** The server hashes every
  transfer with SHA-256 and reports the digest, but the browser side
  ignores it, so corruption would go unnoticed.
- **The PIN is six digits and is not rate-limited.** It is a barrier
  against the wrong person on the same network tapping *Connect*, not
  against a determined attacker. It changes on every restart.
- **The certificate is self-signed**, so each new certificate costs one
  browser warning.
- **mDNS is best-effort.** `airbridge.local` fails to register on some
  Windows setups; the server logs a warning and carries on, and the
  numeric address in the banner always works.
- **Run it from the checkout, not from `pip install`.** `webapp/` sits
  outside the Python package, so an installed wheel has the server but
  none of the web interface, and the `airbridge` console script it puts
  on PATH answers with a plain-text placeholder. Packaging the assets
  properly means moving them into the package.
- `crypto.py` still carries AES-GCM helpers that nothing calls. Transport
  security is TLS; those functions are vestigial.
- **None of this has been security-audited.** It is one person's tool,
  published as-is.

## Development

```bash
pip install -r requirements-dev.txt

pytest tests/ -q                  # 73 tests
ruff check airbridge/ tests/
mypy airbridge/
```

CI runs all three on Windows and Linux across Python 3.10, 3.11 and 3.12.

### Layout

```
airbridge/
  __main__.py     CLI entry point
  server.py       HTTP + WebSocket routes, startup banner
  transfer.py     chunked transfer engine, progress, SHA-256
  auth.py         PIN, pairing URL, QR rendering
  tls.py          self-signed certificate lifecycle
  discovery.py    mDNS/Bonjour announce
  config.py       settings from environment and CLI
  crypto.py       checksums and AES-GCM helpers
webapp/           the PWA the phone loads - no build step
  index.html, css/, js/app.js, sw.js, manifest.json
tests/            pytest, async
docs/screenshots/
```

### WebSocket protocol

JSON text frames carry metadata; the binary frame that follows carries
the bytes.

| direction | message | fields |
|---|---|---|
| to server | `auth` | `pin`, `session_id` |
| to server | `upload_start` | `filename`, `size`, `mime_type` |
| to server | `upload_chunk` | `transfer_id` — binary frame follows |
| to server | `upload_cancel` | `transfer_id` |
| to server | `download_request` | `filename` |
| to client | `auth_result` | `authenticated`, `session_id` |
| to client | `upload_ready` | `transfer_id`, `total_chunks`, `chunk_size` |
| to client | `chunk_ack` | `progress`, `speed_bps`, `eta_seconds` |
| to client | `upload_complete` | `transfer_id`, `checksum` |
| to client | `download_start` | `transfer_id`, `filename`, `file_size` |
| to client | `download_chunk` | `transfer_id`, `chunk_index` — binary follows |
| to client | `download_complete` | `transfer_id`, `checksum` |
| to client | `error` | `message` |

## License

**Source-available, non-commercial.** The code is public to read, study,
modify and share; commercial use requires written permission. Derivative
work carries the same terms and keeps the attribution. Full text in
[LICENSE](LICENSE).

This is not an OSI-approved open-source licence, and GitHub reports it as
`NOASSERTION` because it does not recognise custom terms. That is
expected, not an oversight.

© 2026 [nkVas1](https://github.com/nkVas1)
