# tests/test_discovery.py
"""Discovery slice — mock the BetterContact transport (`submit_and_poll`) and
the embedder, so no network or ONNX model is needed."""
from unittest.mock import patch

import numpy as np
import pytest
from pydantic import ValidationError

from openoutreach import discovery
from openoutreach.core.pipeline.icp import ICPSpec


def _set_key(value):
    from openoutreach.core.models import SiteConfig

    cfg = SiteConfig.load()
    cfg.bettercontact_api_key = value
    cfg.save()


class TestSeniorityVocabulary:
    """`lead_seniority` is the one filter family with a closed vocabulary, and a
    value outside it returns an empty page instead of an error — so the ICP seed
    constrains it in the schema. A bad seed is the worst case: it starves the
    bootstrap phase, which pages the seed until the GP can score."""

    def test_seed_spec_rejects_a_level_lead_finder_does_not_know(self):
        with pytest.raises(ValidationError):
            ICPSpec(seniority="other")

    def test_seed_spec_accepts_every_real_level(self):
        for level in discovery.LEAD_SENIORITIES:
            assert ICPSpec(seniority=level).seniority == level

    def test_vocabulary_is_derived_from_the_type_so_prompt_and_schema_cannot_drift(self):
        # The prompts render this tuple; the schema validates the same Literal.
        assert "mid-level" in discovery.LEAD_SENIORITIES
        assert "other" not in discovery.LEAD_SENIORITIES
        assert len(discovery.LEAD_SENIORITIES) == 12


class TestSearch:
    def test_returns_leads_and_sends_icp_filters(self, db):
        _set_key("k")
        rows = [{"contact_full_name": "Alice"}]
        with patch.object(discovery, "submit_and_poll", return_value={"leads": rows}) as call:
            result = discovery.search({"lead_seniority": {"include": ["owner"]}}, limit=10)

        assert result == rows
        api_key, url, body = call.call_args.args
        assert api_key == "k"
        assert url == discovery.LEAD_FINDER_URL
        assert body == {"filters": {"lead_seniority": {"include": ["owner"]}}, "limit": 10, "offset": 0}

    def test_no_leads_key_is_empty_list(self, db):
        _set_key("k")
        with patch.object(discovery, "submit_and_poll", return_value={"status": "terminated"}):
            assert discovery.search({}) == []


class TestProfileTextFor:
    def test_joins_fields_in_order_lowercased(self):
        row = {
            "contact_headline": "Head of Growth", "contact_industry": "SaaS",
            "contact_job_title": "CMO", "company_name": "Acme",
            "contact_seniority": "Founder", "company_industry": "B2B",
            "contact_location_state": "California",
            "contact_location_country": "United States",
        }
        assert discovery.profile_text_for(row) == (
            "head of growth saas cmo acme founder b2b california united states"
        )

    def test_tolerates_missing_and_null_fields(self):
        assert discovery.profile_text_for({"contact_headline": "Hi", "company_name": None}) == "hi"

    def test_skips_absent_fields_without_padding(self):
        # a sparse row stays short rather than padding out to a rich row's shape
        assert discovery.profile_text_for({"contact_job_title": "CEO"}) == "ceo"

    def test_drops_the_fabricated_company_free_text(self):
        # Lead Finder staples a fuzzy-matched company record onto every row (1-4
        # distinct per 100-row page), so these carry no per-lead signal to rank on.
        row = {
            "contact_job_title": "Founder",
            "company_description": "Meta's mission is to build the future of human connection",
            "company_keywords": ["bee keeper", "chaplin", "dive master"],
            "contact_location": "Berlin",
        }
        assert discovery.profile_text_for(row) == "founder"


class TestEmbedRow:
    def test_builds_ordered_lowercased_text(self):
        row = {
            "contact_headline": "Head of Growth",
            "contact_industry": "SaaS",
            "contact_job_title": "CMO",
            "company_name": "Acme",
        }
        with patch("openoutreach.core.ml.embeddings.embed_text", return_value=np.ones(384)) as embed:
            discovery.embed_row(row)
        embed.assert_called_once_with("head of growth saas cmo acme")

    def test_tolerates_missing_and_null_fields(self):
        with patch("openoutreach.core.ml.embeddings.embed_text", return_value=np.ones(384)) as embed:
            discovery.embed_row({"contact_headline": "Hi", "company_name": None})
        embed.assert_called_once_with("hi")


class TestDescribeFilters:
    """The log rendering of a Lead Finder filter set. Pure (no colour) so the
    call sites own presentation and these stay readable."""

    def test_renders_the_families_a_mutation_varies(self):
        assert discovery.describe_filters({
            "company_headcount_min": 1, "company_headcount_max": 20,
            "lead_job_title": {"include": ["Founder", "CTO"], "exact_match": False},
            "lead_location": {"include": ["United States"]},
        }) == "headcount 1–20 · job_title Founder, CTO · location United States"

    def test_collapses_the_two_headcount_bounds_into_one_range(self):
        """min/max are two keys describing one thing; they read as one."""
        out = discovery.describe_filters({"company_headcount_min": 1, "company_headcount_max": 20})
        assert out == "headcount 1–20"

    def test_marks_an_open_ended_headcount_bound(self):
        assert discovery.describe_filters({"company_headcount_min": 50}) == "headcount 50–?"
        assert discovery.describe_filters({"company_headcount_max": 50}) == "headcount ?–50"

    def test_keeps_exact_match_because_it_changes_what_matches(self):
        assert discovery.describe_filters(
            {"lead_job_title": {"include": ["SDR"], "exact_match": True}}
        ) == "job_title SDR (exact)"

    def test_strips_the_lead_and_company_prefixes(self):
        assert discovery.describe_filters({
            "company_technology": {"include": ["hubspot"]},
            "lead_skills": {"include": ["negotiation"]},
        }) == "technology hubspot · skills negotiation"

    def test_empty_filters_say_so_rather_than_render_blank(self):
        """An all-unset proposal means 'the LLM is dry' — it must not read as a query."""
        assert discovery.describe_filters({}) == "(no filters)"

    def test_survives_an_empty_include_list(self):
        assert discovery.describe_filters({"lead_skills": {"include": []}}) == "skills (none)"

    def test_renders_an_unknown_key_rather_than_dropping_it(self):
        """Filters are free-form dicts; a key we don't model must still be visible."""
        assert discovery.describe_filters({"some_new_filter": "x"}) == "some_new_filter x"
