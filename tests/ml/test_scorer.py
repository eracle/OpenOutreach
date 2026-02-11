# tests/ml/test_scorer.py
import os
import tempfile
from unittest.mock import patch

import numpy as np
import pytest

from linkedin.ml.scorer import ProfileScorer, ANALYTICS_DB


def _make_profile(degree=2, n_positions=3, n_educations=1, summary="Bio", headline="Engineer"):
    return {
        "profile": {
            "connection_degree": degree,
            "positions": [{"company_name": f"Co{i}"} for i in range(n_positions)],
            "educations": [{"school_name": f"Uni{i}"} for i in range(n_educations)],
            "summary": summary,
            "headline": headline,
        },
        "public_identifier": "test-user",
        "url": "https://www.linkedin.com/in/test-user/",
    }


class TestScorerNoData:
    def test_train_returns_false_no_db(self, tmp_path):
        with patch("linkedin.ml.scorer.ANALYTICS_DB", tmp_path / "nonexistent.duckdb"):
            scorer = ProfileScorer(seed=42)
            assert scorer.train() is False

    def test_train_returns_false_few_rows(self, tmp_path):
        import duckdb

        db_path = tmp_path / "analytics.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("""
            CREATE TABLE ml_connection_accepted (
                connection_degree INT,
                num_positions INT,
                num_educations INT,
                has_summary INT,
                headline_length INT,
                accepted INT
            )
        """)
        for i in range(5):
            con.execute(
                "INSERT INTO ml_connection_accepted VALUES (?, ?, ?, ?, ?, ?)",
                [2, 3, 1, 1, 20, i % 2],
            )
        con.close()

        with patch("linkedin.ml.scorer.ANALYTICS_DB", db_path):
            scorer = ProfileScorer(seed=42)
            assert scorer.train() is False

    def test_train_returns_false_single_class(self, tmp_path):
        import duckdb

        db_path = tmp_path / "analytics.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("""
            CREATE TABLE ml_connection_accepted (
                connection_degree INT,
                num_positions INT,
                num_educations INT,
                has_summary INT,
                headline_length INT,
                accepted INT
            )
        """)
        for i in range(20):
            con.execute(
                "INSERT INTO ml_connection_accepted VALUES (?, ?, ?, ?, ?, ?)",
                [2, 3, 1, 1, 20, 1],  # all accepted
            )
        con.close()

        with patch("linkedin.ml.scorer.ANALYTICS_DB", db_path):
            scorer = ProfileScorer(seed=42)
            assert scorer.train() is False


class TestScorerTrained:
    @pytest.fixture
    def trained_scorer(self, tmp_path):
        import duckdb

        db_path = tmp_path / "analytics.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("""
            CREATE TABLE ml_connection_accepted (
                connection_degree INT,
                num_positions INT,
                num_educations INT,
                has_summary INT,
                headline_length INT,
                accepted INT
            )
        """)
        for i in range(50):
            con.execute(
                "INSERT INTO ml_connection_accepted VALUES (?, ?, ?, ?, ?, ?)",
                [i % 3 + 1, i % 5 + 1, i % 3, i % 2, 10 + i, i % 2],
            )
        con.close()

        with patch("linkedin.ml.scorer.ANALYTICS_DB", db_path):
            scorer = ProfileScorer(seed=42)
            assert scorer.train() is True
        return scorer

    def test_score_profiles_returns_ranked_list(self, trained_scorer):
        profiles = [
            _make_profile(degree=1, n_positions=5, summary="Long bio"),
            _make_profile(degree=3, n_positions=1, summary=""),
            _make_profile(degree=2, n_positions=3, summary="Short"),
        ]
        ranked = trained_scorer.score_profiles(profiles)
        assert len(ranked) == 3
        assert all("public_identifier" in p for p in ranked)

    def test_untrained_scorer_returns_fifo(self):
        scorer = ProfileScorer(seed=42)
        profiles = [
            _make_profile(degree=1),
            _make_profile(degree=2),
            _make_profile(degree=3),
        ]
        result = scorer.score_profiles(profiles)
        assert result == profiles

    def test_same_seed_same_ranking(self, tmp_path):
        import duckdb

        db_path = tmp_path / "analytics.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("""
            CREATE TABLE ml_connection_accepted (
                connection_degree INT,
                num_positions INT,
                num_educations INT,
                has_summary INT,
                headline_length INT,
                accepted INT
            )
        """)
        for i in range(50):
            con.execute(
                "INSERT INTO ml_connection_accepted VALUES (?, ?, ?, ?, ?, ?)",
                [i % 3 + 1, i % 5 + 1, i % 3, i % 2, 10 + i, i % 2],
            )
        con.close()

        profiles = [
            _make_profile(degree=1, n_positions=5),
            _make_profile(degree=3, n_positions=1),
            _make_profile(degree=2, n_positions=3),
        ]

        with patch("linkedin.ml.scorer.ANALYTICS_DB", db_path):
            scorer1 = ProfileScorer(seed=42)
            scorer1.train()
            ranking1 = [p["profile"]["connection_degree"] for p in scorer1.score_profiles(profiles)]

            scorer2 = ProfileScorer(seed=42)
            scorer2.train()
            ranking2 = [p["profile"]["connection_degree"] for p in scorer2.score_profiles(profiles)]

        assert ranking1 == ranking2

    def test_explain_profile_outputs_contributions(self, trained_scorer):
        profile = _make_profile(degree=2, n_positions=3)
        explanation = trained_scorer.explain_profile(profile)
        assert "Feature contributions" in explanation
        assert "connection_degree" in explanation
        assert "num_positions" in explanation

    def test_explain_profile_untrained(self):
        scorer = ProfileScorer(seed=42)
        profile = _make_profile()
        explanation = scorer.explain_profile(profile)
        assert "not trained" in explanation.lower()
