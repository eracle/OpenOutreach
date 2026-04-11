"""Tests for linkedin/db/summaries.py — the mem0-style fact-list boundary."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from tests.factories import LeadFactory, DealFactory


FAKE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Senior Engineer at Acme",
    "positions": [{"company_name": "Acme Corp", "title": "Senior Engineer"}],
    "urn": "urn:li:fsd_profile:ABC123",
}


@pytest.fixture
def deal_with_lead(db, fake_session):
    lead = LeadFactory(
        public_identifier="alice",
        linkedin_url="https://www.linkedin.com/in/alice/",
    )
    return DealFactory(lead=lead, campaign=fake_session.campaign)


class TestExtractFacts:
    def test_empty_input_returns_empty_list(self, db):
        from linkedin.db.summaries import extract_facts

        assert extract_facts("") == []
        assert extract_facts("   \n  ") == []

    def test_invokes_llm_with_structured_output(self, db):
        from linkedin.db.summaries import extract_facts, FactList

        fake_facts = FactList(facts=["Works at Acme.", "Based in Berlin."])
        fake_structured = MagicMock()
        fake_structured.invoke.return_value = fake_facts
        fake_llm = MagicMock()
        fake_llm.with_structured_output.return_value = fake_structured

        with patch("langchain_openai.ChatOpenAI", return_value=fake_llm), \
             patch("linkedin.conf.get_llm_config",
                   return_value=("sk-test", "gpt-4o-mini", "https://api.openai.com/v1")):
            facts = extract_facts("Alice works at Acme. She lives in Berlin.",
                                  context="Campaign objective: hire engineers")

        assert facts == ["Works at Acme.", "Based in Berlin."]
        # Structured output is requested with the FactList schema.
        fake_llm.with_structured_output.assert_called_once_with(FactList)
        # Two messages: system (with vendored prompt + context) + user.
        sent_messages = fake_structured.invoke.call_args[0][0]
        assert len(sent_messages) == 2
        assert sent_messages[0]["role"] == "system"
        assert "Campaign objective" in sent_messages[0]["content"]
        assert sent_messages[1]["role"] == "user"
        assert "Alice works at Acme" in sent_messages[1]["content"]


class TestMaterializeProfileSummary:
    def test_noop_when_already_built(self, db, deal_with_lead):
        from linkedin.db.summaries import materialize_profile_summary_if_missing

        deal_with_lead.profile_summary = {"facts": ["already built"]}
        deal_with_lead.save(update_fields=["profile_summary"])

        with patch("linkedin.db.summaries.extract_facts") as mock_extract:
            materialize_profile_summary_if_missing(deal_with_lead, None)

        mock_extract.assert_not_called()

    def test_builds_via_rescrape_and_persists(self, db, fake_session, deal_with_lead):
        from linkedin.db.summaries import materialize_profile_summary_if_missing

        with patch.object(deal_with_lead.lead, "get_profile", return_value=FAKE_PROFILE) as mock_refresh, \
             patch("linkedin.db.summaries.extract_facts",
                   return_value=["Senior Engineer at Acme.", "URN ABC123."]) as mock_extract:
            materialize_profile_summary_if_missing(deal_with_lead, fake_session)

        mock_refresh.assert_called_once_with(fake_session)
        mock_extract.assert_called_once()
        deal_with_lead.refresh_from_db()
        assert deal_with_lead.profile_summary == {
            "facts": ["Senior Engineer at Acme.", "URN ABC123."]
        }

    def test_empty_profile_logs_and_skips(self, db, fake_session, deal_with_lead, caplog):
        from linkedin.db.summaries import materialize_profile_summary_if_missing

        with patch.object(deal_with_lead.lead, "get_profile", return_value=None), \
             patch("linkedin.db.summaries.extract_facts") as mock_extract:
            materialize_profile_summary_if_missing(deal_with_lead, fake_session)

        mock_extract.assert_not_called()
        deal_with_lead.refresh_from_db()
        assert deal_with_lead.profile_summary is None


class TestUpdateChatSummary:
    def _msg(self, content, is_outgoing):
        m = MagicMock()
        m.content = content
        m.is_outgoing = is_outgoing
        return m

    def test_noop_on_empty_messages(self, db, deal_with_lead):
        from linkedin.db.summaries import update_chat_summary

        with patch("linkedin.db.summaries.extract_facts") as mock_extract:
            update_chat_summary(deal_with_lead, [])

        mock_extract.assert_not_called()
        deal_with_lead.refresh_from_db()
        assert deal_with_lead.chat_summary is None

    def test_first_pass_persists_facts(self, db, deal_with_lead):
        from linkedin.db.summaries import update_chat_summary

        msgs = [
            self._msg("Hi, are you the founder?", is_outgoing=True),
            self._msg("Yeah, what's up?", is_outgoing=False),
        ]
        with patch("linkedin.db.summaries.extract_facts",
                   return_value=["Lead is the founder.", "Conversation has been cordial."]) as mock_extract:
            update_chat_summary(deal_with_lead, iter(msgs))

        sent_text = mock_extract.call_args[0][0]
        assert "Me: Hi, are you the founder?" in sent_text
        assert "Lead: Yeah, what's up?" in sent_text
        deal_with_lead.refresh_from_db()
        assert deal_with_lead.chat_summary == {
            "facts": ["Lead is the founder.", "Conversation has been cordial."],
        }

    def test_second_pass_merges_dedups(self, db, deal_with_lead):
        from linkedin.db.summaries import update_chat_summary

        deal_with_lead.chat_summary = {"facts": ["Lead is the founder."]}
        deal_with_lead.save(update_fields=["chat_summary"])

        msgs = [self._msg("We have budget.", is_outgoing=False)]
        with patch("linkedin.db.summaries.extract_facts",
                   return_value=["Lead is the founder.", "Lead has budget."]):
            update_chat_summary(deal_with_lead, msgs)

        deal_with_lead.refresh_from_db()
        assert deal_with_lead.chat_summary == {
            "facts": ["Lead is the founder.", "Lead has budget."],
        }

    def test_blank_messages_treated_as_empty(self, db, deal_with_lead):
        from linkedin.db.summaries import update_chat_summary

        msgs = [self._msg("   ", is_outgoing=True), self._msg("", is_outgoing=False)]
        with patch("linkedin.db.summaries.extract_facts") as mock_extract:
            update_chat_summary(deal_with_lead, msgs)

        mock_extract.assert_not_called()
