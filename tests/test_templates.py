# tests/test_templates.py
from unittest.mock import MagicMock, patch

from linkedin.templates.renderer import render_template


class TestRenderTemplate:
    def _make_session(self, booking_link=None):
        session = MagicMock()
        session.account_cfg = {"booking_link": booking_link}
        return session

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_renders_through_llm(self, mock_llm, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Hi {{ first_name }}, I saw you work at {{ company }}.")

        session = self._make_session()
        result = render_template(
            session,
            str(tpl),
            {"first_name": "Alice", "company": "Acme"},
        )
        mock_llm.assert_called_once()
        prompt = mock_llm.call_args[0][0]
        assert "Hi Alice, I saw you work at Acme." in prompt
        assert result == "LLM response"

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_with_booking_link(self, mock_llm, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Hello {{ first_name }}!")

        session = self._make_session(booking_link="https://cal.com/me")
        result = render_template(session, str(tpl), {"first_name": "Bob"})
        assert "LLM response" in result
        assert "https://cal.com/me" in result

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_without_booking_link(self, mock_llm, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Hello {{ first_name }}!")

        session = self._make_session(booking_link=None)
        result = render_template(session, str(tpl), {"first_name": "Bob"})
        assert result == "LLM response"

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_missing_variable_renders_empty(self, mock_llm, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Hi {{ first_name }}, your title is {{ headline }}.")

        session = self._make_session()
        result = render_template(session, str(tpl), {"first_name": "Alice"})
        prompt = mock_llm.call_args[0][0]
        assert "Hi Alice" in prompt

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_product_description_injected(self, mock_llm, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Product: {{ product_description }}")

        product_docs = tmp_path / "product_docs.txt"
        product_docs.write_text("We sell widgets")

        session = self._make_session()
        with patch("linkedin.templates.renderer.PRODUCT_DOCS_FILE", product_docs):
            render_template(session, str(tpl), {})

        prompt = mock_llm.call_args[0][0]
        assert "We sell widgets" in prompt

    @patch("linkedin.templates.renderer.call_llm", return_value="LLM response")
    def test_product_description_empty_when_file_missing(self, mock_llm, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Product: '{{ product_description }}'")

        missing = tmp_path / "nonexistent.txt"

        session = self._make_session()
        with patch("linkedin.templates.renderer.PRODUCT_DOCS_FILE", missing):
            render_template(session, str(tpl), {})

        prompt = mock_llm.call_args[0][0]
        assert "Product: ''" in prompt
