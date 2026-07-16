# tests/test_discovery_wiring.py
"""Discovery→qualify wiring: create_lead, the discover() leg, and the ICP generator.

Mocks the Lead Finder transport (`openoutreach.discovery.search`) and the embedder
so no network / ONNX model is touched.
"""
from unittest.mock import MagicMock, patch

import numpy as np

from openoutreach.core.db.leads import create_lead
from openoutreach.core.models import DiscoveryQuery
from openoutreach.core.pipeline.discover import discover
from openoutreach.core.models import Clause
from openoutreach.core.pipeline.frontier import clause_key
from openoutreach.core.pipeline.icp import ICPSpec, _seed_conjunction, generate_seed


def _campaign(**kw):
    from openoutreach.core.models import Campaign

    defaults = dict(name="C", product_docs="we sell widgets", campaign_target="book demos")
    defaults.update(kw)
    return Campaign.objects.create(**defaults)


def _node(campaign, clauses, offset=0):
    node = DiscoveryQuery.objects.create(
        campaign=campaign, clause_key=clause_key(clauses), offset=offset,
    )
    node.clauses.set(Clause.rows_for(clauses))
    return node


SEED = [("lead_seniority", "owner")]
OTHER = [("lead_location", "Japan")]
THIRD = [("lead_location", "Germany")]


def _rejected(campaign, node, tag):
    """A first-touch lead of ``node`` the LLM rejected — makes the node rankable
    and barren, which is what drives the walk to a wall."""
    from openoutreach.crm.models import Deal, DealState, Lead, Outcome

    lead = Lead.objects.create(profile_url=f"https://x/{node.pk}-{tag}/", discovered_by=node)
    return Deal.objects.create(lead=lead, campaign=campaign, state=DealState.FAILED,
                               outcome=Outcome.WRONG_FIT)


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
        assert lead.profile_text == "cmo acme"
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
        first = _node(c, SEED)
        second = _node(c, OTHER)
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
        assert discover(session) == 0

    def test_skips_without_finder_key(self, db):
        session = MagicMock(campaign=_campaign())
        assert discover(session) == 0

    def test_skips_without_product_or_objective(self, db):
        _set_key()
        session = MagicMock(campaign=_campaign(product_docs="", campaign_target=""))
        assert discover(session) == 0

    def test_bootstrap_deepens_existing_seed_node(self, db):
        _set_key()
        campaign = _campaign(country_code="us")
        _node(campaign, SEED, offset=0)  # seed page already fetched
        session = MagicMock(campaign=campaign)
        rows = [
            {"contact_linkedin_profile_url": "https://www.linkedin.com/in/a/"},
            {"contact_linkedin_profile_url": "https://www.linkedin.com/in/b/"},
        ]
        with patch("openoutreach.discovery.search", return_value=rows) as search, \
             patch("openoutreach.core.db.leads.create_lead", return_value=True) as create:
            assert discover(session) == 2

        # bootstrap deepens the seed line to the next page
        assert search.call_args.kwargs["offset"] == 100
        node = DiscoveryQuery.objects.get(campaign=campaign, offset=100)
        assert node.clause_pairs == SEED and not node.exhausted
        assert create.call_count == 2
        assert create.call_args.kwargs == {"country_code": "us", "discovered_by": node}

    def test_empty_page_exhausts_line_and_returns_zero(self, db):
        _set_key()
        campaign = _campaign()
        _node(campaign, SEED, offset=0)  # seed line, one page already fetched
        session = MagicMock(campaign=campaign)
        with patch("openoutreach.discovery.search", return_value=[]):
            assert discover(session) == 0
        # the dry deepen is recorded and its line marked exhausted (never re-picked)
        assert DiscoveryQuery.objects.filter(
            campaign=campaign, clause_key=clause_key(SEED), exhausted=True,
        ).count() == 2

    def test_empty_wall_ends_move_without_looping(self, db):
        _set_key()
        # existing line, examined and barren → walls to a new region; that region is empty too
        campaign = _campaign()
        _rejected(campaign, _node(campaign, SEED, offset=0), "a1")
        session = MagicMock(campaign=campaign)
        with patch("openoutreach.discovery.search", return_value=[]), \
             patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   side_effect=[OTHER, THIRD]):
            assert discover(session) == 0
        # exactly one new region was opened (and exhausted); the second is never fetched
        assert DiscoveryQuery.objects.filter(campaign=campaign, clause_key=clause_key(OTHER)).exists()
        assert not DiscoveryQuery.objects.filter(campaign=campaign, clause_key=clause_key(THIRD)).exists()

    def test_cold_start_generates_seed_from_icp(self, db):
        _set_key()
        campaign = _campaign()  # no nodes yet — cold start
        session = MagicMock(campaign=campaign)
        seed = [("lead_seniority", "vp")]
        rows = [{"contact_linkedin_profile_url": "https://www.linkedin.com/in/c/"}]
        with patch("openoutreach.core.pipeline.icp.generate_seed", return_value=seed), \
             patch("openoutreach.discovery.search", return_value=rows), \
             patch("openoutreach.core.db.leads.create_lead", return_value=True):
            assert discover(session) == 1
        # the generated seed is embodied by its first fetched node, not cached
        node = DiscoveryQuery.objects.get(campaign=campaign, offset=0)
        assert node.clause_pairs == seed


