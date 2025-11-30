# linkedin/csv_launcher.py
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, Callable
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


def load_profile_urls_from_csv(csv_path: Path | str) -> List[str]:
    """
    Loads LinkedIn profile URLs from a CSV file.
    Assumes the file has at least one column containing URLs.
    Supported column names (case-insensitive):
        - url
        - linkedin_url
        - profile_url
        - Url, URL, etc.

    Returns a clean list of string URLs (deduped, stripped, no NaN).
    """
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    df = pd.read_csv(csv_path)

    # Find the first column that looks like it contains URLs
    possible_cols = ["url", "linkedin_url", "profile_url"]
    url_column = next(
        (col for col in df.columns if col.lower() in [c.lower() for c in possible_cols]),
        None,
    )

    if url_column is None:
        raise ValueError(
            f"Could not find a URL column in {csv_path}\n"
            f"   Found columns: {list(df.columns)}\n"
            f"   Expected one of: {possible_cols}"
        )

    urls = (
        df[url_column]
        .astype(str)
        .str.strip()
        .replace({"nan": None, "<NA>": None})
        .dropna()
        .tolist()
    )

    return urls


def launch_from_csv(
        handle: str,
        csv_path: Path | str = INPUT_CSV_PATH,
        campaign_name: str = CAMPAIGN_NAME,
        *,
        process_row_func: Callable[..., Dict[str, Any]] = process_profile_row,
        max_concurrency: int = 1,  # placeholder for future async/threading support
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
    process_row_func : Callable
        The function that processes a single row. Defaults to the one in connect_follow_up.
    max_concurrency : int
        Reserved for future parallel execution (currently runs sequentially).

    Returns
    -------
    List[Dict[str, Any]]
        List of result dictionaries returned by `process_row_func` for each row.
    """
    from linkedin.sessions import AccountSessionRegistry
    
    logger.info(f"Launching campaign '{campaign_name}' from CSV: {csv_path}")

    csv_hash = hash_file(csv_path)
    logger.info(f"CSV hash for this run: {csv_hash}")

    profiles = load_profile_urls_from_csv(csv_path)
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

        try:
            result = process_row_func(
                profile_url=profile_url,
                handle=handle,
                campaign_name=campaign_name,
                csv_hash=csv_hash,
            )
            results.append(result)
            logger.info(f"[{idx}] Completed → {handle} | Status: {result.get('status')}")
        except Exception as exc:
            logger.error(f"[{idx}] Failed processing {handle} ({profile_url}): {exc}", exc_info=True)
            results.append(
                {
                    "handle": handle,
                    "profile_url": profile_url,
                    "status": "error",
                    "error": str(exc),
                }
            )

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
