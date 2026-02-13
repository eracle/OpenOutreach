# tests/ml/test_scorer.py
from unittest.mock import patch

import numpy as np
import pytest
import yaml

from linkedin.ml.scorer import ProfileScorer, ANALYTICS_DB, MECHANICAL_FEATURES


# All 24 mechanical columns + profile_text + accepted
_ALL_COLUMNS = list(MECHANICAL_FEATURES) + ["profile_text", "accepted"]

_CREATE_TABLE_SQL = (
    "CREATE TABLE ml_connection_accepted (\n"
    + ",\n".join(
        f"  {col} {'VARCHAR' if col == 'profile_text' else 'FLOAT'}"
        for col in _ALL_COLUMNS
    )
    + "\n)"
)


def _make_profile(
    degree=2,
    n_positions=3,
    n_educations=1,
    summary="Bio",
    headline="Engineer",
    location="San Francisco",
    industry_name="Technology",
    geo_name="San Francisco Bay Area",
    company_name="Acme",
    with_dates=False,
):
    positions = []
    for i in range(n_positions):
        pos = {"company_name": f"Co{i}", "title": f"Role{i}", "location": f"City{i}", "description": f"Did stuff at Co{i}"}
        if with_dates:
            pos["start_year"] = 2015 + i
            pos["start_month"] = 1
            if i < n_positions - 1:
                pos["end_year"] = 2016 + i
                pos["end_month"] = 12
        positions.append(pos)

    educations = []
    for i in range(n_educations):
        educations.append({
            "school_name": f"Uni{i}",
            "degree": "BS" if i == 0 else "MS",
            "field_of_study": "Computer Science",
        })

    return {
        "profile": {
            "connection_degree": degree,
            "positions": positions,
            "educations": educations,
            "summary": summary,
            "headline": headline,
            "location_name": location,
            "company_name": company_name,
            "industry": {"name": industry_name},
            "geo": {"defaultLocalizedNameWithoutCountryName": geo_name},
        },
        "public_identifier": "test-user",
        "url": "https://www.linkedin.com/in/test-user/",
    }


def _insert_row(con, row_values):
    """Insert a row with values for all mechanical features + profile_text + accepted."""
    placeholders = ", ".join(["?"] * len(_ALL_COLUMNS))
    con.execute(f"INSERT INTO ml_connection_accepted VALUES ({placeholders})", row_values)


def _make_row(accepted, degree=2, n_positions=3, profile_text="engineer at company"):
    """Build a row tuple with reasonable defaults for all 24 mech features + profile_text + accepted."""
    return [
        degree,           # connection_degree
        n_positions,      # num_positions
        1,                # num_educations
        1,                # has_summary
        20,               # headline_length
        50,               # summary_length
        1,                # has_industry
        1,                # has_geo
        1,                # has_location
        1,                # has_company
        n_positions,      # num_distinct_companies
        0.5,              # positions_with_description_ratio
        n_positions,      # num_position_locations
        100,              # total_description_length
        1,                # has_position_descriptions
        1,                # is_currently_employed
        15.0,             # avg_title_length
        5.0,              # years_experience
        24.0,             # current_position_tenure_months
        18.0,             # avg_position_tenure_months
        36.0,             # longest_tenure_months
        1,                # has_education_degree
        1,                # has_field_of_study
        0,                # has_multiple_degrees
        profile_text,     # profile_text
        accepted,         # accepted
    ]


def _make_keywords_file(tmp_path, positive=None, negative=None, exploratory=None):
    """Write a keywords YAML file and return its path."""
    kw_file = tmp_path / "keywords.yaml"
    data = {
        "positive": positive or [],
        "negative": negative or [],
        "exploratory": exploratory or [],
    }
    kw_file.write_text(yaml.dump(data))
    return kw_file