# ── ICP generator ────────────────────────────────────────────────────


def _icp_returns(spec):
    """Patch the ICP's LLM call to yield ``spec``."""
    return patch("openoutreach.core.llm.run_agent_sync",
                 return_value=MagicMock(output=spec))


class TestICP:
    def test_folds_country_onto_the_campaign(self, db):
        # The country stamps every discovered Lead for the contacts geo-gate, and
        # the ICP is the only thing that knows it — so the fold lives with the call
        # that produced it, not in the frontier.
        campaign = _campaign()
        spec = ICPSpec(job_titles=["CMO"], country_code="GB")
        with _icp_returns(spec), patch("openoutreach.core.llm.get_llm_model"), \
             patch("pydantic_ai.Agent"):
            clauses = generate_seed(campaign)
        campaign.refresh_from_db()
        assert campaign.country_code == "gb"  # lowercased
        assert ("lead_job_title", "CMO") in clauses

    def test_composes_the_seed_from_the_spec(self):
        spec = ICPSpec(
            job_titles=["CMO"], seniorities=["owner"],
            locations=["United States"], headcount_min=1, headcount_max=50, country_code="us",
        )
        assert _seed_conjunction(spec) == [
            ("company_headcount_max", "50"),
            ("company_headcount_min", "1"),
            ("lead_job_title", "CMO"),
            ("lead_location", "United States"),
            ("lead_seniority", "owner"),
        ]

    def test_takes_one_value_per_family_not_an_or(self):
        # An include-list of 5 titles packs 5 sampling windows into 1. The seed takes
        # the model's top pick; the rest are not ORed in.
        spec = ICPSpec(job_titles=["CMO", "CTO", "Founder"], locations=["Germany", "Italy"])
        seed = dict(_seed_conjunction(spec))
        assert seed["lead_job_title"] == "CMO"
        assert seed["lead_location"] == "Germany"

    def test_the_pool_keeps_every_value_the_seed_could_not_carry(self, db):
        # The seed takes one title and the other two are what the descent composes
        # the *next* conjunctions from. They used to be dropped on the floor and
        # re-invented by an LLM call at every wall.
        campaign = _campaign()
        spec = ICPSpec(job_titles=["CMO", "CTO", "Founder"], locations=["Germany", "Italy"],
                       headcount_min=1, headcount_max=50)
        with _icp_returns(spec), patch("openoutreach.core.llm.get_llm_model"), \
             patch("pydantic_ai.Agent"):
            generate_seed(campaign)

        assert set(campaign.clauses.values_list("family", "value")) == {
            ("company_headcount_min", "1"), ("company_headcount_max", "50"),
            ("lead_job_title", "CMO"), ("lead_job_title", "CTO"),
            ("lead_job_title", "Founder"),
            ("lead_location", "Germany"), ("lead_location", "Italy"),
        }

    def test_the_pool_leaves_every_clause_unprobed(self, db):
        # NULL is "nobody has looked", which is what licenses the descent's sweep.
        # A pool that defaulted to live would skip the sweep and let `Europe` poison
        # query after query; one that defaulted to dead would prune the ICP unread.
        campaign = _campaign()
        with _icp_returns(ICPSpec(job_titles=["CMO"])), \
             patch("openoutreach.core.llm.get_llm_model"), patch("pydantic_ai.Agent"):
            generate_seed(campaign)

        assert campaign.clauses.exists()
        assert not campaign.clauses.exclude(is_live=None).exists()

    def test_seed_carries_no_inert_family(self):
        # lead_industry rode every seed while doing nothing (probed 2026-07-16:
        # both a real value and an absurd control returned the unfiltered page).
        spec = ICPSpec(job_titles=["CMO"], seniorities=["owner"], locations=["Germany"])
        assert not any(f == "lead_industry" for f, _ in _seed_conjunction(spec))

    def test_omits_empty_families(self):
        assert dict(_seed_conjunction(ICPSpec())).keys() == {
            "company_headcount_min", "company_headcount_max",
        }
