# linkedin/csv_launcher.py
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from linkedin.campaigns.connect_follow_up import process_profile_row, CAMPAIGN_NAME, INPUT_CSV_PATH
from linkedin.conf import get_first_active_account
from linkedin.db.profiles import get_updated_at_df
from linkedin.db.profiles import url_to_public_id
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.navigation.utils import save_page
from linkedin.sessions.registry import AccountSessionRegistry

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


def sort_profiles(session: "AccountSession", profiles_df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a new DataFrame sorted by updated_at (oldest first).
    Profiles not in the database come first.
    """
    if profiles_df.empty:
        return profiles_df.copy()

    public_ids = profiles_df["public_identifier"].tolist()

    # Get DB timestamps as DataFrame
    db_df = get_updated_at_df(session, public_ids)

    # Left join: keep all input profiles
    merged = profiles_df.merge(db_df, on="public_identifier", how="left")

    # Sentinel for profiles not in DB
    sentinel = pd.Timestamp("1970-01-01")
    merged["updated_at"] = merged["updated_at"].fillna(sentinel)

    # Sort: oldest (including new profiles) first
    sorted_df = merged.sort_values(by="updated_at").drop(columns="updated_at")

    logger.debug(f"Sorted:\n"
                 f"{sorted_df.head(10).to_string(index=False)}"
                 )
    not_in_db = (merged["updated_at"] == sentinel).sum()
    logger.info(
        f"Sorted {len(sorted_df):,} profiles by last updated: "
        f"{not_in_db} new, {len(sorted_df) - not_in_db} existing (oldest first)"
    )

    return sorted_df


def launch_from_csv(
        handle: str,
        csv_path: Path | str = INPUT_CSV_PATH,
        campaign_name: str = CAMPAIGN_NAME,
):
    session, key = AccountSessionRegistry.get_or_create_from_path(
        handle=handle,
        campaign_name=campaign_name,
        csv_path=csv_path,
    )

    logger.info(f"Launching campaign '{campaign_name}' → running as @{handle} | CSV: {csv_path}")

    profiles_df = load_profiles_df(csv_path)
    profiles_df = sort_profiles(session, profiles_df)

    profiles = profiles_df.to_dict(orient="records")
    logger.info(f"Loaded {len(profiles):,} profiles from CSV – ready for battle!")

    session.ensure_browser()
    perform_connections = True
    for profile in profiles:
        go_ahead = True
        while go_ahead:
            try:
                profile = process_profile_row(
                    key=key,
                    session=session,
                    profile=profile,
                    perform_connections=perform_connections,
                )
                go_ahead = bool(profile)
            except SkipProfile as e:
                public_identifier = profile["public_identifier"]
                logger.info(f"\033[91mSkipping profile: {public_identifier} reason: {e}\033[0m")
                save_page(session, profile)
                go_ahead = False
            except ReachedConnectionLimit as e:
                perform_connections = False
                public_identifier = profile["public_identifier"]
                logger.info(f"\033[91mSkipping profile: {public_identifier} reason: {e}\033[0m")

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

    launch_from_csv(handle=handle)
