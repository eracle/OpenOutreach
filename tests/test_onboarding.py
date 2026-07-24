# tests/test_onboarding.py
"""The onboarding step runner and its two crux steps (mailbox, account).

The regressions these lock down:

  * ``missing_keys`` reflects live DB state, so a satisfied step drops out and
    ``onboard_interactive`` never revisits it — no whole-wizard restart.
  * A mailbox whose SMTP auth is rejected re-asks *its own* fields with the
    previously typed values retained, stores nothing, and never rewinds to an
    earlier step. On the next (successful) attempt it stores exactly one box.
  * The operator account is created from the connected mailbox's address, and a
    declined Legal Notice aborts rather than looping.
"""
from unittest.mock import patch

import pytest

from openoutreach.core import onboarding


# ── Runner idempotency ───────────────────────────────────────────

@pytest.mark.django_db
def test_missing_keys_starts_with_every_step():
    assert onboarding.missing_keys() == {"campaign", "llm", "mailbox", "bettercontact", "account"}


@pytest.mark.django_db
def test_satisfied_step_drops_out_of_missing_keys():
    from openoutreach.core.models import Campaign

    Campaign.objects.create(name="C", product_docs="p", campaign_target="o")
    assert "campaign" not in onboarding.missing_keys()


@pytest.mark.django_db
def test_onboard_interactive_skips_done_steps():
    """Every step is already done → no step's run() is invoked."""
    with patch.object(onboarding, "STEPS", [
        onboarding.Step("a", lambda: True, _boom),
        onboarding.Step("b", lambda: True, _boom),
    ]):
        onboarding.onboard_interactive()  # _boom never fires


def _boom():
    raise AssertionError("run() called for an already-satisfied step")


# ── Mailbox step ─────────────────────────────────────────────────

@pytest.mark.django_db
def test_mailbox_retry_retains_values_and_stores_one_box():
    from openoutreach.emails.models import Mailbox

    texts = ["a@b.com", "pw1", "smtp.h", "imap.h",   # attempt 1 (auth rejected)
             "a@b.com", "pw2", "smtp.h", "imap.h"]   # attempt 2 (auth ok)
    with patch("openoutreach.core.onboarding.wiz.text", side_effect=texts) as text, \
         patch("openoutreach.core.onboarding.wiz.integer", side_effect=[587, 993, 587, 993]), \
         patch("openoutreach.core.onboarding.wiz.confirm", return_value=False), \
         patch("openoutreach.emails.smtp.verify_auth",
               side_effect=[(False, "auth rejected (535)"), (True, "ok")]):
        onboarding._run_mailbox()

    # Exactly one box, stored only on the successful attempt.
    assert Mailbox.objects.count() == 1
    assert Mailbox.objects.get().from_address == "a@b.com"
    # Never asked yet — the signature step owns that prompt.
    assert Mailbox.objects.get().signature is None
    # The retry re-seeded the host field with what was typed the first time.
    host_prompts = [c for c in text.call_args_list if c.args and c.args[0].startswith("SMTP host")]
    assert host_prompts[1].kwargs["default"] == "smtp.h"


@pytest.mark.django_db
def test_mailbox_cancel_with_existing_box_returns_cleanly():
    from openoutreach.emails.models import Mailbox

    Mailbox.objects.create(username="x@y.com", from_address="x@y.com", password="p")
    with patch("openoutreach.core.onboarding.wiz.text", return_value=None):
        onboarding._run_mailbox()  # returns, does not raise


@pytest.mark.django_db
def test_mailbox_cancel_without_box_aborts():
    with patch("openoutreach.core.onboarding.wiz.text", return_value=None):
        with pytest.raises(SystemExit):
            onboarding._run_mailbox()


# ── Signature step ───────────────────────────────────────────────

def _box(address: str, signature=None):
    from openoutreach.emails.models import Mailbox

    return Mailbox.objects.create(
        username=address, from_address=address, password="p", signature=signature,
    )


@pytest.mark.django_db
def test_signature_asked_once_per_never_asked_box():
    """Every NULL box is asked and persisted, including pre-signature boxes."""
    _box("a@b.com")
    _box("c@d.com")
    with patch("openoutreach.core.onboarding.wiz.multiline",
               side_effect=["Eracle", "Someone Else"]) as multiline:
        onboarding._run_signature()

    from openoutreach.emails.models import Mailbox
    assert Mailbox.objects.get(from_address="a@b.com").signature == "Eracle"
    assert Mailbox.objects.get(from_address="c@d.com").signature == "Someone Else"
    # Each box is named in its own prompt, so a two-box operator can tell them apart.
    assert "a@b.com" in multiline.call_args_list[0].args[0]
    assert onboarding._signature_done()


@pytest.mark.django_db
def test_declining_sticks_and_is_never_asked_again():
    """The regression: "" must not be re-asked, or declining costs a prompt a day."""
    _box("a@b.com")
    with patch("openoutreach.core.onboarding.wiz.multiline", return_value=""):
        onboarding._run_signature()

    from openoutreach.emails.models import Mailbox
    assert Mailbox.objects.get().signature == ""
    assert onboarding._signature_done()  # done, despite being blank

    with patch("openoutreach.core.onboarding.wiz.multiline",
               side_effect=AssertionError("re-asked a declined box")):
        onboarding._run_signature()


@pytest.mark.django_db
def test_signature_step_pending_only_while_a_box_is_unasked():
    assert onboarding._signature_done()  # no boxes at all
    _box("a@b.com")
    assert not onboarding._signature_done()
    _box("c@d.com", signature="Eracle")
    assert not onboarding._signature_done()  # the NULL box still pends


