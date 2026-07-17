# tests/test_discovery_wiring.py
"""Discovery→qualify wiring: create_lead, the discover() leg, and the ICP generator.

Mocks the Lead Finder transport (`openoutreach.discovery.search`) and the embedder
so no network / ONNX model is touched.
"""
from unittest.mock import MagicMock, patch

import numpy as np

from openoutreach.core.db.leads import create_lead
from openoutreach.core.models import Clause, DiscoveryQuery, EmptyClauseSet
from openoutreach.core.pipeline.discover import discover
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
    """A first-touch lead of ``node`` the LLM rejected — leaves the node barren, so
    the walk keeps visiting rather than deepening it."""
    from openoutreach.crm.models import Deal, DealState, Lead, Outcome

    lead = Lead.objects.create(profile_url=f"https://x/{node.pk}-{tag}/", discovered_by=node)
    return Deal.objects.create(lead=lead, campaign=campaign, state=DealState.FAILED,
                               outcome=Outcome.WRONG_FIT)


def _qualified(campaign, node, tag):
    """A first-touch lead of ``node`` the LLM accepted — the walk's only evidence that
    a region is worth deepening, and the one thing that pre-empts the visit."""
    from openoutreach.crm.models import Deal, DealState, Lead

    lead = Lead.objects.create(profile_url=f"https://x/{node.pk}-{tag}/", discovered_by=node)
    return Deal.objects.create(lead=lead, campaign=campaign, state=DealState.QUALIFIED)


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

    def test_deepen_pages_a_line_that_qualified(self, db):
        """One qualified lead is the whole trigger — it pre-empts the visit outright."""
        _set_key()
        campaign = _campaign(country_code="us")
        _qualified(campaign, _node(campaign, SEED, offset=0), "a1")
        session = MagicMock(campaign=campaign)
        rows = [
            {"contact_linkedin_profile_url": "https://www.linkedin.com/in/a/"},
            {"contact_linkedin_profile_url": "https://www.linkedin.com/in/b/"},
        ]
        with patch("openoutreach.discovery.search", return_value=rows) as search, \
             patch("openoutreach.core.db.leads.create_lead", return_value=True) as create:
            assert discover(session) == 2

        assert search.call_args.kwargs["offset"] == 100
        node = DiscoveryQuery.objects.get(campaign=campaign, offset=100)
        assert node.clause_pairs == SEED and not node.exhausted
        assert create.call_count == 2
        assert create.call_args.kwargs == {"country_code": "us", "discovered_by": node}

    def test_a_barren_line_is_never_deepened(self, db):
        """Yield retires nothing, but it earns nothing either: a line whose leads were
        all rejected is left where it is and the visit carries on past it."""
        _set_key()
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for(SEED + OTHER))
        _rejected(campaign, _node(campaign, SEED, offset=0), "a1")
        session = MagicMock(campaign=campaign)
        rows = [{"contact_linkedin_profile_url": "https://www.linkedin.com/in/a/"}]
        with patch("openoutreach.discovery.search", return_value=rows) as search, \
             patch("openoutreach.core.db.leads.create_lead", return_value=True):
            assert discover(session) == 1

        assert search.call_args.kwargs["offset"] == 0, "a visit, not a deepen"
        assert not DiscoveryQuery.objects.filter(
            campaign=campaign, clause_key=clause_key(SEED), offset=100,
        ).exists()

    def test_a_dry_vein_exhausts_its_line_without_blacklisting_it(self, db):
        """offset > 0 means we have seen everyone, not that the query matches nobody —
        blacklisting here would convict a conjunction that already produced leads."""
        _set_key()
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for(SEED))
        _qualified(campaign, _node(campaign, SEED, offset=0), "a1")
        session = MagicMock(campaign=campaign)
        with patch("openoutreach.discovery.search", return_value=[]), \
             patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value=[]):
            assert discover(session) == 0

        assert DiscoveryQuery.objects.filter(
            campaign=campaign, clause_key=clause_key(SEED), exhausted=True,
        ).count() == 2
        assert not EmptyClauseSet.objects.exists()

    def test_an_empty_visit_blacklists_the_query_and_ends_the_move(self, db):
        _set_key()
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for(SEED))
        session = MagicMock(campaign=campaign)
        with patch("openoutreach.discovery.search", return_value=[]), \
             patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   side_effect=[OTHER, THIRD]):
            assert discover(session) == 0

        # The dead region is convicted for every campaign, and exactly one was opened:
        # without a probe each visit is a real fetch, so the move stops rather than
        # walking the lattice inside one call.
        assert EmptyClauseSet.objects.get().clause_key == clause_key(OTHER)
        assert DiscoveryQuery.objects.filter(campaign=campaign, clause_key=clause_key(OTHER)).exists()
        assert not DiscoveryQuery.objects.filter(campaign=campaign, clause_key=clause_key(THIRD)).exists()

    def test_cold_start_seeds_the_pool_then_visits_a_singleton(self, db):
        """The ICP's job is the pool, not the first query: the seed conjunction needs
        no special case because the visit reaches level 1 before any conjunction."""
        _set_key()
        campaign = _campaign()  # no nodes, no pool — cold start
        session = MagicMock(campaign=campaign)
        pool = [("lead_seniority", "vp"), ("lead_location", "Japan")]

        def _seed(c):
            c.clauses.set(Clause.rows_for(pool))
            return pool

        rows = [{"contact_linkedin_profile_url": "https://www.linkedin.com/in/c/"}]
        with patch("openoutreach.core.pipeline.icp.generate_seed", side_effect=_seed), \
             patch("openoutreach.discovery.search", return_value=rows), \
             patch("openoutreach.core.db.leads.create_lead", return_value=True):
            assert discover(session) == 1

        assert set(campaign.clauses.values_list("family", "value")) == set(pool)
        node = DiscoveryQuery.objects.get(campaign=campaign, offset=0)
        assert node.clause_pairs == [("lead_location", "Japan")], "level 1 leads"


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

    def test_the_pool_convicts_nothing_on_the_way_in(self, db):
        # The ICP proposes; only a fetch that matched nobody may retire anything. A
        # seed that pre-blacklisted its own clauses would prune the ICP unread.
        campaign = _campaign()
        with _icp_returns(ICPSpec(job_titles=["CMO"])), \
             patch("openoutreach.core.llm.get_llm_model"), patch("pydantic_ai.Agent"):
            generate_seed(campaign)

        assert campaign.clauses.exists()
        assert not EmptyClauseSet.objects.exists()

    def test_seed_carries_no_inert_family(self):
        # lead_industry rode every seed while doing nothing (probed 2026-07-16:
        # both a real value and an absurd control returned the unfiltered page).
        spec = ICPSpec(job_titles=["CMO"], seniorities=["owner"], locations=["Germany"])
        assert not any(f == "lead_industry" for f, _ in _seed_conjunction(spec))

    def test_omits_empty_families(self):
        assert dict(_seed_conjunction(ICPSpec())).keys() == {
            "company_headcount_min", "company_headcount_max",
        }
