# tests/test_mint.py
"""Clause minting — grow the axes from the qualified leads.

The LLM call is stubbed (``run_agent_sync``) so no network is touched; the tests
assert what the proposed values do to the pool and the throughput high-water mark.
"""
from unittest.mock import MagicMock, patch

from openoutreach.core.models import Clause
from openoutreach.core.pipeline.mint import _MintedClauses, mint_clauses


def _campaign(**kw):
    from openoutreach.core.models import Campaign

    defaults = dict(name="C", product_docs="p", campaign_target="t")
    defaults.update(kw)
    return Campaign.objects.create(**defaults)


def _llm_returns(minted):
    return patch("openoutreach.core.llm.run_agent_sync",
                 return_value=MagicMock(output=minted))


def _qualified(campaign, url):
    from openoutreach.crm.models import Deal, DealState, Lead

    lead = Lead.objects.create(profile_url=url, profile_text="cmo acme")
    Deal.objects.create(lead=lead, campaign=campaign, state=DealState.QUALIFIED)


class TestMint:
    def test_adds_fresh_clauses_and_skips_existing(self, db):
        # Several values per family is the whole point of a mint — and the render must
        # survive it (filters_for raises on >1 value per family, so the log renders
        # per clause). Regression: describe_clauses(fresh) crashed the daemon here.
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for([("lead_location", "Japan")]))
        minted = _MintedClauses(
            lead_job_title=["VP Sales", "Head of Sales"],
            lead_location=["Japan", "Canada"],
            lead_department=["Sales", "Marketing"],
        )
        with _llm_returns(minted), patch("openoutreach.core.llm.get_llm_model"), \
             patch("pydantic_ai.Agent"):
            added = mint_clauses(campaign)

        assert added == 5  # 2 titles + Canada + 2 departments; Japan already in the pool
        assert set(campaign.clauses.values_list("family", "value")) == {
            ("lead_location", "Japan"), ("lead_location", "Canada"),
            ("lead_job_title", "VP Sales"), ("lead_job_title", "Head of Sales"),
            ("lead_department", "Sales"), ("lead_department", "Marketing"),
        }

    def test_records_the_qualified_high_water_mark(self, db):
        campaign = _campaign()
        _qualified(campaign, "https://x/1/")
        _qualified(campaign, "https://x/2/")
        with _llm_returns(_MintedClauses(lead_location=["Canada"])), \
             patch("openoutreach.core.llm.get_llm_model"), patch("pydantic_ai.Agent"):
            mint_clauses(campaign)

        campaign.refresh_from_db()
        assert campaign.discovery_minted_at_qualified == 2  # so throughput won't re-fire

    def test_nothing_new_returns_zero(self, db):
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for([("lead_location", "Japan")]))
        with _llm_returns(_MintedClauses(lead_location=["Japan"])), \
             patch("openoutreach.core.llm.get_llm_model"), patch("pydantic_ai.Agent"):
            assert mint_clauses(campaign) == 0
        assert campaign.clauses.count() == 1

    def test_llm_failure_is_best_effort(self, db):
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for([("lead_location", "Japan")]))
        with patch("openoutreach.core.llm.run_agent_sync", side_effect=RuntimeError("boom")), \
             patch("openoutreach.core.llm.get_llm_model"), patch("pydantic_ai.Agent"):
            assert mint_clauses(campaign) == 0  # no crash
        assert campaign.clauses.count() == 1
