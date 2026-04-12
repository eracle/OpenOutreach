"""Tests for linkedin/db/summaries.py — the mem0-style fact-list boundary."""
from __future__ import annotations

import json
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

    def test_first_pass_includes_both_sides_labeled(self, db, deal_with_lead):
        """Both sides are sent to extraction with [Me]/[Lead] tags for disambiguation."""
        from linkedin.db.summaries import update_chat_summary

        msgs = [
            self._msg("Hi, are you the founder?", is_outgoing=True),
            self._msg("Yeah, I founded Acme last year.", is_outgoing=False),
        ]
        new_facts = ["Lead founded Acme last year."]
        with patch("linkedin.db.summaries.extract_facts",
                   return_value=new_facts) as mock_extract, \
             patch("linkedin.db.summaries.reconcile_facts",
                   return_value=new_facts) as mock_reconcile:
            update_chat_summary(deal_with_lead, iter(msgs))

        sent_text = mock_extract.call_args[0][0]
        assert "[Me] Hi, are you the founder?" in sent_text
        assert "[Lead] Yeah, I founded Acme last year." in sent_text
        # First pass: existing is empty, reconcile sees only new facts.
        mock_reconcile.assert_called_once_with([], new_facts)
        deal_with_lead.refresh_from_db()
        assert deal_with_lead.chat_summary == {"facts": new_facts}

    def test_all_outgoing_burst_is_noop(self, db, deal_with_lead):
        """A one-sided seller-only burst must not pollute chat_summary with our pitch."""
        from linkedin.db.summaries import update_chat_summary

        msgs = [
            self._msg("Ciao Andrea, sono Diego di Sunnyplans...", is_outgoing=True),
            self._msg("Hai visto il mio messaggio?", is_outgoing=True),
        ]
        with patch("linkedin.db.summaries.extract_facts") as mock_extract, \
             patch("linkedin.db.summaries.reconcile_facts") as mock_reconcile:
            update_chat_summary(deal_with_lead, msgs)

        mock_extract.assert_not_called()
        mock_reconcile.assert_not_called()
        deal_with_lead.refresh_from_db()
        assert deal_with_lead.chat_summary is None

    def test_second_pass_reconciles_via_mem0_prompt(self, db, deal_with_lead):
        """A second sync routes through reconcile_facts → mem0 UPDATE prompt."""
        from linkedin.db.summaries import update_chat_summary

        deal_with_lead.chat_summary = {"facts": ["Lead is the founder."]}
        deal_with_lead.save(update_fields=["chat_summary"])

        msgs = [self._msg("We have budget.", is_outgoing=False)]
        with patch("linkedin.db.summaries.extract_facts",
                   return_value=["Lead has budget."]), \
             patch("linkedin.db.summaries.reconcile_facts",
                   return_value=["Lead is the founder.", "Lead has budget."]) as mock_reconcile:
            update_chat_summary(deal_with_lead, msgs)

        mock_reconcile.assert_called_once_with(
            ["Lead is the founder."], ["Lead has budget."]
        )
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


