# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] — 2026-08-26

Everything 1.1.0 listed as unfinished, finished — plus a fault in 1.1.0's
own TLS work that would have broken the app on the one device this
project exists for.

### Fixed

- **The web interface now ships with the package.** `webapp/` sat beside
  the Python package rather than inside it, so an installed wheel held
  the server and none of the interface. Only a checkout ever worked.
- **Certificates iOS will accept.** 1.1.0's certificate had no
  ExtendedKeyUsage extension; Apple has required `serverAuth` on every
  TLS server certificate issued since July 2019 and rejects certificates
  without it. It was also a bare self-signed leaf bound to one address,
  so every change of network would have meant re-trusting it on the
  phone. There is now a local certificate authority, trusted once, with
  a server certificate under it that rotates freely. The authority is
  served at `/ca.crt` in DER form, the only shape iOS treats as
  installable.
- **A way through when the certificate is not installed.** Safari
  refuses a WebSocket to an untrusted certificate even after the page
  warning is accepted, and AirBridge moves every byte over a WebSocket —
  so 1.1.0 could have loaded on an iPhone and then transferred nothing.
  The server now also listens on plain HTTP one port up, and the web app
  explains the failure and offers that address rather than sitting there
  looking broken.
- **mDNS registration.** zeroconf's synchronous API raises
  `EventLoopBlocked` when called from inside a running loop, which is
  where the aiohttp startup hook lives; it failed on every start. Now
  registered through `AsyncZeroconf`.
- **The startup banner reaches redirected output.** `print` is
  block-buffered when stdout is not a console, and a server that then
  runs forever never filled the buffer, so anyone launching through a
  wrapper script never saw the address or the PIN.

### Added

- **Interrupted uploads resume.** An upload lands in a scratch file
  named for its declared size and is renamed into place only once whole;
  reconnecting reports how many bytes survived and the sender continues
  from there. The digest still spans the whole file.
- **Both ends verify the checksum.** The server always reported a
  SHA-256 digest and nothing compared it. The browser now hashes what it
  sends and receives and checks; a download that fails the check is
  discarded rather than handed over. Needed a streaming SHA-256 in
  JavaScript, since `crypto.subtle` cannot hash incrementally and does
  not exist outside a secure context.
- **Wrong PINs are throttled.** Five failures in a minute lock that
  address out, doubling on repeat up to an hour. Six digits is a million
  guesses and nothing was slowing them down.
- `--no-http-fallback`, and `AIRBRIDGE_HTTP_FALLBACK`.
- Partial uploads are listed separately from finished ones, and never
  offered as if they were complete files.

### Removed

- `crypto.py`. Its AES-GCM helpers were never called by anything —
  transport security is TLS — and a module of unused cryptography reads
  like a promise the product does not keep.

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
  *(Fixed in 1.2.0.)*

## [1.0.0] — 2026-05-23

First working version: chunked WebSocket transfer in both directions,
PIN authentication, mDNS discovery, file previews for images, video,
audio, PDF and text, batch download, and sorting in the received list.
