# linkedin/csv_launcher.py
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from linkedin.campaigns.connect_follow_up import process_profile_row, CAMPAIGN_NAME, INPUT_CSV_PATH
from linkedin.conf import get_first_active_account
from linkedin.db.profiles import url_to_public_id, add_profiles_to_campaign
from linkedin.sessions.registry import AccountSessionRegistry

logger = logging.getLogger(__name__)


def load_profiles_urls_from_csv(csv_path: Path | str):
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
    return urls_df.to_dict(orient="records")


def launch_from_csv(
        handle: str,
        csv_path: Path | str = INPUT_CSV_PATH,
        campaign_name: str = CAMPAIGN_NAME,
):
    logger.info(f"Launching campaign '{campaign_name}' → running as @{handle} | CSV: {csv_path}")

    profiles = load_profiles_urls_from_csv(csv_path)
    logger.info(f"Loaded {len(profiles):,} profiles from CSV – ready for battle!")

    session, key = AccountSessionRegistry.get_or_create_from_path(
        handle=handle,
        campaign_name=campaign_name,
        csv_path=csv_path,
    )

    add_profiles_to_campaign(session, profiles)

    for profile in profiles:
        while go_ahead := process_profile_row(
                key=key,
                session=session,
                profile=profile,
        ):
            if not go_ahead:
                break


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
