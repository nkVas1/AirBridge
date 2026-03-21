"""PIN-based authentication and QR code generation for device pairing."""

from __future__ import annotations

import base64
import io
import json
import logging
import secrets
from dataclasses import dataclass, field

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

    def generate_qr_data(self, host: str, port: int) -> str:
        """Generate connection data for QR code encoding.

        Args:
            host: Server IP address or hostname.
            port: Server port number.

        Returns:
            JSON string with connection parameters.
        """
        return json.dumps(
            {
                "url": f"http://{host}:{port}",
                "pin": self._pin,
                "service": "AirBridge",
            },
            separators=(",", ":"),
        )

    def generate_qr_base64(self, host: str, port: int) -> str:
        """Generate QR code as base64-encoded PNG image.

        Args:
            host: Server IP address or hostname.
            port: Server port number.

        Returns:
            Base64-encoded PNG string for embedding in HTML.
        """
        data = self.generate_qr_data(host, port)
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img: PilImage = qr.make_image(fill_color="black", back_color="white")  # type: ignore[assignment]
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")
