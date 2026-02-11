# linkedin/csv_launcher.py
import logging
from pathlib import Path

import pandas as pd

from linkedin.db.crm_profiles import url_to_public_id

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
