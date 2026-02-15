# tests/ml/test_profile_text.py
from linkedin.ml.profile_text import build_profile_text


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
