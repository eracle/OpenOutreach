"""Tests for WhatsApp enrichment and formatting."""
import pytest
from django.test import Client

from openoutfind.crm.models import Deal, DealState, Lead
from openoutreach.whatsapp import (
    STATE_TO_DDD,
    deduce_lead_ddd,
    enrich_all_leads,
    enrich_lead_whatsapp,
    format_whatsapp,
    generate_deterministic_whatsapp,
    get_whatsapp_url,
    normalize_whatsapp_digits,
)


@pytest.fixture(autouse=True)
def web_settings(settings):
    from openoutreach.web import settings as web
    settings.ROOT_URLCONF = web.ROOT_URLCONF
    settings.MIDDLEWARE = web.MIDDLEWARE
    settings.TEMPLATES = web.TEMPLATES
    settings.ALLOWED_HOSTS = ["testserver", "localhost"]


def test_normalize_whatsapp_digits():
    assert normalize_whatsapp_digits("+55 (11) 98765-4321") == "5511987654321"
    assert normalize_whatsapp_digits("5511987654321") == "5511987654321"
    assert normalize_whatsapp_digits("11987654321") == "5511987654321"
    assert normalize_whatsapp_digits("(21) 99876-5432") == "5521998765432"
    assert normalize_whatsapp_digits("98765-4321", default_ddd="31") == "5531987654321"
    assert normalize_whatsapp_digits("") == ""


def test_format_whatsapp():
    assert format_whatsapp("11987654321") == "+55 (11) 98765-4321"
    assert format_whatsapp("+55 11 98765-4321") == "+55 (11) 98765-4321"
    assert format_whatsapp("21998765432") == "+55 (21) 99876-5432"
    assert format_whatsapp("987654321", default_ddd="41") == "+55 (41) 98765-4321"
    assert format_whatsapp("") == ""


def test_get_whatsapp_url():
    assert get_whatsapp_url("+55 (11) 98765-4321") == "https://wa.me/5511987654321"
    assert get_whatsapp_url("21998765432") == "https://wa.me/5521998765432"
    assert get_whatsapp_url("") == ""


def test_deduce_lead_ddd(db):
    lead_sp = Lead.objects.create(full_name="Lead SP", source_fields={"contact_location_state": "SP"})
    assert deduce_lead_ddd(lead_sp) == "11"

    lead_mg = Lead.objects.create(full_name="Lead MG", source_fields={"contact_location_state": "MG"})
    assert deduce_lead_ddd(lead_mg) == "31"

    lead_rj = Lead.objects.create(full_name="Lead RJ", source_fields={"state": "RJ"})
    assert deduce_lead_ddd(lead_rj) == "21"

    lead_pr = Lead.objects.create(full_name="Lead PR", source_fields={"location": "Curitiba, Paraná, PR"})
    assert deduce_lead_ddd(lead_pr) == "41"

    lead_def = Lead.objects.create(full_name="Lead Default")
    assert deduce_lead_ddd(lead_def) == "11"


def test_generate_deterministic_whatsapp(db):
    lead = Lead.objects.create(full_name="Diretor Logística", source_fields={"contact_location_state": "MG"})
    p1 = generate_deterministic_whatsapp(lead)
    p2 = generate_deterministic_whatsapp(lead)
    assert p1 == p2
    assert p1.startswith("+55 (31) 9")
    assert len(p1) == 19  # +55 (31) 9XXXX-XXXX


def test_enrich_lead_whatsapp(db):
    lead = Lead.objects.create(full_name="Ana Silva", source_fields={"contact_location_state": "RS"})
    phone = enrich_lead_whatsapp(lead, use_llm=False)
    lead.refresh_from_db()
    assert lead.source_fields.get("whatsapp") == phone
    assert phone.startswith("+55 (51) 9")

    # If lead already had a whatsapp, it preserves/formats it
    lead2 = Lead.objects.create(full_name="Bruno Castro", source_fields={"whatsapp": "11988887777"})
    phone2 = enrich_lead_whatsapp(lead2, use_llm=False)
    assert phone2 == "+55 (11) 98888-7777"


def test_enrich_all_leads(db):
    Lead.objects.create(full_name="Lead 1", source_fields={"contact_location_state": "SP"})
    Lead.objects.create(full_name="Lead 2", source_fields={"contact_location_state": "RJ"})
    res = enrich_all_leads(use_llm=False)
    assert res["total"] >= 2
    assert res["enriched"] >= 2
    for r in res["results"]:
        assert r["whatsapp"].startswith("+55 (")
        assert r["whatsapp_url"].startswith("https://wa.me/55")


def test_whatsapp_views_and_api(client, db):
    lead1 = Lead.objects.create(full_name="Carlos Viana", email="carlos@example.com", source_fields={"contact_location_state": "MG"})
    lead2 = Lead.objects.create(full_name="Luciana Dias", email="", source_fields={"contact_location_state": "SP"})
    Deal.objects.create(lead=lead1, state=DealState.QUALIFIED, reason="Fit perfeito")
    Deal.objects.create(lead=lead2, state=DealState.QUALIFIED, reason="Fit perfeito")

    # API enrich endpoint
    enrich_res = client.post("/api/enrich/whatsapp")
    assert enrich_res.status_code == 200
    assert enrich_res.json()["status"] == "ok"
    assert enrich_res.json()["enriched"] >= 2

    # Leads API returns whatsapp
    leads_res = client.get("/api/leads?status=whatsapp")
    assert leads_res.status_code == 200
    data = leads_res.json()
    assert data["total"] == 2
    assert data["rows"][0]["whatsapp"] != ""
    assert data["rows"][0]["whatsapp_url"].startswith("https://wa.me/55")

    # Dashboard API returns whatsapp stat
    dash_res = client.get("/api/dashboard")
    assert dash_res.status_code == 200
    assert dash_res.json()["stats"]["whatsapp"] == 2
