"""Tests for AirBridge auth module."""

from __future__ import annotations

import json

from airbridge.auth import AuthManager


class TestAuthManager:
    def test_pin_generation(self) -> None:
        auth = AuthManager(pin_length=6)
        assert len(auth.pin) == 6
        assert auth.pin.isdigit()

    def test_pin_regeneration(self) -> None:
        auth = AuthManager(pin_length=6)
        pin1 = auth.pin
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

    def test_qr_data(self) -> None:
        auth = AuthManager(pin_length=6)
        data = auth.generate_qr_data("192.168.1.100", 8090)
        parsed = json.loads(data)
        assert parsed["url"] == "http://192.168.1.100:8090"
        assert parsed["pin"] == auth.pin
        assert parsed["service"] == "AirBridge"

    def test_qr_base64(self) -> None:
        auth = AuthManager(pin_length=6)
        b64 = auth.generate_qr_base64("192.168.1.100", 8090)
        assert len(b64) > 0
        # Should be valid base64
        import base64
        decoded = base64.b64decode(b64)
        # PNG signature
        assert decoded[:4] == b"\x89PNG"
