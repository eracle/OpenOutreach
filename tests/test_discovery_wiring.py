# tests/test_discovery_wiring.py
"""Discovery→qualify wiring: create_lead, the discover() leg, and the ICP generator.

Mocks the Lead Finder transport (`openoutreach.discovery.search`) and the embedder
so no network / ONNX model is touched.
"""
from unittest.mock import MagicMock, patch

import numpy as np

from openoutreach.core.db.leads import create_lead
from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.models import DiscoveryQuery
from openoutreach.core.pipeline.discover import discover
from openoutreach.core.pipeline.frontier import params_hash
from openoutreach.core.pipeline.icp import ICPSpec, _to_lead_finder_filters

Status = DiscoveryQuery.Status


def _campaign(**kw):
    from openoutreach.core.models import Campaign

    defaults = dict(name="C", product_docs="we sell widgets", campaign_target="book demos")
    defaults.update(kw)
    return Campaign.objects.create(**defaults)


def _explore_qualifier():
    """Pre-exploit qualifier (negatives don't outnumber positives)."""
    q = MagicMock(spec=BayesianQualifier)
    q.class_counts = (0, 0)
    return q


def _pending_node(campaign, params):
    return DiscoveryQuery.objects.create(
        campaign=campaign, params=params, params_hash=params_hash(params),
        offset=0, status=Status.PENDING,
    )


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

    def test_first_touch_discovered_by_only_on_create(self, db):
        from openoutreach.crm.models import Lead

        c = _campaign()
        first = _pending_node(c, {"a": 1})
        second = _pending_node(c, {"b": 1})
        row = {"contact_linkedin_profile_url": "https://www.linkedin.com/in/alice/"}
        with patch("openoutreach.discovery.embed_row", return_value=np.ones(384, dtype=np.float32)):
            assert create_lead(row, discovered_by=first) is True
            assert create_lead(row, discovered_by=second) is False  # re-surfaced, not re-created

        lead = Lead.objects.get(profile_url="https://www.linkedin.com/in/alice/")
        assert lead.discovered_by_id == first.pk  # keeps the query that FOUND it


# ── discover() ───────────────────────────────────────────────────────


class TestDiscover:
    def test_skips_freemium_campaign(self, db):
        _set_key()
        session = MagicMock(campaign=_campaign(is_freemium=True))
        assert discover(session, _explore_qualifier()) == 0

    def test_skips_without_finder_key(self, db):
        session = MagicMock(campaign=_campaign())
        assert discover(session, _explore_qualifier()) == 0

    def test_skips_without_product_or_objective(self, db):
        _set_key()
        session = MagicMock(campaign=_campaign(product_docs="", campaign_target=""))
        assert discover(session, _explore_qualifier()) == 0

    def test_fetches_picked_node_creates_first_touch_leads(self, db):
        _set_key()
        campaign = _campaign(country_code="us")
        node = _pending_node(campaign, {"lead_seniority": {"include": ["owner"]}})
        session = MagicMock(campaign=campaign)
        rows = [
            {"contact_linkedin_profile_url": "https://www.linkedin.com/in/a/"},
            {"contact_linkedin_profile_url": "https://www.linkedin.com/in/b/"},
        ]
        with patch("openoutreach.discovery.search", return_value=rows), \
             patch("openoutreach.core.db.leads.create_lead", return_value=True) as create, \
             patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value={}):
            assert discover(session, _explore_qualifier()) == 2

        assert create.call_count == 2
        assert create.call_args.kwargs == {"country_code": "us", "discovered_by": node}
        node.refresh_from_db()
        assert node.status == Status.FETCHED
        # LLM dry → depth is the fallback that keeps discovery moving (subsumes the old cursor)
        assert DiscoveryQuery.objects.filter(campaign=campaign, offset=100).exists()

    def test_dry_node_retired_and_returns_zero(self, db):
        _set_key()
        campaign = _campaign()
        node = _pending_node(campaign, {"x": 1})
        session = MagicMock(campaign=campaign)
        with patch("openoutreach.discovery.search", return_value=[]):
            assert discover(session, _explore_qualifier()) == 0
        node.refresh_from_db()
        assert node.status == Status.RETIRED

    def test_seeds_frontier_when_empty(self, db):
        _set_key()
        campaign = _campaign()
        session = MagicMock(campaign=campaign)
        spec = {"filters": {"lead_seniority": {"include": ["vp"]}}, "country_code": "gb"}
        rows = [{"contact_linkedin_profile_url": "https://www.linkedin.com/in/c/"}]
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec", return_value=spec), \
             patch("openoutreach.discovery.search", return_value=rows), \
             patch("openoutreach.core.db.leads.create_lead", return_value=True), \
             patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value={}):
            assert discover(session, _explore_qualifier()) == 1
        campaign.refresh_from_db()
        assert campaign.country_code == "gb"
        assert DiscoveryQuery.objects.filter(campaign=campaign).exists()


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
