# openoutreach/core/tz_country.py
"""Resolve an active-hours timezone from a LinkedIn profile's country.

The active-hours window mimics the *operator's* waking rhythm, so the zone
we want is "the local time where this account plausibly logs in from". The
LinkedIn self-profile exposes no timezone — only an ISO-3166-1 alpha-2
``country_code`` — so we map country → a representative IANA zone.

Country granularity is deliberate: the window is a coarse 10-hour band, and
the only failure that matters is landing on the wrong *continent* (the old
UTC-everywhere bug put a US/Asia operator "active" while asleep). Intra-country
zone spread (US East vs West, ~3h) keeps the window squarely within human
hours, so for multi-zone countries we pick the most-populous zone and move on —
no geocoding, no coordinates, no extra dependency.

Resolving the IANA name into an actual offset still needs a tz database; the
``tzdata`` PyPI package in ``requirements/base.txt`` supplies it so this works
in any base image (slim Docker included).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ISO-3166-1 alpha-2 → representative IANA zone. Single-zone countries map to
# their only zone; multi-zone countries to the most-populous one.
_COUNTRY_TZ = {
    "ad": "Europe/Andorra", "ae": "Asia/Dubai", "af": "Asia/Kabul",
    "ag": "America/Antigua", "ai": "America/Anguilla", "al": "Europe/Tirane",
    "am": "Asia/Yerevan", "ao": "Africa/Luanda", "aq": "Antarctica/McMurdo",
    "ar": "America/Argentina/Buenos_Aires", "as": "Pacific/Pago_Pago",
    "at": "Europe/Vienna", "au": "Australia/Sydney", "aw": "America/Aruba",
    "ax": "Europe/Mariehamn", "az": "Asia/Baku", "ba": "Europe/Sarajevo",
    "bb": "America/Barbados", "bd": "Asia/Dhaka", "be": "Europe/Brussels",
    "bf": "Africa/Ouagadougou", "bg": "Europe/Sofia", "bh": "Asia/Bahrain",
    "bi": "Africa/Bujumbura", "bj": "Africa/Porto-Novo", "bl": "America/St_Barthelemy",
    "bm": "Atlantic/Bermuda", "bn": "Asia/Brunei", "bo": "America/La_Paz",
    "bq": "America/Kralendijk", "br": "America/Sao_Paulo", "bs": "America/Nassau",
    "bt": "Asia/Thimphu", "bw": "Africa/Gaborone", "by": "Europe/Minsk",
    "bz": "America/Belize", "ca": "America/Toronto", "cc": "Indian/Cocos",
    "cd": "Africa/Kinshasa", "cf": "Africa/Bangui", "cg": "Africa/Brazzaville",
    "ch": "Europe/Zurich", "ci": "Africa/Abidjan", "ck": "Pacific/Rarotonga",
    "cl": "America/Santiago", "cm": "Africa/Douala", "cn": "Asia/Shanghai",
    "co": "America/Bogota", "cr": "America/Costa_Rica", "cu": "America/Havana",
    "cv": "Atlantic/Cape_Verde", "cw": "America/Curacao", "cx": "Indian/Christmas",
    "cy": "Asia/Nicosia", "cz": "Europe/Prague", "de": "Europe/Berlin",
    "dj": "Africa/Djibouti", "dk": "Europe/Copenhagen", "dm": "America/Dominica",
    "do": "America/Santo_Domingo", "dz": "Africa/Algiers", "ec": "America/Guayaquil",
    "ee": "Europe/Tallinn", "eg": "Africa/Cairo", "eh": "Africa/El_Aaiun",
    "er": "Africa/Asmara", "es": "Europe/Madrid", "et": "Africa/Addis_Ababa",
    "fi": "Europe/Helsinki", "fj": "Pacific/Fiji", "fk": "Atlantic/Stanley",
    "fm": "Pacific/Pohnpei", "fo": "Atlantic/Faroe", "fr": "Europe/Paris",
    "ga": "Africa/Libreville", "gb": "Europe/London", "gd": "America/Grenada",
    "ge": "Asia/Tbilisi", "gf": "America/Cayenne", "gg": "Europe/Guernsey",
    "gh": "Africa/Accra", "gi": "Europe/Gibraltar", "gl": "America/Nuuk",
    "gm": "Africa/Banjul", "gn": "Africa/Conakry", "gp": "America/Guadeloupe",
    "gq": "Africa/Malabo", "gr": "Europe/Athens", "gs": "Atlantic/South_Georgia",
    "gt": "America/Guatemala", "gu": "Pacific/Guam", "gw": "Africa/Bissau",
    "gy": "America/Guyana", "hk": "Asia/Hong_Kong", "hn": "America/Tegucigalpa",
    "hr": "Europe/Zagreb", "ht": "America/Port-au-Prince", "hu": "Europe/Budapest",
    "id": "Asia/Jakarta", "ie": "Europe/Dublin", "il": "Asia/Jerusalem",
    "im": "Europe/Isle_of_Man", "in": "Asia/Kolkata", "io": "Indian/Chagos",
    "iq": "Asia/Baghdad", "ir": "Asia/Tehran", "is": "Atlantic/Reykjavik",
    "it": "Europe/Rome", "je": "Europe/Jersey", "jm": "America/Jamaica",
    "jo": "Asia/Amman", "jp": "Asia/Tokyo", "ke": "Africa/Nairobi",
    "kg": "Asia/Bishkek", "kh": "Asia/Phnom_Penh", "ki": "Pacific/Tarawa",
    "km": "Indian/Comoro", "kn": "America/St_Kitts", "kp": "Asia/Pyongyang",
    "kr": "Asia/Seoul", "kw": "Asia/Kuwait", "ky": "America/Cayman",
    "kz": "Asia/Almaty", "la": "Asia/Vientiane", "lb": "Asia/Beirut",
    "lc": "America/St_Lucia", "li": "Europe/Vaduz", "lk": "Asia/Colombo",
    "lr": "Africa/Monrovia", "ls": "Africa/Maseru", "lt": "Europe/Vilnius",
    "lu": "Europe/Luxembourg", "lv": "Europe/Riga", "ly": "Africa/Tripoli",
    "ma": "Africa/Casablanca", "mc": "Europe/Monaco", "md": "Europe/Chisinau",
    "me": "Europe/Podgorica", "mf": "America/Marigot", "mg": "Indian/Antananarivo",
    "mh": "Pacific/Majuro", "mk": "Europe/Skopje", "ml": "Africa/Bamako",
    "mm": "Asia/Yangon", "mn": "Asia/Ulaanbaatar", "mo": "Asia/Macau",
    "mp": "Pacific/Saipan", "mq": "America/Martinique", "mr": "Africa/Nouakchott",
    "ms": "America/Montserrat", "mt": "Europe/Malta", "mu": "Indian/Mauritius",
    "mv": "Indian/Maldives", "mw": "Africa/Blantyre", "mx": "America/Mexico_City",
    "my": "Asia/Kuala_Lumpur", "mz": "Africa/Maputo", "na": "Africa/Windhoek",
    "nc": "Pacific/Noumea", "ne": "Africa/Niamey", "nf": "Pacific/Norfolk",
    "ng": "Africa/Lagos", "ni": "America/Managua", "nl": "Europe/Amsterdam",
    "no": "Europe/Oslo", "np": "Asia/Kathmandu", "nr": "Pacific/Nauru",
    "nu": "Pacific/Niue", "nz": "Pacific/Auckland", "om": "Asia/Muscat",
    "pa": "America/Panama", "pe": "America/Lima", "pf": "Pacific/Tahiti",
    "pg": "Pacific/Port_Moresby", "ph": "Asia/Manila", "pk": "Asia/Karachi",
    "pl": "Europe/Warsaw", "pm": "America/Miquelon", "pn": "Pacific/Pitcairn",
    "pr": "America/Puerto_Rico", "ps": "Asia/Gaza", "pt": "Europe/Lisbon",
    "pw": "Pacific/Palau", "py": "America/Asuncion", "qa": "Asia/Qatar",
    "re": "Indian/Reunion", "ro": "Europe/Bucharest", "rs": "Europe/Belgrade",
    "ru": "Europe/Moscow", "rw": "Africa/Kigali", "sa": "Asia/Riyadh",
    "sb": "Pacific/Guadalcanal", "sc": "Indian/Mahe", "sd": "Africa/Khartoum",
    "se": "Europe/Stockholm", "sg": "Asia/Singapore", "sh": "Atlantic/St_Helena",
    "si": "Europe/Ljubljana", "sj": "Arctic/Longyearbyen", "sk": "Europe/Bratislava",
    "sl": "Africa/Freetown", "sm": "Europe/San_Marino", "sn": "Africa/Dakar",
    "so": "Africa/Mogadishu", "sr": "America/Paramaribo", "ss": "Africa/Juba",
    "st": "Africa/Sao_Tome", "sv": "America/El_Salvador", "sx": "America/Lower_Princes",
    "sy": "Asia/Damascus", "sz": "Africa/Mbabane", "tc": "America/Grand_Turk",
    "td": "Africa/Ndjamena", "tf": "Indian/Kerguelen", "tg": "Africa/Lome",
    "th": "Asia/Bangkok", "tj": "Asia/Dushanbe", "tk": "Pacific/Fakaofo",
    "tl": "Asia/Dili", "tm": "Asia/Ashgabat", "tn": "Africa/Tunis",
    "to": "Pacific/Tongatapu", "tr": "Europe/Istanbul", "tt": "America/Port_of_Spain",
    "tv": "Pacific/Funafuti", "tw": "Asia/Taipei", "tz": "Africa/Dar_es_Salaam",
    "ua": "Europe/Kyiv", "ug": "Africa/Kampala", "us": "America/New_York",
    "uy": "America/Montevideo", "uz": "Asia/Tashkent", "va": "Europe/Vatican",
    "vc": "America/St_Vincent", "ve": "America/Caracas", "vg": "America/Tortola",
    "vi": "America/St_Thomas", "vn": "Asia/Ho_Chi_Minh", "vu": "Pacific/Efate",
    "wf": "Pacific/Wallis", "ws": "Pacific/Apia", "ye": "Asia/Aden",
    "yt": "Indian/Mayotte", "za": "Africa/Johannesburg", "zm": "Africa/Lusaka",
    "zw": "Africa/Harare",
}


def timezone_for_country(country_code: str | None) -> str | None:
    """Representative IANA zone for an ISO-3166-1 alpha-2 ``country_code``.

    Returns None for an empty, unknown, or unresolvable code — the caller
    treats None as "no active-hours gating" rather than guessing UTC.
    """
    if not country_code:
        return None
    name = _COUNTRY_TZ.get(country_code.strip().lower())
    if not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name
