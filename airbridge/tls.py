"""Certificate lifecycle for the local HTTPS server.

AirBridge runs on addresses no public authority will ever vouch for, so
it acts as its own authority: a long-lived CA kept on the machine that
runs the server, and a short-lived leaf certificate re-issued whenever
the machine's address changes.

The split matters on iOS. Apple requires a certificate be *installed and
trusted*, not merely waved past a warning dialog, before Safari will open
a WebSocket to it. Trust is granted to certificates, not to hosts, so a
bare self-signed leaf would have to be re-installed on the phone every
time Wi-Fi handed out a new address. With a CA the phone trusts once and
the leaf rotates underneath it.

Certificates are built to Apple's published requirements for trusted TLS
server certificates (support.apple.com/103769): SHA-2 signatures, an
ExtendedKeyUsage carrying id-kp-serverAuth, the server's DNS name in the
SubjectAlternativeName extension, and a validity period under 398 days.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import ssl
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

logger = logging.getLogger(__name__)

CA_CERT_FILENAME = "airbridge-ca.pem"
CA_KEY_FILENAME = "airbridge-ca-key.pem"
CERT_FILENAME = "airbridge-cert.pem"
KEY_FILENAME = "airbridge-key.pem"

# Apple rejects server certificates valid for more than 398 days. Stay
# clear of the edge; the leaf is cheap to re-issue anyway.
LEAF_VALIDITY_DAYS = 397

# The CA is the thing a person installs on their phone, so it should not
# need replacing often.
CA_VALIDITY_DAYS = 3650

# Re-issue before expiry rather than at it, so a long-running server does
# not hand out a certificate that dies mid-transfer.
_RENEW_MARGIN = dt.timedelta(days=1)

_HOSTNAMES = ("airbridge.local", "localhost")
_LOOPBACK = ("127.0.0.1", "::1")


@dataclass(frozen=True)
class CertificatePaths:
    """Locations of the certificate material backing one server run."""

    ca_cert: Path
    ca_key: Path
    cert: Path
    key: Path


def ensure_certificates(cert_dir: Path, ip: str) -> CertificatePaths:
    """Return certificate material valid for `ip`, creating what is missing.

    The CA is created once and reused. The leaf is re-issued whenever it
    is absent, unreadable, close to expiry, or no longer covers `ip`.

    Args:
        cert_dir: Directory holding the certificate material.
        ip: The address the server is about to advertise.

    Returns:
        Paths to the CA certificate, CA key, leaf certificate and leaf key.
    """
    cert_dir.mkdir(parents=True, exist_ok=True)
    paths = CertificatePaths(
        ca_cert=cert_dir / CA_CERT_FILENAME,
        ca_key=cert_dir / CA_KEY_FILENAME,
        cert=cert_dir / CERT_FILENAME,
        key=cert_dir / KEY_FILENAME,
    )

    ca_reason = _ca_reissue_reason(paths)
    if ca_reason is not None:
        logger.info("Issuing AirBridge certificate authority (%s)", ca_reason)
        _write_ca(paths)
        # A new authority invalidates every leaf it did not sign.
        paths.cert.unlink(missing_ok=True)
        paths.key.unlink(missing_ok=True)

    leaf_reason = _leaf_reissue_reason(paths, ip)
    if leaf_reason is not None:
        logger.info("Issuing server certificate for %s (%s)", ip, leaf_reason)
        _write_leaf(paths, ip)

    return paths


def build_ssl_context(paths: CertificatePaths) -> ssl.SSLContext:
    """Build a server-side TLS context.

    TLS 1.3 is used whenever the client supports it; 1.2 is the floor.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(paths.cert), keyfile=str(paths.key))
    return context


def certificate_fingerprint(cert_path: Path) -> str:
    """Return a certificate's SHA-256 fingerprint, colon-separated."""
    certificate = _load(cert_path)
    return ":".join(f"{byte:02X}" for byte in certificate.fingerprint(hashes.SHA256()))


def certificate_der(cert_path: Path) -> bytes:
    """Return a certificate in DER form.

    iOS recognises a DER-encoded certificate as an installable profile;
    handed the PEM text it offers nothing but a file download.
    """
    return _load(cert_path).public_bytes(serialization.Encoding.DER)


# --- Issuing ---


def _write_ca(paths: CertificatePaths) -> None:
    """Create the local certificate authority."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "AirBridge Local CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AirBridge"),
    ])
    now = dt.datetime.now(dt.timezone.utc)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    paths.ca_cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    _write_key(paths.ca_key, key)


def _write_leaf(paths: CertificatePaths, ip: str) -> None:
    """Issue a server certificate for `ip`, signed by the local CA."""
    ca_certificate = _load(paths.ca_cert)
    ca_key = serialization.load_pem_private_key(paths.ca_key.read_bytes(), password=None)

    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "airbridge.local"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AirBridge"),
            ])
        )
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))  # tolerate clock skew
        .not_valid_after(now + dt.timedelta(days=LEAF_VALIDITY_DAYS))
        .add_extension(_subject_alt_name(ip), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        # Apple refuses to trust a server certificate without this.
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_certificate.public_key()  # type: ignore[arg-type]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())  # type: ignore[arg-type]
    )

    paths.cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    _write_key(paths.key, key)


def _subject_alt_name(ip: str) -> x509.SubjectAlternativeName:
    """Cover the current address, loopback, and the mDNS name."""
    names: list[x509.GeneralName] = [x509.DNSName(host) for host in _HOSTNAMES]
    for address in dict.fromkeys((ip, *_LOOPBACK)):
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(address)))
        except ValueError:
            logger.debug("Skipping unparseable address for certificate: %s", address)
    return x509.SubjectAlternativeName(names)


# --- Renewal decisions ---


def _ca_reissue_reason(paths: CertificatePaths) -> str | None:
    """Return why the CA must be re-created, or None if it is usable."""
    if not paths.ca_cert.is_file() or not paths.ca_key.is_file():
        return "no authority yet"
    try:
        certificate = _load(paths.ca_cert)
    except ValueError:
        return "authority certificate is unreadable"
    if certificate.not_valid_after_utc <= dt.datetime.now(dt.timezone.utc) + _RENEW_MARGIN:
        return "authority expired"
    return None


def _leaf_reissue_reason(paths: CertificatePaths, ip: str) -> str | None:
    """Return why the leaf must be re-issued, or None if it is usable."""
    if not paths.cert.is_file() or not paths.key.is_file():
        return "no certificate yet"
    try:
        certificate = _load(paths.cert)
    except ValueError:
        return "certificate is unreadable"
    if certificate.not_valid_after_utc <= dt.datetime.now(dt.timezone.utc) + _RENEW_MARGIN:
        return "certificate expired"
    if ip not in _certificate_addresses(certificate):
        return f"address changed to {ip}"
    if certificate.issuer != _load(paths.ca_cert).subject:
        return "signed by a previous authority"
    return None


def _certificate_addresses(certificate: x509.Certificate) -> set[str]:
    """Collect the IP addresses listed in a certificate's SAN extension."""
    try:
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return set()
    return {str(address) for address in san.value.get_values_for_type(x509.IPAddress)}


# --- Files ---


def _load(cert_path: Path) -> x509.Certificate:
    """Read a PEM certificate from disk."""
    return x509.load_pem_x509_certificate(cert_path.read_bytes())


def _write_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    """Write a private key, restricting access to the owner where possible."""
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        path.chmod(0o600)
    except OSError:
        logger.debug("Could not restrict permissions on %s", path)
