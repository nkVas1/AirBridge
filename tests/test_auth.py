"""Tests for AirBridge auth module."""

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

from airbridge.auth import AuthManager

PNG_SIGNATURE = bytes.fromhex("89504e47")


class TestAuthManager:
    def test_pin_generation(self) -> None:
        auth = AuthManager(pin_length=6)
        assert len(auth.pin) == 6
        assert auth.pin.isdigit()

    def test_pin_regeneration(self) -> None:
        auth = AuthManager(pin_length=6)
        pin2 = auth.regenerate_pin()
        # Pins are random, so they could be the same
        # but regenerate should return the new pin
        assert len(pin2) == 6
        assert auth.pin == pin2

    def test_verify_correct_pin(self) -> None:
        auth = AuthManager(pin_length=6)
        assert auth.verify_pin(auth.pin) is True

    def test_verify_wrong_pin(self) -> None:
        auth = AuthManager(pin_length=6)
        wrong_pin = "000000" if auth.pin != "000000" else "999999"
        assert auth.verify_pin(wrong_pin) is False

    def test_authenticate_session(self) -> None:
        auth = AuthManager(pin_length=6)
        session_id = "test-session-123"
        assert auth.authenticate_session(session_id, auth.pin) is True
        assert auth.is_authenticated(session_id) is True

    def test_failed_authentication(self) -> None:
        auth = AuthManager(pin_length=6)
        session_id = "test-session-456"
        assert auth.authenticate_session(session_id, "wrong") is False
        assert auth.is_authenticated(session_id) is False

    def test_revoke_session(self) -> None:
        auth = AuthManager(pin_length=6)
        session_id = "test-session-789"
        auth.authenticate_session(session_id, auth.pin)
        assert auth.is_authenticated(session_id) is True
        auth.revoke_session(session_id)
        assert auth.is_authenticated(session_id) is False

    def test_regenerate_clears_sessions(self) -> None:
        auth = AuthManager(pin_length=6)
        session_id = "test-session"
        auth.authenticate_session(session_id, auth.pin)
        assert auth.is_authenticated(session_id) is True
        auth.regenerate_pin()
        assert auth.is_authenticated(session_id) is False

    def test_pin_with_leading_zeros(self) -> None:
        auth = AuthManager(pin_length=4)
        # Pin should be zero-padded
        assert len(auth.pin) == 4

    def test_verify_pin_with_whitespace(self) -> None:
        auth = AuthManager(pin_length=6)
        pin = auth.pin
        assert auth.verify_pin(f" {pin} ") is True


    def test_pairing_url_carries_the_pin(self) -> None:
        auth = AuthManager(pin_length=6)
        url = auth.pairing_url("https://192.168.1.100:8090")
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "192.168.1.100:8090"
        assert parse_qs(parsed.query)["pin"] == [auth.pin]

    def test_pairing_url_does_not_double_the_slash(self) -> None:
        auth = AuthManager(pin_length=6)
        assert auth.pairing_url("https://host:8090/").count("//") == 1

    def test_qr_base64(self) -> None:
        auth = AuthManager(pin_length=6)
        b64 = auth.generate_qr_base64("https://192.168.1.100:8090")
        assert len(b64) > 0
        assert base64.b64decode(b64)[:4] == PNG_SIGNATURE

    def test_qr_ascii_is_printable(self) -> None:
        auth = AuthManager(pin_length=6)
        ascii_qr = auth.generate_qr_ascii("https://192.168.1.100:8090")
        assert ascii_qr.strip()
        # More than one row, so it is a real code and not a single line.
        assert len(ascii_qr.splitlines()) > 10
