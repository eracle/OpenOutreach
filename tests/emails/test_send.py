# tests/emails/test_send.py
"""The Layer-1 email send path: Mailbox pacing, the eager flush planner,
the email pool query, and the EMAIL task handler."""
import pytest
from unittest.mock import patch

from django.utils import timezone

from openoutreach.core.agents.email_opener import EmailDraft
from openoutreach.core.db.deals import get_emailable_deals
from openoutreach.core.models import Task
from openoutreach.core.scheduler import flush_email_queue
from openoutreach.crm.models import DealState
from openoutreach.emails.models import Mailbox
from openoutreach.emails.sender import send_email
from openoutreach.emails.tasks.send import handle_email
from tests.factories import DealFactory, LeadFactory


def _box(email="a@b.com", daily_limit=10):
    return Mailbox.objects.create(
        username=email, password="pw", from_address=email, daily_limit=daily_limit,
    )


def _ready(campaign, email="lead@corp.com"):
    """A deal queued for its Layer-1 email (READY_TO_EMAIL, address resolved)."""
    return DealFactory(
        campaign=campaign,
        lead=LeadFactory(email=email),
        state=DealState.READY_TO_EMAIL,
    )


def _record_send(deal, box, user):
    """Register one outgoing email on a box — the cap ledger counts outgoing
    ChatMessages per box, not deals (the agentic loop sends many per deal)."""
    from openoutreach.chat.models import ChatMessage

    deal.mailbox = box
    deal.state = DealState.EMAILED
    deal.email_sent_at = timezone.now()
    deal.save()
    ChatMessage.objects.create(
        deal=deal, external_id=f"<m{deal.pk}@corp.com>", content="body",
        is_outgoing=True, owner=user, creation_date=timezone.now(),
    )


# ── Mailbox pacing ────────────────────────────────────────────────


@pytest.mark.django_db
class TestMailboxPacing:
    def test_sent_today_counts_outgoing_messages_for_this_box(self, fake_session):
        box = _box(daily_limit=10)
        d = _ready(fake_session.campaign)
        assert box.sent_today() == 0
        _record_send(d, box, fake_session.django_user)
        assert box.sent_today() == 1
        assert box.headroom_today() == 9

    def test_remaining_today_sums_headroom_across_boxes(self, fake_session):
        _box("a@b.com", daily_limit=3)
        _box("c@d.com", daily_limit=5)
        assert Mailbox.objects.remaining_today() == 8

    def test_remaining_today_zero_with_no_boxes(self):
        assert Mailbox.objects.remaining_today() == 0

    def test_least_loaded_picks_box_with_most_headroom(self, fake_session):
        light = _box("light@b.com", daily_limit=10)
        heavy = _box("heavy@b.com", daily_limit=10)
        # Spend 4 on heavy.
        for _ in range(4):
            _record_send(_ready(fake_session.campaign), heavy, fake_session.django_user)
        assert Mailbox.objects.least_loaded_under_cap() == light

    def test_least_loaded_returns_none_when_all_capped(self, fake_session):
        box = _box(daily_limit=1)
        _record_send(_ready(fake_session.campaign), box, fake_session.django_user)
        assert Mailbox.objects.least_loaded_under_cap() is None


# ── Email pool ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestEmailableDeals:
    def test_returns_only_ready_to_email(self, fake_session):
        ready = _ready(fake_session.campaign)
        DealFactory(campaign=fake_session.campaign, lead=LeadFactory(), state=DealState.QUALIFIED)
        DealFactory(campaign=fake_session.campaign, lead=LeadFactory(), state=DealState.EMAILED)
        deals = list(get_emailable_deals(fake_session))
        assert deals == [ready]

    def test_excludes_disqualified_lead(self, fake_session):
        deal = _ready(fake_session.campaign)
        deal.lead.disqualified = True
        deal.lead.save()
        assert list(get_emailable_deals(fake_session)) == []

    def test_oldest_first(self, fake_session):
        first = _ready(fake_session.campaign, "first@c.com")
        second = _ready(fake_session.campaign, "second@c.com")
        assert list(get_emailable_deals(fake_session)) == [first, second]


# ── flush_email_queue (the eager planner) ─────────────────────────


