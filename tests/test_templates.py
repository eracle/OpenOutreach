# tests/test_templates.py
from unittest.mock import MagicMock

from linkedin.templates.renderer import render_template


class TestRenderTemplate:
    def _make_session(self, booking_link=None):
        session = MagicMock()
        session.account_cfg = {"booking_link": booking_link}
        return session

    def test_jinja_template_renders_variables(self, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Hi {{ first_name }}, I saw you work at {{ company }}.")

        session = self._make_session()
        result = render_template(
            session,
            str(tpl),
            "jinja",
            {"first_name": "Alice", "company": "Acme"},
        )
        assert result == "Hi Alice, I saw you work at Acme."

    def test_jinja_with_booking_link(self, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Hello {{ first_name }}!")

        session = self._make_session(booking_link="https://cal.com/me")
        result = render_template(session, str(tpl), "jinja", {"first_name": "Bob"})
        assert "Hello Bob!" in result
        assert "https://cal.com/me" in result

    def test_jinja_without_booking_link(self, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Hello {{ first_name }}!")

        session = self._make_session(booking_link=None)
        result = render_template(session, str(tpl), "jinja", {"first_name": "Bob"})
        assert result == "Hello Bob!"

    def test_unknown_template_type_raises(self, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Hi")

        session = self._make_session()
        import pytest
        with pytest.raises(ValueError, match="Unknown template_type"):
            render_template(session, str(tpl), "unknown_type", {})

    def test_jinja_missing_variable_renders_empty(self, tmp_path):
        tpl = tmp_path / "msg.j2"
        tpl.write_text("Hi {{ first_name }}, your title is {{ headline }}.")

        session = self._make_session()
        result = render_template(session, str(tpl), "jinja", {"first_name": "Alice"})
        assert "Hi Alice" in result
