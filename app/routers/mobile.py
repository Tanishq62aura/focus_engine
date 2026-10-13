"""
app/routers/mobile.py
Mobile shell OTA update manifest pipeline.
Serves web asset checksums, version specifications, and delta upgrade triggers.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from app.schemas import MobileManifestResponse

logger = logging.getLogger("focus_engine.mobile")

router = APIRouter()

LATEST_VERSION = "1.0.0"
LATEST_BUILD_NUMBER = 100
RELEASE_NOTES = "Focus Engine 1.0.0 Mobile Shell with OTA manifest synchronization."


def compute_web_asset_hash() -> str:
    """Compute the SHA-256 hash of production static/index.html web assets."""
    static_file = Path(__file__).resolve().parent.parent.parent / "static" / "index.html"
    if not static_file.exists():
        static_file = Path("static/index.html")
    if static_file.exists():
        hasher = hashlib.sha256()
        with open(static_file, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()
    return "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@router.get("/manifest", response_model=MobileManifestResponse)
def get_mobile_manifest(
    current_build: Optional[int] = Query(None, description="Current client build number"),
) -> MobileManifestResponse:
    """
    Returns the production OTA update manifest.
    Determines update_available based on client current_build comparison.
    """
    sha256_hash = compute_web_asset_hash()
    is_update_available = (current_build is not None and current_build < LATEST_BUILD_NUMBER)

    return MobileManifestResponse(
        latest_version=LATEST_VERSION,
        build_number=LATEST_BUILD_NUMBER,
        bundle_url="/static/index.html",
        checksum_sha256=sha256_hash,
        mandatory_update=False,
        release_notes=RELEASE_NOTES,
        update_available=is_update_available,
    )
