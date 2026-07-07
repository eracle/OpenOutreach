# tests/emails/test_find_email.py
"""The two-leg paid email lookup: find_email (submit) → collect_email (poll).

Submit resolves free-hub-first, else fires a provider job and parks the deal at
FINDING_EMAIL with a bound collect task carrying the request_id. Collect polls
that job once and routes hit → READY_TO_EMAIL, miss → FAILED, still-running →
chained backoff (or revert past the deadline).
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from openoutreach.core.conf import COLLECT_DEADLINE_S
from openoutreach.core.models import Task
from openoutreach.crm.models import DealState
from openoutreach.emails.bettercontact import BetterContactUnavailable, PollOutcome
from openoutreach.core.scheduler import flush_find_email_queue
from openoutreach.emails.models import Mailbox
from openoutreach.emails.tasks.collect_email import handle_collect_email
from openoutreach.emails.tasks.find_email import handle_find_email
from tests.factories import DealFactory, LeadFactory

pytestmark = pytest.mark.django_db


def _box(daily_limit=10):
    return Mailbox.objects.create(
        username="a@b.com", password="pw", from_address="a@b.com", daily_limit=daily_limit,
    )


def _deal(campaign, state, email=None):
    return DealFactory(campaign=campaign, lead=LeadFactory(email=email), state=state)


def _collect_tasks(attempt=None):
    qs = Task.objects.filter(task_type=Task.TaskType.COLLECT_EMAIL)
    return qs.filter(payload__attempt=attempt) if attempt is not None else qs


def _email_tasks(campaign):
    return Task.objects.filter(task_type=Task.TaskType.EMAIL, payload__campaign_id=campaign.pk)


# ── Submit leg (handle_find_email) ────────────────────────────────────


class TestSubmitLeg:
    def _run(self, session, candidate_url, resolve=None, submit_ret="req1", submit_exc=None):
        cand = {"profile_url": candidate_url} if candidate_url else None
        submit = patch("openoutreach.emails.bettercontact.submit",
                       side_effect=submit_exc, return_value=submit_ret)
        with patch("openoutreach.emails.tasks.find_email._select_candidate", return_value=cand), \
                patch("openoutreach.contacts.service.resolve", return_value=resolve), \
                patch("openoutreach.emails.bettercontact.is_configured", return_value=True), \
                submit as submit_mock:
            task = Task.objects.create(
                task_type=Task.TaskType.FIND_EMAIL,
                scheduled_at=timezone.now(),
                payload={"campaign_id": session.campaign.pk},
            )
            handle_find_email(task, session, qualifiers={})
        return submit_mock

    def test_hub_hit_routes_to_ready_to_email_without_submit(self, fake_session):
        _box()
        deal = _deal(fake_session.campaign, DealState.READY_TO_FIND_EMAIL)
        submit = self._run(fake_session, deal.lead.profile_url, resolve="hub@acme.com")

        submit.assert_not_called()
        deal.refresh_from_db()
        assert deal.state == DealState.READY_TO_EMAIL
        assert deal.lead.email == "hub@acme.com"
        assert _email_tasks(fake_session.campaign).count() == 1  # opener queued
        assert not _collect_tasks().exists()

    def test_hub_miss_submits_and_parks_finding_email(self, fake_session):
        _box()
        deal = _deal(fake_session.campaign, DealState.READY_TO_FIND_EMAIL)
        self._run(fake_session, deal.lead.profile_url, resolve=None, submit_ret="req1")

        deal.refresh_from_db()
        assert deal.state == DealState.FINDING_EMAIL
        poll = _collect_tasks().get()
        assert poll.payload["request_id"] == "req1"
        assert poll.payload["deal_id"] == deal.pk
        assert poll.payload["provider"] == "bettercontact"
        assert poll.payload["attempt"] == 0

    def test_submit_unavailable_leaves_ready_to_find_email(self, fake_session):
        _box()
        deal = _deal(fake_session.campaign, DealState.READY_TO_FIND_EMAIL)
        self._run(fake_session, deal.lead.profile_url, resolve=None,
                  submit_exc=BetterContactUnavailable("no key"))

        deal.refresh_from_db()
        assert deal.state == DealState.READY_TO_FIND_EMAIL
        assert not _collect_tasks().exists()

    def test_no_mailbox_is_idle(self, fake_session):
        deal = _deal(fake_session.campaign, DealState.READY_TO_FIND_EMAIL)
        self._run(fake_session, deal.lead.profile_url, resolve="hub@acme.com")
        deal.refresh_from_db()
        assert deal.state == DealState.READY_TO_FIND_EMAIL

    def test_no_candidate_is_noop(self, fake_session):
        _box()
        self._run(fake_session, candidate_url=None)
        assert not _collect_tasks().exists()
        assert not _email_tasks(fake_session.campaign).exists()


# ── Collect leg (handle_collect_email) ────────────────────────────────


class TestCollectLeg:
    def _task(self, session, deal, attempt=0, age_s=0):
        submitted = timezone.now() - timedelta(seconds=age_s)
        return Task.objects.create(
            task_type=Task.TaskType.COLLECT_EMAIL,
            scheduled_at=timezone.now(),
            payload={
                "campaign_id": session.campaign.pk,
                "deal_id": deal.pk,
                "provider": "bettercontact",
                "request_id": "req1",
                "submitted_at": submitted.isoformat(),
                "attempt": attempt,
            },
        )

    def _run(self, session, task, outcome=None, exc=None):
        with patch("openoutreach.emails.bettercontact.poll_once",
                   side_effect=exc, return_value=outcome) as poll:
            handle_collect_email(task, session, qualifiers={})
        return poll

    def test_hit_resolves_and_routes_to_send(self, fake_session):
        _box()
        deal = _deal(fake_session.campaign, DealState.FINDING_EMAIL)
        task = self._task(fake_session, deal)
        with patch("openoutreach.contacts.service.contribute") as contribute:
            self._run(fake_session, task, outcome=PollOutcome(running=False, email="bob@acme.com"))

        deal.refresh_from_db()
        assert deal.state == DealState.READY_TO_EMAIL
        assert deal.lead.email == "bob@acme.com"
        contribute.assert_called_once()
        assert _email_tasks(fake_session.campaign).count() == 1

    def test_miss_fails_deal(self, fake_session):
        deal = _deal(fake_session.campaign, DealState.FINDING_EMAIL)
        task = self._task(fake_session, deal)
        self._run(fake_session, task, outcome=PollOutcome(running=False, email=""))

        deal.refresh_from_db()
        assert deal.state == DealState.FAILED
        assert deal.reason == "no email"

    def test_running_before_deadline_chains_next_poll(self, fake_session):
        deal = _deal(fake_session.campaign, DealState.FINDING_EMAIL)
        task = self._task(fake_session, deal, attempt=0, age_s=1)
        self._run(fake_session, task, outcome=PollOutcome(running=True))

        deal.refresh_from_db()
        assert deal.state == DealState.FINDING_EMAIL
        nxt = _collect_tasks(attempt=1).get()
        assert nxt.scheduled_at > timezone.now()  # backed off into the future

    def test_running_past_deadline_reverts(self, fake_session):
        deal = _deal(fake_session.campaign, DealState.FINDING_EMAIL)
        task = self._task(fake_session, deal, attempt=0, age_s=COLLECT_DEADLINE_S + 1)
        self._run(fake_session, task, outcome=PollOutcome(running=True))

        deal.refresh_from_db()
        assert deal.state == DealState.READY_TO_FIND_EMAIL
        assert not _collect_tasks(attempt=1).exists()

    def test_unavailable_retries_same_attempt(self, fake_session):
        deal = _deal(fake_session.campaign, DealState.FINDING_EMAIL)
        task = self._task(fake_session, deal, attempt=2, age_s=1)
        self._run(fake_session, task, exc=BetterContactUnavailable("down"))

        deal.refresh_from_db()
        assert deal.state == DealState.FINDING_EMAIL
        assert _collect_tasks(attempt=2).count() == 2  # original + retry, attempt not advanced

    def test_stale_deal_drops_poll(self, fake_session):
        deal = _deal(fake_session.campaign, DealState.READY_TO_EMAIL)  # no longer FINDING_EMAIL
        task = self._task(fake_session, deal)
        poll = self._run(fake_session, task, outcome=PollOutcome(running=True))

        poll.assert_not_called()
        assert _collect_tasks(attempt=1).count() == 0


# ── Submit drain (flush_find_email_queue) — spend rides on send headroom ──


class TestFindEmailDrain:
    def _find_email_tasks(self, campaign):
        return Task.objects.filter(
            task_type=Task.TaskType.FIND_EMAIL, payload__campaign_id=campaign.pk,
        )

    def _flush(self, session, configured=True):
        with patch("openoutreach.emails.bettercontact.is_configured", return_value=configured):
            return flush_find_email_queue(session, session.campaign)

    def test_no_op_without_mailbox(self, fake_session):
        assert self._flush(fake_session) == 0

    def test_no_op_when_finder_unconfigured(self, fake_session):
        _box()
        assert self._flush(fake_session, configured=False) == 0

    def test_mints_one_slot_with_send_headroom(self, fake_session):
        _box(daily_limit=10)  # 10 sends free today, pipeline empty
        assert self._flush(fake_session) == 1
        assert self._find_email_tasks(fake_session.campaign).count() == 1

    def test_no_op_when_pipeline_fills_headroom(self, fake_session):
        _box(daily_limit=2)
        _deal(fake_session.campaign, DealState.READY_TO_EMAIL)
        _deal(fake_session.campaign, DealState.FINDING_EMAIL)  # in_pipeline=2 == headroom
        assert self._flush(fake_session) == 0

    def test_finding_email_counts_toward_pipeline(self, fake_session):
        _box(daily_limit=1)
        _deal(fake_session.campaign, DealState.FINDING_EMAIL)  # already fills the 1 slot
        assert self._flush(fake_session) == 0

    def test_no_op_when_find_email_already_pending(self, fake_session):
        _box(daily_limit=10)
        Task.objects.create(
            task_type=Task.TaskType.FIND_EMAIL,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        assert self._flush(fake_session) == 0
        assert self._find_email_tasks(fake_session.campaign).count() == 1
