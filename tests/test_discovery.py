# tests/test_discovery.py
"""Discovery slice — mock the BetterContact transport (`submit_and_poll`) and
the embedder, so no network or ONNX model is needed."""
from unittest.mock import patch

import numpy as np

from openoutreach import discovery


def _set_key(value):
    from openoutreach.core.models import SiteConfig

    cfg = SiteConfig.load()
    cfg.bettercontact_api_key = value
    cfg.save()


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
