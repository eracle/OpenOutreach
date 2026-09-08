"""The UI's boundary: shared data, preserved secrets, and explicit local writes."""
import csv
import io

import pytest
from django.test import Client

from openoutfind.crm.models import Deal, DealState, Lead
from openoutreach.config.models import SiteConfig


@pytest.fixture(autouse=True)
def web_settings(settings):
    from openoutreach.web import settings as web
    settings.ROOT_URLCONF = web.ROOT_URLCONF
    settings.MIDDLEWARE = web.MIDDLEWARE
    settings.TEMPLATES = web.TEMPLATES
    settings.ALLOWED_HOSTS = ["testserver", "localhost"]


def test_dashboard_is_read_only_and_uses_existing_qualification_contract(client, db):
    good = Lead.objects.create(full_name="Maria Exemplo", email="maria@example.test")
    rejected = Lead.objects.create(full_name="Rejected")
    opted_out = Lead.objects.create(full_name="Opted out", disqualified=True)
    Lead.objects.create(synthetic=True)
    for lead, state in [(good, DealState.QUALIFIED), (rejected, DealState.FAILED), (opted_out, DealState.QUALIFIED)]:
        Deal.objects.create(lead=lead, state=state, reason="Qualification evidence")
    result = client.get("/api/dashboard").json()
    assert result["stats"]["discovered"] == 3
    assert result["stats"]["qualified"] == 1
    assert result["recent"][0]["name"] == "Maria Exemplo"
    assert result["recent"][0]["reason"] == "Qualification evidence"
    assert not SiteConfig.objects.exists()
    assert client.get("/api/leads", {"q":"MARIA", "status":"email"}).json()["total"] == 1
    assert client.get("/api/leads", {"status":"pending"}).json()["total"] == 0


def test_secrets_never_leave_server_and_blank_preserves_existing_secret(client, db):
    SiteConfig.objects.create(llm_api_key="test-secret-llm", mailbox_password="test-secret-mail")
    result = client.post("/api/config/integrations", {"ai_model":"provider:model", "llm_api_key":""})
    assert result.status_code == 200
    assert "test-secret" not in result.content.decode()
    assert "llm_api_key" not in result.json()  # only a configured boolean is exposed
    assert result.json()["configured"]["llm_api_key"] is True
    assert SiteConfig.load().llm_api_key == "test-secret-llm"
    assert SiteConfig.load().mailbox_password == "test-secret-mail"


def test_campaign_validation_and_persistence(client, db):
    assert client.post("/api/config/campaign", {"product_docs":"CRM"}).status_code == 400
    payload = {"product_docs":"CRM for teams", "campaign_target":"Small B2B companies", "booking_link":"javascript:alert(1)"}
    assert client.post("/api/config/campaign", payload).status_code == 400
    payload["booking_link"] = "https://example.test/booking"
    assert client.post("/api/config/campaign", payload).status_code == 200
    assert SiteConfig.load().campaign_target == "Small B2B companies"
    assert not SiteConfig.load().accepted_legal_notice


def test_csv_filters_rejections_and_neutralizes_formulas(client, db):
    good = Lead.objects.create(first_name="=1+1", full_name="Visible")
    bad = Lead.objects.create(full_name="Rejected")
    Deal.objects.create(lead=good, state=DealState.QUALIFIED, reason="Good fit")
    Deal.objects.create(lead=bad, state=DealState.FAILED, reason="Bad fit")
    response = client.get("/api/export")
    rows = list(csv.DictReader(io.StringIO(response.content.decode())))
    assert len(rows) == 1
    assert rows[0]["first_name"] == "'=1+1"
    assert rows[0]["reason"] == "Good fit"
    assert "attachment" in response["Content-Disposition"]


def test_csrf_and_local_only_are_enforced(db):
    client = Client(enforce_csrf_checks=True)
    assert client.post("/api/config/campaign", {}).status_code == 403
    assert client.get("/api/dashboard", REMOTE_ADDR="192.0.2.1").status_code == 403
    assert client.get("/api/dashboard", HTTP_HOST="untrusted.example").status_code == 400
    assert client.post("/api/job", {"count":"5"}).status_code == 403
    response = client.get("/")
    assert response.status_code == 200
    assert "csrftoken" in response.cookies
    assert "frame-ancestors 'none'" in response["Content-Security-Policy"]
    assert response["Cache-Control"] == "no-store"
    token = response.cookies["csrftoken"].value
    assert client.post("/api/config/campaign", {"product_docs":"CRM", "campaign_target":"Teams"}, HTTP_X_CSRFTOKEN=token).status_code == 200


def test_job_only_runs_explicit_bounded_find(client, mocker):
    from openoutreach.web import jobs
    mocker.patch.object(jobs, "_process", None)
    mocker.patch.object(jobs, "_state", {"status":"idle"})
    popen = mocker.patch.object(jobs.subprocess, "Popen")
    popen.return_value.poll.return_value = None
    mocker.patch.object(jobs.threading, "Thread")
    assert client.get("/api/job").json()["status"] == "idle"
    popen.assert_not_called()
    assert client.post("/api/job", {"count":"101"}).status_code == 400
    popen.assert_not_called()
    assert client.post("/api/job", {"count":"4"}).status_code == 202
    args = popen.call_args.args[0]
    assert args[-2:] == ["find", "4"]
    assert "emails" not in args and "send" not in args
    assert popen.call_args.kwargs["env"]["DJANGO_SETTINGS_MODULE"] == "openoutreach.settings"
    assert client.post("/api/job", {"count":"4"}).status_code == 409


def test_asset_allowlist(client):
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/settings.py").status_code == 404
