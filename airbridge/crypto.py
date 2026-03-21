"""AES-256-GCM encryption and decryption for secure file transfer."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_SIZE = 32  # 256 bits
_NONCE_SIZE = 12  # 96 bits (standard for GCM)


@dataclass(frozen=True)
class EncryptedPayload:
    """Container for encrypted data with its nonce."""

    nonce: bytes
    ciphertext: bytes

    def to_bytes(self) -> bytes:
        """Serialize to bytes: [nonce (12 bytes)][ciphertext]."""
        return self.nonce + self.ciphertext

    @classmethod
    def from_bytes(cls, data: bytes) -> "EncryptedPayload":
        """Deserialize from bytes."""
        if len(data) < _NONCE_SIZE:
            raise ValueError(f"Data too short: expected at least {_NONCE_SIZE} bytes, got {len(data)}")
        return cls(nonce=data[:_NONCE_SIZE], ciphertext=data[_NONCE_SIZE:])


def generate_key() -> bytes:
    """Generate a cryptographically secure random 256-bit key."""
    return os.urandom(_KEY_SIZE)


def derive_key_from_pin(pin: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """Derive AES-256 key from a PIN using PBKDF2-HMAC-SHA256.

    Args:
        pin: The PIN string to derive the key from.
        salt: Optional salt bytes; generated randomly if not provided.

    Returns:
        Tuple of (derived_key, salt) — both as bytes.
    """
    if salt is None:
        salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac(
        hash_name="sha256",
        password=pin.encode("utf-8"),
        salt=salt,
        iterations=100_000,
        dklen=_KEY_SIZE,
    )
    return key, salt


def encrypt(plaintext: bytes, key: bytes) -> EncryptedPayload:
    """Encrypt data using AES-256-GCM.

    Args:
        plaintext: Data to encrypt.
        key: 256-bit encryption key.

    Returns:
        EncryptedPayload containing nonce and ciphertext.
    """
    if len(key) != _KEY_SIZE:
        raise ValueError(f"Key must be {_KEY_SIZE} bytes, got {len(key)}")
    nonce = os.urandom(_NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return EncryptedPayload(nonce=nonce, ciphertext=ciphertext)


def decrypt(payload: EncryptedPayload, key: bytes) -> bytes:
    """Decrypt data using AES-256-GCM.

    Args:
        payload: EncryptedPayload containing nonce and ciphertext.
        key: 256-bit encryption key (must match the key used for encryption).

    Returns:
        Decrypted plaintext bytes.

    Raises:
        cryptography.exceptions.InvalidTag: If the key is wrong or data is corrupted.
    """
    if len(key) != _KEY_SIZE:
        raise ValueError(f"Key must be {_KEY_SIZE} bytes, got {len(key)}")
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(payload.nonce, payload.ciphertext, None)


def compute_checksum(data: bytes) -> str:
    """Compute SHA-256 checksum of data for integrity verification.

    Args:
        data: Data to hash.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(data).hexdigest()
