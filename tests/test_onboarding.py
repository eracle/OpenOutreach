# tests/test_onboarding.py
"""Tests for the onboarding module (keyword generation from CLI file args)."""
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
# ensure_keywords
# ---------------------------------------------------------------------------
class TestEnsureKeywords:
    def test_noop_when_no_files(self):
        """No file args → does nothing."""
        with patch.object(onboarding, "generate_keywords") as mock_gen:
            ensure_keywords()
            mock_gen.assert_not_called()

    def test_noop_when_only_product_docs(self, tmp_path):
        """Only --product-docs without --campaign-objective → does nothing."""
        f = tmp_path / "prod.md"
        f.write_text("product", encoding="utf-8")
        with patch.object(onboarding, "generate_keywords") as mock_gen:
            ensure_keywords(product_docs_path=str(f))
            mock_gen.assert_not_called()

    def test_noop_when_only_objective(self, tmp_path):
        """Only --campaign-objective without --product-docs → does nothing."""
        f = tmp_path / "obj.md"
        f.write_text("objective", encoding="utf-8")
        with patch.object(onboarding, "generate_keywords") as mock_gen:
            ensure_keywords(campaign_objective_path=str(f))
            mock_gen.assert_not_called()

    def test_generates_when_both_files(self, tmp_path):
        """Both files provided → generates and writes keywords."""
        prod = tmp_path / "prod.md"
        prod.write_text("My SaaS product", encoding="utf-8")
        obj = tmp_path / "obj.md"
        obj.write_text("Sell to CTOs", encoding="utf-8")
        kw_file = tmp_path / "keywords.yaml"

        data = {"positive": ["sales"], "negative": ["student"], "exploratory": ["fintech"]}

        with patch.object(onboarding, "generate_keywords", return_value=data) as mock_gen:
            with patch("linkedin.onboarding.KEYWORDS_FILE", kw_file):
                ensure_keywords(
                    product_docs_path=str(prod),
                    campaign_objective_path=str(obj),
                )

        mock_gen.assert_called_once_with("My SaaS product", "Sell to CTOs")
        loaded = yaml.safe_load(kw_file.read_text(encoding="utf-8"))
        assert loaded == data
