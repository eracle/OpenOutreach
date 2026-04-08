"""Pre-Django filesystem migrations."""
import importlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DIR = Path(__file__).parent
_ROOT = _DIR.parent.parent
_DATA_DIR = _ROOT / "data"
_RECORD = _DATA_DIR / ".premigrations"


def _applied():
    if _RECORD.exists():
        return set(json.loads(_RECORD.read_text()))
    return set()


def _save(applied):
    _RECORD.write_text(json.dumps(sorted(applied)))


def _discover():
    return sorted(p.stem for p in _DIR.glob("[0-9]*.py"))


def run_premigrations():
    applied = _applied()
    for name in _discover():
        if name in applied:
            continue
        logger.info("pre-migration: %s", name)
        mod = importlib.import_module(f"linkedin.premigrations.{name}")
        mod.forward(_ROOT)
        applied.add(name)
        _save(applied)
