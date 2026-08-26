# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — 2026-08-26

The 1.0.0 README promised encryption, a pairing QR code and an offline
web app. None of the three were actually wired up. This release either
implements them or stops claiming them.

### Added

- **TLS on by default.** The server issues its own certificate on first
  run and serves HTTPS. Measured on the negotiated connection: TLS 1.3,
  `TLS_AES_256_GCM_SHA384`. The certificate is re-issued automatically
  when the machine's address changes — switching between Wi-Fi and a
  phone hotspot no longer breaks it.
- **Pairing QR code in the terminal.** The banner prints a code that the
  stock iPhone camera can open, with the PIN already in the link.
- **Offline caching actually runs.** `webapp/sw.js` shipped since 1.0.0
  but was never registered by anything; it is registered now. This only
  became possible with TLS, because browsers restrict service workers to
  secure contexts.
- `--no-tls` for plain HTTP, and `AIRBRIDGE_TLS=0` as the environment
  equivalent. Both say plainly what is given up.
- `/favicon.ico` is served, instead of a 404 on every page load.
- `/api/info` reports `scheme` and `encrypted` so a client can tell what
  it is connected over.

### Fixed

- **Startup crash on non-UTF-8 consoles.** `python -m airbridge` raised
  `UnicodeEncodeError` on the banner's aeroplane glyph whenever the
  console code page was not UTF-8 — on a Russian-locale Windows, every
  time. `start.bat` happened to mask it with `chcp 65001`, so the
  documented command failed while the launcher worked.
- **Connect button off-screen on phones.** The PIN field's min-content
  width kept the row from shrinking: at a 414 px viewport the page
  measured 485 px wide and the button sat outside it.
- The pairing QR encoded a JSON object rather than a URL, so a camera
  app had nothing to open even where the code was shown.

### Changed

- `AuthManager.generate_qr_data(host, port)` is replaced by
  `pairing_url(base_url)`; `generate_qr_base64` now takes a base URL.
  `generate_qr_ascii` is new.
- Licence metadata in `pyproject.toml` now matches the `LICENSE` file.
  It claimed MIT while the file had been proprietary since May 2026.
- Repository author fields corrected from `nkVasi` to `nkVas1` — the
  old spelling linked to a profile that does not exist.

### Known to be still broken

- `webapp/` is not part of the Python package, so `pip install` yields a
  server without a web interface. Run from a checkout instead.

## [1.0.0] — 2026-05-23

First working version: chunked WebSocket transfer in both directions,
PIN authentication, mDNS discovery, file previews for images, video,
audio, PDF and text, batch download, and sorting in the received list.
