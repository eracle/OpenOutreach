# tests/test_discovery_wiring.py
"""Discovery→qualify wiring: create_lead, the discover() leg, and the ICP generator.

Mocks the Lead Finder transport (`openoutreach.discovery.search`), the embedder and
the GP so no network / ONNX model is touched. The GP is the query selector now, so
``discover`` takes a qualifier; a qualifier whose ``acquisition_scores`` returns
``None`` is an unfitted (cold-start) model, which makes selection deterministic:
seed-first, fresh-before-deep.
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from pydantic import ValidationError

from openoutreach.core.db.leads import create_lead
from openoutreach.core.models import Clause, DiscoveryQuery, EmptyClauseSet
from openoutreach.core.pipeline.discover import discover
from openoutreach.core.pipeline.icp import ICPSpec, _seed_conjunction, generate_seed
from openoutreach.core.pipeline.select import clause_key


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


def _cold_qualifier():
    """An unfitted GP — ``acquisition_mode`` returns None, so selection is the
    deterministic seed-first, fresh-first fallback (no exact-embed, no scoring)."""
    q = MagicMock()
    q.acquisition_mode.return_value = None
    q.acquisition_scores.return_value = None
    return q


SEED = [("lead_seniority", "owner")]
OTHER = [("lead_location", "Japan")]


def _qualified(campaign, node, tag):
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
        with patch("openoutreach.discovery.embed_profile", return_value=np.ones(384, dtype=np.float32)):
            assert create_lead(row, country_code="us") is True

        lead = Lead.objects.get(profile_url="https://www.linkedin.com/in/alice/")
        assert lead.country_code == "us"
        assert lead.profile_text == "cmo acme"
        assert lead.embedding_array is not None

    def test_query_terms_go_into_the_embedding_not_the_profile_text(self, db):
        # The keyword injection: the retrieving query's terms shape the embedding (so
        # the GP learns query→fit) but never the profile_text the LLM reads.
        from openoutreach.crm.models import Lead

        row = {
            "contact_linkedin_profile_url": "https://www.linkedin.com/in/alice/",
            "contact_headline": "CMO",
        }
        with patch("openoutreach.discovery.embed_profile",
                   return_value=np.ones(384, dtype=np.float32)) as embed:
            create_lead(row, query_terms="seniority owner")

        embed.assert_called_once_with("cmo", "seniority owner")
        assert Lead.objects.get(profile_url="https://www.linkedin.com/in/alice/").profile_text == "cmo"

    def test_missing_profile_url_returns_false(self, db):
        assert create_lead({"contact_headline": "no url"}) is False

    def test_first_touch_discovered_by_only_on_create(self, db):
        from openoutreach.crm.models import Lead

        c = _campaign()
        first = _node(c, SEED)
        second = _node(c, OTHER)
        row = {"contact_linkedin_profile_url": "https://www.linkedin.com/in/alice/"}
        with patch("openoutreach.discovery.embed_profile", return_value=np.ones(384, dtype=np.float32)):
            assert create_lead(row, discovered_by=first) is True
            assert create_lead(row, discovered_by=second) is False  # re-surfaced, not re-created

        lead = Lead.objects.get(profile_url="https://www.linkedin.com/in/alice/")
        assert lead.discovered_by_id == first.pk  # keeps the query that FOUND it


# ── discover() ───────────────────────────────────────────────────────


def _patch_select_embed():
    """Stub the candidate embedder so selection touches no ONNX model."""
    return patch("openoutreach.core.pipeline.select.embed_query",
                 return_value=np.ones(384, dtype=np.float64))


class TestDiscover:
    def test_skips_freemium_campaign(self, db):
        _set_key()
        session = MagicMock(campaign=_campaign(is_freemium=True))
        assert discover(session, _cold_qualifier()) == 0

    def test_skips_without_finder_key(self, db):
        session = MagicMock(campaign=_campaign())
        assert discover(session, _cold_qualifier()) == 0

    def test_skips_without_product_or_objective(self, db):
        _set_key()
        session = MagicMock(campaign=_campaign(product_docs="", campaign_target=""))
        assert discover(session, _cold_qualifier()) == 0

    def test_fetches_the_seed_maximal_and_injects_its_keywords(self, db):
        _set_key()
        campaign = _campaign(country_code="us")
        campaign.clauses.set(Clause.rows_for(SEED))
        session = MagicMock(campaign=campaign)
        rows = [
            {"contact_linkedin_profile_url": "https://www.linkedin.com/in/a/"},
            {"contact_linkedin_profile_url": "https://www.linkedin.com/in/b/"},
        ]
        with _patch_select_embed(), \
             patch("openoutreach.discovery.search", return_value=rows) as search, \
             patch("openoutreach.core.db.leads.create_lead", return_value=True) as create:
            assert discover(session, _cold_qualifier()) == 2

        assert search.call_args.kwargs["offset"] == 0
        node = DiscoveryQuery.objects.get(campaign=campaign, offset=0)
        assert node.clause_pairs == SEED
        # keywords of the retrieving query ride the embedding, not profile_text
        assert create.call_args.kwargs == {
            "country_code": "us", "discovered_by": node, "query_terms": "seniority owner",
        }

    def test_deepens_a_fetched_line_to_the_next_page(self, db):
        # One maximal, already fetched at offset 0 and not exhausted: the only
        # candidate is its next page. No qualified-lead precondition — the GP (here
        # cold) ranks it; deepen is just the next offset of a live vein.
        _set_key()
        campaign = _campaign(country_code="us")
        campaign.clauses.set(Clause.rows_for(SEED))
        _node(campaign, SEED, offset=0)
        session = MagicMock(campaign=campaign)
        rows = [{"contact_linkedin_profile_url": "https://www.linkedin.com/in/a/"}]
        with _patch_select_embed(), \
             patch("openoutreach.discovery.search", return_value=rows) as search, \
             patch("openoutreach.core.db.leads.create_lead", return_value=True):
            assert discover(session, _cold_qualifier()) == 1

        assert search.call_args.kwargs["offset"] == 100

    def test_a_provider_timeout_retires_the_query_without_blacklisting_it(self, db):
        from openoutreach.emails.bettercontact import BetterContactUnavailable

        _set_key()
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for(SEED))
        session = MagicMock(campaign=campaign)
        with _patch_select_embed(), \
             patch("openoutreach.discovery.search",
                   side_effect=BetterContactUnavailable("poll timed out")):
            assert discover(session, _cold_qualifier()) == 0

        node = DiscoveryQuery.objects.get(campaign=campaign, clause_key=clause_key(SEED))
        assert node.exhausted
        assert not EmptyClauseSet.objects.exists()

    def test_a_dry_vein_exhausts_its_line_without_blacklisting_it(self, db):
        # offset > 0 empty means we have seen everyone, not that the query matches
        # nobody — blacklisting would convict a conjunction that already produced leads.
        _set_key()
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for(SEED))
        _node(campaign, SEED, offset=0)  # already fetched → next candidate is offset 100
        session = MagicMock(campaign=campaign)
        with _patch_select_embed(), \
             patch("openoutreach.discovery.search", return_value=[]), \
             patch("openoutreach.core.pipeline.mint.mint_clauses", return_value=0):
            assert discover(session, _cold_qualifier()) == 0

        assert DiscoveryQuery.objects.filter(
            campaign=campaign, clause_key=clause_key(SEED), exhausted=True,
        ).exists()
        assert not EmptyClauseSet.objects.exists()

    def test_an_empty_fresh_maximal_is_blacklisted_then_the_pool_saturates(self, db):
        # A single-value pool spans one maximal; empty at offset 0 blacklists it, the
        # selector then has nothing, and saturation mints (here adding nothing → 0).
        _set_key()
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for(SEED))
        session = MagicMock(campaign=campaign)
        with _patch_select_embed(), \
             patch("openoutreach.discovery.search", return_value=[]), \
             patch("openoutreach.core.pipeline.mint.mint_clauses", return_value=0) as mint:
            assert discover(session, _cold_qualifier()) == 0

        assert EmptyClauseSet.objects.get().clause_key == clause_key(SEED)
        mint.assert_called_once()  # saturation trigger

    def test_saturation_mint_that_adds_clauses_reselects(self, db):
        # The only maximal is exhausted (a dry vein, not blacklisted), so the pool is
        # saturated; minting a new family opens a fresh maximal the selector then fetches.
        from openoutreach.core.pipeline.select import mark_exhausted, persist_fetched

        _set_key()
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for(SEED))
        persist_fetched(campaign, SEED, 0)
        mark_exhausted(campaign, SEED)  # saturated, but SEED not recorded empty
        session = MagicMock(campaign=campaign)
        rows = [{"contact_linkedin_profile_url": "https://www.linkedin.com/in/a/"}]

        def _mint(c):
            c.clauses.add(*Clause.rows_for(OTHER))  # widen the pool with a new family
            return 1

        with _patch_select_embed(), \
             patch("openoutreach.discovery.search", return_value=rows), \
             patch("openoutreach.core.pipeline.mint.mint_clauses", side_effect=_mint), \
             patch("openoutreach.core.db.leads.create_lead", return_value=True):
            assert discover(session, _cold_qualifier()) == 1

        # fetched the minted maximal (owner AND Japan), fresh at offset 0
        assert DiscoveryQuery.objects.filter(
            campaign=campaign, clause_key=clause_key(sorted(SEED + OTHER)), offset=0,
        ).exists()

    def test_throughput_mint_fires_after_enough_qualified(self, db):
        # Every mint_every_n_qualified new qualified leads, discover folds them in
        # before selecting — the throughput trigger, on a count, not a confidence bar.
        from openoutreach.core.conf import CAMPAIGN_CONFIG

        _set_key()
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for(SEED))
        node = _node(campaign, SEED, offset=0)
        for i in range(CAMPAIGN_CONFIG["mint_every_n_qualified"]):
            _qualified(campaign, node, f"q{i}")
        session = MagicMock(campaign=campaign)
        rows = [{"contact_linkedin_profile_url": "https://www.linkedin.com/in/a/"}]
        with _patch_select_embed(), \
             patch("openoutreach.discovery.search", return_value=rows), \
             patch("openoutreach.core.pipeline.mint.mint_clauses", return_value=0) as mint, \
             patch("openoutreach.core.db.leads.create_lead", return_value=True):
            discover(session, _cold_qualifier())

        mint.assert_called_once()

    def test_cold_start_seeds_the_pool_then_fetches_the_seed(self, db):
        _set_key()
        campaign = _campaign()  # no pool — cold start
        session = MagicMock(campaign=campaign)
        pool = [("lead_seniority", "vp"), ("lead_location", "Japan")]

        def _seed(c):
            c.clauses.set(Clause.rows_for(pool))
            return pool

        rows = [{"contact_linkedin_profile_url": "https://www.linkedin.com/in/c/"}]
        with _patch_select_embed(), \
             patch("openoutreach.core.pipeline.icp.generate_seed", side_effect=_seed), \
             patch("openoutreach.discovery.search", return_value=rows), \
             patch("openoutreach.core.db.leads.create_lead", return_value=True):
            assert discover(session, _cold_qualifier()) == 1

        assert set(campaign.clauses.values_list("family", "value")) == set(pool)
        node = DiscoveryQuery.objects.get(campaign=campaign, offset=0)
        assert node.clause_pairs == sorted(pool)  # the single seed maximal


# ── ICP generator ────────────────────────────────────────────────────


def _icp_returns(spec):
    """Patch the ICP's LLM call to yield ``spec``."""
    return patch("openoutreach.core.llm.run_agent_sync",
                 return_value=MagicMock(output=spec))


