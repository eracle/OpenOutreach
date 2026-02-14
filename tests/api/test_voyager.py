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


# ---------------------------------------------------------------------------
# Helpers for building Voyager-shaped JSON fixtures
# ---------------------------------------------------------------------------

_PROFILE_TYPE = "com.linkedin.voyager.dash.identity.profile.Profile"
_GEO_TYPE = "com.linkedin.voyager.dash.common.Geo"
_FULL_RECIPE = "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities"
_MINI_RECIPE = "com.linkedin.voyager.dash.deco.identity.profile.MiniProfile"
_IWE_RECIPE = "com.linkedin.voyager.dash.deco.relationships.ProfileWithIweWarned"
_EMAIL_RECIPE = "com.linkedin.voyager.dash.deco.relationships.ProfileWithEmailRequired"


def _mini_profile(public_id, first, last, urn_suffix):
    """A MiniProfile entity — no location, no geo, no positions.

    Real MiniProfiles omit location/geo keys entirely (rather than
    setting them to None), so this helper mirrors that structure.
    """
    return {
        "entityUrn": f"urn:li:fsd_profile:{urn_suffix}",
        "$type": _PROFILE_TYPE,
        "$recipeTypes": [_EMAIL_RECIPE, _MINI_RECIPE],
        "firstName": first,
        "lastName": last,
        "publicIdentifier": public_id,
    }


def _full_profile(
    public_id, first, last, urn_suffix,
    geo_urn=None, country_code=None,
    industry_urn=None, position_groups_urn=None, education_urn=None,
    member_rel_urn=None, headline=None, summary=None,
):
    """A FullProfileWithEntities entity — the target profile."""
    entity = {
        "entityUrn": f"urn:li:fsd_profile:{urn_suffix}",
        "$type": _PROFILE_TYPE,
        "$recipeTypes": [_FULL_RECIPE, _EMAIL_RECIPE, _MINI_RECIPE],
        "firstName": first,
        "lastName": last,
        "publicIdentifier": public_id,
        "locationName": None,
        "*geo": None,
        "headline": headline,
        "summary": summary,
    }
    if geo_urn:
        entity["geoLocation"] = {
            "*geo": geo_urn,
            "geoUrn": geo_urn,
            "$type": "com.linkedin.voyager.dash.identity.profile.ProfileGeoLocation",
        }
    if country_code:
        entity["location"] = {
            "countryCode": country_code,
            "$type": "com.linkedin.voyager.dash.identity.profile.ProfileLocation",
        }
    if industry_urn:
        entity["*industry"] = industry_urn
    if position_groups_urn:
        entity["*profilePositionGroups"] = position_groups_urn
    if education_urn:
        entity["*profileEducations"] = education_urn
    if member_rel_urn:
        entity["*memberRelationship"] = member_rel_urn
    return entity


def _geo_entity(urn, name, name_without_country, country_urn):
    return {
        "entityUrn": urn,
        "$type": _GEO_TYPE,
        "$recipeTypes": ["com.linkedin.voyager.dash.deco.identity.profile.Geo"],
        "defaultLocalizedName": name,
        "defaultLocalizedNameWithoutCountryName": name_without_country,
        "countryUrn": country_urn,
        "*country": country_urn,
    }


def _wrap(included):
    """Wrap included entities into a minimal Voyager response envelope."""
    first_urn = next(
        (e["entityUrn"] for e in included if e.get("$type") == _PROFILE_TYPE),
        None,
    )
    return {
        "data": {"*elements": [first_urn] if first_urn else []},
        "included": included,
    }


# ---------------------------------------------------------------------------
# Full-profile selection among multiple Profile entities
# ---------------------------------------------------------------------------

