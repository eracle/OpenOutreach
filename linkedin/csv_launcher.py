# linkedin/csv_launcher.py
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from linkedin.api.emails import ensure_newsletter_subscription
from linkedin.campaigns.connect_follow_up import process_profiles
from linkedin.conf import get_first_active_account
from linkedin.db.crm_profiles import get_updated_at_map
from linkedin.db.crm_profiles import url_to_public_id
from linkedin.sessions.registry import get_session

logger = logging.getLogger(__name__)


def load_profiles_df(csv_path: Path | str):
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

    # Clean, dedupe, keep as DataFrame
    urls_df = (
        df[[url_column]]
        .astype(str)
        .apply(lambda col: col.str.strip())
        .replace({"nan": None, "<NA>": None})
        .dropna()
        .drop_duplicates()
    )

    # Add public identifier
    urls_df["public_identifier"] = urls_df[url_column].apply(url_to_public_id)
    logger.debug(f"First 10 rows of {csv_path.name}:\n"
                 f"{urls_df.head(10).to_string(index=False)}"
                 )
    logger.info(f"Loaded {len(urls_df):,} pristine LinkedIn profile URLs")
    return urls_df


def sort_profiles(session: "AccountSession", profiles_df: pd.DataFrame) -> list:
    """
    Return profiles sorted by updated_at (oldest first).
    Profiles not in the database come first.
    """
    if profiles_df.empty:
        return []

    records = profiles_df.to_dict(orient="records")
    public_ids = [r["public_identifier"] for r in records]

    # Get DB timestamps as dict: public_id → datetime
    ts_map = get_updated_at_map(session, public_ids)

    NOT_IN_DB = datetime(1970, 1, 1, tzinfo=timezone.utc)

    # Sort: profiles not in DB (NOT_IN_DB) come first, then oldest
    records.sort(key=lambda r: ts_map.get(r["public_identifier"], NOT_IN_DB))

    not_in_db = sum(1 for r in records if r["public_identifier"] not in ts_map)
    logger.info(
        f"Sorted {len(records):,} profiles by last updated: "
        f"{not_in_db} new, {len(records) - not_in_db} existing (oldest first)"
    )
    return records


def launch_connect_follow_up_campaign(
        handle: Optional[str] = None,
):
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
        logger.info(f"No handle chosen → auto-picking the boss account: @{handle}")

    session = get_session(
        handle=handle,
    )

    input_csv = session.account_cfg['input_csv']
    logger.info(f"Launching campaign → running as @{handle} | CSV: {input_csv}")

    profiles_df = load_profiles_df(input_csv)
    profiles = sort_profiles(session, profiles_df)

    logger.info(f"Loaded {len(profiles):,} profiles from CSV – ready for battle!")

    session.ensure_browser()
    ensure_newsletter_subscription(session)
    process_profiles(handle, session, profiles)
