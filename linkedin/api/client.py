import logging
import random
from time import sleep
from typing import Optional, Dict
from urllib.parse import urlparse

from jsonpath_ng import parse
from linkedin_api import Linkedin
from linkedin_api.client import Client

logger = logging.getLogger(__name__)


def my_default_evade():
    """
    A catch-all method to try and evade suspension from Linkedin.
    Currenly, just delays the request by a random (bounded) time
    """
    sleep(
        random.uniform(0.2, 0.7)
    )  # sleep a random duration to try and evade suspention


class CustomClient(Client):
    def _set_session_cookies(self, cookies: list):
        """
        Set cookies of the current session and save them to a file named as the username.
        """
        for cookie in cookies:
            self.session.cookies.set(
                cookie['name'],
                cookie['value'],
                domain=cookie['domain'],
                path=cookie['path'],
            )
        if cookies:
            self.session.headers["csrf-token"] = self.session.cookies["JSESSIONID"].strip('"')


class PlaywrightLinkedinAPI(Linkedin):

    def __init__(
            self,
            username: str = None,
            password: str = None,
            resources=None,
            *,
            authenticate=True,
            refresh_cookies=False,
            debug=False,
            proxies={},
            cookies=None,
            cookies_dir: str = "",
    ):
        """Constructor method"""
        if resources:
            # Extract cookies from Playwright resources
            page, context, browser, playwright = resources  # Unpack for clarity
            logger.info("Extracting cookies from provided Playwright resources.")

            # Extract cookies from the browser context
            cookies = context.cookies()
            logger.info(f"Extracted {len(cookies)} cookies from Playwright session.")

        """Constructor method"""
        self.client = CustomClient(
            # self.client = Client(
            refresh_cookies=refresh_cookies,
            debug=debug,
            proxies=proxies,
            cookies_dir=cookies_dir,
        )
        logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)
        self.logger = logger

        if authenticate:
            if cookies:
                # If the cookies are expired, the API won't work anymore since
                # `username` and `password` are not used at all in this case.
                self.client._set_session_cookies(cookies)
            else:
                self.client.authenticate(username, password)

    def _fetch(self, uri, evade=my_default_evade, **kwargs):
        """
        GET request to Linkedin API
        """
        return super()._fetch(uri, evade, **kwargs)

    def _post(self, uri, evade=my_default_evade, **kwargs):
        """
        POST request to Linkedin API
        """
        return super()._post(uri, evade, **kwargs)

    def get_profile(
            self, public_id: Optional[str] = None, profile_url: Optional[str] = None
    ) -> Dict:
        """Fetch data for a given LinkedIn profile.

        :param public_id: LinkedIn public ID for a profile
        :type public_id: str, optional
        :param profile_url: Full LinkedIn profile URL
        :type profile_url: str, optional

        :return: Profile data
        :rtype: dict
        """
        if not public_id and profile_url:
            public_id = urlparse(profile_url).path.strip('/').split('/')[-1]

        if not public_id:
            raise ValueError("Either public_id or profile_url must be provided.")

        member_identity = public_id
        params = {
            'decorationId': 'com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-91',
            'memberIdentity': member_identity,
            'q': 'memberIdentity',
        }
        res = self._fetch("/identity/dash/profiles", params=params)

        data = res.json()

        if data and "status" in data and data["status"] != 200:
            self.logger.info(f"request failed: {res.content}")
            return {}

        # Now, extract using JSONPath

        # 1. Full Name
        first_name_expr = parse('$.elements[0].multiLocaleFirstName.en_US')
        first_name = first_name_expr.find(data)[0].value if first_name_expr.find(data) else None

        last_name_expr = parse('$.elements[0].multiLocaleLastName.en_US')
        last_name = last_name_expr.find(data)[0].value if last_name_expr.find(data) else None

        full_name = f"{first_name} {last_name}"

        # 2. Headline
        headline_expr = parse('$.elements[0].multiLocaleHeadline.en_US')
        headline = headline_expr.find(data)[0].value if headline_expr.find(data) else None

        # 3. Summary
        summary_expr = parse('$.elements[0].multiLocaleSummary.en_US')
        summary = summary_expr.find(data)[0].value if summary_expr.find(data) else None

        # 4. Location/Address
        address_expr = parse('$.elements[0].multiLocaleAddress.en_US')
        address = address_expr.find(data)[0].value if address_expr.find(data) else None

        country_expr = parse('$.elements[0].geoLocation.geo.defaultLocalizedName')
        country = country_expr.find(data)[0].value if country_expr.find(data) else None

        # 5. Education (list of dicts with key details)
        education_expr = parse('$.elements[0].profileEducations.elements[*]')
        educations_raw = [match.value for match in education_expr.find(data)]
        educations = []
        for edu in educations_raw:
            school = parse('multiLocaleSchoolName.en_US').find(edu)[0].value if parse(
                'multiLocaleSchoolName.en_US').find(edu) else None
            degree = parse('multiLocaleDegreeName.en_US').find(edu)[0].value if parse(
                'multiLocaleDegreeName.en_US').find(edu) else None
            field = parse('multiLocaleFieldOfStudy.en_US').find(edu)[0].value if parse(
                'multiLocaleFieldOfStudy.en_US').find(edu) else None
            start_year = parse('dateRange.start.year').find(edu)[0].value if parse('dateRange.start.year').find(
                edu) else None
            end_year = parse('dateRange.end.year').find(edu)[0].value if parse('dateRange.end.year').find(edu) else None
            educations.append({
                'school': school,
                'degree': degree,
                'field': field,
                'start_year': start_year,
                'end_year': end_year
            })

        # 6. Experience/Positions (list of dicts with key details)
        positions_expr = parse('$.elements[0].profilePositionGroups.elements[*]')
        positions_raw = [match.value for match in positions_expr.find(data)]
        positions = []
        for pos_group in positions_raw:
            title_expr = parse('profilePositionInPositionGroup.elements[0].multiLocaleTitle.en_US')
            title = title_expr.find(pos_group)[0].value if title_expr.find(pos_group) else None

            company_expr = parse('multiLocaleCompanyName.en_US')
            company = company_expr.find(pos_group)[0].value if company_expr.find(pos_group) else (
                parse('company.name').find(pos_group)[0].value if parse('company.name').find(pos_group) else None)

            start_month = parse('dateRange.start.month').find(pos_group)[0].value if parse(
                'dateRange.start.month').find(pos_group) else None
            start_year = parse('dateRange.start.year').find(pos_group)[0].value if parse('dateRange.start.year').find(
                pos_group) else None
            end_month = parse('dateRange.end.month').find(pos_group)[0].value if parse('dateRange.end.month').find(
                pos_group) else None
            end_year = parse('dateRange.end.year').find(pos_group)[0].value if parse('dateRange.end.year').find(
                pos_group) else None

            description_expr = parse('profilePositionInPositionGroup.elements[0].multiLocaleDescription.en_US')
            description = description_expr.find(pos_group)[0].value if description_expr.find(pos_group) else None

            positions.append({
                'title': title,
                'company': company,
                'start': f"{start_month}/{start_year}" if start_month else start_year,
                'end': f"{end_month}/{end_year}" if end_month else end_year,
                'description': description
            })

        # 7. Skills (list of strings)
        skills_expr = parse('$.elements[0].profileSkills.elements[*].multiLocaleName.en_US')
        skills = [match.value for match in skills_expr.find(data)]

        # 8. Certifications (list of dicts)
        certifications_expr = parse('$.elements[0].profileCertifications.elements[*]')
        certs_raw = [match.value for match in certifications_expr.find(data)]
        certifications = []
        for cert in certs_raw:
            name = parse('multiLocaleName.en_US').find(cert)[0].value if parse('multiLocaleName.en_US').find(
                cert) else None
            authority = parse('authority').find(cert)[0].value if parse('authority').find(cert) else None
            certifications.append({'name': name, 'authority': authority})

        # 9. Languages (list of dicts)
        languages_expr = parse('$.elements[0].profileLanguages.elements[*]')
        langs_raw = [match.value for match in languages_expr.find(data)]
        languages = []
        for lang in langs_raw:
            name = parse('multiLocaleName.en_US').find(lang)[0].value if parse('multiLocaleName.en_US').find(
                lang) else None
            proficiency = parse('proficiency').find(lang)[0].value if parse('proficiency').find(lang) else None
            languages.append({'name': name, 'proficiency': proficiency})

        # 10. Volunteer Experiences (list of dicts)
        volunteer_expr = parse('$.elements[0].profileVolunteerExperiences.elements[*]')
        vol_raw = [match.value for match in volunteer_expr.find(data)]
        volunteers = []
        for vol in vol_raw:
            role = parse('multiLocaleRole.en_US').find(vol)[0].value if parse('multiLocaleRole.en_US').find(
                vol) else None
            company = parse('multiLocaleCompanyName.en_US').find(vol)[0].value if parse(
                'multiLocaleCompanyName.en_US').find(vol) else None
            volunteers.append({'role': role, 'company': company})

        # 11. Profile Picture URL (example 200x200)
        pic_root_url_expr = parse('$.elements[0].profilePicture.displayImageReference.vectorImage.rootUrl')
        pic_root_url = pic_root_url_expr.find(data)[0].value if pic_root_url_expr.find(data) else None

        pic_path_expr = parse(
            '$.elements[0].profilePicture.displayImageReference.vectorImage.artifacts[0].fileIdentifyingUrlPathSegment')
        pic_path = pic_path_expr.find(data)[0].value if pic_path_expr.find(data) else None

        profile_pic_url = pic_root_url + pic_path if pic_root_url and pic_path else None

        # 12. Volunteer Causes (list)
        causes_expr = parse('$.elements[0].volunteerCauses[*]')
        causes = [match.value for match in causes_expr.find(data)]

        # Output all extracted info
        extracted_info = {
            'public_id': public_id,
            'full_name': full_name,
            'headline': headline,
            'summary': summary,
            'address': address,
            'country': country,
            'educations': educations,
            'positions': positions,
            'skills': skills,
            'certifications': certifications,
            'languages': languages,
            'volunteer_experiences': volunteers,
            'profile_pic_url': profile_pic_url,
            'volunteer_causes': causes
        }

        return extracted_info
