# tests/test_mutate.py
"""LLM query-mutation generator — the swappable interface and its inputs.

The actual LLM call is not exercised (as with the ICP generator, the model is
mocked at the boundary); these cover the past-query summary, the swap hook, and
the failure fallback that keeps a failed mutation from losing a landed fetch."""
from openoutreach.core.models import Campaign, DiscoveryQuery
from openoutreach.core.pipeline import mutate
from openoutreach.core.pipeline.frontier import params_hash

Status = DiscoveryQuery.Status


def _campaign():
    return Campaign.objects.create(name="C", product_docs="widgets", campaign_target="demos")


def _node(c, params, status=Status.FETCHED, score=None):
    return DiscoveryQuery.objects.create(
        campaign=c, params=params, params_hash=params_hash(params),
        offset=0, status=status, score=score,
    )


class TestPastQueryStats:
    def test_excludes_pending_and_reports_value(self, db):
        c = _campaign()
        _node(c, {"a": 1}, status=Status.FETCHED, score=3)
        _node(c, {"b": 1}, status=Status.PENDING)  # not yet measured → excluded
        stats = mutate._past_query_stats(c)
        assert len(stats) == 1
        assert stats[0]["params"] == {"a": 1} and stats[0]["score"] == 3


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
