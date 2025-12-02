# linkedin/csv_launcher.py
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any
from typing import List

import pandas as pd

from linkedin.campaigns.connect_follow_up import process_profile_row, CAMPAIGN_NAME, INPUT_CSV_PATH

logger = logging.getLogger(__name__)


def hash_file(path: Path | str, chunk_size: int = 8192, algorithm: str = "sha256") -> str:
    """
    Compute a stable cryptographic hash of a file's contents.
    Used to detect if the input CSV has changed → new campaign run.

    Returns hex digest (first 16 chars by default for brevity + safety).
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

    # Simple preview of first 10 rows
    logger.debug(f"First 10 rows of {csv_path.name}:\n{df.head(10).to_string(index=False)}")

    # Find URL column
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
        .dropna()
        .replace({"nan": None})
        .tolist()
    )

    # Simple dedupe
    unique_urls = list(dict.fromkeys(urls))  # preserves order

    logger.info(f"Loaded {len(unique_urls)} unique profile URLs from CSV")

    return unique_urls


def launch_from_csv(
        handle: str,
        csv_path: Path | str = INPUT_CSV_PATH,
        campaign_name: str = CAMPAIGN_NAME,
) -> List[Dict[str, Any]]:
    """
    Generic CSV launcher used by all campaigns.

    Parameters
    ----------
    handle : str
        Account used to perform the automation.
    csv_path : Path | str
        Path to the input CSV containing at least `profile_url` and `handle` columns.
    campaign_name : str
        Name of the running campaign (used for logging & state tracking).

    Returns
    -------
    List[Dict[str, Any]]
        List of result dictionaries returned by `process_row_func` for each row.
    """
    from linkedin.sessions import AccountSessionRegistry
    
    logger.info(f"Launching campaign '{campaign_name}' from CSV: {csv_path}")

    profiles = load_profiles_urls_from_csv(csv_path)
    logger.info(f"Loaded {len(profiles)} profiles from CSV")

    session = AccountSessionRegistry.get_or_create_from_path(
        handle=handle,
        campaign_name=campaign_name,
        csv_path=csv_path,
    )
    _ = session.resources

    results: List[Dict[str, Any]] = []

    # Sequential execution (easy to make concurrent later)
    for idx, profile_url in enumerate(profiles, start=1):
        logger.info(f"[{idx}/{len(profiles)}] Starting → {handle}")

        result = process_profile_row(
            profile_url=profile_url,
            handle=handle,
            campaign_name=campaign_name,
        )
        results.append(result)
        logger.info(f"[{idx}] Completed → {handle} | Status: {result.get('status')}")


    # Summary
    successful = sum(1 for r in results if r.get("status") == "completed")
    waiting = sum(1 for r in results if r.get("status") == "waiting_for_acceptance")
    errors = len(results) - successful - waiting

    logger.info(
        f"Campaign '{campaign_name}' finished | "
        f"Completed: {successful} | Waiting: {waiting} | Errors: {errors}"
    )

    return results


# ——————————————— Convenience wrapper for this specific campaign ———————————————
def launch_connect_follow_up_campaign(
        handle: str,
) -> List[Dict[str, Any]]:
    """
    One-liner you can call from scripts or notebooks to run the
    connect → follow-up campaign.
    """
    return launch_from_csv(
        handle=handle,
    )
