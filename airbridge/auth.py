"""PIN-based authentication and QR code generation for device pairing."""

from __future__ import annotations

import base64
import io
import logging
import secrets
from dataclasses import dataclass, field
from urllib.parse import quote

import qrcode
from qrcode.image.pil import PilImage

logger = logging.getLogger(__name__)


@dataclass
class AuthManager:
    """Manages PIN generation, validation, and QR code creation."""

    pin_length: int = 6
    _pin: str = field(default="", init=False, repr=False)
    _authenticated_sessions: set[str] = field(default_factory=set, init=False)

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
        max_val = 10**self.pin_length - 1
        self._pin = str(secrets.randbelow(max_val)).zfill(self.pin_length)
        self._authenticated_sessions.clear()
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

    def authenticate_session(self, session_id: str, pin: str) -> bool:
        """Authenticate a session with a PIN.

        Args:
            session_id: Unique session identifier.
            pin: PIN attempt.

        Returns:
            True if authentication succeeded.
        """
        if self.verify_pin(pin):
            self._authenticated_sessions.add(session_id)
            logger.info("Session %s authenticated", session_id[:8])
            return True
        logger.warning("Failed authentication attempt for session %s", session_id[:8])
        return False

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

    def pairing_url(self, base_url: str) -> str:
        """Build the address that pairs a device in one step.

        The PIN travels as a query parameter so that scanning the code
        with the stock iPhone camera opens the web app already
        authenticated. A QR code holding JSON — the previous format —
        is not a link, so the camera offers nothing to tap.

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
        qr = self._build_qr(base_url)
        img: PilImage = qr.make_image(fill_color="black", back_color="white")  # type: ignore[assignment]
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
        buffer = io.StringIO()
        self._build_qr(base_url).print_ascii(out=buffer, invert=True)
        return buffer.getvalue()

    def _build_qr(self, base_url: str) -> qrcode.QRCode:
        """Encode the pairing URL into a QR code object."""
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=4,
        )
        qr.add_data(self.pairing_url(base_url))
        qr.make(fit=True)
        return qr
