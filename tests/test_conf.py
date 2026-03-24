# tests/test_conf.py
import pytest

from linkedin.browser.registry import get_first_active_profile


@pytest.mark.django_db
class TestGetFirstActiveProfile:
    def test_returns_profile_when_exists(self, fake_session):
        result = get_first_active_profile()
        assert result is not None
        assert result.user.username == "testuser"

    def test_returns_none_when_no_profiles(self, db):
        from linkedin.models import LinkedInProfile
        LinkedInProfile.objects.all().delete()
        assert get_first_active_profile() is None

    def test_returns_none_when_all_inactive(self, fake_session):
        from linkedin.models import LinkedInProfile
        LinkedInProfile.objects.all().update(active=False)
        assert get_first_active_profile() is None
