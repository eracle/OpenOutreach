from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Literal, Any

ConnectionDistance = Literal["DISTANCE_1", "DISTANCE_2", "DISTANCE_3", "OUT_OF_NETWORK", None]

# Mapping: LinkedIn's internal string → human-readable degree number
DISTANCE_TO_DEGREE: Dict[str, Optional[int]] = {
    "DISTANCE_1": 1,
    "DISTANCE_2": 2,
    "DISTANCE_3": 3,
    "OUT_OF_NETWORK": None,  # or use float('inf') or 999 if you prefer
}


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

    # Connection info
    connection_distance: Optional[ConnectionDistance] = None  # e.g. "DISTANCE_2"
    connection_degree: Optional[int] = None  # e.g. 2 (or None for out-of-network)


def _resolve_references(data: dict) -> Dict[str, dict]:
    """Build urn → entity lookup from 'included' array."""
    return {
        entity.get("entityUrn"): entity
        for entity in data.get("included", [])
        if entity.get("entityUrn")
    }


def _resolve_star_field(entity: dict, urn_map: dict, field_name: str):
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


def _enrich_position(pos: dict, urn_map: dict) -> Position:
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


def _enrich_education(edu: dict, urn_map: dict) -> Education:
    school = _resolve_star_field(edu, urn_map, "*school")

    return Education(
        school_name=school.get("name") if school else edu.get("schoolName", "Unknown School"),
        degree_name=edu.get("degreeName"),
        field_of_study=edu.get("fieldOfStudy"),
        date_range=_date_range_from_raw(edu.get("dateRange")),
        urn=edu.get("entityUrn"),
    )


def _extract_connection_info(profile_entity: dict, urn_map: dict) -> tuple[Optional[str], Optional[int]]:
    """Extract connection distance using the clean DISTANCE_TO_DEGREE map."""
    member_rel_urn = profile_entity.get("*memberRelationship")
    if not member_rel_urn:
        return None, None

    rel = urn_map.get(member_rel_urn)
    if not rel:
        return None, None

    union = rel.get("memberRelationshipUnion") or rel.get("memberRelationshipData")
    if not union:
        return None, None

    # 1st degree connection
    if "connected" in union:
        return "DISTANCE_1", 1

    # 2nd, 3rd, or out-of-network
    if "noConnection" in union:
        distance_str = union["noConnection"].get("memberDistance")
        degree = DISTANCE_TO_DEGREE.get(distance_str)
        return distance_str, degree

    return None, None


def parse_linkedin_voyager_response(json_response: dict, public_identifier: str = None):
    """
    Main function: parses full Voyager profile JSON → LinkedInProfile dataclass.
    """
    urn_map = _resolve_references(json_response)

    # Find the main Profile entity
    profile_entity = None
    for entity in json_response.get("included", []):
        if entity.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile":
            entity_id = entity.get("publicIdentifier")

            # Only enforce match if public_identifier is given
            if public_identifier is None or entity_id == public_identifier:
                profile_entity = entity
                break

    if not profile_entity:
        # Fallback: first element in data.*elements
        main_urn = json_response.get("data", {}).get("*elements", [None])[0]
        profile_entity = urn_map.get(main_urn)

    if not profile_entity:
        raise ValueError("Could not find profile entity in the response")

    # Basic fields
    first_name = profile_entity.get("firstName", "")
    last_name = profile_entity.get("lastName", "")

    profile_data = {
        "urn": profile_entity["entityUrn"],
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}".strip(),
        "headline": profile_entity.get("headline"),
        "summary": profile_entity.get("summary"),
        "public_identifier": profile_entity.get("publicIdentifier"),
        "location_name": profile_entity.get("locationName"),
        "geo": _resolve_star_field(profile_entity, urn_map, "*geo"),
        "industry": _resolve_star_field(profile_entity, urn_map, "*industry"),
        "url": f"https://www.linkedin.com/in/{profile_entity.get('publicIdentifier', '')}/",
        "positions": [],
        "educations": [],
        "connection_distance": None,
        "connection_degree": None,
    }

    # Connection distance (via map)
    profile_data["connection_distance"], profile_data["connection_degree"] = _extract_connection_info(
        profile_entity, urn_map
    )

    # === EXPERIENCE (via Position Groups) ===
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
                                profile_data["positions"].append(_enrich_position(pos, urn_map))

    # === EDUCATION ===
    educations_urn = profile_entity.get("*profileEducations")
    if educations_urn:
        edu_coll = urn_map.get(educations_urn)
        if edu_coll and edu_coll.get("*elements"):
            for edu_urn in edu_coll["*elements"]:
                edu = urn_map.get(edu_urn)
                if edu:
                    profile_data["educations"].append(_enrich_education(edu, urn_map))

    return LinkedInProfile(**profile_data), profile_data


# Helper: pretty print as dict (useful for debugging)
def profile_to_dict(profile: LinkedInProfile) -> dict:
    return asdict(profile)
