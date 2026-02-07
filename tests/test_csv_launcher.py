# tests/test_csv_launcher.py
import pytest

from linkedin.csv_launcher import load_profiles_df


class TestLoadProfilesDf:
    def test_loads_url_column(self, tmp_path):
        csv = tmp_path / "urls.csv"
        csv.write_text("url\nhttps://www.linkedin.com/in/alice/\nhttps://www.linkedin.com/in/bob/\n")
        df = load_profiles_df(csv)
        assert len(df) == 2
        assert "public_identifier" in df.columns

    def test_recognizes_linkedin_url_column(self, tmp_path):
        csv = tmp_path / "urls.csv"
        csv.write_text("linkedin_url\nhttps://www.linkedin.com/in/alice/\n")
        df = load_profiles_df(csv)
        assert len(df) == 1

    def test_recognizes_profile_url_column(self, tmp_path):
        csv = tmp_path / "urls.csv"
        csv.write_text("profile_url\nhttps://www.linkedin.com/in/alice/\n")
        df = load_profiles_df(csv)
        assert len(df) == 1

    def test_deduplicates_urls(self, tmp_path):
        csv = tmp_path / "urls.csv"
        csv.write_text("url\nhttps://www.linkedin.com/in/alice/\nhttps://www.linkedin.com/in/alice/\n")
        df = load_profiles_df(csv)
        assert len(df) == 1

    def test_strips_whitespace(self, tmp_path):
        csv = tmp_path / "urls.csv"
        csv.write_text("url\n  https://www.linkedin.com/in/alice/  \n")
        df = load_profiles_df(csv)
        assert len(df) == 1

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_profiles_df(tmp_path / "nope.csv")

    def test_no_url_column_raises(self, tmp_path):
        csv = tmp_path / "bad.csv"
        csv.write_text("name,email\nalice,alice@example.com\n")
        with pytest.raises(ValueError, match="No URL column found"):
            load_profiles_df(csv)

    def test_extracts_public_identifier(self, tmp_path):
        csv = tmp_path / "urls.csv"
        csv.write_text("url\nhttps://www.linkedin.com/in/johndoe/\n")
        df = load_profiles_df(csv)
        assert df.iloc[0]["public_identifier"] == "johndoe"
