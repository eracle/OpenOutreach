
import logging
from urllib.parse import urlencode, urlparse

from .login import build_playwright
from ..api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)


def extract_profile_from_url(url, cookies):
    logger.debug(f"extract_profile_id_from_url: {url}")
    api_client = PlaywrightLinkedinAPI(
        username=None, password=None, authenticate=True, cookies=cookies, debug=True
    )

    # Parse the URL
    parsed_url = urlparse(url)

    # Split the path and get the second part
    profile_id = parsed_url.path.split("/")[2]

    logger.debug(f"profile_id: {profile_id}")
    return extract_profile_info(api_client, profile_id)


def filter_istruction_dict(elem):
    wanted_istr = {
        "schoolName",
        "degreeName",
        "fieldOfStudy",
        "timePeriod",
        # 'description',
        "grade",
    }
    return dict([(k, v) for k, v in elem.items() if k in wanted_istr])


def filter_experience_dict(elem):
    wanted_experience = {
        "companyName",
        "industries",
        "title",
        "startDate",
        "timePeriod",
        "geoLocationName",
        "description",
        "locationName",
        "company",
    }
    return dict([(k, v) for k, v in elem.items() if k in wanted_experience])


def filter_fields(contact_profile):
    allowed_fields = [
        "linkedinUrl",
        "firstName",
        "lastName",
        "headline",
        "summary",
        "industryName",
        "geoCountryName",
        "geoCountryUrn",
        "publicIdentifier",
        "connection_msg",
        "email_address",
        "phone_numbers",
        "education",
        "experience",
        "skills",
        "locale",
    ]

    # Filter out the fields using dictionary comprehension
    filtered_dict = {k: v for k, v in contact_profile.items() if k in allowed_fields}

    return filtered_dict


def extract_profile_info(api_client, contact_public_id):
    """
    Extracts profile information for a given LinkedIn user.

    Args:
        api_client: The API client instance.
        contact_public_id: The public ID of the LinkedIn user.

    Returns:
        A dictionary containing the user's profile information.
    """
    contact_profile = api_client.get_profile(contact_public_id)
    contact_info = api_client.get_profile_contact_info(contact_public_id)

    email_address = contact_info.get("email_address")
    phone_numbers = contact_info.get("phone_numbers")

    education = list(map(filter_istruction_dict, contact_profile.pop("education", [])))
    experience = list(
        map(filter_experience_dict, contact_profile.pop("experience", []))
    )

    return dict(
        email_address=email_address,
        phone_numbers=phone_numbers,
        education=education,
        experience=experience,
        **filter_fields(contact_profile),
    )


BASE_SEARCH_URL = "https://www.linkedin.com/search/results/people/"


def search(keyword: str, max_results: int = 10):
    """
    Searches for people on LinkedIn by keyword.
    """
    page, context, browser, playwright = build_playwright(login=True)

    params = {
        "keywords": keyword,
        "origin": "GLOBAL_SEARCH_HEADER",
    }
    search_url = BASE_SEARCH_URL + "?" + urlencode(params)

    logger.info(f"Navigating to search URL: {search_url}")
    page.goto(search_url)

    results = []
    result_container_xpath = "//li[contains(@class, 'reusable-search__result-container')]"

    # Wait for the search results to load
    page.wait_for_selector(f"xpath={result_container_xpath}", timeout=15000)

    # Get all result containers
    result_locators = page.locator(f"xpath={result_container_xpath}").all()

    logger.info(f"Found {len(result_locators)} search results on the first page.")

    for i, result_locator in enumerate(result_locators):
        if i >= max_results:
            break

        profile_link_locator = result_locator.locator(
            "xpath=.//a[contains(@href, '/in/') and not(contains(@href, '/sales/lead/'))]"
        )
        if profile_link_locator.count() > 0:
            profile_url = profile_link_locator.first.get_attribute("href")
            if profile_url:
                name_locator = result_locator.locator(
                    "xpath=.//span[@aria-hidden='true']"
                ).first
                headline_locator = result_locator.locator(
                    "xpath=.//div[contains(@class, 'entity-result__primary-subtitle')]"
                ).first

                name = name_locator.inner_text() if name_locator else ""
                headline = headline_locator.inner_text() if headline_locator else ""

                results.append(
                    {
                        "profile_url": profile_url,
                        "name": name,
                        "headline": headline,
                    }
                )

    # Enrich profiles with API data
    if results:
        cookies = context.cookies()
        api = PlaywrightLinkedinAPI(
            username=None, password=None, authenticate=True, cookies=cookies, debug=True
        )
        enriched_results = []
        for profile in results:
            try:
                profile_id = urlparse(profile["profile_url"]).path.split("/")[2]
                profile_data = extract_profile_info(api, profile_id)
                enriched_results.append(profile_data)
            except Exception as e:
                logger.error(f"Could not enrich profile {profile['profile_url']}: {e}")
                enriched_results.append(profile) # append basic info if enrichment fails

        results = enriched_results

    import time
    time.sleep(100)
    # Clean up
    context.close()
    browser.close()
    playwright.stop()

    return results
