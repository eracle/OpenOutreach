"""Tests for the follow-up agent context builder + Jinja template."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from tests.factories import LeadFactory, DealFactory


@pytest.fixture
def deal_with_summaries(db, fake_session):
    lead = LeadFactory(public_identifier="alice")
    return DealFactory(
        lead=lead,
        campaign=fake_session.campaign,
        profile_summary={"facts": [
            "Senior engineer at Acme Corp.",
            "Based in Berlin, Germany.",
            "Speaks English and German.",
        ]},
        chat_summary={"facts": [
            "Lead is curious about pricing.",
            "Lead has a small team budget.",
        ]},
    )


def _msg(content, is_outgoing):
    m = MagicMock()
    m.content = content
    m.is_outgoing = is_outgoing
    return m


class TestRenderSystemPrompt:
    def test_includes_three_summary_blocks(self, db, fake_session, deal_with_summaries):
        from linkedin.agents.follow_up import _render_system_prompt

        # Stub session.self_profile so the prompt builder works without a browser.
        fake_session.self_profile = {"first_name": "Bob", "last_name": "Builder", "urn": "urn:li:fsd_profile:SELF"}

        recent = [_msg("Hi, what do you do?", is_outgoing=True), _msg("Sales tooling.", is_outgoing=False)]
        prompt = _render_system_prompt(fake_session, deal_with_summaries, recent)

        # Profile facts appear under the lead-knowledge block.
        assert "Senior engineer at Acme Corp." in prompt
        assert "Based in Berlin, Germany." in prompt
        # Chat facts appear under the conversation-knowledge block.
        assert "Lead is curious about pricing." in prompt
        # Verbatim recent messages appear in Me:/Lead: format.
        assert "Me: Hi, what do you do?" in prompt
        assert "Lead: Sales tooling." in prompt
        # The legacy flat fields are gone.
        assert "Headline:" not in prompt
        assert "Company:" not in prompt

    def test_handles_missing_summaries_gracefully(self, db, fake_session):
        from linkedin.agents.follow_up import _render_system_prompt

        lead = LeadFactory(public_identifier="bob")
        deal = DealFactory(lead=lead, campaign=fake_session.campaign)
        fake_session.self_profile = {"first_name": "Bob", "last_name": "Builder", "urn": "urn:li:fsd_profile:SELF"}

        prompt = _render_system_prompt(fake_session, deal, [])

        # Renders without crashing and shows the empty placeholders.
        assert "(none yet)" in prompt
        assert "No recent messages." in prompt


class TestLoadRecentMessages:
    def test_returns_last_n_in_chronological_order(self, db, fake_session):
        from chat.models import ChatMessage
        from django.contrib.contenttypes.models import ContentType
        from django.utils import timezone
        from datetime import timedelta

        from linkedin.agents.follow_up import _load_recent_messages, RECENT_MESSAGES_WINDOW

        lead = LeadFactory(public_identifier="alice")
        deal = DealFactory(lead=lead, campaign=fake_session.campaign)
        ct = ContentType.objects.get_for_model(lead)

        base = timezone.now()
        for i in range(RECENT_MESSAGES_WINDOW + 3):
            ChatMessage.objects.create(
                content_type=ct, object_id=lead.pk,
                content=f"msg-{i}",
                is_outgoing=(i % 2 == 0),
                owner=fake_session.django_user,
                linkedin_urn=f"urn:msg:{i}",
                creation_date=base + timedelta(minutes=i),
            )

        recent = _load_recent_messages(deal)

        # Window respected and chronological order preserved.
        assert len(recent) == RECENT_MESSAGES_WINDOW
        contents = [m.content for m in recent]
        assert contents == sorted(contents, key=lambda c: int(c.split("-")[1]))
        # Returned the *latest* messages.
        assert contents[-1] == f"msg-{RECENT_MESSAGES_WINDOW + 2}"
