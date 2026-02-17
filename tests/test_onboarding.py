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
            patch.object(onboarding, "_ensure_llm_api_key"),
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
            patch.object(onboarding, "_ensure_llm_api_key"),
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
        campaign = Campaign.objects.filter(department=dept).first()

        with (
            patch.object(onboarding, "_onboard_campaign") as mock_campaign,
            patch.object(onboarding, "_onboard_account") as mock_account,
            patch.object(onboarding, "_ensure_llm_api_key"),
        ):
            ensure_onboarding()
            mock_campaign.assert_not_called()
            mock_account.assert_called_once_with(campaign)


@pytest.mark.django_db
class TestEnsureLlmApiKey:
    def test_noop_when_key_set(self):
        """If LLM_API_KEY is already set → does nothing."""
        with patch("linkedin.conf.LLM_API_KEY", "sk-test"):
            onboarding._ensure_llm_api_key()
            # Should not prompt for input

    def test_prompts_and_writes_when_missing(self, tmp_path):
        """If LLM_API_KEY is missing → prompts and writes to .env."""
        env_file = tmp_path / ".env"

        with (
            patch("linkedin.conf.LLM_API_KEY", None),
            patch("linkedin.onboarding.ENV_FILE", env_file),
            patch("builtins.input", return_value="sk-new-key"),
        ):
            onboarding._ensure_llm_api_key()

        content = env_file.read_text()
        assert "LLM_API_KEY=sk-new-key" in content
