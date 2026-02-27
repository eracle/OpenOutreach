# tests/test_action_log.py
from datetime import timedelta

import pytest
from django.utils import timezone

from linkedin.models import ActionLog


@pytest.mark.django_db
class TestCanExecuteDaily:
    def test_within_daily_limit(self, fake_session):
        lp = fake_session.linkedin_profile
        assert lp.can_execute(ActionLog.ActionType.CONNECT)

    def test_at_daily_limit(self, fake_session):
        lp = fake_session.linkedin_profile
        lp.connect_daily_limit = 2
        lp.save(update_fields=["connect_daily_limit"])

        lp.record_action(ActionLog.ActionType.CONNECT, fake_session.campaign)
        lp.record_action(ActionLog.ActionType.CONNECT, fake_session.campaign)

        assert not lp.can_execute(ActionLog.ActionType.CONNECT)

    def test_above_daily_limit(self, fake_session):
        lp = fake_session.linkedin_profile
        lp.connect_daily_limit = 1
        lp.save(update_fields=["connect_daily_limit"])

        lp.record_action(ActionLog.ActionType.CONNECT, fake_session.campaign)
        lp.record_action(ActionLog.ActionType.CONNECT, fake_session.campaign)

        assert not lp.can_execute(ActionLog.ActionType.CONNECT)

    def test_zero_limit_blocks(self, fake_session):
        lp = fake_session.linkedin_profile
        lp.connect_daily_limit = 0
        lp.save(update_fields=["connect_daily_limit"])

        assert not lp.can_execute(ActionLog.ActionType.CONNECT)


@pytest.mark.django_db
class TestCanExecuteWeekly:
    def test_within_weekly_limit(self, fake_session):
        lp = fake_session.linkedin_profile
        lp.connect_weekly_limit = 5
        lp.save(update_fields=["connect_weekly_limit"])

        for _ in range(4):
            lp.record_action(ActionLog.ActionType.CONNECT, fake_session.campaign)
        assert lp.can_execute(ActionLog.ActionType.CONNECT)

    def test_at_weekly_limit(self, fake_session):
        lp = fake_session.linkedin_profile
        lp.connect_weekly_limit = 3
        lp.save(update_fields=["connect_weekly_limit"])

        for _ in range(3):
            lp.record_action(ActionLog.ActionType.CONNECT, fake_session.campaign)
        assert not lp.can_execute(ActionLog.ActionType.CONNECT)


@pytest.mark.django_db
class TestCanExecuteReset:
    def test_daily_reset_via_old_actions(self, fake_session):
        lp = fake_session.linkedin_profile
        lp.connect_daily_limit = 1
        lp.save(update_fields=["connect_daily_limit"])

        lp.record_action(ActionLog.ActionType.CONNECT, fake_session.campaign)
        assert not lp.can_execute(ActionLog.ActionType.CONNECT)

        # Backdate the action to yesterday
        ActionLog.objects.update(created_at=timezone.now() - timedelta(days=1))
        assert lp.can_execute(ActionLog.ActionType.CONNECT)

    def test_weekly_reset_via_old_actions(self, fake_session):
        lp = fake_session.linkedin_profile
        lp.connect_weekly_limit = 1
        lp.save(update_fields=["connect_weekly_limit"])

        lp.record_action(ActionLog.ActionType.CONNECT, fake_session.campaign)
        assert not lp.can_execute(ActionLog.ActionType.CONNECT)

        # Backdate the action to last week
        ActionLog.objects.update(created_at=timezone.now() - timedelta(days=8))
        assert lp.can_execute(ActionLog.ActionType.CONNECT)


@pytest.mark.django_db
class TestExhaustion:
    def test_mark_exhausted_blocks(self, fake_session):
        lp = fake_session.linkedin_profile
        assert lp.can_execute(ActionLog.ActionType.CONNECT)

        lp.mark_exhausted(ActionLog.ActionType.CONNECT)
        assert not lp.can_execute(ActionLog.ActionType.CONNECT)

    def test_exhaustion_resets_next_day(self, fake_session):
        from datetime import date, timedelta

        lp = fake_session.linkedin_profile
        lp.mark_exhausted(ActionLog.ActionType.CONNECT)
        assert not lp.can_execute(ActionLog.ActionType.CONNECT)

        # Simulate next day by backdating the exhaustion marker
        lp._exhausted[ActionLog.ActionType.CONNECT] = date.today() - timedelta(days=1)
        assert lp.can_execute(ActionLog.ActionType.CONNECT)


@pytest.mark.django_db
class TestFollowUpLimits:
    def test_follow_up_daily_limit(self, fake_session):
        lp = fake_session.linkedin_profile
        lp.follow_up_daily_limit = 2
        lp.save(update_fields=["follow_up_daily_limit"])

        lp.record_action(ActionLog.ActionType.FOLLOW_UP, fake_session.campaign)
        lp.record_action(ActionLog.ActionType.FOLLOW_UP, fake_session.campaign)
        assert not lp.can_execute(ActionLog.ActionType.FOLLOW_UP)

    def test_follow_up_no_weekly_limit(self, fake_session):
        """Follow-up has no weekly limit — only daily matters."""
        lp = fake_session.linkedin_profile
        lp.follow_up_daily_limit = 100
        lp.save(update_fields=["follow_up_daily_limit"])

        for _ in range(50):
            lp.record_action(ActionLog.ActionType.FOLLOW_UP, fake_session.campaign)
        assert lp.can_execute(ActionLog.ActionType.FOLLOW_UP)


@pytest.mark.django_db
class TestDynamicLimitChanges:
    def test_limit_change_takes_effect(self, fake_session):
        lp = fake_session.linkedin_profile
        lp.connect_daily_limit = 1
        lp.save(update_fields=["connect_daily_limit"])

        lp.record_action(ActionLog.ActionType.CONNECT, fake_session.campaign)
        assert not lp.can_execute(ActionLog.ActionType.CONNECT)

        # Raise the limit in DB
        from linkedin.models import LinkedInProfile
        LinkedInProfile.objects.filter(pk=lp.pk).update(connect_daily_limit=10)

        # can_execute calls refresh_from_db, so it should pick up the new limit
        assert lp.can_execute(ActionLog.ActionType.CONNECT)


@pytest.mark.django_db
class TestRecordAction:
    def test_creates_action_log(self, fake_session):
        lp = fake_session.linkedin_profile
        lp.record_action(ActionLog.ActionType.CONNECT, fake_session.campaign)

        assert ActionLog.objects.count() == 1
        log = ActionLog.objects.first()
        assert log.linkedin_profile == lp
        assert log.campaign == fake_session.campaign
        assert log.action_type == ActionLog.ActionType.CONNECT
