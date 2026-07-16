# tests/test_mutate.py
"""LLM query-mutation generator — the swappable interface and its inputs.

The actual LLM call is not exercised (as with the ICP generator, the model is
mocked at the boundary); these cover the past-query summary, the swap hook, and
the failure fallback that keeps a failed mutation from losing a landed fetch."""
import pytest
from pydantic import ValidationError

from openoutreach import discovery
from openoutreach.core.models import Campaign, DiscoveryQuery
from openoutreach.core.pipeline import mutate
from openoutreach.core.pipeline.frontier import params_hash


def _enums_in(schema) -> list[list]:
    """Every `enum` list anywhere in a JSON schema, at any nesting depth."""
    found = []
    if isinstance(schema, dict):
        if "enum" in schema:
            found.append(schema["enum"])
        for value in schema.values():
            found += _enums_in(value)
    elif isinstance(schema, list):
        for value in schema:
            found += _enums_in(value)
    return found


def _campaign():
    return Campaign.objects.create(name="C", product_docs="widgets", campaign_target="demos")


def _node(c, params, offset=0, score=None):
    return DiscoveryQuery.objects.create(
        campaign=c, params=params, params_hash=params_hash(params),
        offset=offset, score=score,
    )


class TestPastQueryStats:
    def test_reports_every_fetched_node_with_value(self, db):
        c = _campaign()
        _node(c, {"a": 1}, score=3)
        _node(c, {"b": 1}, offset=100, score=0)
        stats = mutate._past_query_stats(c)
        assert {s["params"]["a"] if "a" in s["params"] else s["params"]["b"] for s in stats} == {1}
        assert {s["score"] for s in stats} == {3, 0}
        assert len(stats) == 2


class TestFilterSchema:
    """The typed output that keeps the LLM from inventing a silently-dead query.

    Lead Finder answers an unknown filter key/value with an empty page rather than
    an error, and the frontier reads an empty page as end-of-depth — so an
    unconstrained filter dict would let one hallucinated value mark a healthy query
    line exhausted. These lock the constraint at the schema, not the prompt.
    """

    def test_rejects_seniority_outside_lead_finders_vocabulary(self):
        # "other" is what both prompts used to advertise; Lead Finder matches
        # nothing for it and says so with an empty page, not an error.
        with pytest.raises(ValidationError):
            mutate._Filters(lead_seniority={"include": ["other"]})

    def test_rejects_empty_include_list(self):
        with pytest.raises(ValidationError):
            mutate._Filters(lead_industry={"include": []})

    def test_accepts_the_probe_cleared_params(self):
        filters = mutate._Filters(
            lead_seniority={"include": ["mid-level"]},
            company_technology={"include": ["salesforce"]},
            lead_skills={"include": ["negotiation"]},
        )
        assert filters.model_dump(exclude_none=True) == {
            "lead_seniority": {"include": ["mid-level"]},
            "company_technology": {"include": ["salesforce"]},
            "lead_skills": {"include": ["negotiation"]},
        }

    def test_department_and_function_are_free_text_not_enums(self):
        # Lead Finder matches these against a search index, so their published
        # "enum" is not binding — plain labels are what actually match. A value
        # that matches nothing just returns an empty page, as for any free-text
        # family, so the schema must not constrain them.
        filters = mutate._Filters(
            lead_department={"include": ["Sales"]},
            lead_function={"include": ["Human Resources"]},
        )
        assert filters.model_dump(exclude_none=True) == {
            "lead_department": {"include": ["Sales"]},
            "lead_function": {"include": ["Human Resources"]},
        }

    def test_unset_families_drop_out(self):
        filters = mutate._Filters(lead_location={"include": ["Italy"]})
        assert filters.model_dump(exclude_none=True) == {"lead_location": {"include": ["Italy"]}}

    def test_all_unset_degrades_to_empty_meaning_llm_is_dry(self):
        # frontier.next_query treats {} as "no new region" and returns None.
        assert mutate._Filters().model_dump(exclude_none=True) == {}

    def test_seniority_vocabulary_reaches_the_model_json_schema(self):
        # Assert on the emitted enum itself, not a substring of the whole schema —
        # prose in a docstring lands in the schema's descriptions and would make a
        # naive text search lie.
        enums = _enums_in(mutate._FilterSet.model_json_schema())
        assert list(discovery.LEAD_SENIORITIES) in enums
        assert not any("other" in e for e in enums)


class TestGeneratorInterface:
    def test_generate_delegates_to_active_generator(self, db):
        c = _campaign()
        original = mutate._generator
        try:
            mutate.set_generator(lambda campaign: {"x": 1})
            assert mutate.generate_mutation(c) == {"x": 1}
        finally:
            mutate.set_generator(original)

    def test_failure_degrades_to_empty(self, db):
        c = _campaign()
        original = mutate._generator
        try:
            def _boom(campaign):
                raise RuntimeError("llm down")
            mutate.set_generator(_boom)
            assert mutate.generate_mutation(c) == {}
        finally:
            mutate.set_generator(original)
