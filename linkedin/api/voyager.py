# linkedin/api/voyager.py
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Literal, Any

ConnectionDistance = Literal["DISTANCE_1", "DISTANCE_2", "DISTANCE_3", "OUT_OF_NETWORK", None]

DISTANCE_TO_DEGREE: Dict[str, Optional[int]] = {
    "DISTANCE_1": 1,
    "DISTANCE_2": 2,
    "DISTANCE_3": 3,
    "OUT_OF_NETWORK": None,
}


# ======================
# Internal dataclasses (only used for validation & structure)
# ======================

@dataclass
class Date:
    year: Optional[int] = None
    month: Optional[int] = None


@dataclass
class DateRange:
    start: Optional[Date] = None
    end: Optional[Date] = None


@dataclass
class Position:
    title: str
    company_name: str
    company_urn: Optional[str] = None
    location: Optional[str] = None
    date_range: Optional[DateRange] = None
    description: Optional[str] = None
    urn: Optional[str] = None


@dataclass
class Education:
    school_name: str
    degree_name: Optional[str] = None
    field_of_study: Optional[str] = None
    date_range: Optional[DateRange] = None
    urn: Optional[str] = None


@dataclass
class LinkedInProfile:
    url: str
    urn: str
    full_name: str
    first_name: str
    last_name: str

    headline: Optional[str] = None
    summary: Optional[str] = None
    public_identifier: Optional[str] = None
    location_name: Optional[str] = None
    geo: Optional[Dict[str, Any]] = None
    industry: Optional[Dict[str, Any]] = None

    positions: List[Position] = field(default_factory=list)
    educations: List[Education] = field(default_factory=list)

    country_code: Optional[str] = None

    connection_distance: Optional[ConnectionDistance] = None
    connection_degree: Optional[int] = None


# ======================
# Private helpers
# ======================

def _resolve_references(data: dict) -> Dict[str, dict]:
    """Build urn → entity lookup from 'included' array."""
    return {
        entity.get("entityUrn"): entity
        for entity in data.get("included", [])
        if entity.get("entityUrn")
    }


def _resolve_star_field(entity: dict, urn_map: Dict[str, dict], field_name: str) -> Any:
    """Resolve *company, *school, *elements, etc."""
    value = entity.get(field_name)
    if not value:
        return None
    if isinstance(value, list):
        return [urn_map.get(urn) for urn in value if urn_map.get(urn)]
    return urn_map.get(value)


def _date_from_raw(raw: Optional[dict]) -> Optional[Date]:
    if not raw:
        return None
    return Date(year=raw.get("year"), month=raw.get("month"))


def _date_range_from_raw(raw: Optional[dict]) -> Optional[DateRange]:
    if not raw:
        return None
    return DateRange(
        start=_date_from_raw(raw.get("start")),
        end=_date_from_raw(raw.get("end")),
    )


def _enrich_position(pos: dict, urn_map: Dict[str, dict]) -> Position:
    company = _resolve_star_field(pos, urn_map, "*company")

    return Position(
        title=pos.get("title") or "Unknown Title",
        company_name=company.get("name") if company else pos.get("companyName", "Unknown Company"),
        company_urn=company.get("entityUrn") if company else pos.get("companyUrn"),
        location=pos.get("locationName"),
        date_range=_date_range_from_raw(pos.get("dateRange")),
        description=pos.get("description"),
        urn=pos.get("entityUrn"),
    )


def _enrich_education(edu: dict, urn_map: Dict[str, dict]) -> Education:
    school = _resolve_star_field(edu, urn_map, "*school")

    return Education(
        school_name=school.get("name") if school else edu.get("schoolName", "Unknown School"),
        degree_name=edu.get("degreeName"),
        field_of_study=edu.get("fieldOfStudy"),
        date_range=_date_range_from_raw(edu.get("dateRange")),
        urn=edu.get("entityUrn"),
    )


def _extract_connection_info(profile_entity: dict, urn_map: Dict[str, dict]) -> tuple[Optional[str], Optional[int]]:
    member_rel_urn = profile_entity.get("*memberRelationship")
    if not member_rel_urn:
        return None, None

    rel = urn_map.get(member_rel_urn)
    if not rel:
        return None, None

    union = rel.get("memberRelationshipUnion") or rel.get("memberRelationshipData")
    if not union:
        return None, None

    if "connectedMember" in union or "connected" in union:
        return "DISTANCE_1", 1

    if "noConnection" in union:
        distance_str = union["noConnection"].get("memberDistance")
        degree = DISTANCE_TO_DEGREE.get(distance_str)
        return distance_str, degree

    return None, None


