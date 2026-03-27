"""Ed25519 key management, credential storage, and cryptographic operations.

Credential files
----------------
All credentials are stored in ~/.runespy/ with restricted permissions:

    worker_key.pem      Ed25519 private key (PEM/PKCS8, unencrypted), chmod 600
    worker_secret.key   Raw 32-byte HMAC shared secret,               chmod 600
    worker_id           Plain-text UUID assigned by the master server

Secret delivery scheme
----------------------
When the admin approves a worker, the server generates a 32-byte random
shared secret and encrypts it specifically for that worker so it can only
be decrypted by the holder of the matching Ed25519 private key:

    1. Generate a random 32-byte AES key.
    2. XOR it byte-by-byte with the worker's 32-byte Ed25519 public key,
       binding the AES key to this worker's identity.
    3. Encrypt the shared secret with AES-256-GCM using a random 12-byte nonce.
    4. Transmit: base64(nonce[12] + xored_key[32] + ciphertext[32+16])

To decrypt (decrypt_secret below), reverse the process:
    - XOR xored_key with own public key bytes to recover the AES key.
    - AES-GCM decrypt the ciphertext.

This scheme ensures the plaintext secret is never stored server-side and
can only be recovered by someone who possesses the private key.
"""

import base64
import hashlib
import hmac as _hmac
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

CONFIG_DIR = Path.home() / ".runespy"


def ensure_config_dir() -> Path:
    """Create ~/.runespy/ if it does not exist. Returns the path."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate a new Ed25519 keypair.

    Returns a tuple of (private_key, public_key_b64) where public_key_b64 is
    the base64-encoded 32-byte raw public key sent to the server at registration.
    """
    private_key = Ed25519PrivateKey.generate()
    pub_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private_key, base64.b64encode(pub_raw).decode()


def save_private_key(key: Ed25519PrivateKey, path: Path | None = None) -> Path:
    """Serialise *key* to a PEM/PKCS8 file and set permissions to 0600."""
    if path is None:
        path = ensure_config_dir() / "worker_key.pem"
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    path.write_bytes(pem)
    os.chmod(path, 0o600)
    return path


def load_private_key(path: Path | None = None) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from a PEM file."""
    if path is None:
        path = CONFIG_DIR / "worker_key.pem"
    pem = path.read_bytes()
    key = load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Expected Ed25519 private key")
    return key


def get_public_key_b64(private_key: Ed25519PrivateKey) -> str:
    """Return the base64-encoded 32-byte raw public key for *private_key*."""
    pub_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(pub_raw).decode()


def save_secret(secret: bytes, path: Path | None = None) -> Path:
    """Write the raw shared secret bytes to disk and set permissions to 0600."""
    if path is None:
        path = ensure_config_dir() / "worker_secret.key"
    path.write_bytes(secret)
    os.chmod(path, 0o600)
    return path


def load_secret(path: Path | None = None) -> bytes:
    """Read and return the raw shared secret bytes from disk."""
    if path is None:
        path = CONFIG_DIR / "worker_secret.key"
    return path.read_bytes()


def save_worker_id(worker_id: str, path: Path | None = None) -> Path:
    """Write the worker UUID string to ~/.runespy/worker_id."""
    if path is None:
        path = ensure_config_dir() / "worker_id"
    path.write_text(worker_id)
    return path


def load_worker_id(path: Path | None = None) -> str:
    """Read and return the worker UUID string from ~/.runespy/worker_id."""
    if path is None:
        path = CONFIG_DIR / "worker_id"
    return path.read_text().strip()


def decrypt_secret(encrypted_b64: str, public_key_b64: str) -> bytes:
    """Decrypt the shared secret blob issued by the server at approval time.

    The blob layout (after base64 decoding) is:
        bytes  0–11   : AES-GCM nonce (12 bytes)
        bytes 12–43   : AES key XOR'd with the worker's public key (32 bytes)
        bytes 44–end  : AES-GCM ciphertext + 16-byte authentication tag

    Decryption steps:
        1. XOR bytes 12–43 with own public key bytes to recover the AES key.
        2. AES-256-GCM decrypt using the recovered key and the nonce.

    Raises cryptography.exceptions.InvalidTag if the blob has been tampered
    with or was not encrypted for this worker's public key.
    """
    payload = base64.b64decode(encrypted_b64)
    pub_raw = base64.b64decode(public_key_b64)

    nonce = payload[:12]
    xored_key = payload[12:44]
    ciphertext = payload[44:]

    # Recover the AES key by reversing the XOR with the public key.
    aes_key = bytes(a ^ b for a, b in zip(xored_key, pub_raw))
    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def sign_challenge(private_key: Ed25519PrivateKey, nonce_hex: str) -> str:
    """Sign the hex-encoded challenge nonce with Ed25519.

    Returns the 64-byte signature as a hex string. The server verifies this
    against the public key stored at registration to confirm the worker's
    identity.
    """
    signature = private_key.sign(bytes.fromhex(nonce_hex))
    return signature.hex()


def hmac_challenge(secret: bytes, nonce_hex: str) -> str:
    """Compute HMAC-SHA256 of the challenge nonce using the shared secret.

    Returns the digest as a hex string. This is included alongside the
    Ed25519 signature in the auth message, proving possession of both the
    private key and the shared secret issued at approval time.
    """
    return _hmac.new(secret, bytes.fromhex(nonce_hex), hashlib.sha256).hexdigest()
