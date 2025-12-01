from dataclasses import asdict, is_dataclass
from dataclasses import dataclass, field
from typing import List, Optional, Dict


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
    employment_type: Optional[str] = None
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
    urn: str
    full_name: str
    first_name: str
    last_name: str
    headline: Optional[str] = None
    summary: Optional[str] = None
    public_identifier: Optional[str] = None
    location_name: Optional[str] = None
    geo: Optional[dict] = None
    industry: Optional[dict] = None
    positions: List[Position] = field(default_factory=list)
    educations: List[Education] = field(default_factory=list)


def resolve_references(data: dict) -> Dict[str, dict]:
    """Build urn → entity lookup from 'included' array"""
    return {entity.get("entityUrn"): entity for entity in data.get("included", []) if entity.get("entityUrn")}


def resolve_star_field(entity: dict, urn_map: dict, field_name: str):
    """Resolve *company, *school, *elements, etc."""
    value = entity.get(field_name)
    if not value:
        return None
    if isinstance(value, list):
        return [urn_map.get(urn) for urn in value if urn_map.get(urn)]
    return urn_map.get(value)


def date_from_raw(raw: dict) -> Optional[Date]:
    if not raw:
        return None
    return Date(year=raw.get("year"), month=raw.get("month"))


def date_range_from_raw(raw: dict) -> Optional[DateRange]:
    if not raw:
        return None
    return DateRange(
        start=date_from_raw(raw.get("start")),
        end=date_from_raw(raw.get("end"))
    )


def enrich_position(pos: dict, urn_map: dict) -> Position:
    company = resolve_star_field(pos, urn_map, "*company")
    emp_type = resolve_star_field(pos, urn_map, "*employmentType")

    return Position(
        title=pos.get("title", "Unknown Title"),
        company_name=company.get("name") if company else pos.get("companyName", "Unknown Company"),
        company_urn=company.get("entityUrn") if company else pos.get("companyUrn"),
        location=pos.get("locationName"),
        date_range=date_range_from_raw(pos.get("dateRange")),
        description=pos.get("description"),
        employment_type=emp_type.get("name") if emp_type else None,
        urn=pos.get("entityUrn")
    )


def enrich_education(edu: dict, urn_map: dict) -> Education:
    school = resolve_star_field(edu, urn_map, "*school")
    degree = resolve_star_field(edu, urn_map, "*degree")

    return Education(
        school_name=school.get("name") if school else edu.get("schoolName", "Unknown School"),
        degree_name=edu.get("degreeName"),
        field_of_study=edu.get("fieldOfStudy"),
        date_range=date_range_from_raw(edu.get("dateRange")),
        urn=edu.get("entityUrn")
    )


def parse_linkedin_voyager_response(json_response: dict) -> LinkedInProfile:
    urn_map = resolve_references(json_response)

    # Find main profile entity
    profile_entity = None
    for entity in json_response.get("included", []):
        if entity.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile":
            profile_entity = entity
            break

    if not profile_entity:
        # Fallback: use first element from data.*elements
        main_urn = json_response.get("data", {}).get("*elements", [None])[0]
        profile_entity = urn_map.get(main_urn)

    if not profile_entity:
        raise ValueError("Could not find profile in response")

    # Build clean profile dict
    profile_data = {
        "urn": profile_entity["entityUrn"],
        "first_name": profile_entity.get("firstName", ""),
        "last_name": profile_entity.get("lastName", ""),
        "headline": profile_entity.get("headline"),
        "summary": profile_entity.get("summary"),
        "public_identifier": profile_entity.get("publicIdentifier"),
        "location_name": profile_entity.get("locationName"),
        "geo": resolve_star_field(profile_entity, urn_map, "*geo"),
        "industry": resolve_star_field(profile_entity, urn_map, "*industry"),
        "positions": [],
        "educations": []
    }
    profile_data['full_name'] = profile_data['first_name'] + profile_data['last_name']

    # === POSITIONS (via PositionGroups) ===
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
                                profile_data["positions"].append(enrich_position(pos, urn_map))

    # === EDUCATIONS ===
    educations_urn = profile_entity.get("*profileEducations")
    if educations_urn:
        edu_coll = urn_map.get(educations_urn)
        if edu_coll and edu_coll.get("*elements"):
            for edu_urn in edu_coll["*elements"]:
                edu = urn_map.get(edu_urn)
                if edu:
                    profile_data["educations"].append(enrich_education(edu, urn_map))

    return LinkedInProfile(**profile_data)


def to_dict(obj):
    if is_dataclass(obj):
        return to_dict(asdict(obj))  # ← converts dataclass → dict
    elif isinstance(obj, (list, tuple)):
        return [to_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: to_dict(value) for key, value in obj.items()}
    else:
        return obj  # str, int, None, etc → already JSON-safe