class TestFullProfileSelection:
    """When 'included' contains MiniProfile entities for other people
    (mutual connections, etc.) the parser must select the FullProfile entity,
    not the first Profile it encounters."""

    def test_picks_full_profile_without_public_id(self):
        """FullProfile appears after several MiniProfiles."""
        data = _wrap([
            _mini_profile("person-a", "Alice", "A", "AAA01"),
            _mini_profile("person-b", "Bob", "B", "AAA02"),
            _mini_profile("person-c", "Carol", "C", "AAA03"),
            _full_profile(
                "target-user", "Target", "User", "AAA99",
                geo_urn="urn:li:fsd_geo:900001",
                country_code="de",
            ),
            _geo_entity(
                "urn:li:fsd_geo:900001",
                "Metro Area, Country", "Metro Area",
                "urn:li:fsd_geo:900000",
            ),
        ])
        profile = parse_linkedin_voyager_response(data)

        assert profile["full_name"] == "Target User"
        assert profile["public_identifier"] == "target-user"
        assert profile["location_name"] == "Metro Area, Country"
        assert profile["country_code"] == "de"

    def test_with_and_without_public_id_agree(self):
        """Both paths must resolve to the same profile and location."""
        data = _wrap([
            _mini_profile("person-x", "Xavier", "X", "BBB01"),
            _full_profile(
                "target-user", "Target", "User", "BBB99",
                geo_urn="urn:li:fsd_geo:900002",
                country_code="ch",
            ),
            _geo_entity(
                "urn:li:fsd_geo:900002",
                "Zurich, Switzerland", "Zurich",
                "urn:li:fsd_geo:900000",
            ),
        ])
        with_id = parse_linkedin_voyager_response(data, public_identifier="target-user")
        without_id = parse_linkedin_voyager_response(data)

        assert with_id["full_name"] == without_id["full_name"]
        assert with_id["location_name"] == without_id["location_name"]
        assert with_id["country_code"] == without_id["country_code"]

    def test_three_profile_entities(self):
        """MiniProfile + IweWarned + FullProfile — only FullProfile has geo."""
        iwe_profile = _mini_profile("iwe-user", "Ian", "W", "CCC02")
        iwe_profile["$recipeTypes"] = [_IWE_RECIPE]

        data = _wrap([
            _mini_profile("person-a", "Alice", "A", "CCC01"),
            iwe_profile,
            _full_profile(
                "target-user", "Target", "User", "CCC99",
                geo_urn="urn:li:fsd_geo:900003",
                country_code="it",
            ),
            _geo_entity(
                "urn:li:fsd_geo:900003",
                "Rome Metropolitan Area", "Rome Metropolitan Area",
                "urn:li:fsd_geo:900000",
            ),
        ])
        profile = parse_linkedin_voyager_response(data)

        assert profile["full_name"] == "Target User"
        assert profile["location_name"] == "Rome Metropolitan Area"
        assert profile["country_code"] == "it"

    def test_full_profile_is_first(self):
        """When FullProfile happens to appear first, it still works."""
        data = _wrap([
            _full_profile(
                "target-user", "Target", "User", "DDD01",
                geo_urn="urn:li:fsd_geo:900004",
                country_code="us",
            ),
            _mini_profile("person-a", "Alice", "A", "DDD02"),
            _mini_profile("person-b", "Bob", "B", "DDD03"),
            _geo_entity(
                "urn:li:fsd_geo:900004",
                "New York, United States", "New York",
                "urn:li:fsd_geo:900000",
            ),
        ])
        profile = parse_linkedin_voyager_response(data)

        assert profile["full_name"] == "Target User"
        assert profile["location_name"] == "New York, United States"

    def test_no_full_profile_falls_back_to_first(self):
        """If no FullProfile recipe exists, parser takes the first entity."""
        data = _wrap([
            _mini_profile("person-a", "Alice", "A", "EEE01"),
            _mini_profile("person-b", "Bob", "B", "EEE02"),
        ])
        profile = parse_linkedin_voyager_response(data)
        assert profile["full_name"] == "Alice A"


# ---------------------------------------------------------------------------
# Geo / location resolution patterns
# ---------------------------------------------------------------------------