class TestICP:
    def test_folds_country_onto_the_campaign(self, db):
        campaign = _campaign()
        spec = ICPSpec(job_title="CMO", country_code="GB")
        with _icp_returns(spec), patch("openoutreach.core.llm.get_llm_model"), \
             patch("pydantic_ai.Agent"):
            clauses = generate_seed(campaign)
        campaign.refresh_from_db()
        assert campaign.country_code == "gb"  # lowercased
        assert ("lead_job_title", "CMO") in clauses

    def test_composes_the_seed_from_the_spec(self):
        spec = ICPSpec(
            job_title="CMO", seniority="owner",
            location="United States", headcount_min=1, headcount_max=50, country_code="us",
        )
        assert _seed_conjunction(spec) == [
            ("company_headcount_max", "50"),
            ("company_headcount_min", "1"),
            ("lead_job_title", "CMO"),
            ("lead_location", "United States"),
            ("lead_seniority", "owner"),
        ]

    def test_schema_forbids_more_than_one_value_per_family(self):
        # The seed is one precise conjunction — a list is an OR, and an OR compresses
        # several ~10k-row windows into one. Minting, not the seed, adds alternatives.
        with pytest.raises(ValidationError):
            ICPSpec(job_title=["CMO", "CTO"])

    def test_the_pool_is_the_seed_conjunction(self, db):
        campaign = _campaign()
        spec = ICPSpec(job_title="CMO", location="Germany",
                       headcount_min=1, headcount_max=50)
        with _icp_returns(spec), patch("openoutreach.core.llm.get_llm_model"), \
             patch("pydantic_ai.Agent"):
            generate_seed(campaign)

        assert set(campaign.clauses.values_list("family", "value")) == {
            ("company_headcount_min", "1"), ("company_headcount_max", "50"),
            ("lead_job_title", "CMO"), ("lead_location", "Germany"),
        }

    def test_the_pool_convicts_nothing_on_the_way_in(self, db):
        campaign = _campaign()
        with _icp_returns(ICPSpec(job_title="CMO")), \
             patch("openoutreach.core.llm.get_llm_model"), patch("pydantic_ai.Agent"):
            generate_seed(campaign)

        assert campaign.clauses.exists()
        assert not EmptyClauseSet.objects.exists()

    def test_seed_carries_no_inert_family(self):
        spec = ICPSpec(job_title="CMO", seniority="owner", location="Germany")
        assert not any(f == "lead_industry" for f, _ in _seed_conjunction(spec))

    def test_omits_empty_families(self):
        assert dict(_seed_conjunction(ICPSpec())).keys() == {
            "company_headcount_min", "company_headcount_max",
        }