class TestScorerNoData:
    def test_train_returns_false_no_db(self, tmp_path):
        kw_file = tmp_path / "empty_kw.yaml"
        with patch("linkedin.ml.scorer.ANALYTICS_DB", tmp_path / "nonexistent.duckdb"):
            scorer = ProfileScorer(seed=42, keywords_path=kw_file)
            assert scorer.train() is False

    def test_train_returns_false_few_rows(self, tmp_path):
        import duckdb

        db_path = tmp_path / "analytics.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(_CREATE_TABLE_SQL)
        for i in range(5):
            _insert_row(con, _make_row(accepted=i % 2))
        con.close()

        with patch("linkedin.ml.scorer.ANALYTICS_DB", db_path):
            scorer = ProfileScorer(seed=42, keywords_path=tmp_path / "empty_kw.yaml")
            assert scorer.train() is False

    def test_train_returns_false_single_class(self, tmp_path):
        import duckdb

        db_path = tmp_path / "analytics.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(_CREATE_TABLE_SQL)
        for i in range(20):
            _insert_row(con, _make_row(accepted=1))
        con.close()

        with patch("linkedin.ml.scorer.ANALYTICS_DB", db_path):
            scorer = ProfileScorer(seed=42, keywords_path=tmp_path / "empty_kw.yaml")
            assert scorer.train() is False


class TestScorerTrained:
    @pytest.fixture
    def trained_scorer(self, tmp_path):
        import duckdb

        db_path = tmp_path / "analytics.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(_CREATE_TABLE_SQL)
        for i in range(50):
            _insert_row(con, _make_row(
                accepted=i % 2,
                degree=i % 3 + 1,
                n_positions=i % 5 + 1,
                profile_text=f"engineer manager {'ml' if i % 2 else 'student'}",
            ))
        con.close()

        with patch("linkedin.ml.scorer.ANALYTICS_DB", db_path):
            scorer = ProfileScorer(seed=42, keywords_path=tmp_path / "empty_kw.yaml")
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

    def test_untrained_scorer_returns_fifo(self, tmp_path):
        scorer = ProfileScorer(seed=42, keywords_path=tmp_path / "empty_kw.yaml")
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
        con.execute(_CREATE_TABLE_SQL)
        for i in range(50):
            _insert_row(con, _make_row(
                accepted=i % 2,
                degree=i % 3 + 1,
                n_positions=i % 5 + 1,
            ))
        con.close()

        profiles = [
            _make_profile(degree=1, n_positions=5),
            _make_profile(degree=3, n_positions=1),
            _make_profile(degree=2, n_positions=3),
        ]

        with patch("linkedin.ml.scorer.ANALYTICS_DB", db_path):
            scorer1 = ProfileScorer(seed=42, keywords_path=tmp_path / "empty_kw.yaml")
            scorer1.train()
            ranking1 = [p["profile"]["connection_degree"] for p in scorer1.score_profiles(profiles)]

            scorer2 = ProfileScorer(seed=42, keywords_path=tmp_path / "empty_kw.yaml")
            scorer2.train()
            ranking2 = [p["profile"]["connection_degree"] for p in scorer2.score_profiles(profiles)]

        assert ranking1 == ranking2

    def test_explain_profile_outputs_contributions(self, trained_scorer):
        profile = _make_profile(degree=2, n_positions=3)
        explanation = trained_scorer.explain_profile(profile)
        assert "Predicted acceptance probability" in explanation
        assert "Feature contributions" in explanation

    def test_explain_profile_untrained(self, tmp_path):
        scorer = ProfileScorer(seed=42, keywords_path=tmp_path / "empty_kw.yaml")
        profile = _make_profile()
        explanation = scorer.explain_profile(profile)
        assert "not trained" in explanation.lower()


class TestColdStart:
    def test_cold_start_with_keywords_ranks_by_heuristic(self, tmp_path):
        kw_file = _make_keywords_file(
            tmp_path,
            positive=["machine learning", "data science"],
            negative=["student", "intern"],
        )
        scorer = ProfileScorer(seed=42, keywords_path=kw_file)
        # Not trained — should use cold-start

        good_profile = _make_profile(headline="Machine Learning Engineer in Data Science")
        bad_profile = _make_profile(headline="Student Intern")
        neutral_profile = _make_profile(headline="Product Manager")

        ranked = scorer.score_profiles([bad_profile, neutral_profile, good_profile])
        # Good profile should be first (highest score)
        assert ranked[0]["profile"]["headline"] == "Machine Learning Engineer in Data Science"
        # Bad profile should be last (negative score)
        assert ranked[-1]["profile"]["headline"] == "Student Intern"

    def test_cold_start_without_keywords_returns_fifo(self, tmp_path):
        scorer = ProfileScorer(seed=42, keywords_path=tmp_path / "nonexistent.yaml")
        profiles = [
            _make_profile(degree=1),
            _make_profile(degree=2),
            _make_profile(degree=3),
        ]
        result = scorer.score_profiles(profiles)
        assert result == profiles

    def test_cold_start_explain(self, tmp_path):
        kw_file = _make_keywords_file(tmp_path, positive=["engineer"], negative=["student"])
        scorer = ProfileScorer(seed=42, keywords_path=kw_file)
        profile = _make_profile(headline="Senior Engineer")
        explanation = scorer.explain_profile(profile)
        assert "Cold-start" in explanation
        assert "positive hits: engineer" in explanation

    def test_cold_start_explain_no_matches(self, tmp_path):
        kw_file = _make_keywords_file(tmp_path, positive=["blockchain"])
        scorer = ProfileScorer(seed=42, keywords_path=kw_file)
        profile = _make_profile(headline="Senior Engineer")
        explanation = scorer.explain_profile(profile)
        assert "no keyword matches" in explanation


