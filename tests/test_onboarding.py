# tests/test_onboarding.py
"""Tests for the DB-backed onboarding module."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from linkedin import onboarding
from linkedin.onboarding import ensure_onboarding


@pytest.mark.django_db
class TestEnsureOnboardingAlreadyExist:
    def test_noop_when_campaign_and_profile_exist(self, fake_session):
        """If Campaign and active LinkedInProfile exist → does nothing."""
        with (
            patch.object(onboarding, "_onboard_campaign") as mock_campaign,
            patch.object(onboarding, "_onboard_account") as mock_account,
            patch.object(onboarding, "_ensure_llm_config"),
        ):
            ensure_onboarding()
            mock_campaign.assert_not_called()
            mock_account.assert_not_called()

    def test_runs_campaign_onboarding_when_no_campaign(self, db):
        """If no Campaign exists → runs _onboard_campaign."""
        from linkedin.models import Campaign
        Campaign.objects.all().delete()

        mock_campaign_obj = MagicMock()
        with (
            patch.object(onboarding, "_onboard_campaign", return_value=mock_campaign_obj) as mock_campaign,
            patch.object(onboarding, "_onboard_account") as mock_account,
            patch.object(onboarding, "_ensure_llm_config"),
        ):
            ensure_onboarding()
            mock_campaign.assert_called_once()
            mock_account.assert_called_once_with(mock_campaign_obj)

    def test_runs_account_onboarding_when_no_profile(self, db):
        """If Campaign exists but no active profile → runs _onboard_account."""
        from linkedin.models import Campaign, LinkedInProfile
        from common.models import Department

        LinkedInProfile.objects.all().delete()
        dept = Department.objects.get(name="LinkedIn Outreach")
        campaign, _ = Campaign.objects.get_or_create(department=dept)

        with (
            patch.object(onboarding, "_onboard_campaign") as mock_campaign,
            patch.object(onboarding, "_onboard_account") as mock_account,
            patch.object(onboarding, "_ensure_llm_config"),
        ):
            ensure_onboarding()
            mock_campaign.assert_not_called()
            mock_account.assert_called_once_with(campaign)


@pytest.mark.django_db
class TestEnsureLlmConfig:
    def test_noop_when_all_set(self):
        """If all LLM vars are already set → does nothing."""
        with (
            patch("linkedin.conf.LLM_API_KEY", "sk-test"),
            patch("linkedin.conf.AI_MODEL", "gpt-4o"),
            patch("linkedin.conf.LLM_API_BASE", "https://api.example.com"),
        ):
            onboarding._ensure_llm_config()
            # Should not prompt for input

    def test_prompts_and_writes_when_missing(self, tmp_path):
        """If LLM vars are missing → prompts and writes to .env."""
        env_file = tmp_path / ".env"
        inputs = iter(["sk-new-key", "gpt-4o", ""])

        with (
            patch("linkedin.conf.LLM_API_KEY", None),
            patch("linkedin.conf.AI_MODEL", None),
            patch("linkedin.conf.LLM_API_BASE", None),
            patch("linkedin.onboarding.ENV_FILE", env_file),
            patch("builtins.input", lambda _: next(inputs)),
        ):
            onboarding._ensure_llm_config()

        content = env_file.read_text()
        assert "LLM_API_KEY=sk-new-key" in content
        assert "AI_MODEL=gpt-4o" in content
        assert "LLM_API_BASE" not in content
