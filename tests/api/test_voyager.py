# tests/api/test_profile.py
import json
from pathlib import Path

import pytest

from linkedin.api.voyager import parse_linkedin_voyager_response, to_dict


@pytest.fixture
def profile_data():
    """Load the real LinkedIn profile JSON once per test session"""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "linkedin_profile.json"
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


def test_profile_parsing_and_to_dict_conversion(profile_data):
    # 1. Parse the raw Voyager response into our beautiful dataclass
    profile = parse_linkedin_voyager_response(profile_data)

    # 2. Convert to plain dict (this is the magic you wanted to test)
    profile_dict = to_dict(profile)

    # 3. Basic sanity checks — we don't care about exact values, just that data exists
    assert profile_dict.get("first_name"), "First name should not be empty"
    assert profile_dict.get("last_name"), "Last name should not be empty"
    assert profile_dict.get("headline"), "Headline should not be empty"
    assert profile_dict.get("public_identifier"), "Public identifier should exist"

    # 4. Bonus: prove it's JSON-serializable (very useful for APIs/DBs)
    json_str = json.dumps(profile_dict, ensure_ascii=False)
    assert isinstance(json_str, str)

    # Optional pretty print (runs only when you use -s)
    print("\nProfile successfully parsed and converted to dict!")
    print(f"Name: {profile_dict['first_name']} {profile_dict['last_name']}")
    print("\nto_dict() output sample:")
    print(json.dumps(profile_dict, indent=2, ensure_ascii=False))

    # assert False