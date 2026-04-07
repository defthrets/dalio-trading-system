"""
JWT Authentication Middleware for Dalios.

Provides token-based auth for multi-user deployments.
Disabled by default (single-user mode). Enable by setting:
  DALIOS_AUTH_ENABLED=true
  DALIOS_JWT_SECRET=<random-64-char-secret>

Usage:
  POST /api/auth/register  — create account (username + password)
  POST /api/auth/login     — get JWT token
  All /api/* routes require Authorization: Bearer <token> when auth is enabled.
"""

import hashlib
import hmac
import json
import time
import os
import secrets
from datetime import datetime
from typing import Optional

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import declarative_base

from data.storage.models import Base, get_session


# ── User Model ────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    salt = Column(String(32), nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)


# ── Config ────────────────────────────────────────────────────

AUTH_ENABLED = os.environ.get("DALIOS_AUTH_ENABLED", "false").lower() == "true"
JWT_SECRET = os.environ.get("DALIOS_JWT_SECRET", secrets.token_hex(32))
JWT_EXPIRY_HOURS = int(os.environ.get("DALIOS_JWT_EXPIRY_HOURS", "24"))


# ── Password Hashing (no bcrypt dependency) ──────────────────

def _hash_password(password: str, salt: str) -> str:
    """PBKDF2-SHA256 password hash (stdlib, no extra deps)."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), iterations=100_000
    ).hex()


def _verify_password(password: str, salt: str, password_hash: str) -> bool:
    return hmac.compare_digest(_hash_password(password, salt), password_hash)


# ── Minimal JWT (no PyJWT dependency) ────────────────────────
# Uses HMAC-SHA256 — suitable for single-issuer server-side auth.

import base64


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)


def create_token(user_id: int, username: str, is_admin: bool = False) -> str:
    """Create a JWT token with HMAC-SHA256 signature."""
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())

    payload = {
        "sub": user_id,
        "username": username,
        "admin": is_admin,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600,
    }
    payload_enc = _b64url_encode(json.dumps(payload).encode())

    signature = hmac.new(
        JWT_SECRET.encode(), f"{header}.{payload_enc}".encode(), hashlib.sha256
    ).digest()
    sig_enc = _b64url_encode(signature)

    return f"{header}.{payload_enc}.{sig_enc}"


def verify_token(token: str) -> Optional[dict]:
    """Verify and decode a JWT token. Returns payload dict or None."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header, payload_enc, sig_enc = parts

        # Verify signature
        expected_sig = hmac.new(
            JWT_SECRET.encode(), f"{header}.{payload_enc}".encode(), hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(sig_enc)

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        # Decode payload
        payload = json.loads(_b64url_decode(payload_enc))

        # Check expiry
        if payload.get("exp", 0) < time.time():
            return None

        return payload
    except Exception:
        return None


# ── Auth Helpers ──────────────────────────────────────────────

def register_user(username: str, password: str, is_admin: bool = False) -> dict:
    """Create a new user account."""
    session = get_session()
    try:
        existing = session.query(User).filter_by(username=username).first()
        if existing:
            return {"error": "Username already exists"}

        salt = secrets.token_hex(16)
        password_hash = _hash_password(password, salt)

        user = User(
            username=username,
            password_hash=password_hash,
            salt=salt,
            is_admin=is_admin,
        )
        session.add(user)
        session.commit()

        logger.info(f"User registered: {username}")
        return {"user_id": user.id, "username": username}
    except Exception as e:
        session.rollback()
        logger.error(f"Registration failed: {e}")
        return {"error": str(e)}
    finally:
        session.close()


def login_user(username: str, password: str) -> dict:
    """Authenticate user and return JWT token."""
    session = get_session()
    try:
        user = session.query(User).filter_by(username=username).first()
        if not user:
            return {"error": "Invalid credentials"}

        if not _verify_password(password, user.salt, user.password_hash):
            return {"error": "Invalid credentials"}

        user.last_login = datetime.utcnow()
        session.commit()

        token = create_token(user.id, user.username, user.is_admin)
        return {"token": token, "username": username, "expires_in": JWT_EXPIRY_HOURS * 3600}
    except Exception as e:
        logger.error(f"Login failed: {e}")
        return {"error": str(e)}
    finally:
        session.close()


def get_current_user(request: Request) -> Optional[dict]:
    """Extract and verify user from Authorization header."""
    if not AUTH_ENABLED:
        return {"sub": 0, "username": "local", "admin": True}  # Single-user mode

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]
    return verify_token(token)


# ── Middleware ────────────────────────────────────────────────

async def auth_middleware(request: Request, call_next):
    """Require valid JWT for /api/ routes when auth is enabled."""
    if not AUTH_ENABLED:
        return await call_next(request)

    path = request.url.path

    # Public routes (no auth required)
    public_paths = [
        "/api/auth/login",
        "/api/auth/register",
        "/api/health",
        "/docs",
        "/openapi.json",
    ]
    if any(path.startswith(p) for p in public_paths):
        return await call_next(request)

    # Static files and UI don't need auth
    if not path.startswith("/api/"):
        return await call_next(request)

    user = get_current_user(request)
    if user is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required. POST /api/auth/login to get a token."},
        )

    # Attach user to request state for route handlers
    request.state.user = user
    return await call_next(request)
