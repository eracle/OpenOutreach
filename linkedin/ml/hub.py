# linkedin/ml/hub.py
"""Campaign kit: download from HuggingFace, lazy-load, partner campaign import."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from linkedin.conf import MODELS_DIR, PARTNER_LOG_LEVEL

logger = logging.getLogger(__name__)


_KIT_DIR = MODELS_DIR / "hub"
_cached_kit: Optional[dict] = None
_cache_attempted = False


# ------------------------------------------------------------------
# Kit download & loading
# ------------------------------------------------------------------

_DEFAULT_REPO_ID = "eracle/campaign-kit"


def download_kit(revision: str = "v1") -> Optional[Path]:
    """Download campaign kit from HuggingFace Hub. Returns path or None."""
    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(
            repo_id=_DEFAULT_REPO_ID,
            revision=revision,
            local_dir=str(_KIT_DIR),
        )
        logger.log(PARTNER_LOG_LEVEL, "Kit downloaded to %s", path)
        return Path(path)
    except Exception:
        logger.log(PARTNER_LOG_LEVEL, "Kit download failed", exc_info=True)
        return None


def load_kit_config(kit_dir: Path) -> Optional[dict]:
    """Parse config.json from kit directory. Returns dict or None."""
    try:
        config_path = kit_dir / "config.json"
        data = json.loads(config_path.read_text())

        required = ("action_fraction", "product_docs", "campaign_objective",
                     "booking_link", "followup_template")
        for key in required:
            if key not in data:
                logger.log(PARTNER_LOG_LEVEL, "Kit config missing key: %s", key)
                return None

        logger.log(PARTNER_LOG_LEVEL, "Kit config loaded (action_fraction=%.2f)", data["action_fraction"])
        return data
    except Exception:
        logger.log(PARTNER_LOG_LEVEL, "Kit config load failed", exc_info=True)
        return None


def load_kit_model(kit_dir: Path):
    """Load pre-trained model from kit. Returns any sklearn-compatible estimator or None.

    The loaded object just needs a ``predict(X)`` method — it can be a
    Pipeline, a bare estimator, or any future model architecture.
    """
    try:
        import joblib

        model = joblib.load(kit_dir / "model.joblib")

        if not hasattr(model, "predict"):
            logger.log(PARTNER_LOG_LEVEL, "Kit model has no predict() method")
            return None

        logger.log(PARTNER_LOG_LEVEL, "Kit model loaded (%s)", type(model).__name__)
        return model
    except Exception:
        logger.log(PARTNER_LOG_LEVEL, "Kit model load failed", exc_info=True)
        return None


def get_kit() -> Optional[dict]:
    """Lazy-load and cache the kit. Returns {"config": ..., "model": ...} or None."""
    global _cached_kit, _cache_attempted

    if _cache_attempted:
        return _cached_kit

    _cache_attempted = True

    kit_dir = download_kit()
    if kit_dir is None:
        return None

    config = load_kit_config(kit_dir)
    if config is None:
        return None

    model = load_kit_model(kit_dir)
    if model is None:
        return None

    _cached_kit = {"config": config, "model": model}
    return _cached_kit


# ------------------------------------------------------------------
# Partner campaign import
# ------------------------------------------------------------------

def import_partner_campaign(kit_config: dict):
    """Create or update a partner Campaign from kit config.

    Creates the department, pipeline, and adds all active users to the group.
    Returns the Campaign instance or None.
    """
    from common.models import Department
    from linkedin.management.setup_crm import ensure_campaign_pipeline
    from linkedin.models import Campaign, LinkedInProfile

    dept_name = kit_config.get("campaign_name", "Partner Outreach")
    dept, _ = Department.objects.get_or_create(name=dept_name)

    ensure_campaign_pipeline(dept)

    campaign, _ = Campaign.objects.update_or_create(
        department=dept,
        defaults={
            "product_docs": kit_config["product_docs"],
            "campaign_objective": kit_config["campaign_objective"],
            "followup_template": kit_config["followup_template"],
            "booking_link": kit_config["booking_link"],
            "is_partner": True,
            "action_fraction": kit_config["action_fraction"],
        },
    )

    # Add all active LinkedIn users to this department group
    for lp in LinkedInProfile.objects.filter(active=True).select_related("user"):
        if dept not in lp.user.groups.all():
            lp.user.groups.add(dept)

    from termcolor import colored
    logger.log(PARTNER_LOG_LEVEL, colored("Campaign imported: %s (action_fraction=%.2f)", "yellow", attrs=["bold"]),
               dept_name, kit_config["action_fraction"])
    return campaign
