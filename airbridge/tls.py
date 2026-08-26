"""Self-signed TLS certificate lifecycle for the local HTTPS server.

The browser only exposes WebCrypto, Service Workers and other
privileged APIs in a *secure context*. On a LAN address that means
HTTPS, so AirBridge generates its own certificate on first run and
re-issues it whenever the machine's address changes.

The certificate is not signed by a public CA — there is no CA that
issues certificates for `192.168.x.x`. The phone shows a warning once,
per certificate; see the README for what that looks like.
"""

from __future__ import annotations

import ipaddress
import logging
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

CERT_FILENAME = "airbridge-cert.pem"
KEY_FILENAME = "airbridge-key.pem"

# Safari refuses to trust a TLS certificate valid for more than 398 days,
# even when the user accepts it manually. Stay just under the limit.
_VALIDITY_DAYS = 397

# Re-issue before expiry rather than at it, so a long-running server
# does not hand out a certificate that dies mid-session.
_RENEW_MARGIN = timedelta(days=1)

_STATIC_HOSTNAMES = ("airbridge.local", "localhost")
_STATIC_ADDRESSES = ("127.0.0.1", "::1")


def ensure_certificate(cert_dir: Path, ip: str) -> tuple[Path, Path]:
    """Return paths to a certificate and key valid for `ip`, creating them if needed.

    Args:
        cert_dir: Directory holding the certificate and private key.
        ip: The address the server is about to advertise.

    Returns:
        Tuple of (certificate path, private key path).
    """
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / CERT_FILENAME
    key_path = cert_dir / KEY_FILENAME

    reason = _reissue_reason(cert_path, key_path, ip)
    if reason is None:
        return cert_path, key_path

    logger.info("Issuing self-signed certificate for %s (%s)", ip, reason)
    _write_certificate(cert_path, key_path, ip)
    return cert_path, key_path


def build_ssl_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    """Build a server-side TLS context from a certificate/key pair.

    TLS 1.3 is used whenever the client supports it; 1.2 is the floor.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return context


def certificate_fingerprint(cert_path: Path) -> str:
    """Return the SHA-256 fingerprint of a certificate, colon-separated."""
    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    digest = certificate.fingerprint(hashes.SHA256())
    return ":".join(f"{byte:02X}" for byte in digest)


def _reissue_reason(cert_path: Path, key_path: Path, ip: str) -> str | None:
    """Return why the certificate must be re-issued, or None if it is usable."""
    if not cert_path.is_file() or not key_path.is_file():
        return "no certificate yet"

    try:
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except ValueError:
        return "certificate is unreadable"

    if certificate.not_valid_after_utc <= datetime.now(timezone.utc) + _RENEW_MARGIN:
        return "certificate expired"

    if ip not in _certificate_addresses(certificate):
        return f"address changed to {ip}"

    return None


def _certificate_addresses(certificate: x509.Certificate) -> set[str]:
    """Collect the IP addresses listed in a certificate's SAN extension."""
    try:
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return set()
    return {str(address) for address in san.value.get_values_for_type(x509.IPAddress)}


def _write_certificate(cert_path: Path, key_path: Path, ip: str) -> None:
    """Generate a self-signed certificate/key pair covering `ip`."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "AirBridge"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AirBridge"),
    ])
    now = datetime.now(timezone.utc)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))  # tolerate small clock skew
        .not_valid_after(now + timedelta(days=_VALIDITY_DAYS))
        .add_extension(_subject_alt_name(ip), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    _restrict_to_owner(key_path)


def _subject_alt_name(ip: str) -> x509.SubjectAlternativeName:
    """Build the SAN extension covering the current address and the fixed names."""
    names: list[x509.GeneralName] = [x509.DNSName(host) for host in _STATIC_HOSTNAMES]
    addresses = dict.fromkeys((ip, *_STATIC_ADDRESSES))
    for address in addresses:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(address)))
        except ValueError:
            logger.debug("Skipping unparseable address in certificate: %s", address)
    return x509.SubjectAlternativeName(names)


def _restrict_to_owner(path: Path) -> None:
    """Best-effort tightening of private key permissions."""
    try:
        path.chmod(0o600)
    except OSError:
        logger.debug("Could not restrict permissions on %s", path)
