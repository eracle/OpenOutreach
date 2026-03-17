# linkedin/ml/hub.py
"""Campaign kit: download from HuggingFace, lazy-load, freemium campaign import."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_cached_kit: Optional[dict] = None
_cache_attempted = False


# ------------------------------------------------------------------
# Kit download & loading
# ------------------------------------------------------------------

_DEFAULT_REPO_ID = "eracle/campaign-kit"


def download_kit(revision: str = "v1") -> Optional[Path]:
    """Download campaign kit from HuggingFace Hub to a temp directory. Returns path or None."""
    try:
        import huggingface_hub
        from huggingface_hub import snapshot_download

        huggingface_hub.utils.disable_progress_bars()
        logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
        logging.getLogger("filelock").setLevel(logging.WARNING)

        kit_dir = Path(tempfile.mkdtemp(prefix="openoutreach-kit-"))
        path = snapshot_download(
            repo_id=_DEFAULT_REPO_ID,
            revision=revision,
            local_dir=str(kit_dir),
        )
        # Remove HF download metadata cache — not needed after download
        shutil.rmtree(kit_dir / ".cache", ignore_errors=True)
        logger.info("[Freemium] Kit downloaded to %s", path)
        return Path(path)
    except Exception:
        logger.info("[Freemium] Kit download failed", exc_info=True)
        return None


def load_kit_config(kit_dir: Path) -> Optional[dict]:
    """Parse config.json from kit directory. Returns dict or None."""
    try:
        config_path = kit_dir / "config.json"
        data = json.loads(config_path.read_text())

        required = ("action_fraction", "product_docs", "campaign_objective",
                     "booking_link")
        for key in required:
            if key not in data:
                logger.info("[Freemium] Kit config missing key: %s", key)
                return None

        logger.info("[Freemium] Kit config loaded (action_fraction=%.2f)", data["action_fraction"])
        return data
    except Exception:
        logger.info("[Freemium] Kit config load failed", exc_info=True)
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
            logger.info("[Freemium] Kit model has no predict() method")
            return None

        logger.info("[Freemium] Kit model loaded (%s)", type(model).__name__)
        return model
    except Exception:
        logger.info("[Freemium] Kit model load failed", exc_info=True)
        return None


def fetch_kit() -> Optional[dict]:
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
