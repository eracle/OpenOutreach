# linkedin/csv_launcher.py
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from linkedin.campaigns.engine import start_campaign
from linkedin.conf import get_first_active_account
from linkedin.db.crm_profiles import get_updated_at_df
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
    Return a new DataFrame sorted by updated_at (oldest first).
    Profiles not in the database come first.
    """
    if profiles_df.empty:
        return []

    public_ids = profiles_df["public_identifier"].tolist()

    # Get DB timestamps as DataFrame
    db_df = get_updated_at_df(session, public_ids)

    # Left join: keep all input profiles
    merged = profiles_df.merge(db_df, on="public_identifier", how="left")

    NOT_IN_DB = pd.Timestamp("1970-01-01 00:00:00", tz="UTC")

    # Force datetime conversion first + fillna
    merged["updated_at"] = (
        pd.to_datetime(merged["updated_at"], errors="coerce")
        .fillna(NOT_IN_DB)
    )

    # Sort: oldest (including new profiles) first
    sorted_df = merged.sort_values(by="updated_at").drop(columns="updated_at")

    logger.debug(f"Sorted:\n"
                 f"{sorted_df.head(10).to_string(index=False)}"
                 )
    not_in_db = (merged["updated_at"] == NOT_IN_DB).sum()
    logger.info(
        f"Sorted {len(sorted_df):,} profiles by last updated: "
        f"{not_in_db} new, {len(sorted_df) - not_in_db} existing (oldest first)"
    )
    return sorted_df.to_dict(orient="records")


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

    start_campaign(handle, session, profiles)
