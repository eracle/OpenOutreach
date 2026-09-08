"""Tests for Resend SMTP adapter, Free Email Finder, Free Discovery, and Web integrations."""
from unittest.mock import MagicMock
import pytest

from openoutreach.adapters import (
    FreeEmailFinder,
    free_discovery_search,
    is_resend_config,
)
from openoutreach.config.models import SiteConfig
from openoutreach.web.forms import IntegrationForm
from openoutreach.web.views import public_config


def test_is_resend_config():
    assert is_resend_config("smtp.resend.com", "any_password") is True
    assert is_resend_config("SMTP.RESEND.COM", "") is True
    assert is_resend_config("smtp.gmail.com", "re_123456789") is True
    assert is_resend_config("smtp.gmail.com", "app_password") is False
    assert is_resend_config("", "") is False


def test_free_email_finder_properties(db):
    assert FreeEmailFinder.NAME == "free"
    assert FreeEmailFinder.is_configured() is True
    assert FreeEmailFinder.credit_balance() == 999999


def test_free_email_finder_resolves_corporate_patterns(db):
    from openoutfind.crm.models import Company, Lead

    company = Company.objects.create(name="Constrular Materiais", domain="constrular.com.br")
    lead = Lead.objects.create(
        full_name="Ricardo Oliveira",
        first_name="Ricardo",
        last_name="Oliveira",
        company=company,
        profile_url="https://www.linkedin.com/in/ricardo-oliveira-test",
    )

    lookup = FreeEmailFinder.start(lead.profile_url)
    assert lookup.outcome.running is False
    assert lookup.outcome.email == "ricardo.oliveira@constrular.com.br"
    assert lookup.outcome.first_name == "Ricardo"
    assert lookup.outcome.last_name == "Oliveira"


def test_free_discovery_search_returns_valid_page(db):
    site_config = SiteConfig.load()
    site_config.product_docs = "Software de gestão logística para distribuidoras"
    site_config.campaign_target = "Diretores de logística de distribuidoras no Brasil"
    site_config.save()

    page = free_discovery_search({}, limit=3)
    assert len(page.leads) >= 3
    for lead in page.leads:
        assert "contact_full_name" in lead
        assert "contact_job_title" in lead
        assert "company_name" in lead
        assert "company_domain" in lead
        assert lead["contact_linkedin_profile_url"].startswith("https://www.linkedin.com/in/")


def test_site_config_export_with_resend_and_model_prefix(db):
    config = SiteConfig.load()
    config.ai_model = "cbai/minimax-m3"
    config.llm_api_base = "http://localhost:20128/v1"
    config.mailbox_password = "re_test_key_123"
    config.save()

    env = config.export()
    assert env["OPENOUTFIND_AI_MODEL"] == "openai_compatible:cbai/minimax-m3"
    assert env["OUTSEND_AI_MODEL"] == "openai_compatible:cbai/minimax-m3"
    assert env["OUTSEND_SMTP_HOST"] == "smtp.resend.com"
    assert env["OUTSEND_SMTP_PORT"] == "587"


def test_integration_form_normalizes_ai_model_and_resend(db):
    form_data = {
        "ai_model": "cbai/minimax-m3",
        "llm_api_base": "http://localhost:20128/v1",
        "mailbox_password": "re_secret_resend_key",
        "mailbox_address": "contato@empresa.com.br",
        "operator_country_code": "BR",
    }
    form = IntegrationForm(data=form_data)
    assert form.is_valid(), form.errors
    cleaned = form.cleaned_data
    assert cleaned["ai_model"] == "openai_compatible:cbai/minimax-m3"
    assert cleaned["smtp_host"] == "smtp.resend.com"
    assert cleaned["smtp_port"] == "587"


def test_public_config_reports_free_provider_and_resend(db):
    config = SiteConfig.load()
    config.bettercontact_api_key = ""
    config.mailbox_password = "re_test_resend_key"
    config.smtp_host = "smtp.resend.com"
    config.save()

    pub = public_config(config)
    assert pub["free_provider_active"] is True
    assert pub["is_resend"] is True

    # If BetterContact key is added
    config.bettercontact_api_key = "bc_real_key"
    config.save()
    pub2 = public_config(config)
    assert pub2["free_provider_active"] is False


def test_job_wait_captures_stderr():
    from openoutreach.web import jobs

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "Error: Missing configuration for AI model")
    mock_proc.returncode = 1

    jobs._process = mock_proc
    jobs._state = {"status": "running"}

    jobs._wait(mock_proc)

    assert jobs._state["status"] == "failed"
    assert jobs._state["exit_code"] == 1
    assert "Missing configuration" in jobs._state["error"]


def test_resend_ports_and_idempotency_key(mocker):
    from email.message import EmailMessage
    from cold_outreach.emails.sender import _deliver
    from openoutreach.adapters import (
        RESEND_HOST,
        RESEND_SMTPS_PORTS,
        RESEND_STARTTLS_PORTS,
        RESEND_PORTS,
    )

    assert RESEND_HOST == "smtp.resend.com"
    assert RESEND_SMTPS_PORTS == {465, 2465}
    assert RESEND_STARTTLS_PORTS == {25, 587, 2587}
    assert RESEND_PORTS == {25, 465, 587, 2465, 2587}

    # Verify deliver_hook injects Resend-Idempotency-Key and X-Entity-Ref-ID
    mock_mailbox = MagicMock()
    mock_mailbox.host = "smtp.resend.com"
    mock_mailbox.port = 587
    mock_mailbox.password = "re_test_key_123"

    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "target@example.com"
    msg["Subject"] = "Hello"
    msg["Message-ID"] = "<test-msg-id-123@example.com>"

    mock_row = MagicMock()
    mock_row.pk = 42
    mock_row.message_id = "<test-msg-id-123@example.com>"

    mock_smtp_inst = MagicMock()
    mock_smtp_inst.__enter__.return_value = mock_smtp_inst
    mock_smtp_inst.accepted_response = (250, b"2.0.0 OK queue-abc")
    mocker.patch("openoutreach.adapters._ResendSMTP", return_value=mock_smtp_inst)
    mocker.patch("cold_outreach.emails.delivery_policy.record_acceptance")

    _deliver(mock_mailbox, msg, mock_row)

    assert "Resend-Idempotency-Key" in msg
    assert msg["Resend-Idempotency-Key"] == "openoutreach-42-test-msg-id-123-example-com"
    assert msg["X-Entity-Ref-ID"] == msg["Resend-Idempotency-Key"]
    mock_smtp_inst.login.assert_called_once_with("resend", "re_test_key_123")
    mock_smtp_inst.send_message.assert_called_once_with(msg)