class TestTrainedWithKeywords:
    def test_trained_with_keywords_includes_keyword_features(self, tmp_path):
        import duckdb

        kw_file = _make_keywords_file(
            tmp_path,
            positive=["engineer", "ml"],
            negative=["student"],
        )

        db_path = tmp_path / "analytics.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(_CREATE_TABLE_SQL)
        for i in range(50):
            text = "engineer ml expert" if i % 2 else "student looking for job"
            _insert_row(con, _make_row(accepted=i % 2, profile_text=text))
        con.close()

        with patch("linkedin.ml.scorer.ANALYTICS_DB", db_path):
            scorer = ProfileScorer(seed=42, keywords_path=kw_file)
            assert scorer.train() is True

        # Check feature names include keyword columns
        assert "positive keyword: engineer" in scorer._all_feature_names
        assert "positive keyword: ml" in scorer._all_feature_names
        assert "negative keyword: student" in scorer._all_feature_names

        # Model should be trained on all features (mechanical + keywords)
        assert scorer._model.n_features_in_ == len(MECHANICAL_FEATURES) + 3  # 3 keywords

    def test_explain_with_keywords_shows_top_features(self, tmp_path):
        import duckdb

        kw_file = _make_keywords_file(
            tmp_path,
            positive=["engineer"],
            negative=["student"],
        )

        db_path = tmp_path / "analytics.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(_CREATE_TABLE_SQL)
        for i in range(50):
            text = "engineer expert" if i % 2 else "student intern"
            _insert_row(con, _make_row(accepted=i % 2, profile_text=text))
        con.close()

        with patch("linkedin.ml.scorer.ANALYTICS_DB", db_path):
            scorer = ProfileScorer(seed=42, keywords_path=kw_file)
            scorer.train()

        profile = _make_profile(headline="Senior Engineer")
        explanation = scorer.explain_profile(profile)
        assert "Feature contributions" in explanation


class TestMechanicalFeatures:
    def test_extract_mechanical_features_count(self, tmp_path):
        scorer = ProfileScorer(seed=42, keywords_path=tmp_path / "empty_kw.yaml")
        profile = _make_profile(with_dates=True)
        features = scorer._extract_mechanical_features(profile)
        assert len(features) == len(MECHANICAL_FEATURES)

    def test_extract_mechanical_features_values(self, tmp_path):
        scorer = ProfileScorer(seed=42, keywords_path=tmp_path / "empty_kw.yaml")
        profile = _make_profile(
            degree=2,
            n_positions=2,
            n_educations=1,
            summary="A bio",
            headline="Engineer",
            industry_name="Tech",
            with_dates=True,
        )
        features = scorer._extract_mechanical_features(profile)
        # connection_degree
        assert features[0] == 2
        # num_positions
        assert features[1] == 2
        # num_educations
        assert features[2] == 1
        # has_summary
        assert features[3] == 1
        # headline_length
        assert features[4] == len("Engineer")

    def test_empty_profile_features(self, tmp_path):
        scorer = ProfileScorer(seed=42, keywords_path=tmp_path / "empty_kw.yaml")
        profile = {"profile": {}}
        features = scorer._extract_mechanical_features(profile)
        assert len(features) == len(MECHANICAL_FEATURES)
        # All should be 0 or 0.0
        assert all(f == 0 or f == 0.0 for f in features)
