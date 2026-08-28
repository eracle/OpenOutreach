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
        patch("openoutreach.core.pipeline.top_up.discover", return_value=0),
        patch("openoutreach.core.pipeline.top_up.fetch_qualification_candidates",
              return_value=[]),
        caplog.at_level("INFO"),
    ):
        top_up(campaign, qualifier)

    assert "cold phase" in caplog.text
    assert "0/3 real positive" in caplog.text
