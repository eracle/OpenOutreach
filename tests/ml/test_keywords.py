# tests/ml/test_keywords.py
import pytest
import yaml

from linkedin.ml.keywords import (
    build_profile_text,
    cold_start_score,
    compute_keyword_features,
    keyword_feature_names,
    load_keywords,
)


class TestLoadKeywords:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_keywords(tmp_path / "nonexistent.yaml")
        assert result == {"positive": [], "negative": [], "exploratory": []}

    def test_valid_yaml(self, tmp_path):
        kw_file = tmp_path / "keywords.yaml"
        kw_file.write_text(yaml.dump({
            "positive": ["machine learning", "data science"],
            "negative": ["student", "intern"],
            "exploratory": ["analytics"],
        }))
        result = load_keywords(kw_file)
        assert result["positive"] == ["machine learning", "data science"]
        assert result["negative"] == ["student", "intern"]
        assert result["exploratory"] == ["analytics"]

    def test_lowercase_normalization(self, tmp_path):
        kw_file = tmp_path / "keywords.yaml"
        kw_file.write_text(yaml.dump({
            "positive": ["Machine Learning", "DATA SCIENCE"],
            "negative": ["Student"],
            "exploratory": [],
        }))
        result = load_keywords(kw_file)
        assert result["positive"] == ["machine learning", "data science"]
        assert result["negative"] == ["student"]

    def test_missing_categories_default_to_empty(self, tmp_path):
        kw_file = tmp_path / "keywords.yaml"
        kw_file.write_text(yaml.dump({"positive": ["ml"]}))
        result = load_keywords(kw_file)
        assert result["negative"] == []
        assert result["exploratory"] == []


class TestBuildProfileText:
    def test_basic_profile(self):
        profile = {
            "profile": {
                "headline": "Senior Engineer",
                "summary": "Experienced developer",
                "location_name": "San Francisco",
            }
        }
        text = build_profile_text(profile)
        assert "senior engineer" in text
        assert "experienced developer" in text
        assert "san francisco" in text

    def test_with_positions_and_educations(self):
        profile = {
            "profile": {
                "headline": "CTO",
                "summary": "",
                "location_name": "",
                "industry": {"name": "Technology"},
                "positions": [
                    {"title": "CTO", "company_name": "Acme Corp", "location": "NYC", "description": "Leading tech"},
                ],
                "educations": [
                    {"school_name": "MIT", "degree": "MS", "field_of_study": "Computer Science"},
                ],
            }
        }
        text = build_profile_text(profile)
        assert "cto" in text
        assert "acme corp" in text
        assert "mit" in text
        assert "computer science" in text
        assert "technology" in text

    def test_all_lowercase(self):
        profile = {
            "profile": {
                "headline": "VP Engineering at GOOGLE",
                "summary": "PASSIONATE about AI",
            }
        }
        text = build_profile_text(profile)
        assert text == text.lower()

    def test_empty_profile(self):
        text = build_profile_text({})
        assert isinstance(text, str)
        # Should be all spaces from joining empty strings
        assert text.strip() == ""


class TestKeywordFeatureNames:
    def test_names_order(self):
        keywords = {
            "positive": ["ml", "data science"],
            "negative": ["student"],
            "exploratory": ["analytics"],
        }
        names = keyword_feature_names(keywords)
        assert names == ["kw_pos_ml", "kw_pos_data_science", "kw_neg_student", "kw_exp_analytics"]

    def test_empty_keywords(self):
        keywords = {"positive": [], "negative": [], "exploratory": []}
        assert keyword_feature_names(keywords) == []


class TestComputeKeywordFeatures:
    def test_boolean_presence(self):
        keywords = {
            "positive": ["machine learning"],
            "negative": ["student"],
            "exploratory": [],
        }
        text = "machine learning expert in machine learning and ai"
        features = compute_keyword_features(text, keywords)
        assert features[0] == 1.0  # "machine learning" present (boolean, not count)
        assert features[1] == 0.0  # "student" not present

    def test_no_matches(self):
        keywords = {
            "positive": ["blockchain"],
            "negative": ["crypto"],
            "exploratory": ["web3"],
        }
        text = "software engineer at a saas company"
        features = compute_keyword_features(text, keywords)
        assert features == [0.0, 0.0, 0.0]

    def test_case_insensitive(self):
        keywords = {
            "positive": ["data science"],
            "negative": [],
            "exploratory": [],
        }
        text = "Expert in Data Science and DATA SCIENCE applications"
        features = compute_keyword_features(text, keywords)
        assert features[0] == 1.0  # boolean: present


class TestColdStartScore:
    def test_positive_adds(self):
        keywords = {
            "positive": ["ml", "ai"],
            "negative": [],
            "exploratory": ["data"],
        }
        text = "ml engineer working on ai and ml systems"
        score = cold_start_score(text, keywords)
        assert score == 2.0  # ml present (+1), ai present (+1) — capped at 1 per keyword

    def test_negative_subtracts(self):
        keywords = {
            "positive": [],
            "negative": ["student", "intern"],
            "exploratory": [],
        }
        text = "student intern looking for roles"
        score = cold_start_score(text, keywords)
        # "student" present (-1), "intern" present (-1) → -2
        assert score == -2.0

    def test_exploratory_ignored(self):
        keywords = {
            "positive": [],
            "negative": [],
            "exploratory": ["analytics", "data"],
        }
        text = "analytics expert with data skills in analytics"
        score = cold_start_score(text, keywords)
        assert score == 0.0

    def test_mixed(self):
        keywords = {
            "positive": ["engineer"],
            "negative": ["student"],
            "exploratory": ["data"],
        }
        text = "engineer and student"
        score = cold_start_score(text, keywords)
        assert score == 0.0  # +1 - 1 = 0