@pytest.mark.django_db
class TestFlushEmailQueue:
    def _pending_emails(self, campaign):
        return Task.objects.filter(
            task_type=Task.TaskType.EMAIL, payload__campaign_id=campaign.pk,
        ).count()

    def test_no_op_without_a_mailbox(self, fake_session):
        _ready(fake_session.campaign)
        assert flush_email_queue(fake_session, fake_session.campaign) == 0
        assert self._pending_emails(fake_session.campaign) == 0

    def test_no_op_on_empty_pool(self, fake_session):
        _box()
        assert flush_email_queue(fake_session, fake_session.campaign) == 0

    def test_creates_one_slot_per_queued_deal(self, fake_session):
        _box(daily_limit=10)
        _ready(fake_session.campaign, "x@c.com")
        _ready(fake_session.campaign, "y@c.com")
        assert flush_email_queue(fake_session, fake_session.campaign) == 2
        assert self._pending_emails(fake_session.campaign) == 2

    def test_capped_by_pool_headroom(self, fake_session):
        _box(daily_limit=1)
        _ready(fake_session.campaign, "x@c.com")
        _ready(fake_session.campaign, "y@c.com")
        assert flush_email_queue(fake_session, fake_session.campaign) == 1

    def test_no_op_when_email_task_already_pending(self, fake_session):
        _box(daily_limit=10)
        _ready(fake_session.campaign)
        Task.objects.create(
            task_type=Task.TaskType.EMAIL,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        assert flush_email_queue(fake_session, fake_session.campaign) == 0
        assert self._pending_emails(fake_session.campaign) == 1


# ── sender.send_email (SMTP assembly) ─────────────────────────────


class TestSendEmailBcc:
    def test_bcc_header_set_when_address_given(self):
        box = Mailbox(username="s@infra.com", password="pw", from_address="s@infra.com")
        with patch("openoutreach.emails.sender._deliver") as deliver:
            send_email(box, "lead@corp.com", "Hi", "Body", bcc="me@mine.com")
        message = deliver.call_args.args[1]
        assert message["Bcc"] == "me@mine.com"

    def test_no_bcc_header_when_address_blank(self):
        box = Mailbox(username="s@infra.com", password="pw", from_address="s@infra.com")
        with patch("openoutreach.emails.sender._deliver") as deliver:
            send_email(box, "lead@corp.com", "Hi", "Body", bcc="")
        message = deliver.call_args.args[1]
        assert message["Bcc"] is None


class TestSendEmailSignature:
    def _sent_body(self, signature: str | None) -> str:
        box = Mailbox(
            username="s@infra.com", password="pw", from_address="s@infra.com",
            signature=signature,
        )
        with patch("openoutreach.emails.sender._deliver") as deliver:
            send_email(box, "lead@corp.com", "Hi", "Body")
        return deliver.call_args.args[1].get_content()

    def test_signature_appended_after_blank_line(self):
        body = self._sent_body("Eracle\nhttps://www.linkedin.com/in/eracle")
        assert body == "Body\n\nEracle\nhttps://www.linkedin.com/in/eracle\n"

    def test_body_unchanged_when_signature_blank(self):
        assert self._sent_body("") == "Body\n"

    def test_body_unchanged_when_signature_unset(self):
        """A never-asked box (NULL) sends unsigned rather than crashing on None."""
        assert self._sent_body(None) == "Body\n"


# ── handle_email (the EMAIL task) ─────────────────────────────────


@pytest.mark.django_db
class TestHandleEmail:
    def _run(self, fake_session):
        task = Task.objects.create(
            task_type=Task.TaskType.EMAIL,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        with patch(
            "openoutreach.core.db.summaries.materialize_profile_summary_if_missing",
        ), patch(
            "openoutreach.core.agents.email_opener.compose_opener_email",
            return_value=EmailDraft(subject="Hi there", body="Short opener.", follow_up_hours=48),
        ), patch(
            "openoutreach.emails.sender.send_email", return_value="<mid@corp.com>",
        ) as send:
            handle_email(task, fake_session, qualifiers={})
        return send

    def test_sends_and_records_then_moves_to_emailed(self, fake_session):
        box = _box(daily_limit=10)
        deal = _ready(fake_session.campaign, "lead@corp.com")
        send = self._run(fake_session)

        send.assert_called_once_with(
            box, "lead@corp.com", "Hi there", "Short opener.",
            bcc="testuser@example.com",
        )
        deal.refresh_from_db()
        assert deal.state == DealState.EMAILED
        assert deal.mailbox == box
        assert deal.email_subject == "Hi there"
        assert deal.email_message_id == "<mid@corp.com>"
        assert deal.email_sent_at is not None

    def test_no_op_when_every_box_is_capped(self, fake_session):
        box = _box(daily_limit=1)
        spent = _ready(fake_session.campaign, "spent@corp.com")
        _record_send(spent, box, fake_session.django_user)
        queued = _ready(fake_session.campaign, "queued@corp.com")

        send = self._run(fake_session)
        send.assert_not_called()
        queued.refresh_from_db()
        assert queued.state == DealState.READY_TO_EMAIL