class TestGeoLocationResolution:
    """locationName is typically None in current API responses;
    location must be resolved via the geoLocation.*geo URN fallback."""

    def test_geo_location_fallback_with_country(self):
        data = _wrap([
            _full_profile(
                "user-a", "Jane", "Doe", "FFF01",
                geo_urn="urn:li:fsd_geo:800001",
                country_code="es",
            ),
            _geo_entity(
                "urn:li:fsd_geo:800001",
                "Barcelona, Catalonia, Spain", "Barcelona, Catalonia",
                "urn:li:fsd_geo:800000",
            ),
        ])
        profile = parse_linkedin_voyager_response(data)

        assert profile["location_name"] == "Barcelona, Catalonia, Spain"
        assert profile["country_code"] == "es"
        assert profile["geo"]["defaultLocalizedName"] == "Barcelona, Catalonia, Spain"
        assert profile["geo"]["defaultLocalizedNameWithoutCountryName"] == "Barcelona, Catalonia"

    def test_no_geo_no_location(self):
        """Profile with no geoLocation and no locationName → None."""
        data = _wrap([
            _full_profile("user-b", "Empty", "Geo", "FFF02"),
        ])
        profile = parse_linkedin_voyager_response(data)

        assert profile["location_name"] is None
        assert profile["geo"] is None
        assert profile["country_code"] is None

    def test_geo_urn_missing_from_included(self):
        """geoLocation references a URN not present in included → None."""
        data = _wrap([
            _full_profile(
                "user-c", "Missing", "Geo", "FFF03",
                geo_urn="urn:li:fsd_geo:999999",
                country_code="fr",
            ),
        ])
        profile = parse_linkedin_voyager_response(data)

        assert profile["location_name"] is None
        assert profile["geo"] is None
        assert profile["country_code"] == "fr"

    def test_geo_via_geoUrn_fallback(self):
        """geoLocation has geoUrn but no *geo — parser tries both keys."""
        entity = _full_profile("user-d", "Urn", "Fallback", "FFF04", country_code="be")
        entity["geoLocation"] = {
            "geoUrn": "urn:li:fsd_geo:800002",
            "$type": "com.linkedin.voyager.dash.identity.profile.ProfileGeoLocation",
        }
        data = _wrap([
            entity,
            _geo_entity(
                "urn:li:fsd_geo:800002",
                "Brussels Metropolitan Area", "Brussels Metropolitan Area",
                "urn:li:fsd_geo:800000",
            ),
        ])
        profile = parse_linkedin_voyager_response(data)

        assert profile["location_name"] == "Brussels Metropolitan Area"


# ---------------------------------------------------------------------------
# Positions & educations
# ---------------------------------------------------------------------------

class TestPositionsEducations:

    def _position_chain(self):
        """Build a profile with positions resolved via URN chain."""
        return _wrap([
            {
                **_full_profile(
                    "user-pos", "Jane", "Doe", "GGG01",
                    position_groups_urn="urn:li:collectionResponse:posGroups",
                    education_urn="urn:li:collectionResponse:edus",
                ),
            },
            # position group collection
            {
                "entityUrn": "urn:li:collectionResponse:posGroups",
                "$type": "com.linkedin.restli.common.CollectionResponse",
                "*elements": ["urn:li:fsd_profilePositionGroup:grp1"],
            },
            # one position group
            {
                "entityUrn": "urn:li:fsd_profilePositionGroup:grp1",
                "$type": "com.linkedin.voyager.dash.identity.profile.ProfilePositionGroup",
                "*profilePositionInPositionGroup": "urn:li:collectionResponse:posInGrp1",
            },
            # positions within the group
            {
                "entityUrn": "urn:li:collectionResponse:posInGrp1",
                "$type": "com.linkedin.restli.common.CollectionResponse",
                "*elements": ["urn:li:fsd_profilePosition:pos1", "urn:li:fsd_profilePosition:pos2"],
            },
            # individual positions
            {
                "entityUrn": "urn:li:fsd_profilePosition:pos1",
                "$type": "com.linkedin.voyager.dash.identity.profile.Position",
                "title": "Senior Engineer",
                "companyName": "Acme Corp",
                "locationName": "Berlin, Germany",
                "dateRange": {"start": {"year": 2022, "month": 1}},
                "description": "Building things",
            },
            {
                "entityUrn": "urn:li:fsd_profilePosition:pos2",
                "$type": "com.linkedin.voyager.dash.identity.profile.Position",
                "title": "Junior Engineer",
                "companyName": "Startup Inc",
                "locationName": None,
                "dateRange": {"start": {"year": 2019, "month": 6}, "end": {"year": 2021, "month": 12}},
            },
            # education collection
            {
                "entityUrn": "urn:li:collectionResponse:edus",
                "$type": "com.linkedin.restli.common.CollectionResponse",
                "*elements": ["urn:li:fsd_profileEducation:edu1"],
            },
            {
                "entityUrn": "urn:li:fsd_profileEducation:edu1",
                "$type": "com.linkedin.voyager.dash.identity.profile.Education",
                "schoolName": "State University",
                "degreeName": "B.Sc.",
                "fieldOfStudy": "Computer Science",
                "dateRange": {"start": {"year": 2015}, "end": {"year": 2019}},
            },
        ])

    def test_positions_parsed(self):
        data = self._position_chain()
        profile = parse_linkedin_voyager_response(data)

        assert len(profile["positions"]) == 2
        assert profile["positions"][0]["title"] == "Senior Engineer"
        assert profile["positions"][0]["company_name"] == "Acme Corp"
        assert profile["positions"][0]["location"] == "Berlin, Germany"
        assert profile["positions"][0]["description"] == "Building things"
        assert profile["positions"][1]["title"] == "Junior Engineer"
        assert profile["positions"][1]["location"] is None

    def test_position_date_range(self):
        data = self._position_chain()
        profile = parse_linkedin_voyager_response(data)

        dr = profile["positions"][1]["date_range"]
        assert dr["start"]["year"] == 2019
        assert dr["start"]["month"] == 6
        assert dr["end"]["year"] == 2021

    def test_educations_parsed(self):
        data = self._position_chain()
        profile = parse_linkedin_voyager_response(data)

        assert len(profile["educations"]) == 1
        edu = profile["educations"][0]
        assert edu["school_name"] == "State University"
        assert edu["degree_name"] == "B.Sc."
        assert edu["field_of_study"] == "Computer Science"


