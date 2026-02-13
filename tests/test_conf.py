# tests/test_conf.py
import pytest
from unittest.mock import patch

from linkedin.conf import (
    list_active_accounts,
    get_first_active_account,
    get_account_config,
    get_first_account_config,
)


def _full_account(**overrides):
    base = {
        "active": True,
        "username": "user",
        "password": "pass",
        "followup_template": "templates/followup.j2",
        "followup_template_type": "jinja",
    }
    base.update(overrides)
    return base


class TestListActiveAccounts:
    def test_returns_active_accounts(self):
        config = {
            "alice": {"active": True},
            "bob": {"active": False},
            "carol": {"active": True},
        }
        with patch("linkedin.conf._accounts_config", config):
            assert list_active_accounts() == ["alice", "carol"]

    def test_defaults_to_active(self):
        config = {"alice": {"username": "a"}}
        with patch("linkedin.conf._accounts_config", config):
            assert list_active_accounts() == ["alice"]

    def test_empty_config(self):
        with patch("linkedin.conf._accounts_config", {}):
            assert list_active_accounts() == []


class TestGetFirstActiveAccount:
    def test_returns_first(self):
        config = {
            "alice": {"active": True},
            "bob": {"active": True},
        }
        with patch("linkedin.conf._accounts_config", config):
            assert get_first_active_account() == "alice"

    def test_returns_none_when_empty(self):
        with patch("linkedin.conf._accounts_config", {}):
            assert get_first_active_account() is None


class TestGetAccountConfig:
    def test_valid_account_returns_config(self):
        config = {"alice": _full_account()}
        with patch("linkedin.conf._accounts_config", config):
            result = get_account_config("alice")
        assert result["handle"] == "alice"
        assert result["username"] == "user"
        assert result["followup_template_type"] == "jinja"

    def test_missing_account_raises_key_error(self):
        with patch("linkedin.conf._accounts_config", {}):
            with pytest.raises(KeyError):
                get_account_config("unknown")

    def test_missing_followup_template_raises(self):
        config = {"alice": _full_account(followup_template=None)}
        with patch("linkedin.conf._accounts_config", config):
            with pytest.raises(ValueError, match="followup_template"):
                get_account_config("alice")

    def test_missing_followup_template_type_raises(self):
        config = {"alice": _full_account(followup_template_type=None)}
        with patch("linkedin.conf._accounts_config", config):
            with pytest.raises(ValueError, match="followup_template_type"):
                get_account_config("alice")


class TestGetFirstAccountConfig:
    def test_returns_config_for_first_active(self):
        config = {"alice": _full_account()}
        with patch("linkedin.conf._accounts_config", config):
            result = get_first_account_config()
        assert result is not None
        assert result["handle"] == "alice"

    def test_returns_none_when_no_active(self):
        with patch("linkedin.conf._accounts_config", {}):
            assert get_first_account_config() is None
