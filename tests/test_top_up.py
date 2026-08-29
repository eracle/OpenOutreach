# tests/test_top_up.py
"""`top_up` narrates which regime it is in — cold phase, explore, or exploit —
because it is exactly the question an operator watching the run asks."""
from unittest.mock import patch

import numpy as np
import pytest

from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.top_up import top_up


@pytest.mark.django_db
def test_the_cold_phase_names_itself_and_the_anchor_progress(campaign, caplog):
    qualifier = BayesianQualifier(embedding_dim=8)
    qualifier.set_anchors(np.random.RandomState(0).rand(3, 8))

    with (
        patch("openoutreach.core.pipeline.top_up.discover", return_value=False),
        patch("openoutreach.core.pipeline.top_up.fetch_qualification_candidates",
              return_value=[]),
        caplog.at_level("INFO"),
    ):
        top_up(campaign, qualifier)

    assert "cold phase" in caplog.text
    assert "0/3 real positive" in caplog.text


def _exploiting_qualifier():
    """A fitted, anchor-free qualifier whose negatives outnumber its positives."""
    qualifier = BayesianQualifier(embedding_dim=8)
    rng = np.random.RandomState(0)
    qualifier.warm_start(rng.rand(7, 8), np.array([1, 1, 1, 0, 0, 0, 0]))
    return qualifier


class _Candidate:
    def __init__(self):
        self.embedding_array = np.zeros(8, dtype=np.float64)


@pytest.mark.django_db
def test_exploit_qualifies_only_a_lead_clearing_the_spend_gate(campaign):
    qualifier = _exploiting_qualifier()

    with (
        patch.object(qualifier, "predict_probs", return_value=np.array([0.95])),
        patch("openoutreach.core.pipeline.top_up.fetch_qualification_candidates",
              return_value=[_Candidate()]),
        patch("openoutreach.core.pipeline.top_up.run_qualification",
              return_value="https://example.com/in/alice/") as qualify,
        patch("openoutreach.core.pipeline.top_up.discover") as discover,
    ):
        assert top_up(campaign, qualifier) is True

    assert qualify.called
    assert not discover.called


@pytest.mark.django_db
def test_exploit_falls_to_the_informative_lead_below_the_spend_gate(campaign):
    """Below the spend gate is not "nothing to do" — it is "the model cannot tell these
    apart yet", which is a reason to *label*, not to widen.

    This is the 14h33m failure. On a live campaign with 3 real positives the whole
    26,737-lead pool topped out at P=0.37, so exploit cleared nobody and discovered every
    pass; discovery labels nothing, so the posterior that would open the gate never moved.
    295 pages, 19 verdicts, 0 addresses, ended by the operator."""
    qualifier = _exploiting_qualifier()

    with (
        patch.object(qualifier, "predict_probs", return_value=np.array([0.37])),
        patch.object(qualifier, "compute_bald", return_value=np.array([0.05])),
        patch("openoutreach.core.pipeline.top_up.fetch_qualification_candidates",
              return_value=[_Candidate()]),
        patch("openoutreach.core.pipeline.top_up.run_qualification",
              return_value="https://example.com/in/alice/") as qualify,
        patch("openoutreach.core.pipeline.top_up.discover", return_value=True) as discover,
    ):
        assert top_up(campaign, qualifier) is True

    assert qualify.called
    assert not discover.called


@pytest.mark.django_db
def test_exploit_discovers_when_the_pool_teaches_nothing_either(campaign):
    """Both arms shut is the one honest reason to widen: the model will not pay for these
    leads *and* cannot learn from them, so the pool really is redundant.

    The floor has to be an absolute one for this branch to exist at all — a quantile can
    never be empty, so discovery would never run again."""
    qualifier = _exploiting_qualifier()

    with (
        patch.object(qualifier, "predict_probs", return_value=np.array([0.5])),
        patch.object(qualifier, "compute_bald", return_value=np.array([0.001])),
        patch("openoutreach.core.pipeline.top_up.fetch_qualification_candidates",
              return_value=[_Candidate()]),
        patch("openoutreach.core.pipeline.top_up.run_qualification") as qualify,
        patch("openoutreach.core.pipeline.top_up.discover", return_value=True) as discover,
    ):
        assert top_up(campaign, qualifier) is True

    assert not qualify.called
    assert discover.called


@pytest.mark.django_db
def test_explore_counts_a_page_of_familiar_profiles_as_work(campaign):
    """A fired page is the unit of work, whatever fraction of it was new.

    A live run ended `goal_unreached` on this shape: the page came back 100 rows all
    already ours, so it left no candidate to label — which is not the same fact as a
    spanned frontier, and must not stop the job with the walk one node in."""
    qualifier = BayesianQualifier(embedding_dim=8)
    rng = np.random.RandomState(0)
    qualifier.warm_start(rng.rand(7, 8), np.array([1, 1, 1, 1, 0, 0, 0]))
    assert qualifier.acquisition_mode() != "exploit (p)"

    with (
        patch("openoutreach.core.pipeline.top_up.fetch_qualification_candidates",
              return_value=[]),
        patch("openoutreach.core.pipeline.top_up.run_qualification") as qualify,
        patch("openoutreach.core.pipeline.top_up.discover", return_value=True),
    ):
        assert top_up(campaign, qualifier) is True

    assert not qualify.called
