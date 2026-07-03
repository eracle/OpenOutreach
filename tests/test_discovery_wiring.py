# tests/test_discovery_wiring.py
"""Discovery→qualify wiring: create_lead, the discover() leg, and the ICP generator.

Mocks the Lead Finder transport (`openoutreach.discovery.search`) and the embedder
so no network / ONNX model is touched.
"""
from unittest.mock import MagicMock, patch

import numpy as np

from openoutreach.core.db.leads import create_lead
from openoutreach.core.pipeline.discover import discover
from openoutreach.core.pipeline.icp import ICPSpec, _to_lead_finder_filters, icp_for


def _campaign(**kw):
    from openoutreach.core.models import Campaign

    defaults = dict(name="C", product_docs="we sell widgets", campaign_objective="book demos")
    defaults.update(kw)
    return Campaign.objects.create(**defaults)


def _set_key(value="k"):
    from openoutreach.core.models import SiteConfig

    cfg = SiteConfig.load()
    cfg.bettercontact_api_key = value
    cfg.save()


# ── create_lead ──────────────────────────────────────────────────────


class TestCreateLead:
    def test_persists_embedded_lead_with_text_and_country(self, db):
        from openoutreach.crm.models import Lead

        row = {
            "contact_linkedin_profile_url": "https://www.linkedin.com/in/alice/",
            "contact_headline": "CMO", "company_name": "Acme",
        }
        with patch("openoutreach.discovery.embed_row", return_value=np.ones(384, dtype=np.float32)):
            assert create_lead(row, country_code="us") is True

        lead = Lead.objects.get(profile_url="https://www.linkedin.com/in/alice/")
        assert lead.country_code == "us"
        assert lead.profile_text == "cmo    acme "
        assert lead.embedding_array is not None

    def test_idempotent_on_duplicate(self, db):
        row = {"contact_linkedin_profile_url": "https://www.linkedin.com/in/alice/"}
        with patch("openoutreach.discovery.embed_row", return_value=np.ones(384, dtype=np.float32)):
            assert create_lead(row) is True
            assert create_lead(row) is False

    def test_missing_profile_url_returns_false(self, db):
        assert create_lead({"contact_headline": "no url"}) is False


# ── discover() ───────────────────────────────────────────────────────


class TestDiscover:
    def test_skips_freemium_campaign(self, db):
        _set_key()
        session = MagicMock(campaign=_campaign(is_freemium=True))
        assert discover(session) == 0

    def test_skips_without_finder_key(self, db):
        session = MagicMock(campaign=_campaign())
        assert discover(session) == 0

    def test_skips_without_product_or_objective(self, db):
        _set_key()
        session = MagicMock(campaign=_campaign(product_docs="", campaign_objective=""))
        assert discover(session) == 0

    def test_pages_creates_and_advances_offset(self, db):
        _set_key()
        campaign = _campaign(icp_filters={"filters": {"lead_seniority": {"include": ["owner"]}}, "country_code": "us"})
        session = MagicMock(campaign=campaign)
        rows = [
            {"contact_linkedin_profile_url": "https://www.linkedin.com/in/a/"},
            {"contact_linkedin_profile_url": "https://www.linkedin.com/in/b/"},
        ]
        with patch("openoutreach.discovery.search", return_value=rows), \
             patch("openoutreach.core.db.leads.create_lead", return_value=True) as create:
            assert discover(session) == 2

        assert create.call_count == 2
        assert create.call_args.kwargs == {"country_code": "us"}
        campaign.refresh_from_db()
        assert campaign.discovery_offset == 2

    def test_dry_page_returns_zero_and_holds_offset(self, db):
        _set_key()
        campaign = _campaign(icp_filters={"filters": {"x": 1}, "country_code": ""})
        session = MagicMock(campaign=campaign)
        with patch("openoutreach.discovery.search", return_value=[]):
            assert discover(session) == 0
        campaign.refresh_from_db()
        assert campaign.discovery_offset == 0


# ── ICP generator ────────────────────────────────────────────────────


class TestICP:
    def test_maps_spec_onto_lead_finder_filters(self):
        spec = ICPSpec(
            job_titles=["CMO"], seniorities=["owner"], industries=["SaaS"],
            locations=["United States"], headcount_min=1, headcount_max=50, country_code="us",
        )
        f = _to_lead_finder_filters(spec)
        assert f["company_headcount_min"] == 1 and f["company_headcount_max"] == 50
        assert f["lead_job_title"] == {"include": ["CMO"], "exact_match": False}
        assert f["lead_seniority"] == {"include": ["owner"]}
        assert f["lead_industry"] == {"include": ["SaaS"]}
        assert f["lead_location"] == {"include": ["United States"]}

    def test_omits_empty_lists(self):
        f = _to_lead_finder_filters(ICPSpec())
        assert set(f) == {"company_headcount_min", "company_headcount_max"}

    def test_icp_for_returns_cache_without_generating(self, db):
        campaign = _campaign(icp_filters={"filters": {"x": 1}, "country_code": "us"})
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec",
                   side_effect=AssertionError("must not regenerate a cached ICP")):
            assert icp_for(campaign) == {"filters": {"x": 1}, "country_code": "us"}

    def test_icp_for_generates_and_persists_once(self, db):
        campaign = _campaign()
        spec = {"filters": {"y": 2}, "country_code": "gb"}
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec", return_value=spec):
            assert icp_for(campaign) == spec
        campaign.refresh_from_db()
        assert campaign.icp_filters == spec
