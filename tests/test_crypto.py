"""Tests for AirBridge crypto module."""

from __future__ import annotations

import pytest

from airbridge.crypto import (
    EncryptedPayload,
    compute_checksum,
    decrypt,
    derive_key_from_pin,
    encrypt,
    generate_key,
)


class TestGenerateKey:
    def test_returns_32_bytes(self) -> None:
        key = generate_key()
        assert len(key) == 32

    def test_keys_are_unique(self) -> None:
        keys = {generate_key() for _ in range(100)}
        assert len(keys) == 100


class TestDeriveKeyFromPin:
    def test_returns_key_and_salt(self) -> None:
        key, salt = derive_key_from_pin("123456")
        assert len(key) == 32
        assert len(salt) == 16

    def test_same_pin_and_salt_produce_same_key(self) -> None:
        key1, salt = derive_key_from_pin("654321")
        key2, _ = derive_key_from_pin("654321", salt=salt)
        assert key1 == key2

    def test_different_pins_produce_different_keys(self) -> None:
        key1, salt = derive_key_from_pin("111111")
        key2, _ = derive_key_from_pin("222222", salt=salt)
        assert key1 != key2

    def test_different_salts_produce_different_keys(self) -> None:
        key1, salt1 = derive_key_from_pin("123456")
        key2, salt2 = derive_key_from_pin("123456")
        # With high probability, random salts differ
        if salt1 != salt2:
            assert key1 != key2


class TestEncryptDecrypt:
    def test_roundtrip(self) -> None:
        key = generate_key()
        plaintext = b"Hello, AirBridge!"
        payload = encrypt(plaintext, key)
        result = decrypt(payload, key)
        assert result == plaintext

    def test_empty_data(self) -> None:
        key = generate_key()
        plaintext = b""
        payload = encrypt(plaintext, key)
        result = decrypt(payload, key)
        assert result == plaintext

    def test_large_data(self) -> None:
        key = generate_key()
        plaintext = b"\x42" * (1024 * 1024)  # 1 MB
        payload = encrypt(plaintext, key)
        result = decrypt(payload, key)
        assert result == plaintext

    def test_wrong_key_fails(self) -> None:
        key1 = generate_key()
        key2 = generate_key()
        payload = encrypt(b"secret", key1)
        with pytest.raises(Exception):
            decrypt(payload, key2)

    def test_corrupted_ciphertext_fails(self) -> None:
        key = generate_key()
        payload = encrypt(b"data", key)
        corrupted = EncryptedPayload(
            nonce=payload.nonce,
            ciphertext=payload.ciphertext[:-1] + bytes([payload.ciphertext[-1] ^ 0xFF]),
        )
        with pytest.raises(Exception):
            decrypt(corrupted, key)

    def test_invalid_key_size(self) -> None:
        with pytest.raises(ValueError, match="Key must be 32 bytes"):
            encrypt(b"data", b"short_key")

    def test_invalid_key_size_decrypt(self) -> None:
        key = generate_key()
        payload = encrypt(b"data", key)
        with pytest.raises(ValueError, match="Key must be 32 bytes"):
            decrypt(payload, b"short_key")


class TestEncryptedPayload:
    def test_serialization_roundtrip(self) -> None:
        key = generate_key()
        original = encrypt(b"test data", key)
        raw = original.to_bytes()
        restored = EncryptedPayload.from_bytes(raw)
        assert restored.nonce == original.nonce
        assert restored.ciphertext == original.ciphertext

    def test_from_bytes_too_short(self) -> None:
        with pytest.raises(ValueError, match="Data too short"):
            EncryptedPayload.from_bytes(b"short")


class TestComputeChecksum:
    def test_deterministic(self) -> None:
        data = b"hello world"
        assert compute_checksum(data) == compute_checksum(data)

    def test_different_data_different_checksum(self) -> None:
        assert compute_checksum(b"hello") != compute_checksum(b"world")

    def test_returns_hex_string(self) -> None:
        checksum = compute_checksum(b"test")
        assert len(checksum) == 64
        assert all(c in "0123456789abcdef" for c in checksum)