class TestReconcileFacts:
    """reconcile_facts wraps mem0's UPDATE prompt — mock the LLM at the boundary."""

    def _stub_llm_with(self, memory_actions, *, raw_text=None):
        """Build a fake ChatOpenAI whose `.invoke().content` returns a JSON string.

        `raw_text` lets a test inject a non-JSON wrapper (markdown fence, think
        block, leading prose) to exercise the parse-fallback chain.
        """
        payload = raw_text if raw_text is not None else json.dumps({"memory": memory_actions})
        fake_response = MagicMock()
        fake_response.content = payload
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = fake_response
        return fake_llm

    def test_empty_new_facts_returns_existing_unchanged(self, db):
        from linkedin.db.summaries import reconcile_facts

        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            result = reconcile_facts(["fact a", "fact b"], [])

        assert result == ["fact a", "fact b"]
        mock_cls.assert_not_called()

    def test_contradiction_drops_stale_fact(self, db):
        """LLM returns DELETE for the stale fact + ADD for the new one — both applied."""
        from linkedin.db.summaries import reconcile_facts

        actions = [
            {"id": "0", "text": "Lead has no budget.", "event": "DELETE"},
            {"id": "1", "text": "Lead has budget.", "event": "ADD"},
        ]
        fake_llm = self._stub_llm_with(actions)
        with patch("langchain_openai.ChatOpenAI", return_value=fake_llm), \
             patch("linkedin.conf.get_llm_config",
                   return_value=("sk-test", "gpt-4o-mini", "https://api.openai.com/v1")):
            result = reconcile_facts(
                ["Lead has no budget."],
                ["Lead has budget."],
            )

        assert result == ["Lead has budget."]

    def test_update_event_replaces_in_place(self, db):
        from linkedin.db.summaries import reconcile_facts

        actions = [
            {"id": "0", "text": "Lead is CTO at Acme.", "event": "UPDATE",
             "old_memory": "Lead is an engineer at Acme."},
        ]
        fake_llm = self._stub_llm_with(actions)
        with patch("langchain_openai.ChatOpenAI", return_value=fake_llm), \
             patch("linkedin.conf.get_llm_config",
                   return_value=("sk-test", "gpt-4o-mini", "https://api.openai.com/v1")):
            result = reconcile_facts(
                ["Lead is an engineer at Acme."],
                ["Lead is CTO at Acme."],
            )

        assert result == ["Lead is CTO at Acme."]

    def test_unknown_id_in_update_is_skipped(self, db, caplog):
        """LLM hallucinates an id that doesn't exist — log + skip, don't crash."""
        from linkedin.db.summaries import reconcile_facts

        actions = [
            {"id": "999", "text": "Hallucinated.", "event": "UPDATE"},
            {"id": "0", "text": "Real ADD.", "event": "ADD"},
        ]
        fake_llm = self._stub_llm_with(actions)
        with caplog.at_level("WARNING"), \
             patch("langchain_openai.ChatOpenAI", return_value=fake_llm), \
             patch("linkedin.conf.get_llm_config",
                   return_value=("sk-test", "gpt-4o-mini", "https://api.openai.com/v1")):
            result = reconcile_facts(["existing fact"], ["new fact"])

        assert "existing fact" in result
        assert "Real ADD." in result
        assert "Hallucinated." not in result
        assert any("UPDATE skipped" in r.message for r in caplog.records)

    def test_none_event_is_noop(self, db):
        from linkedin.db.summaries import reconcile_facts

        actions = [
            {"id": "0", "text": "Lead is the founder.", "event": "NONE"},
            {"id": "1", "text": "Lead replied politely.", "event": "ADD"},
        ]
        fake_llm = self._stub_llm_with(actions)
        with patch("langchain_openai.ChatOpenAI", return_value=fake_llm), \
             patch("linkedin.conf.get_llm_config",
                   return_value=("sk-test", "gpt-4o-mini", "https://api.openai.com/v1")):
            result = reconcile_facts(
                ["Lead is the founder."],
                ["Lead replied politely."],
            )

        assert result == ["Lead is the founder.", "Lead replied politely."]

    def test_markdown_wrapped_json_is_parsed(self, db):
        """Provider that wraps JSON in ```json ... ``` should still parse via fallback."""
        from linkedin.db.summaries import reconcile_facts

        wrapped = (
            "```json\n"
            '{"memory": [{"id": "0", "text": "Lead is in Berlin.", "event": "ADD"}]}\n'
            "```"
        )
        fake_llm = self._stub_llm_with([], raw_text=wrapped)
        with patch("langchain_openai.ChatOpenAI", return_value=fake_llm), \
             patch("linkedin.conf.get_llm_config",
                   return_value=("sk-test", "gpt-4o-mini", "https://api.openai.com/v1")):
            result = reconcile_facts([], ["Lead is in Berlin."])

        assert result == ["Lead is in Berlin."]

    def test_reasoning_model_think_block_is_stripped(self, db):
        """Reasoning model output with <think> blocks before the JSON parses cleanly."""
        from linkedin.db.summaries import reconcile_facts

        wrapped = (
            "<think>The user wants me to add this fact about location.</think>\n"
            '{"memory": [{"id": "0", "text": "Lead is in Berlin.", "event": "ADD"}]}'
        )
        fake_llm = self._stub_llm_with([], raw_text=wrapped)
        with patch("langchain_openai.ChatOpenAI", return_value=fake_llm), \
             patch("linkedin.conf.get_llm_config",
                   return_value=("sk-test", "gpt-4o-mini", "https://api.openai.com/v1")):
            result = reconcile_facts([], ["Lead is in Berlin."])

        assert result == ["Lead is in Berlin."]
