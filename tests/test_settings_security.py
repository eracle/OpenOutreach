from __future__ import annotations

import importlib.util
from pathlib import Path

SETTINGS_PY = Path(__file__).resolve().parent.parent / "openoutreach" / "settings.py"


def _load_settings(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SETTINGS_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_settings_use_deployment_secret_when_provided(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "deployment-secret")

    settings = _load_settings("settings_with_env_secret")

    assert settings.SECRET_KEY == "deployment-secret"


def test_settings_generate_nondefault_secret_when_env_missing(monkeypatch):
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)

    settings_a = _load_settings("settings_without_env_secret_a")
    settings_b = _load_settings("settings_without_env_secret_b")

    assert settings_a.SECRET_KEY != "openoutreach-local-dev-key-change-in-production"
    assert settings_b.SECRET_KEY != "openoutreach-local-dev-key-change-in-production"
    assert settings_a.SECRET_KEY != settings_b.SECRET_KEY
