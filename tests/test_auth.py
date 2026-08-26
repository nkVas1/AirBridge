"""Tests for AirBridge auth module."""

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

import pytest

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


class TestThrottling:
    """A six-digit PIN only holds up if wrong guesses get expensive."""

    def _exhaust(self, auth: AuthManager, client: str, attempts: int = 5) -> None:
        wrong = "000000" if auth.pin != "000000" else "111111"
        for _ in range(attempts):
            auth.authenticate_session("session", wrong, client)

    def test_a_run_of_wrong_pins_locks_the_client_out(self) -> None:
        auth = AuthManager(pin_length=6)
        assert auth.lockout_remaining("10.0.0.5") == 0
        self._exhaust(auth, "10.0.0.5")
        assert auth.lockout_remaining("10.0.0.5") > 0

    def test_the_right_pin_is_refused_while_locked_out(self) -> None:
        auth = AuthManager(pin_length=6)
        self._exhaust(auth, "10.0.0.5")
        # Guessing correctly during the penalty must not pay off, or the
        # lockout would only cost an attacker one extra round trip.
        assert auth.authenticate_session("session", auth.pin, "10.0.0.5") is False
        assert auth.is_authenticated("session") is False

    def test_lockout_is_per_client(self) -> None:
        auth = AuthManager(pin_length=6)
        self._exhaust(auth, "10.0.0.5")
        assert auth.lockout_remaining("10.0.0.9") == 0
        assert auth.authenticate_session("other", auth.pin, "10.0.0.9") is True

    def test_a_few_mistakes_do_not_lock_anyone_out(self) -> None:
        auth = AuthManager(pin_length=6)
        self._exhaust(auth, "10.0.0.5", attempts=4)
        assert auth.lockout_remaining("10.0.0.5") == 0
        assert auth.authenticate_session("session", auth.pin, "10.0.0.5") is True

    def test_success_clears_the_failure_history(self) -> None:
        auth = AuthManager(pin_length=6)
        self._exhaust(auth, "10.0.0.5", attempts=4)
        auth.authenticate_session("session", auth.pin, "10.0.0.5")
        self._exhaust(auth, "10.0.0.5", attempts=4)
        assert auth.lockout_remaining("10.0.0.5") == 0

    def test_repeated_lockouts_get_longer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Driving a fake clock rather than sleeping: the point is the
        # penalty after the previous one has run out, not the wall time.
        now = [1000.0]
        monkeypatch.setattr("airbridge.auth.time.monotonic", lambda: now[0])

        auth = AuthManager(pin_length=6)
        self._exhaust(auth, "10.0.0.5")
        first = auth.lockout_remaining("10.0.0.5")
        assert first > 0

        now[0] += first + 1  # wait it out
        assert auth.lockout_remaining("10.0.0.5") == 0

        self._exhaust(auth, "10.0.0.5")
        assert auth.lockout_remaining("10.0.0.5") > first

    def test_lockout_expires_on_its_own(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = [1000.0]
        monkeypatch.setattr("airbridge.auth.time.monotonic", lambda: now[0])

        auth = AuthManager(pin_length=6)
        self._exhaust(auth, "10.0.0.5")
        now[0] += auth.lockout_remaining("10.0.0.5") + 1

        assert auth.lockout_remaining("10.0.0.5") == 0
        assert auth.authenticate_session("session", auth.pin, "10.0.0.5") is True

    def test_old_failures_fall_out_of_the_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = [1000.0]
        monkeypatch.setattr("airbridge.auth.time.monotonic", lambda: now[0])

        auth = AuthManager(pin_length=6)
        wrong = "000000" if auth.pin != "000000" else "111111"
        # Four mistakes spread over an hour must not add up to a lockout.
        for _ in range(4):
            auth.authenticate_session("session", wrong, "10.0.0.5")
            now[0] += 900
        auth.authenticate_session("session", wrong, "10.0.0.5")
        assert auth.lockout_remaining("10.0.0.5") == 0

    def test_a_new_pin_forgets_everything(self) -> None:
        auth = AuthManager(pin_length=6)
        self._exhaust(auth, "10.0.0.5")
        auth.regenerate_pin()
        assert auth.lockout_remaining("10.0.0.5") == 0

