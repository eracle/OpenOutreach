# tests/emails/test_nudge.py
"""The per-launch email nudge: state machine, copy, and mailbox import."""
from unittest.mock import patch

from openoutreach.core.models import Campaign, SiteConfig
from openoutreach.crm.models import DealState
from openoutreach.emails import nudge
from openoutreach.emails.models import Mailbox
from tests.factories import DealFactory, LeadFactory


def _set_finder_key(value: str = "k"):
    cfg = SiteConfig.load()
    cfg.finder_api_key = value
    cfg.save()


def _box(email="a@b.com"):
    return Mailbox.objects.create(username=email, password="p", from_address=email)


# ── State machine ────────────────────────────────────────────────

def test_state_is_no_finder_when_key_blank():
    _set_finder_key("")
    assert nudge.email_state() == nudge.NO_FINDER


def test_state_is_no_mailbox_when_finder_set_but_no_box():
    _set_finder_key()
    assert nudge.email_state() == nudge.NO_MAILBOX


def test_state_is_configured_with_a_box():
    _set_finder_key()
    _box()
    assert nudge.email_state() == nudge.CONFIGURED


# ── Copy ─────────────────────────────────────────────────────────

def test_render_no_finder_uses_numbers_and_finder_link():
    out = nudge.render(nudge.NO_FINDER, {
        "qualified": 42, "pending": 0, "resolved_emails": 0, "connect_cap": 20,
    })
    assert "42" in out and "20" in out and nudge.FINDER_AFFILIATE_URL in out


def test_render_no_mailbox_uses_numbers_and_sender_link():
    out = nudge.render(nudge.NO_MAILBOX, {
        "qualified": 0, "pending": 480, "resolved_emails": 312, "connect_cap": 20,
    })
    assert "480" in out and "312" in out and nudge.SENDER_AFFILIATE_URL in out


def test_pipeline_stats_counts_the_pipeline():
    campaign = Campaign.objects.create(name="stats-test")
    DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.QUALIFIED)
    DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.PENDING)
    DealFactory(campaign=campaign, lead=LeadFactory(api_email="x@y.com"), state=DealState.QUALIFIED)

    stats = nudge.pipeline_stats()
    assert stats["qualified"] == 2
    assert stats["pending"] == 1
    assert stats["resolved_emails"] == 1
    assert stats["connect_cap"] >= 1


# ── Mailbox import ───────────────────────────────────────────────

def test_import_stores_box_when_auth_succeeds():
    with patch("openoutreach.emails.nudge.verify_auth", return_value=(True, "ok")):
        report = nudge.import_mailboxes("Email\tPassword\na@b.com\tpw")
    assert (report.parsed, report.stored, report.failures) == (1, 1, [])
    box = Mailbox.objects.get(username="a@b.com")
    assert box.from_address == "a@b.com"


def test_import_skips_box_and_records_failure_on_auth_error():
    with patch("openoutreach.emails.nudge.verify_auth", return_value=(False, "auth rejected (534)")):
        report = nudge.import_mailboxes("Email\tPassword\na@b.com\tpw")
    assert report.stored == 0
    assert report.failures == [("a@b.com", "auth rejected (534)")]
    assert not Mailbox.objects.filter(username="a@b.com").exists()


def test_import_upserts_existing_mailbox_by_username():
    Mailbox.objects.create(username="a@b.com", password="old", from_address="a@b.com")
    with patch("openoutreach.emails.nudge.verify_auth", return_value=(True, "ok")):
        nudge.import_mailboxes("Email\tPassword\na@b.com\tnewpw")
    box = Mailbox.objects.get(username="a@b.com")
    assert box.password == "newpw"
    assert Mailbox.objects.filter(username="a@b.com").count() == 1
