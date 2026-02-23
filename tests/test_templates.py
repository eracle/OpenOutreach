# tests/test_templates.py
from unittest.mock import MagicMock, patch

from linkedin.conf import DEFAULT_FOLLOWUP_TEMPLATE_PATH
from linkedin.templates.renderer import render_template


class TestRenderTemplate:
    def _make_session(self, booking_link=None, product_docs=""):
        session = MagicMock()
        session.campaign.product_docs = product_docs
        session.campaign.booking_link = booking_link
        return session

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_renders_through_llm(self, mock_llm):
        template_content = "Hi {{ first_name }}, I saw you work at {{ company }}."

        session = self._make_session()
        result = render_template(
            session,
            template_content,
            {"first_name": "Alice", "company": "Acme"},
        )
        mock_llm.assert_called_once()
        prompt = mock_llm.call_args[0][0]
        assert "Hi Alice, I saw you work at Acme." in prompt
        assert result == "LLM response"

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_with_booking_link(self, mock_llm):
        template_content = "Hello {{ first_name }}!"

        session = self._make_session(booking_link="https://cal.com/me")
        result = render_template(session, template_content, {"first_name": "Bob"})
        assert "LLM response" in result
        assert "https://cal.com/me" in result

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_without_booking_link(self, mock_llm):
        template_content = "Hello {{ first_name }}!"

        session = self._make_session(booking_link=None)
        result = render_template(session, template_content, {"first_name": "Bob"})
        assert result == "LLM response"

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_missing_variable_renders_empty(self, mock_llm):
        template_content = "Hi {{ first_name }}, your title is {{ headline }}."

        session = self._make_session()
        result = render_template(session, template_content, {"first_name": "Alice"})
        prompt = mock_llm.call_args[0][0]
        assert "Hi Alice" in prompt

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_product_description_injected(self, mock_llm):
        template_content = "Product: {{ product_description }}"

        session = self._make_session(product_docs="We sell widgets")
        render_template(session, template_content, {})

        prompt = mock_llm.call_args[0][0]
        assert "We sell widgets" in prompt

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_product_description_empty_when_not_set(self, mock_llm):
        template_content = "Product: '{{ product_description }}'"

        session = self._make_session(product_docs="")
        render_template(session, template_content, {})

        prompt = mock_llm.call_args[0][0]
        assert "Product: ''" in prompt

    @patch("linkedin.templates.renderer.call_llm", return_value="Hi Alice, great to connect!")
    def test_default_followup_template_forbids_signature(self, mock_llm):
        template_content = DEFAULT_FOLLOWUP_TEMPLATE_PATH.read_text()

        session = self._make_session(product_docs="We sell widgets")
        render_template(
            session,
            template_content,
            {
                "full_name": "Alice Smith",
                "headline": "Engineer",
                "current_company": "Acme",
                "location": "London",
                "shared_connections": 3,
            },
        )

        prompt = mock_llm.call_args[0][0]
        assert "Do **not** sign the message" in prompt
        assert "never with a name" in prompt
