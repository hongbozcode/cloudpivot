from hashlib import sha256
from secrets import token_urlsafe

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


_hasher = PasswordHasher()
_dummy_hash = _hasher.hash(token_urlsafe(32))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(encoded: str | None, password: str) -> bool:
    try:
        verified = _hasher.verify(encoded or _dummy_hash, password)
    except (VerificationError, InvalidHashError):
        return False
    return verified and encoded is not None


def new_token() -> str:
    return token_urlsafe(32)


def token_digest(token: str) -> bytes:
    return sha256(token.encode()).digest()
