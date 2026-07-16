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
            ICPSpec(seniorities=["other"])

    def test_seed_spec_accepts_every_real_level(self):
        assert ICPSpec(seniorities=list(discovery.LEAD_SENIORITIES)).seniorities == list(
            discovery.LEAD_SENIORITIES
        )

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
            "contact_headline": "Head of Growth", "contact_location": "Berlin",
            "contact_industry": "SaaS", "contact_job_title": "CMO",
            "company_name": "Acme", "company_description": "We sell widgets",
        }
        assert discovery.profile_text_for(row) == "head of growth berlin saas cmo acme we sell widgets"

    def test_tolerates_missing_and_null_fields(self):
        assert discovery.profile_text_for({"contact_headline": "Hi", "company_name": None}) == "hi     "

    def test_appends_extra_fields_when_present(self):
        row = {
            "contact_job_title": "Founder", "company_name": "Acme",
            "contact_seniority": "Founder", "company_industry": "SaaS",
            "contact_location_state": "California", "contact_location_country": "United States",
            "company_keywords": ["dev tools", "api"],
        }
        # base slots (headline/location/industry/description absent) then the extras
        assert discovery.profile_text_for(row) == (
            "   founder acme  founder saas california united states dev tools api"
        )

    def test_skips_absent_extra_fields(self):
        # a bare row gains no trailing padding for extras it never carried
        assert discovery.profile_text_for({"contact_job_title": "CEO"}) == "   ceo  "


class TestEmbedRow:
    def test_builds_ordered_lowercased_text(self):
        row = {
            "contact_headline": "Head of Growth",
            "contact_location": "Berlin",
            "contact_industry": "SaaS",
            "contact_job_title": "CMO",
            "company_name": "Acme",
            "company_description": "We sell widgets",
        }
        with patch("openoutreach.core.ml.embeddings.embed_text", return_value=np.ones(384)) as embed:
            discovery.embed_row(row)
        embed.assert_called_once_with("head of growth berlin saas cmo acme we sell widgets")

    def test_tolerates_missing_and_null_fields(self):
        with patch("openoutreach.core.ml.embeddings.embed_text", return_value=np.ones(384)) as embed:
            discovery.embed_row({"contact_headline": "Hi", "company_name": None})
        embed.assert_called_once_with("hi     ")