# ======================
# Public function – returns plain dict
# ======================

def parse_linkedin_voyager_response(
        json_response: dict,
        public_identifier: Optional[str] = None,
) -> dict:
    """
    Parse a full LinkedIn Voyager profile response and return a clean dictionary.

    Uses dataclasses internally for validation and structure,
    but returns a plain, JSON-serializable dict (no dataclass leakage).

    Args:
        json_response: Raw JSON from Voyager API (with "data" and "included")
        public_identifier: Optional filter – only parse profile with this public ID

    Returns:
        dict with clean, structured LinkedIn profile data
    """
    urn_map = _resolve_references(json_response)

    # Find the main Profile entity
    profile_entity = None
    for entity in json_response.get("included", []):
        if entity.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile":
            entity_id = entity.get("publicIdentifier")
            if public_identifier is not None and entity_id == public_identifier:
                profile_entity = entity
                break
            if public_identifier is None:
                recipes = entity.get("$recipeTypes", [])
                is_full = any("FullProfile" in r for r in recipes)
                if is_full:
                    profile_entity = entity
                    break
                if profile_entity is None:
                    profile_entity = entity

    # Fallback if not found via $type
    if not profile_entity:
        main_urn = json_response.get("data", {}).get("*elements", [None])[0]
        profile_entity = urn_map.get(main_urn)

    if not profile_entity:
        raise ValueError("Could not find profile entity in the Voyager response")

    first_name = profile_entity.get("firstName", "")
    last_name = profile_entity.get("lastName", "")

    # Extract connection info
    connection_distance, connection_degree = _extract_connection_info(profile_entity, urn_map)

    # Build positions
    positions: List[Position] = []
    pos_groups_urn = profile_entity.get("*profilePositionGroups")
    if pos_groups_urn:
        pos_groups_resp = urn_map.get(pos_groups_urn)
        if pos_groups_resp and pos_groups_resp.get("*elements"):
            for group_urn in pos_groups_resp["*elements"]:
                group = urn_map.get(group_urn)
                if not group:
                    continue
                positions_coll_urn = group.get("*profilePositionInPositionGroup")
                if positions_coll_urn:
                    positions_coll = urn_map.get(positions_coll_urn)
                    if positions_coll and positions_coll.get("*elements"):
                        for pos_urn in positions_coll["*elements"]:
                            pos = urn_map.get(pos_urn)
                            if pos:
                                positions.append(_enrich_position(pos, urn_map))

    # Build educations
    educations: List[Education] = []
    educations_urn = profile_entity.get("*profileEducations")
    if educations_urn:
        edu_coll = urn_map.get(educations_urn)
        if edu_coll and edu_coll.get("*elements"):
            for edu_urn in edu_coll["*elements"]:
                edu = urn_map.get(edu_urn)
                if edu:
                    educations.append(_enrich_education(edu, urn_map))

    # Resolve geo — try direct *geo first, then nested geoLocation.*geo
    geo_entity = _resolve_star_field(profile_entity, urn_map, "*geo")
    if not geo_entity:
        geo_location = profile_entity.get("geoLocation")
        if geo_location:
            geo_urn = geo_location.get("*geo") or geo_location.get("geoUrn")
            if geo_urn:
                geo_entity = urn_map.get(geo_urn)

    location_name = profile_entity.get("locationName")
    if not location_name and geo_entity:
        location_name = geo_entity.get("defaultLocalizedName")

    # Extract country code from profile location
    country_code = profile_entity.get("location", {}).get("countryCode")

    # Assemble data for dataclass validation
    profile_data = {
        "urn": profile_entity["entityUrn"],
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}".strip() or None,
        "headline": profile_entity.get("headline"),
        "summary": profile_entity.get("summary"),
        "public_identifier": profile_entity.get("publicIdentifier"),
        "location_name": location_name,
        "geo": geo_entity,
        "industry": _resolve_star_field(profile_entity, urn_map, "*industry"),
        "country_code": country_code,
        "url": f"https://www.linkedin.com/in/{profile_entity.get('publicIdentifier', '')}/",
        "positions": positions,
        "educations": educations,
        "connection_distance": connection_distance,
        "connection_degree": connection_degree,
    }

    # Validate with dataclass (will raise if something is wrong)
    profile_obj = LinkedInProfile(**profile_data)

    # Return clean dictionary – perfect for JSON, APIs, logging, etc.
    return asdict(profile_obj)
