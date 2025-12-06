# linkedin/csv_launcher.py
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

import pandas as pd

from linkedin.campaigns.connect_follow_up import process_profile_row, CAMPAIGN_NAME, INPUT_CSV_PATH
from linkedin.conf import get_first_active_account
from linkedin.navigation.utils import decode_url_path_only

logger = logging.getLogger(__name__)


def hash_file(path: Path | str, chunk_size: int = 8192, algorithm: str = "sha256") -> str:
    """
    Compute a stable cryptographic hash of a file's contents.
    Used to detect if the input CSV has changed → new campaign run.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Cannot hash file: {path} does not exist or is not a file")

    hasher = hashlib.new(algorithm)

    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)

    full_hex = hasher.hexdigest()
    short_hex = full_hex[:16]
    logger.debug(f"Hashed file {path.name} → {short_hex} (full: {full_hex})")
    return short_hex


def load_profiles_urls_from_csv(csv_path: Path | str) -> List[str]:
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    df = pd.read_csv(csv_path)

    possible_cols = ["url", "linkedin_url", "profile_url"]
    url_column = next(
        (col for col in df.columns if col.lower() in [c.lower() for c in possible_cols]),
        None,
    )

    if url_column is None:
        raise ValueError(f"No URL column found. Available: {list(df.columns)}")

    urls = (
        df[url_column]
        .astype(str)
        .str.strip()
        .replace({"nan": None, "<NA>": None})
        .dropna()
        .apply(decode_url_path_only)  # Only clean decoded path, no query params
        .drop_duplicates()  # Remove duplicates (preserves order)

    )

    logger.debug(f"First 10 rows of {csv_path.name}:\n{urls.head(10).to_string(index=False)}")

    logger.info(f"Loaded {len(urls)} unique clean LinkedIn profile URLs")
    return urls.tolist()


def launch_from_csv(
        handle: str,
        csv_path: Path | str = INPUT_CSV_PATH,
        campaign_name: str = CAMPAIGN_NAME,
) -> List[Dict[str, Any]]:
    from linkedin.sessions import AccountSessionRegistry

    logger.info(f"Launching campaign '{campaign_name}' as @{handle} from CSV: {csv_path}")

    profiles = load_profiles_urls_from_csv(csv_path)
    logger.info(f"Loaded {len(profiles)} profiles from CSV")

    session = AccountSessionRegistry.get_or_create_from_path(
        handle=handle,
        campaign_name=campaign_name,
        csv_path=csv_path,
    )

    results: List[Dict[str, Any]] = []

    for idx, profile_url in enumerate(profiles, start=1):
        logger.info(f"[{idx}/{len(profiles)}] Processing → {profile_url}")

        result = process_profile_row(
            profile_url=profile_url,
            handle=handle,
            campaign_name=campaign_name,
        )
        results.append(result)
        status = result.get('status', 'unknown')
        logger.info(f"[{idx}] Completed → @{handle} | Status: {status}")

    # Summary
    successful = sum(1 for r in results if r.get("status") == "completed")
    waiting = sum(1 for r in results if r.get("status") == "waiting_for_acceptance")
    errors = len(results) - successful - waiting

    logger.info(
        f"Campaign '{campaign_name}' finished | "
        f"Completed: {successful} | Waiting: {waiting} | Errors: {errors}"
    )

    return results


def launch_connect_follow_up_campaign(
        handle: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    One-liner to run the connect → follow-up campaign.

    If handle is not provided, automatically uses the first active account
    from accounts.secrets.yaml — perfect for quick tests and notebooks!
    """
    if handle is None:
        handle = get_first_active_account()
        if handle is None:
            raise RuntimeError(
                "No handle provided and no active accounts found in assets/accounts.secrets.yaml. "
                "Please either pass a handle explicitly or add at least one active account."
            )
        logger.info(f"No handle specified → using first active account: @{handle}")

    return launch_from_csv(handle=handle)