@pytest.mark.django_db
def test_signature_cancel_aborts_without_persisting():
    _box("a@b.com")
    with patch("openoutreach.core.onboarding.wiz.multiline", return_value=None):
        with pytest.raises(SystemExit):
            onboarding._run_signature()

    from openoutreach.emails.models import Mailbox
    assert Mailbox.objects.get().signature is None  # still unasked — retried next run


# ── Account step ─────────────────────────────────────────────────

@pytest.mark.django_db
def test_account_created_from_operator_email_not_mailbox():
    """The operator's own email (asked at onboarding) becomes User.email and the
    newsletter target — NOT the sending mailbox's from_address."""
    from django.contrib.auth.models import User

    from openoutreach.core.models import Campaign, SiteConfig
    from openoutreach.emails.models import Mailbox

    Campaign.objects.create(name="C", product_docs="p", campaign_target="o")
    Mailbox.objects.create(username="robot@acme.com", from_address="robot@acme.com", password="p")

    # wiz.text is asked twice now: operator email, then country.
    with patch("openoutreach.core.onboarding.wiz.text", side_effect=["diego.r@posteo.eu", "US"]), \
         patch("openoutreach.core.onboarding.wiz.confirm", side_effect=[True, True]), \
         patch("openoutreach.emails.newsletter.subscribe_to_newsletter") as sub:
        onboarding._run_account()

    user = User.objects.get(is_staff=True, is_active=True)
    # email is the human's inbox, not the mailbox; handle derives from its local-part.
    assert user.email == "diego.r@posteo.eu"
    assert user.username == "diego_r"
    assert SiteConfig.load().country_code == "us"
    # the newsletter subscribes the operator's inbox, not the sending mailbox.
    sub.assert_called_once_with("diego.r@posteo.eu")


@pytest.mark.django_db
def test_account_not_done_for_blank_email_user():
    """A staff user with a blank email (e.g. predating the address prompt) must NOT
    satisfy the account step — else the address prompt is skipped and BCC/newsletter
    have nowhere to go."""
    from django.contrib.auth.models import User

    User.objects.create(username="legacy", email="", is_staff=True, is_active=True)
    assert onboarding._account_done() is False

    User.objects.filter(username="legacy").update(email="me@posteo.eu")
    assert onboarding._account_done() is True


@pytest.mark.django_db
def test_account_shows_funding_notice_before_legal_gate():
    """The plain-language funding-behaviour notice (Legal Notice §4/§6) is shown
    during the account step, before the Legal Notice acceptance prompt."""
    from openoutreach.core.models import Campaign
    from openoutreach.emails.models import Mailbox

    Campaign.objects.create(name="C", product_docs="p", campaign_target="o")
    Mailbox.objects.create(username="robot@acme.com", from_address="robot@acme.com", password="p")

    with patch("openoutreach.core.onboarding.wiz.text", side_effect=["me@posteo.eu", "US"]), \
         patch("openoutreach.core.onboarding.wiz.confirm", side_effect=[True, True]), \
         patch("openoutreach.emails.newsletter.subscribe_to_newsletter"), \
         patch("openoutreach.core.onboarding._show_information_notice") as notice, \
         patch("openoutreach.core.onboarding._require_legal") as legal:
        onboarding._run_account()

    notice.assert_called_once()  # the funding/contacts notice is rendered…
    legal.assert_called_once()   # …and the acceptance gate still runs after it


def test_legal_notice_sections_are_read_verbatim():
    """§4/§6 are lifted verbatim from the authoritative LEGAL_NOTICE.md, and
    neighbouring sections (§5, §7) don't leak into the excerpt."""
    assert onboarding.LEGAL_NOTICE_PATH.exists()
    text = onboarding._legal_notice_sections(4, 6)

    assert text.startswith("### 4. How the Project Is Funded")
    assert "### 6. Central Contacts Store" in text
    # Verbatim, not paraphrased — exact phrases (with markdown) from the notice survive.
    assert "**Freemium promotional campaign.**" in text
    assert "No name, headline, company, title, phone, or profile text is sent." in text
    # Boundaries: the sections between/around §4 and §6 are excluded.
    assert "### 5." not in text
    assert "### 7." not in text


def test_legal_notice_sections_fall_back_to_url_when_missing(tmp_path, monkeypatch):
    """A missing notice file degrades to the canonical link, never a crash."""
    monkeypatch.setattr(onboarding, "LEGAL_NOTICE_PATH", tmp_path / "nope.md")
    assert onboarding.LEGAL_NOTICE_URL in onboarding._legal_notice_sections(4, 6)


@pytest.mark.django_db
def test_declined_legal_aborts_without_creating_account():
    from django.contrib.auth.models import User

    from openoutreach.core.models import Campaign
    from openoutreach.emails.models import Mailbox

    Campaign.objects.create(name="C", product_docs="p", campaign_target="o")
    Mailbox.objects.create(username="joe@acme.com", from_address="joe@acme.com", password="p")

    # newsletter yes, then legal declined, then cancel the legal re-ask.
    with patch("openoutreach.core.onboarding.wiz.text", return_value="US"), \
         patch("openoutreach.core.onboarding.wiz.confirm", side_effect=[True, False, None]):
        with pytest.raises(SystemExit):
            onboarding._run_account()

    assert not User.objects.filter(is_staff=True).exists()
