# tests/api/test_profile.py

import json
from pathlib import Path

import pytest

from linkedin.api.voyager import parse_linkedin_voyager_response


@pytest.fixture
def profile(profile_data):
    """
    SINGLE POINT OF PARSING
    """
    # CHANGE 1: Remove the tuple unpacking → now returns only dict
    return parse_linkedin_voyager_response(profile_data)


@pytest.fixture
def profile_data():
    fixture_path = Path(__file__).parent.parent / "fixtures" / "profiles" /"linkedin_profile.json"
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


def test_profile_parsing_structure_only(profile):
    assert isinstance(profile["first_name"], str) and profile["first_name"].strip()
    assert isinstance(profile["last_name"], str) and profile["last_name"].strip()
    assert isinstance(profile["full_name"], str) and profile["full_name"].strip()
    assert isinstance(profile["url"], str) and profile["url"].startswith("https://www.linkedin.com/in/")
    assert isinstance(profile["public_identifier"], str) and profile["public_identifier"]

    assert profile["headline"] is None or isinstance(profile["headline"], str)
    assert profile["summary"] is None or isinstance(profile["summary"], str)
    assert profile["location_name"] is None or isinstance(profile["location_name"], str)

    assert profile["geo"] is None or isinstance(profile["geo"], dict)
    assert profile["industry"] is None or isinstance(profile["industry"], dict)

    assert isinstance(profile["positions"], list)
    if profile["positions"]:
        pos = profile["positions"][0]
        assert isinstance(pos["title"], str) and pos["title"]
        assert isinstance(pos["company_name"], str) and pos["company_name"]
        assert pos["company_urn"] is None or isinstance(pos["company_urn"], str)
        assert pos["location"] is None or isinstance(pos["location"], str)
        assert pos["description"] is None or isinstance(pos["description"], str)
        # CHANGE 4: date_range is now dict, not object
        assert pos["date_range"] is None or isinstance(pos["date_range"], dict)

    assert isinstance(profile["educations"], list)
    if profile["educations"]:
        edu = profile["educations"][0]
        assert isinstance(edu["school_name"], str) and edu["school_name"]
        assert edu["degree_name"] is None or isinstance(edu["degree_name"], str)
        assert edu["field_of_study"] is None or isinstance(edu["field_of_study"], str)
        assert edu["date_range"] is None or isinstance(edu["date_range"], dict)

    valid_distances = {None, "DISTANCE_1", "DISTANCE_2", "DISTANCE_3", "OUT_OF_NETWORK"}
    assert profile["connection_distance"] in valid_distances

    if profile["connection_distance"] == "DISTANCE_1":
        assert profile["connection_degree"] == 1
    elif profile["connection_distance"] == "DISTANCE_2":
        assert profile["connection_degree"] == 2
    elif profile["connection_distance"] == "DISTANCE_3":
        assert profile["connection_degree"] == 3
    elif profile["connection_distance"] == "OUT_OF_NETWORK":
        assert profile["connection_degree"] is None
    else:
        assert profile["connection_degree"] is None


def test_profile_is_fully_json_serializable(profile):
    # CHANGE 5: Use the dict directly, not .__dict__
    json.dumps(profile, ensure_ascii=False, default=str)


def test_no_exceptions_on_empty_or_minimal_profiles():
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
    # CHANGE 6: Remove tuple unpacking
    profile = parse_linkedin_voyager_response(minimal)
    assert profile["full_name"] == "John Doe"
    assert profile["connection_degree"] is None


def test_country_code_extracted():
    """country_code is extracted from profile_entity.location.countryCode."""
    data = {
        "data": {"*elements": ["urn:li:fsd_profile:ACoAAA789"]},
        "included": [
            {
                "entityUrn": "urn:li:fsd_profile:ACoAAA789",
                "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
                "firstName": "Anna",
                "lastName": "Schmidt",
                "publicIdentifier": "anna-schmidt",
                "location": {
                    "countryCode": "de",
                    "$type": "com.linkedin.voyager.dash.identity.profile.ProfileLocation",
                },
            }
        ],
    }
    profile = parse_linkedin_voyager_response(data)
    assert profile["country_code"] == "de"


def test_country_code_missing():
    """country_code is None when location block is absent."""
    data = {
        "data": {"*elements": ["urn:li:fsd_profile:ACoAAA000"]},
        "included": [
            {
                "entityUrn": "urn:li:fsd_profile:ACoAAA000",
                "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
                "firstName": "No",
                "lastName": "Location",
                "publicIdentifier": "no-location",
            }
        ],
    }
    profile = parse_linkedin_voyager_response(data)
    assert profile["country_code"] is None


def test_location_fallback_from_geo_location():
    """locationName is None but geoLocation.*geo resolves to a geo entity."""
    data = {
        "data": {"*elements": ["urn:li:fsd_profile:ACoAAA456"]},
        "included": [
            {
                "entityUrn": "urn:li:fsd_profile:ACoAAA456",
                "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
                "firstName": "Diego",
                "lastName": "Ramirez",
                "publicIdentifier": "diego-ramirez",
                "locationName": None,
                "*geo": None,
                "geoLocation": {
                    "*geo": "urn:li:fsd_geo:104189151",
                    "geoUrn": "urn:li:fsd_geo:104189151",
                },
            },
            {
                "entityUrn": "urn:li:fsd_geo:104189151",
                "$type": "com.linkedin.voyager.dash.common.Geo",
                "defaultLocalizedName": "Brazoria, Texas, United States",
                "defaultLocalizedNameWithoutCountryName": "Brazoria, Texas",
                "countryUrn": "urn:li:fsd_geo:103644278",
            },
        ],
    }
    profile = parse_linkedin_voyager_response(data)
    assert profile["location_name"] == "Brazoria, Texas, United States"
    assert profile["geo"]["defaultLocalizedName"] == "Brazoria, Texas, United States"
