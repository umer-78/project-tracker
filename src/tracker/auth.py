"""Password hashing and bearer tokens.

Passwords use `hashlib.scrypt` from the standard library with a per-user salt.
Tokens are random, stored hashed-free but opaque, and expire. Comparisons use
`hmac.compare_digest`, so a wrong password takes the same time as a right one.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from .db import Database

TOKEN_DAYS = 14
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password: str, salt: bytes | None = None) -> str:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), dklen=32, **SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected.hex(), digest_hex)


def create_user(db: Database, email: str, name: str, password: str, role: str = "member") -> dict:
    if role not in ("admin", "member", "viewer"):
        raise ValueError(f"unknown role {role!r}")
    user_id = db.execute(
        "INSERT INTO users (email, name, role, password_hash) VALUES (?, ?, ?, ?)",
        (email.lower().strip(), name.strip(), role, hash_password(password)),
    )
    return db.one("SELECT id, email, name, role FROM users WHERE id = ?", (user_id,))


def issue_token(db: Database, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS)).isoformat()
    db.execute("INSERT INTO tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
               (token, user_id, expires))
    return token


def login(db: Database, email: str, password: str) -> tuple[dict, str] | None:
    user = db.one("SELECT * FROM users WHERE email = ?", (email.lower().strip(),))
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return {k: user[k] for k in ("id", "email", "name", "role")}, issue_token(db, user["id"])


def user_for_token(db: Database, token: str) -> dict | None:
    row = db.one(
        """SELECT u.id, u.email, u.name, u.role, t.expires_at
           FROM tokens t JOIN users u ON u.id = t.user_id WHERE t.token = ?""",
        (token,),
    )
    if not row:
        return None
    if datetime.fromisoformat(row.pop("expires_at")) < datetime.now(timezone.utc):
        db.execute("DELETE FROM tokens WHERE token = ?", (token,))
        return None
    return row


def revoke(db: Database, token: str) -> None:
    db.execute("DELETE FROM tokens WHERE token = ?", (token,))
