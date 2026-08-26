"""Tests for the local certificate authority and the server certificates it issues.

The assertions about extensions are not cosmetic: Apple refuses to trust a
TLS server certificate that omits any of them, and an untrusted certificate
means Safari will not open a WebSocket, which means no transfers at all.
"""

from __future__ import annotations

import datetime as dt
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID

from airbridge.tls import (
    CA_CERT_FILENAME,
    CERT_FILENAME,
    KEY_FILENAME,
    build_ssl_context,
    certificate_der,
    certificate_fingerprint,
    ensure_certificates,
)


def _load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _san_addresses(path: Path) -> set[str]:
    san = _load(path).extensions.get_extension_for_class(x509.SubjectAlternativeName)
    return {str(value) for value in san.value.get_values_for_type(x509.IPAddress)}


def _san_hostnames(path: Path) -> set[str]:
    san = _load(path).extensions.get_extension_for_class(x509.SubjectAlternativeName)
    return set(san.value.get_values_for_type(x509.DNSName))


class TestIssuing:
    def test_creates_authority_and_certificate(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path / "certs", "192.168.1.5")
        assert paths.ca_cert.name == CA_CERT_FILENAME
        assert paths.cert.name == CERT_FILENAME
        assert paths.key.name == KEY_FILENAME
        for path in (paths.ca_cert, paths.ca_key, paths.cert, paths.key):
            assert path.is_file()

    def test_certificate_is_signed_by_the_authority(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        leaf, ca = _load(paths.cert), _load(paths.ca_cert)
        assert leaf.issuer == ca.subject

        # Raises InvalidSignature if the chain does not hold.
        ca.public_key().verify(  # type: ignore[union-attr]
            leaf.signature,
            leaf.tbs_certificate_bytes,
            ec.ECDSA(hashes.SHA256()),
        )

    def test_covers_the_requested_address(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        assert "192.168.1.5" in _san_addresses(paths.cert)
        # Loopback stays valid so the desktop can open the app locally.
        assert "127.0.0.1" in _san_addresses(paths.cert)


class TestAppleRequirements:
    """support.apple.com/103769 — violating any of these breaks iOS entirely."""

    def test_authority_is_marked_as_a_certificate_authority(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        constraints = _load(paths.ca_cert).extensions.get_extension_for_class(
            x509.BasicConstraints
        )
        assert constraints.value.ca is True
        assert constraints.critical is True

    def test_certificate_declares_server_authentication(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        eku = _load(paths.cert).extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        assert ExtendedKeyUsageOID.SERVER_AUTH in eku.value

    def test_certificate_carries_a_dns_name(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        assert "airbridge.local" in _san_hostnames(paths.cert)

    def test_certificate_is_not_a_certificate_authority(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        constraints = _load(paths.cert).extensions.get_extension_for_class(x509.BasicConstraints)
        assert constraints.value.ca is False

    def test_validity_stays_under_the_limit(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        leaf = _load(paths.cert)
        assert leaf.not_valid_after_utc - leaf.not_valid_before_utc < dt.timedelta(days=398)
        assert leaf.not_valid_after_utc > dt.datetime.now(dt.timezone.utc)

    def test_signatures_use_sha2(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        for path in (paths.ca_cert, paths.cert):
            algorithm = _load(path).signature_hash_algorithm
            assert algorithm is not None
            assert algorithm.name.startswith("sha2")


class TestRenewal:
    def test_reuses_material_that_is_still_valid(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        before = (certificate_fingerprint(paths.ca_cert), certificate_fingerprint(paths.cert))
        ensure_certificates(tmp_path, "192.168.1.5")
        after = (certificate_fingerprint(paths.ca_cert), certificate_fingerprint(paths.cert))
        assert before == after

    def test_address_change_rotates_the_leaf_but_keeps_the_authority(
        self, tmp_path: Path
    ) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        ca_before = certificate_fingerprint(paths.ca_cert)
        leaf_before = certificate_fingerprint(paths.cert)

        # Moving from Wi-Fi to a phone hotspot changes the local address.
        ensure_certificates(tmp_path, "172.20.10.2")

        # The phone trusted the authority, so that must survive; otherwise
        # every network change would mean installing a certificate again.
        assert certificate_fingerprint(paths.ca_cert) == ca_before
        assert certificate_fingerprint(paths.cert) != leaf_before
        assert "172.20.10.2" in _san_addresses(paths.cert)

    def test_corrupt_certificate_is_replaced(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        paths.cert.write_text("not a certificate")
        ensure_certificates(tmp_path, "192.168.1.5")
        assert "192.168.1.5" in _san_addresses(paths.cert)

    def test_losing_the_authority_reissues_everything(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        leaf_before = certificate_fingerprint(paths.cert)
        paths.ca_cert.unlink()

        ensure_certificates(tmp_path, "192.168.1.5")
        # A leaf signed by an authority that no longer exists is unusable.
        assert certificate_fingerprint(paths.cert) != leaf_before
        assert _load(paths.cert).issuer == _load(paths.ca_cert).subject


class TestServing:
    def test_builds_a_tls_server_context(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        context = build_ssl_context(paths)
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2

    def test_authority_is_offered_in_der_form(self, tmp_path: Path) -> None:
        # iOS only recognises a certificate as installable in DER form.
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        der = certificate_der(paths.ca_cert)
        assert x509.load_der_x509_certificate(der).subject == _load(paths.ca_cert).subject

    def test_fingerprint_is_a_sha256_digest(self, tmp_path: Path) -> None:
        paths = ensure_certificates(tmp_path, "192.168.1.5")
        assert len(certificate_fingerprint(paths.ca_cert).split(":")) == 32
