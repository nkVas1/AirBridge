"""Tests for the self-signed certificate lifecycle."""

from __future__ import annotations

import ssl
from datetime import datetime, timedelta, timezone

from cryptography import x509

from airbridge.tls import (
    CERT_FILENAME,
    KEY_FILENAME,
    build_ssl_context,
    certificate_fingerprint,
    ensure_certificate,
)


def _load(cert_path):
    return x509.load_pem_x509_certificate(cert_path.read_bytes())


def _addresses(cert_path) -> set[str]:
    san = _load(cert_path).extensions.get_extension_for_class(x509.SubjectAlternativeName)
    return {str(value) for value in san.value.get_values_for_type(x509.IPAddress)}


class TestEnsureCertificate:
    def test_creates_certificate_and_key(self, tmp_path) -> None:
        cert_path, key_path = ensure_certificate(tmp_path / "certs", "192.168.1.5")
        assert cert_path.name == CERT_FILENAME
        assert key_path.name == KEY_FILENAME
        assert cert_path.is_file()
        assert key_path.is_file()

    def test_covers_the_requested_address(self, tmp_path) -> None:
        cert_path, _ = ensure_certificate(tmp_path, "192.168.1.5")
        assert "192.168.1.5" in _addresses(cert_path)
        # Loopback stays valid so the desktop can open the app locally.
        assert "127.0.0.1" in _addresses(cert_path)

    def test_reuses_a_valid_certificate(self, tmp_path) -> None:
        cert_path, _ = ensure_certificate(tmp_path, "192.168.1.5")
        first = certificate_fingerprint(cert_path)
        ensure_certificate(tmp_path, "192.168.1.5")
        assert certificate_fingerprint(cert_path) == first

    def test_reissues_when_the_address_changes(self, tmp_path) -> None:
        cert_path, _ = ensure_certificate(tmp_path, "192.168.1.5")
        first = certificate_fingerprint(cert_path)
        # Switching from Wi-Fi to a phone hotspot changes the local address.
        ensure_certificate(tmp_path, "172.20.10.2")
        assert certificate_fingerprint(cert_path) != first
        assert "172.20.10.2" in _addresses(cert_path)

    def test_reissues_when_the_certificate_is_corrupt(self, tmp_path) -> None:
        cert_path, _ = ensure_certificate(tmp_path, "192.168.1.5")
        cert_path.write_text("not a certificate")
        ensure_certificate(tmp_path, "192.168.1.5")
        assert "192.168.1.5" in _addresses(cert_path)

    def test_validity_stays_under_the_safari_limit(self, tmp_path) -> None:
        # Safari rejects certificates valid for more than 398 days.
        cert_path, _ = ensure_certificate(tmp_path, "192.168.1.5")
        certificate = _load(cert_path)
        lifetime = certificate.not_valid_after_utc - certificate.not_valid_before_utc
        assert lifetime < timedelta(days=398)
        assert certificate.not_valid_after_utc > datetime.now(timezone.utc)


class TestSslContext:
    def test_builds_a_tls_server_context(self, tmp_path) -> None:
        cert_path, key_path = ensure_certificate(tmp_path, "192.168.1.5")
        context = build_ssl_context(cert_path, key_path)
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


class TestFingerprint:
    def test_is_a_sha256_digest(self, tmp_path) -> None:
        cert_path, _ = ensure_certificate(tmp_path, "192.168.1.5")
        fingerprint = certificate_fingerprint(cert_path)
        assert len(fingerprint.split(":")) == 32
