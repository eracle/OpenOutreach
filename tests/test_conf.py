# tests/test_conf.py
import pytest
from unittest.mock import patch

from linkedin.conf import get_first_active_profile_handle


@pytest.mark.django_db
class TestGetFirstActiveProfileHandle:
    def test_returns_handle_when_profile_exists(self, fake_session):
        result = get_first_active_profile_handle()
        assert result == "testuser"

    def test_returns_none_when_no_profiles(self, db):
        from linkedin.models import LinkedInProfile
        LinkedInProfile.objects.all().delete()
        assert get_first_active_profile_handle() is None

    def test_returns_none_when_all_inactive(self, fake_session):
        from linkedin.models import LinkedInProfile
        LinkedInProfile.objects.all().update(active=False)
        assert get_first_active_profile_handle() is None
