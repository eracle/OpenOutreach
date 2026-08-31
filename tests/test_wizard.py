"""The three gaps between the two installs — the whole of what the wizard adds.

The questions themselves belong to the children and are tested there. What is tested here
is only what neither child can do alone: carry one set of answers across two singletons,
and name the operator that the finder creates and the sender then skips.
"""
import pytest
from cold_outreach.core.models import SiteConfig as SenderConfig
from cold_outreach.errors import OutsendError
from django.contrib.auth.models import User
from openoutfind.core.models import SiteConfig as FinderConfig

from openoutreach import wizard


@pytest.fixture
def finder_config(db):
    config = FinderConfig.load()
    config.product_docs = "A lead finder for small B2B teams."
    config.campaign_target = "Founders selling to other founders."
    config.ai_model = "anthropic:claude-sonnet-4-5-20250929"
    config.llm_api_key = "sk-not-a-real-key"
    config.save()
    return config


def test_the_shared_fields_reach_the_sender(finder_config):
    wizard._seed_sender_config()

    sender = SenderConfig.load()
    assert sender.product_docs == finder_config.product_docs
    assert sender.campaign_target == finder_config.campaign_target
    assert sender.ai_model == finder_config.ai_model
    assert sender.llm_api_key == finder_config.llm_api_key


def test_an_answer_the_sender_already_holds_is_not_overwritten(finder_config):
    """The rule both children already follow: the environment seeds, it never reverts."""
    sender = SenderConfig.load()
    sender.campaign_target = "Whoever this install decided on, on its own side."
    sender.save()

    wizard._seed_sender_config()

    assert SenderConfig.load().campaign_target == "Whoever this install decided on, on its own side."


def test_the_environment_wins_over_the_finders_answer(finder_config, monkeypatch):
    monkeypatch.setenv("OUTSEND_PRODUCT_DOCS", "What the unit file says.")

    wizard._seed_sender_config()

    assert SenderConfig.load().product_docs == "What the unit file says."


def test_the_operator_gets_a_name_from_the_environment(db, monkeypatch):
    """The finder makes the user out of an email address, so `first_name` is empty."""
    User.objects.create(username="ada", email="ada@example.com", is_staff=True, is_active=True)
    monkeypatch.setenv("OUTSEND_OPERATOR_NAME", "Ada Lovelace")

    wizard._name_the_operator()

    operator = User.objects.get(username="ada")
    assert (operator.first_name, operator.last_name) == ("Ada", "Lovelace")
    assert operator.email == "ada@example.com"


def test_a_nameless_headless_operator_is_named_as_the_missing_variable(db, monkeypatch):
    User.objects.create(username="ada", email="ada@example.com", is_staff=True, is_active=True)
    monkeypatch.delenv("OUTSEND_OPERATOR_NAME", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)

    with pytest.raises(OutsendError, match="OUTSEND_OPERATOR_NAME"):
        wizard._name_the_operator()


def test_an_operator_who_already_has_a_name_is_left_alone(db, monkeypatch):
    User.objects.create(username="ada", first_name="Ada", is_staff=True, is_active=True)
    monkeypatch.setenv("OUTSEND_OPERATOR_NAME", "Somebody Else")

    wizard._name_the_operator()

    assert User.objects.get(username="ada").first_name == "Ada"
