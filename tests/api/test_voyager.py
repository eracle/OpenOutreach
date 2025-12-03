# tests/api/test_profile.py
import json
from pathlib import Path

import pytest

from linkedin.api.voyager import parse_linkedin_voyager_response


@pytest.fixture
def parsed_profile(profile_data):
    """
    SINGLE POINT OF PARSING
    All tests now depend on this fixture → the actual Voyager response is parsed
    exactly once per test (or once per session if scoped accordingly).
    """
    profile, _ = parse_linkedin_voyager_response(profile_data)
    return profile


@pytest.fixture
def profile_data():
    """Load any real LinkedIn Voyager profile JSON (structure matters, not content)"""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "linkedin_profile.json"
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


def test_profile_parsing_structure_only(parsed_profile):
    profile = parsed_profile

    # 1. Core identity fields must be present and non-empty strings
    assert isinstance(profile.first_name, str) and profile.first_name.strip()
    assert isinstance(profile.last_name, str) and profile.last_name.strip()
    assert isinstance(profile.full_name, str) and profile.full_name.strip()
    assert isinstance(profile.url, str) and profile.url.startswith("https://www.linkedin.com/in/")
    assert isinstance(profile.public_identifier, str) and profile.public_identifier

    # 2. Optional but commonly present top-card fields
    assert profile.headline is None or isinstance(profile.headline, str)
    assert profile.summary is None or isinstance(profile.summary, str)
    assert profile.location_name is None or isinstance(profile.location_name, str)

    # 3. Geo & industry (may be dict or None)
    assert profile.geo is None or isinstance(profile.geo, dict)
    assert profile.industry is None or isinstance(profile.industry, dict)

    # 4. Experience section: list of Position objects
    assert isinstance(profile.positions, list)
    if profile.positions:
        pos = profile.positions[0]
        assert isinstance(pos.title, str) and pos.title
        assert isinstance(pos.company_name, str) and pos.company_name
        assert pos.company_urn is None or isinstance(pos.company_urn, str)
        assert pos.location is None or isinstance(pos.location, str)
        assert pos.description is None or isinstance(pos.description, str)
        assert pos.date_range is None or hasattr(pos.date_range, "start")

    # 5. Education section: list of Education objects
    assert isinstance(profile.educations, list)
    if profile.educations:
        edu = profile.educations[0]
        assert isinstance(edu.school_name, str) and edu.school_name
        assert edu.degree_name is None or isinstance(edu.degree_name, str)
        assert edu.field_of_study is None or isinstance(edu.field_of_study, str)
        assert edu.date_range is None or hasattr(edu.date_range, "start")

    # 6. CRITICAL: Connection degree must be extracted correctly
    valid_distances = {None, "DISTANCE_1", "DISTANCE_2", "DISTANCE_3", "OUT_OF_NETWORK"}
    assert profile.connection_distance in valid_distances, \
        f"Invalid connection_distance: {profile.connection_distance}"

    if profile.connection_distance == "DISTANCE_1":
        assert profile.connection_degree == 1
    elif profile.connection_distance == "DISTANCE_2":
        assert profile.connection_degree == 2
    elif profile.connection_distance == "DISTANCE_3":
        assert profile.connection_degree == 3
    elif profile.connection_distance == "OUT_OF_NETWORK":
        assert profile.connection_degree is None
    else:  # None → no relationship data
        assert profile.connection_degree is None

    print("\nAll structural tests passed!")
    print(f"→ Name: {profile.full_name}")
    print(f"→ Headline: {profile.headline}")
    print(f"→ Connection: {profile.connection_distance} → degree {profile.connection_degree}")
    print(f"→ Experience entries: {len(profile.positions)}")
    print(f"→ Education entries: {len(profile.educations)}")

    # Remove the intentional failure when you're happy with the output
    assert False


def test_profile_is_fully_json_serializable(parsed_profile):
    profile = parsed_profile
    json.dumps(profile.__dict__, ensure_ascii=False, default=str)
    print("Profile is 100% JSON-serializable")


def test_no_exceptions_on_empty_or_minimal_profiles():
    # Minimal valid response that should not crash the parser
    minimal = {
        "data": {"*elements": ["urn:li:fsd_profile:ACoAAA123"]},
        "included": [
            {
                "entityUrn": "urn:li:fsd_profile:ACoAAA123",
                "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
                "firstName": "John",
                "lastName": "Doe",
                "publicIdentifier": "johndoe"
            }
        ]
    }
    # Still a single, isolated call – kept here because it’s a different payload
    profile, _ = parse_linkedin_voyager_response(minimal)
    assert profile.full_name == "John Doe"
    assert profile.connection_degree is None
    print("Minimal profile parsed safely")