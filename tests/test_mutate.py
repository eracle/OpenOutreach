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
from openoutreach.core.pipeline.frontier import _clauses_for, clause_key


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


def _node(c, clauses, offset=0):
    node = DiscoveryQuery.objects.create(
        campaign=c, clause_key=clause_key(clauses), offset=offset,
    )
    node.clauses.set(_clauses_for(clauses))
    return node


class TestPastQueryStats:
    def test_reports_every_fetched_node_with_its_counts(self, db):
        from openoutreach.crm.models import Deal, DealState, Lead, Outcome

        c = _campaign()
        paid = _node(c, [("lead_job_title", "Founder")])
        for tag, state, outcome in (("a1", DealState.QUALIFIED, ""),
                                    ("a2", DealState.FAILED, Outcome.WRONG_FIT)):
            lead = Lead.objects.create(profile_url=f"https://x/{tag}/", discovered_by=paid)
            Deal.objects.create(lead=lead, campaign=c, state=state, outcome=outcome)
        unseen = _node(c, [("lead_location", "Japan")], offset=100)
        Lead.objects.create(profile_url="https://x/b1/", discovered_by=unseen)

        stats = {s["query"]: s for s in mutate._past_query_stats(c)}
        assert len(stats) == 2
        founder, japan = stats["job_title Founder"], stats["location Japan"]
        assert (founder["n_leads"], founder["examined"], founder["qualified"]) == (2, 2, 1)
        # discovered but never ruled on — the LLM must see 0 examined, not 0 qualified
        assert (japan["n_leads"], japan["examined"], japan["qualified"]) == (1, 0, 0)


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
            mutate._Filters(lead_seniority="other")

    def test_rejects_an_include_list_where_one_value_belongs(self):
        # An OR is strictly dominated: five titles in one query share a single
        # ~10k-row window that five separate queries would each get in full, for
        # free. The schema is what makes it unrepresentable.
        with pytest.raises(ValidationError):
            mutate._Filters(lead_location=["Germany", "Italy"])

    @pytest.mark.parametrize("family", ["lead_industry", "company_technology", "lead_skills"])
    def test_rejects_families_that_do_not_steer(self, family):
        # Probed live 2026-07-16 against an unfiltered baseline with an absurd-value
        # control: each returned the *baseline page* for a value nothing could match,
        # so the filter is dropped, not applied. An inert family is an identity
        # element — it makes the LLM believe it is steering while producing a node
        # indistinguishable from its parent. Unrepresentable is the only safe state.
        with pytest.raises(ValidationError):
            mutate._Filters(**{family: "anything"})

    def test_accepts_the_families_verified_to_steer(self):
        filters = mutate._Filters(lead_seniority="mid-level", lead_location="Germany")
        assert filters.model_dump(exclude_none=True) == {
            "lead_seniority": "mid-level",
            "lead_location": "Germany",
        }

    def test_department_and_function_are_free_text_not_enums(self):
        # Lead Finder matches these against a search index, so their published
        # "enum" is not binding — plain labels are what actually match. A value
        # that matches nothing just returns an empty page, as for any free-text
        # family, so the schema must not constrain them.
        filters = mutate._Filters(lead_department="Sales", lead_function="Human Resources")
        assert filters.model_dump(exclude_none=True) == {
            "lead_department": "Sales",
            "lead_function": "Human Resources",
        }

    def test_unset_families_drop_out(self):
        filters = mutate._Filters(lead_location="Italy")
        assert filters.model_dump(exclude_none=True) == {"lead_location": "Italy"}

    def test_all_unset_degrades_to_empty_meaning_llm_is_dry(self):
        # frontier.next_query treats an empty clause set as "no new region" → None.
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
            mutate.set_generator(lambda campaign: [("lead_location", "Italy")])
            assert mutate.generate_mutation(c) == [("lead_location", "Italy")]
        finally:
            mutate.set_generator(original)

    def test_failure_degrades_to_empty(self, db):
        c = _campaign()
        original = mutate._generator
        try:
            def _boom(campaign):
                raise RuntimeError("llm down")
            mutate.set_generator(_boom)
            assert mutate.generate_mutation(c) == []
        finally:
            mutate.set_generator(original)
