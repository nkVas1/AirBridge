"""PIN-based authentication and QR code generation for device pairing."""

from __future__ import annotations

import base64
import io
import logging
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import qrcode
from qrcode.image.pil import PilImage

logger = logging.getLogger(__name__)

# A six-digit PIN is a million guesses. Unthrottled, a script on the same
# network works through that in minutes, so wrong answers get expensive.
_MAX_ATTEMPTS = 5
_ATTEMPT_WINDOW_SECONDS = 60.0
_LOCKOUT_SECONDS = 30.0
_MAX_LOCKOUT_SECONDS = 3600.0


@dataclass
class _ClientRecord:
    """Failed-attempt history for one client address."""

    failures: list[float] = field(default_factory=list)
    lockouts: int = 0
    locked_until: float = 0.0


@dataclass
class AuthManager:
    """Manages PIN generation, validation, and QR code creation."""

    pin_length: int = 6
    _pin: str = field(default="", init=False, repr=False)
    _authenticated_sessions: set[str] = field(default_factory=set, init=False)
    _clients: dict[str, _ClientRecord] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.regenerate_pin()

    @property
    def pin(self) -> str:
        """Get the current PIN."""
        return self._pin

    def regenerate_pin(self) -> str:
        """Generate a new random numeric PIN.

        Returns:
            The newly generated PIN string.
        """
        upper_bound = 10**self.pin_length
        self._pin = str(secrets.randbelow(upper_bound)).zfill(self.pin_length)
        self._authenticated_sessions.clear()
        self._clients.clear()
        logger.info("New PIN generated")
        return self._pin

    def verify_pin(self, pin: str) -> bool:
        """Verify a PIN attempt using constant-time comparison.

        Args:
            pin: The PIN string to verify.

        Returns:
            True if the PIN matches, False otherwise.
        """
        return secrets.compare_digest(pin.strip(), self._pin)

    # --- Throttling ---

    def lockout_remaining(self, client: str) -> float:
        """Seconds until `client` may try a PIN again; 0 when it may now."""
        record = self._clients.get(client)
        if record is None:
            return 0.0
        return max(0.0, record.locked_until - time.monotonic())

    def authenticate_session(self, session_id: str, pin: str, client: str = "") -> bool:
        """Authenticate a session with a PIN.

        Args:
            session_id: Unique session identifier.
            pin: PIN attempt.
            client: Address the attempt came from, used for throttling.

        Returns:
            True if authentication succeeded.
        """
        if self.lockout_remaining(client) > 0:
            logger.warning("Rejected PIN attempt from %s during lockout", client or "unknown")
            return False

        if self.verify_pin(pin):
            self._authenticated_sessions.add(session_id)
            self._clients.pop(client, None)
            logger.info("Session %s authenticated", session_id[:8])
            return True

        self._record_failure(client)
        logger.warning("Failed authentication attempt for session %s", session_id[:8])
        return False

    def _record_failure(self, client: str) -> None:
        """Count a wrong PIN and lock the client out once they add up."""
        now = time.monotonic()
        record = self._clients.setdefault(client, _ClientRecord())
        cutoff = now - _ATTEMPT_WINDOW_SECONDS
        record.failures = [at for at in record.failures if at > cutoff]
        record.failures.append(now)

        if len(record.failures) < _MAX_ATTEMPTS:
            return

        # Each further round of failures doubles the wait, so a script
        # gets slower the longer it runs while a human retyping a digit
        # waits half a minute at most.
        record.lockouts += 1
        delay = min(_LOCKOUT_SECONDS * (2 ** (record.lockouts - 1)), _MAX_LOCKOUT_SECONDS)
        record.locked_until = now + delay
        record.failures.clear()
        logger.warning(
            "Locking out %s for %.0fs after repeated failures", client or "unknown", delay
        )

    # --- Sessions ---

    def is_authenticated(self, session_id: str) -> bool:
        """Check if a session is authenticated.

        Args:
            session_id: Session identifier to check.

        Returns:
            True if the session has been authenticated.
        """
        return session_id in self._authenticated_sessions

    def revoke_session(self, session_id: str) -> None:
        """Revoke authentication for a session."""
        self._authenticated_sessions.discard(session_id)

    # --- Pairing ---

    def pairing_url(self, base_url: str) -> str:
        """Build the address that pairs a device in one step.

        The PIN travels as a query parameter so that scanning the code
        with the stock iPhone camera opens the web app already
        authenticated. A QR code holding JSON — the previous format — is
        not a link, so the camera offers nothing to tap.

        Args:
            base_url: Server root, for example `https://192.168.1.5:8090`.

        Returns:
            A URL carrying the current PIN.
        """
        return f"{base_url.rstrip('/')}/?pin={quote(self._pin)}"

    def generate_qr_base64(self, base_url: str) -> str:
        """Render the pairing URL as a base64-encoded PNG.

        Args:
            base_url: Server root, for example `https://192.168.1.5:8090`.

        Returns:
            Base64-encoded PNG string for embedding in HTML.
        """
        img: PilImage = self._build_qr(self.pairing_url(base_url)).make_image(
            fill_color="black", back_color="white"
        )  # type: ignore[assignment]
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def generate_qr_ascii(self, base_url: str) -> str:
        """Render the pairing URL as text, for printing in the terminal.

        Args:
            base_url: Server root, for example `https://192.168.1.5:8090`.

        Returns:
            The QR code drawn with block characters.
        """
        return self.render_qr_ascii(self.pairing_url(base_url))

    def render_qr_ascii(self, data: str) -> str:
        """Render arbitrary text as a QR code drawn with block characters."""
        buffer = io.StringIO()
        self._build_qr(data).print_ascii(out=buffer, invert=True)
        return buffer.getvalue()

    def _build_qr(self, data: str) -> qrcode.QRCode:
        """Encode `data` into a QR code object."""
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        return qr
