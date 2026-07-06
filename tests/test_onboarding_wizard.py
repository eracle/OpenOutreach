# tests/test_onboarding_wizard.py
"""Regression guard for the wizard's Question subclasses.

A Question subclass that overrides a dataclass field default (``default`` /
``required``) must be re-decorated with ``@dataclass`` — else it inherits
Question's generated __init__ and the override is silently dropped, which once
made every Confirm come out ``required=True`` (the "can't answer no, loops back
to the start" onboarding bug).
"""
import dataclasses

from openoutreach.core.onboarding_wizard import Confirm, IntText, Question


def test_confirm_defaults_to_optional():
    # Confirm overrides required=False; a plain yes/no must be answerable "no".
    assert Confirm("k", "m").required is False
    assert Confirm("k", "m", default=False).required is False


def test_inttext_defaults_to_optional():
    assert IntText("k", "m", default=587).required is False


def test_subclasses_overriding_field_defaults_regenerate_init():
    """Any subclass that redeclares a Question field must own its __init__."""
    question_fields = {f.name for f in dataclasses.fields(Question)}
    for cls in Question.__subclasses__():
        redeclares = question_fields & set(getattr(cls, "__annotations__", {}))
        if redeclares:
            assert "__init__" in cls.__dict__, (
                f"{cls.__name__} redeclares {redeclares} but isn't @dataclass — "
                "its field-default overrides are silently ignored."
            )
