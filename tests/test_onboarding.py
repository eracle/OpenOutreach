# tests/test_onboarding.py
"""Tests for the onboarding module (keyword generation + interactive onboarding)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from linkedin import onboarding
from linkedin.onboarding import ensure_keywords, generate_keywords


SAMPLE_KEYWORDS_YAML = """\
positive:
  - sales
  - enterprise
negative:
  - student
  - intern
exploratory:
  - startup
  - fintech
"""

SAMPLE_DATA = {"positive": ["sales", "enterprise"], "negative": ["student", "intern"], "exploratory": ["startup", "fintech"]}


# ---------------------------------------------------------------------------
# generate_keywords with mocked LLM
# ---------------------------------------------------------------------------
class TestGenerateKeywords:
    def test_success(self, tmp_path):
        with patch("linkedin.onboarding.ASSETS_DIR", tmp_path):
            prompts_dir = tmp_path / "templates" / "prompts"
            prompts_dir.mkdir(parents=True)
            (prompts_dir / "generate_keywords.j2").write_text(
                "Generate keywords for {{ product_docs }} with {{ campaign_objective }}",
                encoding="utf-8",
            )
            with patch("linkedin.templates.renderer.call_llm", return_value=SAMPLE_KEYWORDS_YAML):
                data = generate_keywords("my product", "sell to CTOs")
                assert data["positive"] == ["sales", "enterprise"]
                assert data["negative"] == ["student", "intern"]
                assert data["exploratory"] == ["startup", "fintech"]

    def test_strips_code_fences(self, tmp_path):
        with patch("linkedin.onboarding.ASSETS_DIR", tmp_path):
            prompts_dir = tmp_path / "templates" / "prompts"
            prompts_dir.mkdir(parents=True)
            (prompts_dir / "generate_keywords.j2").write_text("{{ product_docs }}", encoding="utf-8")
            fenced = f"```yaml\n{SAMPLE_KEYWORDS_YAML}```"
            with patch("linkedin.templates.renderer.call_llm", return_value=fenced):
                data = generate_keywords("product", "objective")
                assert "sales" in data["positive"]

    def test_invalid_yaml(self, tmp_path):
        with patch("linkedin.onboarding.ASSETS_DIR", tmp_path):
            prompts_dir = tmp_path / "templates" / "prompts"
            prompts_dir.mkdir(parents=True)
            (prompts_dir / "generate_keywords.j2").write_text("{{ product_docs }}", encoding="utf-8")
            with patch("linkedin.templates.renderer.call_llm", return_value="just a string"):
                with pytest.raises(ValueError, match="did not parse as YAML dict"):
                    generate_keywords("product", "objective")

    def test_missing_key(self, tmp_path):
        with patch("linkedin.onboarding.ASSETS_DIR", tmp_path):
            prompts_dir = tmp_path / "templates" / "prompts"
            prompts_dir.mkdir(parents=True)
            (prompts_dir / "generate_keywords.j2").write_text("{{ product_docs }}", encoding="utf-8")
            incomplete = "positive:\n  - sales\nnegative:\n  - student\n"
            with patch("linkedin.templates.renderer.call_llm", return_value=incomplete):
                with pytest.raises(ValueError, match="exploratory"):
                    generate_keywords("product", "objective")


# ---------------------------------------------------------------------------
# ensure_keywords — keywords already exist (skip)
# ---------------------------------------------------------------------------
class TestEnsureKeywordsAlreadyExist:
    def test_noop_when_keywords_exist(self, tmp_path):
        """If keywords file already exists → does nothing."""
        kw_file = tmp_path / "keywords.yaml"
        kw_file.write_text("positive: []\nnegative: []\nexploratory: []\n", encoding="utf-8")
        with (
            patch.object(onboarding, "generate_keywords") as mock_gen,
            patch.object(onboarding, "_interactive_onboarding") as mock_interactive,
            patch("linkedin.onboarding.KEYWORDS_FILE", kw_file),
        ):
            ensure_keywords()
            mock_gen.assert_not_called()
            mock_interactive.assert_not_called()


# ---------------------------------------------------------------------------
# ensure_keywords — interactive onboarding
# ---------------------------------------------------------------------------
class TestInteractiveOnboarding:
    def test_interactive_flow(self, tmp_path):
        """No CLI args, no keywords file → runs interactive onboarding."""
        kw_file = tmp_path / "keywords.yaml"
        product_docs_file = tmp_path / "product_docs.txt"
        objective_file = tmp_path / "campaign_objective.txt"

        # _read_multiline called twice: first for product docs, then for objective
        read_results = iter([
            "My awesome product\nIt does great things",
            "sell analytics to CTOs",
        ])

        with (
            patch("linkedin.onboarding.KEYWORDS_FILE", kw_file),
            patch("linkedin.onboarding.CAMPAIGN_DIR", tmp_path),
            patch("linkedin.onboarding.PRODUCT_DOCS_FILE", product_docs_file),
            patch("linkedin.onboarding.CAMPAIGN_OBJECTIVE_FILE", objective_file),
            patch.object(onboarding, "_read_multiline", side_effect=read_results),
            patch.object(onboarding, "generate_keywords", return_value=SAMPLE_DATA) as mock_gen,
            patch.object(onboarding, "_save_keywords") as mock_save,
        ):
            ensure_keywords()

        mock_gen.assert_called_once_with(
            "My awesome product\nIt does great things",
            "sell analytics to CTOs",
        )
        mock_save.assert_called_once_with(SAMPLE_DATA)
        assert product_docs_file.read_text(encoding="utf-8") == "My awesome product\nIt does great things"
        assert objective_file.read_text(encoding="utf-8") == "sell analytics to CTOs"

    def test_interactive_reprompts_empty_product_docs(self, tmp_path):
        """Empty product docs → re-prompts, then accepts valid input."""
        kw_file = tmp_path / "keywords.yaml"
        product_docs_file = tmp_path / "product_docs.txt"
        objective_file = tmp_path / "campaign_objective.txt"

        # 1st call: empty (product docs), 2nd: valid (product docs retry),
        # 3rd: objective
        read_results = iter(["", "Valid product description", "sell to CTOs"])

        with (
            patch("linkedin.onboarding.KEYWORDS_FILE", kw_file),
            patch("linkedin.onboarding.CAMPAIGN_DIR", tmp_path),
            patch("linkedin.onboarding.PRODUCT_DOCS_FILE", product_docs_file),
            patch("linkedin.onboarding.CAMPAIGN_OBJECTIVE_FILE", objective_file),
            patch.object(onboarding, "_read_multiline", side_effect=read_results),
            patch.object(onboarding, "generate_keywords", return_value=SAMPLE_DATA),
            patch.object(onboarding, "_save_keywords"),
        ):
            ensure_keywords()

        assert product_docs_file.read_text(encoding="utf-8") == "Valid product description"

    def test_interactive_reprompts_empty_objective(self, tmp_path):
        """Empty objective → re-prompts, then accepts valid input."""
        kw_file = tmp_path / "keywords.yaml"
        product_docs_file = tmp_path / "product_docs.txt"
        objective_file = tmp_path / "campaign_objective.txt"

        # 1st call: product docs, 2nd: empty (objective), 3rd: valid (objective retry)
        read_results = iter(["My product", "", "sell to CTOs"])

        with (
            patch("linkedin.onboarding.KEYWORDS_FILE", kw_file),
            patch("linkedin.onboarding.CAMPAIGN_DIR", tmp_path),
            patch("linkedin.onboarding.PRODUCT_DOCS_FILE", product_docs_file),
            patch("linkedin.onboarding.CAMPAIGN_OBJECTIVE_FILE", objective_file),
            patch.object(onboarding, "_read_multiline", side_effect=read_results),
            patch.object(onboarding, "generate_keywords", return_value=SAMPLE_DATA),
            patch.object(onboarding, "_save_keywords"),
        ):
            ensure_keywords()

        assert objective_file.read_text(encoding="utf-8") == "sell to CTOs"
