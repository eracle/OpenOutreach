"""What this host adds: one row of answers, and the environment both children read.

The questions themselves are this program's, but what they *mean* belongs to the children
and is tested there. What is tested here is only what neither child can do alone — hold a
person's answers between runs, and hand each child its own vocabulary of them.
"""
import pytest

from openoutreach import wizard
from openoutreach.config.models import SiteConfig

ANSWERS = dict(
    product_docs="A lead finder for small B2B teams.",
    campaign_target="Founders selling to other founders.",
    ai_model="anthropic:claude-sonnet-4-5-20250929",
    llm_api_key="sk-not-a-real-key",
    bettercontact_api_key="bc-not-a-real-key",
    operator_name="Ada Lovelace",
    operator_email="ada@example.com",
    country_code="us",
    accepted_legal_notice=True,
    mailbox_address="ada@example.com",
    mailbox_password="app-password",
)


@pytest.fixture
def configured(db):
    config = SiteConfig.load()
    for field, value in ANSWERS.items():
        setattr(config, field, value)
    config.save()
    return config


@pytest.fixture(autouse=True)
def _no_inherited_variables(monkeypatch):
    """A developer's own exports must not decide what a test sees."""
    import os

    for name in [n for n in os.environ if n.startswith(("OPENOUTFIND_", "OUTSEND_"))]:
        monkeypatch.delenv(name)


class TestTheExport:
    """One answer, two vocabularies — the only thing that knows both names."""

    def test_a_shared_answer_reaches_both_children_under_their_own_names(self, configured):
        environment = configured.export()

        assert environment["OPENOUTFIND_PRODUCT_DOCS"] == ANSWERS["product_docs"]
        assert environment["OUTSEND_PRODUCT_DOCS"] == ANSWERS["product_docs"]
        assert environment["OPENOUTFIND_LLM_API_KEY"] == environment["OUTSEND_LLM_API_KEY"]

    def test_the_suffixes_are_not_assumed_to_match(self, configured):
        """`COUNTRY` against a sender that asks nothing about jurisdiction, and
        `OPERATOR_NAME` against a finder that signs nothing."""
        environment = configured.export()

        assert environment["OPENOUTFIND_COUNTRY"] == "us"
        assert "OUTSEND_COUNTRY" not in environment
        assert environment["OUTSEND_OPERATOR_NAME"] == "Ada Lovelace"
        assert "OPENOUTFIND_OPERATOR_NAME" not in environment

    def test_a_blank_field_exports_nothing_rather_than_an_empty_value(self, configured):
        """Unset means *use your default* to a child; blank would override it with
        nothing — the sender's SMTP host is the case that bites."""
        environment = configured.export()

        assert "OUTSEND_SMTP_HOST" not in environment
        assert "OUTSEND_BOOKING_LINK" not in environment

    def test_the_two_gates_export_as_words_the_children_accept(self, configured):
        environment = configured.export()

        assert environment["OPENOUTFIND_ACCEPT_LEGAL_NOTICE"] == "true"
        assert environment["OPENOUTFIND_NEWSLETTER"] == "false"


class TestApplyingIt:
    def test_the_answers_land_in_the_environment(self, configured):
        wizard.apply_to_environment(configured)

        import os
        assert os.environ["OPENOUTFIND_BETTERCONTACT_API_KEY"] == "bc-not-a-real-key"
        assert os.environ["OUTSEND_MAILBOX_PASSWORD"] == "app-password"

    def test_an_explicit_export_beats_the_stored_answer(self, configured, monkeypatch):
        """A variable set for this run was set on purpose; a stored answer quietly
        reverting it is the failure the children's own seeding rule prevents."""
        monkeypatch.setenv("OUTSEND_PRODUCT_DOCS", "What the unit file says.")

        wizard.apply_to_environment(configured)

        import os
        assert os.environ["OUTSEND_PRODUCT_DOCS"] == "What the unit file says."
        assert os.environ["OPENOUTFIND_PRODUCT_DOCS"] == ANSWERS["product_docs"]


class TestWhatItAsksFor:
    def test_a_configured_install_is_asked_nothing(self, configured, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr(wizard, "_ask", _refuse)
        monkeypatch.setattr(wizard, "_ask_secret", _refuse)

        wizard._ask_what_is_missing(configured)  # must not prompt

    def test_a_headless_install_is_told_which_variables_would_answer(self, db, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        with pytest.raises(SystemExit) as raised:
            wizard._ask_what_is_missing(SiteConfig.load())

        message = str(raised.value)
        assert "OPENOUTFIND_PRODUCT_DOCS" in message
        assert "OUTSEND_MAILBOX_ADDRESS" in message
        assert "OPENOUTFIND_ACCEPT_LEGAL_NOTICE" in message

    def test_a_variable_already_exported_is_not_asked_for_again(self, db, monkeypatch):
        """Being told is being told, whichever way round it happened."""
        config = SiteConfig.load()
        for field, value in ANSWERS.items():
            if field != "bettercontact_api_key":
                setattr(config, field, value)
        monkeypatch.setenv("OPENOUTFIND_BETTERCONTACT_API_KEY", "from-the-unit-file")
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        wizard._ask_what_is_missing(config)  # must not raise

    def test_the_legal_notice_is_not_carried_by_a_row_that_never_accepted_it(self, db, monkeypatch):
        config = SiteConfig.load()
        for field, value in ANSWERS.items():
            setattr(config, field, value)
        config.accepted_legal_notice = False
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        with pytest.raises(SystemExit, match="ACCEPT_LEGAL_NOTICE"):
            wizard._ask_what_is_missing(config)


def _refuse(*args, **kwargs):
    raise AssertionError("asked a question this install had already answered")
