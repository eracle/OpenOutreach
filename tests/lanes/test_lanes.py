# tests/lanes/test_lanes.py
import pytest
from datetime import date, timedelta

from linkedin.db.crm_profiles import set_profile_state, save_scraped_profile
from linkedin.lanes.enrich import EnrichLane
from linkedin.lanes.connect import ConnectLane
from linkedin.lanes.check_pending import CheckPendingLane
from linkedin.lanes.follow_up import FollowUpLane
from linkedin.ml.scorer import ProfileScorer
from linkedin.navigation.enums import ProfileState
from linkedin.rate_limiter import RateLimiter


@pytest.mark.django_db
class TestEnrichLaneCanExecute:
    def test_can_execute_with_discovered(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.DISCOVERED.value)
        lane = EnrichLane(fake_session)
        assert lane.can_execute() is True

    def test_cannot_execute_empty(self, fake_session):
        lane = EnrichLane(fake_session)
        assert lane.can_execute() is False

    def test_cannot_execute_only_enriched(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.ENRICHED.value)
        lane = EnrichLane(fake_session)
        assert lane.can_execute() is False


@pytest.mark.django_db
class TestConnectLaneCanExecute:
    def test_can_execute_with_enriched_and_rate_ok(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.ENRICHED.value)
        rl = RateLimiter(daily_limit=10)
        scorer = ProfileScorer(seed=42)
        lane = ConnectLane(fake_session, rl, scorer)
        assert lane.can_execute() is True

    def test_cannot_execute_rate_limited(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.ENRICHED.value)
        rl = RateLimiter(daily_limit=0)
        scorer = ProfileScorer(seed=42)
        lane = ConnectLane(fake_session, rl, scorer)
        assert lane.can_execute() is False

    def test_cannot_execute_no_enriched(self, fake_session):
        rl = RateLimiter(daily_limit=10)
        scorer = ProfileScorer(seed=42)
        lane = ConnectLane(fake_session, rl, scorer)
        assert lane.can_execute() is False


@pytest.mark.django_db
class TestCheckPendingLaneCanExecute:
    def test_can_execute_with_old_pending(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.PENDING.value)
        from crm.models import Deal
        deal = Deal.objects.filter(owner=fake_session.django_user).first()
        deal.next_step_date = date.today() - timedelta(days=5)
        deal.save()

        scorer = ProfileScorer(seed=42)
        lane = CheckPendingLane(fake_session, min_age_days=3, scorer=scorer)
        assert lane.can_execute() is True

    def test_cannot_execute_too_recent(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.PENDING.value)
        # next_step_date is already date.today() from set_profile_state
        scorer = ProfileScorer(seed=42)
        lane = CheckPendingLane(fake_session, min_age_days=3, scorer=scorer)
        assert lane.can_execute() is False

    def test_cannot_execute_empty(self, fake_session):
        scorer = ProfileScorer(seed=42)
        lane = CheckPendingLane(fake_session, min_age_days=3, scorer=scorer)
        assert lane.can_execute() is False


@pytest.mark.django_db
class TestFollowUpLaneCanExecute:
    def test_can_execute_with_old_connected(self, fake_session):
        save_scraped_profile(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            {"first_name": "Alice", "last_name": "Smith", "headline": "Eng", "positions": []},
            None,
        )
        set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)
        from crm.models import Deal
        deal = Deal.objects.filter(owner=fake_session.django_user).first()
        deal.next_step_date = date.today() - timedelta(days=3)
        deal.save()

        rl = RateLimiter(daily_limit=10)
        lane = FollowUpLane(fake_session, rl, min_age_days=1)
        assert lane.can_execute() is True

    def test_cannot_execute_rate_limited(self, fake_session):
        save_scraped_profile(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            {"first_name": "Alice", "last_name": "Smith", "headline": "Eng", "positions": []},
            None,
        )
        set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)
        from crm.models import Deal
        deal = Deal.objects.filter(owner=fake_session.django_user).first()
        deal.next_step_date = date.today() - timedelta(days=3)
        deal.save()

        rl = RateLimiter(daily_limit=0)
        lane = FollowUpLane(fake_session, rl, min_age_days=1)
        assert lane.can_execute() is False

    def test_cannot_execute_empty(self, fake_session):
        rl = RateLimiter(daily_limit=10)
        lane = FollowUpLane(fake_session, rl, min_age_days=1)
        assert lane.can_execute() is False