# ---------------------------------------------------------------------------
# Connection info
# ---------------------------------------------------------------------------

class TestConnectionInfo:

    def _profile_with_relationship(self, rel_union):
        return _wrap([
            {
                **_full_profile("user-conn", "Conn", "Test", "HHH01",
                                member_rel_urn="urn:li:fsd_memberRelationship:rel1"),
            },
            {
                "entityUrn": "urn:li:fsd_memberRelationship:rel1",
                "$type": "com.linkedin.voyager.dash.relationships.MemberRelationship",
                "memberRelationshipUnion": rel_union,
            },
        ])

    def test_distance_1(self):
        data = self._profile_with_relationship({"connectedMember": {}})
        profile = parse_linkedin_voyager_response(data)
        assert profile["connection_distance"] == "DISTANCE_1"
        assert profile["connection_degree"] == 1

    def test_distance_2(self):
        data = self._profile_with_relationship({
            "noConnection": {"memberDistance": "DISTANCE_2"},
        })
        profile = parse_linkedin_voyager_response(data)
        assert profile["connection_distance"] == "DISTANCE_2"
        assert profile["connection_degree"] == 2

    def test_distance_3(self):
        data = self._profile_with_relationship({
            "noConnection": {"memberDistance": "DISTANCE_3"},
        })
        profile = parse_linkedin_voyager_response(data)
        assert profile["connection_distance"] == "DISTANCE_3"
        assert profile["connection_degree"] == 3

    def test_out_of_network(self):
        data = self._profile_with_relationship({
            "noConnection": {"memberDistance": "OUT_OF_NETWORK"},
        })
        profile = parse_linkedin_voyager_response(data)
        assert profile["connection_distance"] == "OUT_OF_NETWORK"
        assert profile["connection_degree"] is None

    def test_no_relationship(self):
        data = _wrap([_full_profile("user-no-rel", "No", "Rel", "HHH02")])
        profile = parse_linkedin_voyager_response(data)
        assert profile["connection_distance"] is None
        assert profile["connection_degree"] is None


# ---------------------------------------------------------------------------
# Industry resolution
# ---------------------------------------------------------------------------

class TestIndustryResolution:

    def test_industry_resolved_via_star_field(self):
        data = _wrap([
            _full_profile(
                "user-ind", "Ind", "Test", "III01",
                industry_urn="urn:li:fsd_industry:1234",
            ),
            {
                "entityUrn": "urn:li:fsd_industry:1234",
                "$type": "com.linkedin.voyager.dash.common.Industry",
                "name": "Information Technology",
            },
        ])
        profile = parse_linkedin_voyager_response(data)
        assert profile["industry"]["name"] == "Information Technology"

    def test_industry_missing(self):
        data = _wrap([_full_profile("user-no-ind", "No", "Ind", "III02")])
        profile = parse_linkedin_voyager_response(data)
        assert profile["industry"] is None
