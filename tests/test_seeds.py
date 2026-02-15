# tests/test_seeds.py
"""Tests for seed URL loading and creation."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from linkedin.seeds import ensure_seeds, load_seed_urls


class TestLoadSeedUrls:
    def test_loads_urls_from_csv(self, tmp_path):
        csv_file = tmp_path / "urls.csv"
        csv_file.write_text("url\nhttps://www.linkedin.com/in/alice/\nhttps://www.linkedin.com/in/bob/\n")

        with patch("linkedin.seeds.SEED_URLS_FILE", csv_file):
            urls = load_seed_urls()

        assert len(urls) == 2
        assert "https://www.linkedin.com/in/alice/" in urls
        assert "https://www.linkedin.com/in/bob/" in urls

    def test_empty_csv(self, tmp_path):
        csv_file = tmp_path / "urls.csv"
        csv_file.write_text("url\n")

        with patch("linkedin.seeds.SEED_URLS_FILE", csv_file):
            urls = load_seed_urls()

        assert urls == []

    def test_missing_file_raises(self, tmp_path):
        with patch("linkedin.seeds.SEED_URLS_FILE", tmp_path / "nonexistent.csv"):
            with pytest.raises(FileNotFoundError):
                load_seed_urls()

    def test_skips_empty_rows(self, tmp_path):
        csv_file = tmp_path / "urls.csv"
        csv_file.write_text("url\nhttps://www.linkedin.com/in/alice/\n\n  \nhttps://www.linkedin.com/in/bob/\n")

        with patch("linkedin.seeds.SEED_URLS_FILE", csv_file):
            urls = load_seed_urls()

        assert len(urls) == 2


@pytest.mark.django_db
class TestEnsureSeeds:
    def test_creates_seed_leads(self, fake_session, tmp_path):
        csv_file = tmp_path / "urls.csv"
        csv_file.write_text("url\nhttps://www.linkedin.com/in/seed-alice/\n")

        with patch("linkedin.seeds.SEED_URLS_FILE", csv_file):
            count = ensure_seeds(fake_session)

        assert count == 1

    def test_idempotent(self, fake_session, tmp_path):
        csv_file = tmp_path / "urls.csv"
        csv_file.write_text("url\nhttps://www.linkedin.com/in/seed-alice/\n")

        with patch("linkedin.seeds.SEED_URLS_FILE", csv_file):
            count1 = ensure_seeds(fake_session)
            count2 = ensure_seeds(fake_session)

        assert count1 == 1
        assert count2 == 0  # Already tagged

    def test_no_seeds_returns_zero(self, fake_session, tmp_path):
        csv_file = tmp_path / "urls.csv"
        csv_file.write_text("url\n")

        with patch("linkedin.seeds.SEED_URLS_FILE", csv_file):
            count = ensure_seeds(fake_session)

        assert count == 0

    def test_tags_deal_with_seed_metadata(self, fake_session, tmp_path):
        import json

        from crm.models import Deal

        csv_file = tmp_path / "urls.csv"
        csv_file.write_text("url\nhttps://www.linkedin.com/in/seed-bob/\n")

        with patch("linkedin.seeds.SEED_URLS_FILE", csv_file):
            ensure_seeds(fake_session)

        deal = Deal.objects.get(lead__website="https://www.linkedin.com/in/seed-bob/")
        meta = json.loads(deal.next_step)
        assert meta["seed"] is True
