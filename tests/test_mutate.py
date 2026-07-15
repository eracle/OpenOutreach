# tests/test_mutate.py
"""LLM query-mutation generator — the swappable interface and its inputs.

The actual LLM call is not exercised (as with the ICP generator, the model is
mocked at the boundary); these cover the past-query summary, the swap hook, and
the failure fallback that keeps a failed mutation from losing a landed fetch."""
from openoutreach.core.models import Campaign, DiscoveryQuery
from openoutreach.core.pipeline import mutate
from openoutreach.core.pipeline.frontier import params_hash


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
