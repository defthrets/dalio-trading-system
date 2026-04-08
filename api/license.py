"""
DALIOS License Validation
Validates license keys via LemonSqueezy's API.
Stores activation locally in data/license.json.
"""

import hashlib
import json
import platform
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

# ── Paths ──────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
LICENSE_FILE = DATA_DIR / "license.json"

# ── LemonSqueezy API ──────────────────────────────
LEMON_ACTIVATE_URL = "https://api.lemonsqueezy.com/v1/licenses/activate"
LEMON_VALIDATE_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"
LEMON_DEACTIVATE_URL = "https://api.lemonsqueezy.com/v1/licenses/deactivate"

# Revalidate online every 30 days
REVALIDATION_DAYS = 30

# ── Master admin key (bypasses LemonSqueezy) ──────
MASTER_KEY = "DALIOS-MASTER-9F3A-7X2K-ADMIN"


def _machine_fingerprint() -> str:
    """Generate a unique machine fingerprint from hardware identifiers."""
    raw = f"{platform.node()}-{platform.machine()}-{platform.system()}-{uuid.getnode()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _load_license() -> Optional[dict]:
    """Load stored license data from disk."""
    if LICENSE_FILE.exists():
        try:
            return json.loads(LICENSE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_license(data: dict):
    """Persist license data to disk."""
    LICENSE_FILE.write_text(json.dumps(data, indent=2))


def _clear_license():
    """Remove stored license data."""
    if LICENSE_FILE.exists():
        LICENSE_FILE.unlink()


def is_licensed() -> bool:
    """Check if the app has a valid local activation."""
    lic = _load_license()
    if not lic:
        return False

    # Check machine fingerprint matches
    if lic.get("machine_id") != _machine_fingerprint():
        return False

    # Check if activation is present
    if not lic.get("activated"):
        return False

    return True


def needs_revalidation() -> bool:
    """Check if online revalidation is due. Master keys never need revalidation."""
    lic = _load_license()
    if not lic:
        return True
    if lic.get("is_master"):
        return False

    last = lic.get("last_validated")
    if not last:
        return True

    try:
        last_dt = datetime.fromisoformat(last)
        return datetime.now() - last_dt > timedelta(days=REVALIDATION_DAYS)
    except (ValueError, TypeError):
        return True


async def activate_license(key: str) -> dict:
    """
    Activate a license key via LemonSqueezy API.
    Master key bypasses online validation entirely.
    Returns dict with 'success', 'message', and optionally 'data'.
    """
    fingerprint = _machine_fingerprint()

    # Master key — instant activation, no internet needed
    if key.strip() == MASTER_KEY:
        _save_license({
            "license_key": key,
            "instance_id": "master",
            "machine_id": fingerprint,
            "activated": True,
            "activated_at": datetime.now().isoformat(),
            "last_validated": datetime.now().isoformat(),
            "customer_name": "Admin",
            "customer_email": "admin",
            "product_name": "DALIOS Master",
            "is_master": True,
        })
        logger.info("Master license activated")
        return {"success": True, "message": "Master license activated."}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                LEMON_ACTIVATE_URL,
                json={
                    "license_key": key,
                    "instance_name": f"DALIOS-{platform.node()}",
                },
                headers={"Accept": "application/json"},
            )

        body = resp.json()

        if resp.status_code == 200 and body.get("activated"):
            # Store activation locally
            _save_license({
                "license_key": key,
                "instance_id": body.get("instance", {}).get("id", ""),
                "machine_id": fingerprint,
                "activated": True,
                "activated_at": datetime.now().isoformat(),
                "last_validated": datetime.now().isoformat(),
                "customer_name": body.get("meta", {}).get("customer_name", ""),
                "customer_email": body.get("meta", {}).get("customer_email", ""),
                "product_name": body.get("meta", {}).get("product_name", "DALIOS"),
            })
            logger.info(f"License activated successfully for {platform.node()}")
            return {"success": True, "message": "License activated successfully!"}

        # Handle specific error cases
        error = body.get("error", "Activation failed")
        if "limit" in str(error).lower():
            return {"success": False, "message": "Device limit reached. Deactivate another device first or contact support."}
        if "invalid" in str(error).lower() or resp.status_code == 404:
            return {"success": False, "message": "Invalid license key. Please check and try again."}
        if "expired" in str(error).lower():
            return {"success": False, "message": "This license key has expired."}
        if "disabled" in str(error).lower():
            return {"success": False, "message": "This license key has been disabled."}

        return {"success": False, "message": str(error)}

    except httpx.ConnectError:
        return {"success": False, "message": "Cannot connect to activation server. Check your internet connection."}
    except httpx.TimeoutException:
        return {"success": False, "message": "Activation server timed out. Please try again."}
    except Exception as e:
        logger.error(f"License activation error: {e}")
        return {"success": False, "message": f"Activation error: {str(e)}"}


async def validate_license() -> dict:
    """
    Validate the stored license key online (periodic revalidation).
    Returns dict with 'valid' and 'message'.
    """
    lic = _load_license()
    if not lic or not lic.get("license_key"):
        return {"valid": False, "message": "No license found"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                LEMON_VALIDATE_URL,
                json={"license_key": lic["license_key"]},
                headers={"Accept": "application/json"},
            )

        body = resp.json()

        if resp.status_code == 200 and body.get("valid"):
            # Update last validated timestamp
            lic["last_validated"] = datetime.now().isoformat()
            _save_license(lic)
            return {"valid": True, "message": "License is valid"}

        # License revoked or expired — clear local activation
        _clear_license()
        return {"valid": False, "message": "License is no longer valid. Please re-activate."}

    except (httpx.ConnectError, httpx.TimeoutException):
        # Can't reach server — allow offline grace period
        logger.warning("Cannot reach license server for revalidation — allowing offline grace")
        return {"valid": True, "message": "Offline — using cached activation"}
    except Exception as e:
        logger.error(f"License validation error: {e}")
        return {"valid": True, "message": "Validation error — using cached activation"}


async def deactivate_license() -> dict:
    """Deactivate the current license (free up a device slot)."""
    lic = _load_license()
    if not lic or not lic.get("license_key"):
        return {"success": False, "message": "No license to deactivate"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                LEMON_DEACTIVATE_URL,
                json={
                    "license_key": lic["license_key"],
                    "instance_id": lic.get("instance_id", ""),
                },
                headers={"Accept": "application/json"},
            )

        if resp.status_code == 200:
            _clear_license()
            return {"success": True, "message": "License deactivated. You can activate on another device."}

        return {"success": False, "message": "Deactivation failed. Please try again."}

    except Exception as e:
        logger.error(f"License deactivation error: {e}")
        return {"success": False, "message": f"Deactivation error: {str(e)}"}


def get_license_status() -> dict:
    """Get current license status for the UI."""
    lic = _load_license()
    if not lic:
        return {"licensed": False}

    return {
        "licensed": is_licensed(),
        "customer_email": lic.get("customer_email", ""),
        "activated_at": lic.get("activated_at", ""),
        "last_validated": lic.get("last_validated", ""),
        "needs_revalidation": needs_revalidation(),
    }
