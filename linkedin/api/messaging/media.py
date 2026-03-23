# linkedin/api/messaging/media.py
"""Upload media for LinkedIn messaging via Voyager API."""
import base64
import json
import logging
import mimetypes
from pathlib import Path

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.api.messaging.utils import check_response

logger = logging.getLogger(__name__)

# Module-level cache: (file_path, session_handle) → asset_urn
_upload_cache: dict[tuple[str, str], str] = {}

UPLOAD_METADATA_URL = (
    "https://www.linkedin.com/voyager/api"
    "/voyagerVideoDashMediaUploadMetadata?action=upload"
)


def register_media_upload(
    api: PlaywrightLinkedinAPI,
    filename: str,
    file_size: int,
) -> tuple[str, str | None]:
    """Register a media upload with LinkedIn.

    Returns (asset_urn, upload_url). upload_url may be None if LinkedIn
    handles the upload internally via the asset URN alone.
    """
    payload = {
        "mediaUploadType": "MESSAGING_PHOTO_ATTACHMENT",
        "fileSize": file_size,
        "filename": filename,
    }

    headers = {**api.headers}
    headers["content-type"] = "application/json; charset=UTF-8"

    res = api.post(UPLOAD_METADATA_URL, headers=headers, data=json.dumps(payload))
    check_response(res, "register_media_upload")

    data = res.json()
    logger.debug("Media upload registration response: %s", json.dumps(data, indent=2))

    # Response may be nested under "data.value" or "value" depending on API version
    value = data.get("data", data)
    value = value.get("value", value)

    asset_urn = (
        value.get("urn")
        or value.get("assetUrn")
        or value.get("digitalMediaAsset")
        or ""
    )

    upload_url = value.get("singleUploadUrl") or value.get("uploadUrl") or ""
    if not upload_url:
        instructions = value.get("uploadInstructions", [])
        if instructions:
            upload_url = instructions[0].get("uploadUrl", "")

    if not asset_urn:
        for item in data.get("included", []):
            urn = item.get("entityUrn", "")
            if "digitalmediaAsset" in urn:
                asset_urn = urn
                break

    logger.info("Registered media upload: asset_urn=%s, upload_url=%s", asset_urn, upload_url[:80] if upload_url else "N/A")
    return asset_urn, upload_url or None


def upload_file_bytes(
    api: PlaywrightLinkedinAPI,
    upload_url: str,
    file_bytes: bytes,
    mime_type: str,
) -> None:
    """Upload file bytes to the LinkedIn upload URL."""
    data_b64 = base64.b64encode(file_bytes).decode("ascii")

    res = api.put_binary(upload_url, data_b64, mime_type=mime_type)
    if not res.ok:
        raise IOError(f"Media upload failed: HTTP {res.status}")

    logger.info("Media file uploaded successfully (%d bytes)", len(file_bytes))


def upload_media(
    api: PlaywrightLinkedinAPI,
    file_path: str,
    session_handle: str = "",
) -> dict:
    """Upload a media file for messaging. Returns a file attachment dict
    ready to be placed in renderContentUnions.

    Caches the result per (file_path, session_handle) to avoid re-uploading.
    """
    cache_key = (file_path, session_handle)
    if cache_key in _upload_cache:
        cached = _upload_cache[cache_key]
        logger.debug("Using cached media URN: %s", cached["assetUrn"])
        return cached

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Media file not found: {file_path}")

    file_bytes = path.read_bytes()
    file_size = len(file_bytes)
    filename = path.name
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    # Step 1: Register
    asset_urn, upload_url = register_media_upload(api, filename, file_size)
    if not asset_urn:
        raise IOError("Failed to get asset URN from media upload registration")

    # Step 2: Upload binary (if upload URL provided)
    if upload_url:
        upload_file_bytes(api, upload_url, file_bytes, mime_type)

    # Build the file attachment dict matching LinkedIn's expected shape
    attachment = {
        "assetUrn": asset_urn,
        "byteSize": file_size,
        "mediaType": mime_type,
        "name": filename,
        "url": f"https://www.linkedin.com/dms-uploads/{asset_urn.split(':')[-1]}",
    }

    _upload_cache[cache_key] = attachment
    logger.info("Media ready: %s (%s, %d bytes)", filename, asset_urn, file_size)
    return attachment
