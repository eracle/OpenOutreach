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


def _node(c, params, offset=0):
    return DiscoveryQuery.objects.create(
        campaign=c, params=params, params_hash=params_hash(params), offset=offset,
    )


class TestPastQueryStats:
    def test_reports_every_fetched_node_with_its_counts(self, db):
        from openoutreach.crm.models import Deal, DealState, Lead, Outcome

        c = _campaign()
        paid = _node(c, {"a": 1})
        for tag, state, outcome in (("a1", DealState.QUALIFIED, ""),
                                    ("a2", DealState.FAILED, Outcome.WRONG_FIT)):
            lead = Lead.objects.create(profile_url=f"https://x/{tag}/", discovered_by=paid)
            Deal.objects.create(lead=lead, campaign=c, state=state, outcome=outcome)
        unseen = _node(c, {"b": 1}, offset=100)
        Lead.objects.create(profile_url="https://x/b1/", discovered_by=unseen)

        stats = {tuple(s["params"].items())[0][0]: s for s in mutate._past_query_stats(c)}
        assert len(stats) == 2
        assert (stats["a"]["n_leads"], stats["a"]["examined"], stats["a"]["qualified"]) == (2, 2, 1)
        # discovered but never ruled on — the LLM must see 0 examined, not 0 qualified
        assert (stats["b"]["n_leads"], stats["b"]["examined"], stats["b"]["qualified"]) == (1, 0, 0)


class TestFilterSchema:
    """The typed output that keeps the LLM from inventing a silently-dead query.

    Two distinct failures, and only one of them is loud. An unknown *key* is
    **silently dropped** — the query comes back as if it were never sent, i.e. the
    unfiltered page, with rows — so an inert family reads as *success* and the model
    keeps steering with a knob wired to nothing. An unknown *value* returns an empty
    page, which the frontier reads as end-of-depth and correctly retires.

    So the schema's job is to admit only families proven to steer (verified live
    against a baseline with an absurd-value control, 2026-07-16), and to pin the one
    genuinely closed vocabulary. Values it must not police — a search miss is normal.
    """

    def test_rejects_seniority_outside_lead_finders_vocabulary(self):
        # "other" is what both prompts used to advertise; Lead Finder matches
        # nothing for it and says so with an empty page, not an error.
        with pytest.raises(ValidationError):
            mutate._Filters(lead_seniority={"include": ["other"]})

    def test_rejects_empty_include_list(self):
        with pytest.raises(ValidationError):
            mutate._Filters(lead_location={"include": []})

    @pytest.mark.parametrize("family", ["lead_industry", "company_technology", "lead_skills"])
    def test_rejects_families_that_do_not_steer(self, family):
        # Probed live 2026-07-16 against an unfiltered baseline with an absurd-value
        # control: each returned the *baseline page* for a value nothing could match,
        # so the filter is dropped, not applied. An inert family is an identity
        # element — it makes the LLM believe it is steering while producing a node
        # indistinguishable from its parent. Unrepresentable is the only safe state.
        with pytest.raises(ValidationError):
            mutate._Filters(**{family: {"include": ["anything"]}})

    def test_accepts_the_families_verified_to_steer(self):
        filters = mutate._Filters(
            lead_seniority={"include": ["mid-level"]},
            lead_location={"include": ["Germany"]},
        )
        assert filters.model_dump(exclude_none=True) == {
            "lead_seniority": {"include": ["mid-level"]},
            "lead_location": {"include": ["Germany"]},
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
