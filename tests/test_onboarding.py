# tests/test_onboarding.py
"""Tests for the onboarding module (product docs + campaign objective collection)."""
from __future__ import annotations

from unittest.mock import patch

from linkedin import onboarding
from linkedin.onboarding import ensure_onboarding


# ---------------------------------------------------------------------------
# ensure_onboarding — both files already exist (skip)
# ---------------------------------------------------------------------------
class TestEnsureOnboardingAlreadyExist:
    def test_noop_when_both_files_exist(self, tmp_path):
        """If product docs and campaign objective files exist → does nothing."""
        product_docs_file = tmp_path / "product_docs.txt"
        objective_file = tmp_path / "campaign_objective.txt"
        product_docs_file.write_text("My product", encoding="utf-8")
        objective_file.write_text("Sell to CTOs", encoding="utf-8")

        with (
            patch.object(onboarding, "_interactive_onboarding") as mock_interactive,
            patch("linkedin.onboarding.PRODUCT_DOCS_FILE", product_docs_file),
            patch("linkedin.onboarding.CAMPAIGN_OBJECTIVE_FILE", objective_file),
        ):
            ensure_onboarding()
            mock_interactive.assert_not_called()

    def test_runs_when_product_docs_missing(self, tmp_path):
        """If product docs file is missing → runs interactive."""
        product_docs_file = tmp_path / "product_docs.txt"
        objective_file = tmp_path / "campaign_objective.txt"
        objective_file.write_text("Sell to CTOs", encoding="utf-8")

        with (
            patch.object(onboarding, "_interactive_onboarding") as mock_interactive,
            patch("linkedin.onboarding.PRODUCT_DOCS_FILE", product_docs_file),
            patch("linkedin.onboarding.CAMPAIGN_OBJECTIVE_FILE", objective_file),
        ):
            ensure_onboarding()
            mock_interactive.assert_called_once()

    def test_runs_when_objective_missing(self, tmp_path):
        """If campaign objective file is missing → runs interactive."""
        product_docs_file = tmp_path / "product_docs.txt"
        objective_file = tmp_path / "campaign_objective.txt"
        product_docs_file.write_text("My product", encoding="utf-8")

        with (
            patch.object(onboarding, "_interactive_onboarding") as mock_interactive,
            patch("linkedin.onboarding.PRODUCT_DOCS_FILE", product_docs_file),
            patch("linkedin.onboarding.CAMPAIGN_OBJECTIVE_FILE", objective_file),
        ):
            ensure_onboarding()
            mock_interactive.assert_called_once()


# ---------------------------------------------------------------------------
# ensure_onboarding — interactive onboarding
# ---------------------------------------------------------------------------
class TestInteractiveOnboarding:
    def test_interactive_flow(self, tmp_path):
        """No files → runs interactive onboarding, saves both files."""
        product_docs_file = tmp_path / "product_docs.txt"
        objective_file = tmp_path / "campaign_objective.txt"

        # _read_multiline called twice: first for product docs, then for objective
        read_results = iter([
            "My awesome product\nIt does great things",
            "sell analytics to CTOs",
        ])

        with (
            patch("linkedin.onboarding.CAMPAIGN_DIR", tmp_path),
            patch("linkedin.onboarding.PRODUCT_DOCS_FILE", product_docs_file),
            patch("linkedin.onboarding.CAMPAIGN_OBJECTIVE_FILE", objective_file),
            patch.object(onboarding, "_read_multiline", side_effect=read_results),
        ):
            ensure_onboarding()

        assert product_docs_file.read_text(encoding="utf-8") == "My awesome product\nIt does great things"
        assert objective_file.read_text(encoding="utf-8") == "sell analytics to CTOs"

    def test_interactive_reprompts_empty_product_docs(self, tmp_path):
        """Empty product docs → re-prompts, then accepts valid input."""
        product_docs_file = tmp_path / "product_docs.txt"
        objective_file = tmp_path / "campaign_objective.txt"

        # 1st call: empty (product docs), 2nd: valid (product docs retry),
        # 3rd: objective
        read_results = iter(["", "Valid product description", "sell to CTOs"])

        with (
            patch("linkedin.onboarding.CAMPAIGN_DIR", tmp_path),
            patch("linkedin.onboarding.PRODUCT_DOCS_FILE", product_docs_file),
            patch("linkedin.onboarding.CAMPAIGN_OBJECTIVE_FILE", objective_file),
            patch.object(onboarding, "_read_multiline", side_effect=read_results),
        ):
            ensure_onboarding()

        assert product_docs_file.read_text(encoding="utf-8") == "Valid product description"

    def test_interactive_reprompts_empty_objective(self, tmp_path):
        """Empty objective → re-prompts, then accepts valid input."""
        product_docs_file = tmp_path / "product_docs.txt"
        objective_file = tmp_path / "campaign_objective.txt"

        # 1st call: product docs, 2nd: empty (objective), 3rd: valid (objective retry)
        read_results = iter(["My product", "", "sell to CTOs"])

        with (
            patch("linkedin.onboarding.CAMPAIGN_DIR", tmp_path),
            patch("linkedin.onboarding.PRODUCT_DOCS_FILE", product_docs_file),
            patch("linkedin.onboarding.CAMPAIGN_OBJECTIVE_FILE", objective_file),
            patch.object(onboarding, "_read_multiline", side_effect=read_results),
        ):
            ensure_onboarding()

        assert objective_file.read_text(encoding="utf-8") == "sell to CTOs"
