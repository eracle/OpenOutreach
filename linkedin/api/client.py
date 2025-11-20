import logging
from typing import Optional, Dict, Tuple
from urllib.parse import urlparse

from jsonpath_ng import parse

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Custom exception for 401 Unauthorized errors."""
    pass


class PlaywrightLinkedinAPI:

    def __init__(
            self,
            resources=None,
            *,
            debug=False,
    ):
        """Constructor method"""
        if not resources:
            raise ValueError("Playwright resources must be provided.")

        # Unpack Playwright resources
        self.page, self.context, self.browser, self.playwright = resources  # Assuming sync API
        logger.info("Using provided Playwright resources for requests.")

        # Extract cookies from the browser context to get JSESSIONID for csrf-token
        cookies = self.context.cookies()
        cookies_dict = {c['name']: c['value'] for c in cookies}
        jsessionid = cookies_dict.get('JSESSIONID', '').strip('"')

        # Dynamically fetch browser-specific details using page.evaluate
        user_agent = self.page.evaluate("navigator.userAgent")
        accept_language = self.page.evaluate("navigator.languages ? navigator.languages.join(',') : navigator.language")
        sec_ch_ua = self.page.evaluate("""() => {
            if (navigator.userAgentData) {
                return navigator.userAgentData.brands.map(brand => `"${brand.brand}";v="${brand.version}"`).join(', ');
            }
            return '';
        }""")
        sec_ch_ua_mobile = self.page.evaluate(
            """() => navigator.userAgentData ? (navigator.userAgentData.mobile ? '?1' : '?0') : '?0' """)
        sec_ch_ua_platform = self.page.evaluate(
            """() => navigator.userAgentData ? `"${navigator.userAgentData.platform}"` : '' """)

        # Set up headers with dynamic values
        self.headers = {
            'accept': 'application/vnd.linkedin.normalized+json+2.1',
            'accept-language': accept_language,
            'csrf-token': jsessionid,
            'priority': 'u=1, i',
            'referer': self.page.url,
            'sec-ch-prefers-color-scheme': 'light',  # This might need dynamic detection if possible
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_ua_mobile,
            'sec-ch-ua-platform': sec_ch_ua_platform,
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': user_agent,
            'x-li-lang': 'en_US',
            'x-restli-protocol-version': '2.0.0',
        }

        logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)
        self.logger = logger

    def get_profile(
            self, public_id: Optional[str] = None, profile_url: Optional[str] = None
    ) -> Tuple[Dict, Dict]:
        """Fetch data for a given LinkedIn profile using Playwright context requests.

        :param public_id: LinkedIn public ID for a profile
        :type public_id: str, optional
        :param profile_url: Full LinkedIn profile URL
        :type profile_url: str, optional

        :return: A pair of dictionaries: (parsed_data, original_data)
        :rtype: tuple[dict, dict]
        """
        if not public_id and profile_url:
            public_id = urlparse(profile_url).path.strip('/').split('/')[-1]

        if not public_id:
            raise ValueError("Either public_id or profile_url must be provided.")

        params = {
            'decorationId': 'com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-91',
            'memberIdentity': public_id,
            'q': 'memberIdentity',
        }

        base_url = "https://www.linkedin.com/voyager/api"
        uri = "/identity/dash/profiles"
        full_url = base_url + uri

        # Use Playwright context request to fetch API data
        res = self.context.request.get(full_url, params=params, headers=self.headers)

        if res.status == 401:
            self.logger.error(f"Authentication failed (401): {res.body()}")
            raise AuthenticationError("LinkedIn API returned 401 Unauthorized.")
        elif not res.ok:
            self.logger.info(f"Request failed with status {res.status}: {res.body()}")
            return {}, {}

        data = res.json()
        #self.logger.info(f"Request returned with status {res.status}: {res.body()}")

        # Now, extract using JSONPath with robust descendant searches

        # 1. Full Name
        first_name_expr = parse('$..multiLocaleFirstName.en_US')
        matches = first_name_expr.find(data)
        first_name = matches[0].value if matches else None

        last_name_expr = parse('$..multiLocaleLastName.en_US')
        matches = last_name_expr.find(data)
        last_name = matches[0].value if matches else None

        full_name = f"{first_name} {last_name}" if first_name and last_name else None

        # 2. Headline
        headline_expr = parse('$..multiLocaleHeadline.en_US')
        matches = headline_expr.find(data)
        headline = matches[0].value if matches else None

        # 3. Summary
        summary_expr = parse('$..multiLocaleSummary.en_US')
        matches = summary_expr.find(data)
        summary = matches[0].value if matches else None

        # 4. Location/Address
        address_expr = parse('$..multiLocaleAddress.en_US')
        matches = address_expr.find(data)
        address = matches[0].value if matches else None

        country_expr = parse('$..geoLocation.geo.defaultLocalizedName')
        matches = country_expr.find(data)
        country = matches[0].value if matches else None

        # 5. Education (list of dicts with key details)
        education_expr = parse('$..profileEducations.elements[*]')
        educations_raw = [match.value for match in education_expr.find(data)]
        educations = []
        for edu in educations_raw:
            school_matches = parse('multiLocaleSchoolName.en_US').find(edu)
            school = school_matches[0].value if school_matches else None

            degree_matches = parse('multiLocaleDegreeName.en_US').find(edu)
            degree = degree_matches[0].value if degree_matches else None

            field_matches = parse('multiLocaleFieldOfStudy.en_US').find(edu)
            field = field_matches[0].value if field_matches else None

            start_year_matches = parse('dateRange.start.year').find(edu)
            start_year = start_year_matches[0].value if start_year_matches else None

            end_year_matches = parse('dateRange.end.year').find(edu)
            end_year = end_year_matches[0].value if end_year_matches else None

            educations.append({
                'school': school,
                'degree': degree,
                'field': field,
                'start_year': start_year,
                'end_year': end_year
            })

        # 6. Experience/Positions (list of dicts with key details)
        positions_expr = parse('$..profilePositionGroups.elements[*]')
        positions_raw = [match.value for match in positions_expr.find(data)]
        positions = []
        for pos_group in positions_raw:
            title_expr = parse('profilePositionInPositionGroup.elements[0].multiLocaleTitle.en_US')
            title_matches = title_expr.find(pos_group)
            title = title_matches[0].value if title_matches else None

            company_matches = parse('multiLocaleCompanyName.en_US').find(pos_group)
            company = company_matches[0].value if company_matches else (
                parse('company.name').find(pos_group)[0].value if parse('company.name').find(pos_group) else None)

            start_month_matches = parse('dateRange.start.month').find(pos_group)
            start_month = start_month_matches[0].value if start_month_matches else None

            start_year_matches = parse('dateRange.start.year').find(pos_group)
            start_year = start_year_matches[0].value if start_year_matches else None

            end_month_matches = parse('dateRange.end.month').find(pos_group)
            end_month = end_month_matches[0].value if end_month_matches else None

            end_year_matches = parse('dateRange.end.year').find(pos_group)
            end_year = end_year_matches[0].value if end_year_matches else None

            description_expr = parse('profilePositionInPositionGroup.elements[0].multiLocaleDescription.en_US')
            description_matches = description_expr.find(pos_group)
            description = description_matches[0].value if description_matches else None

            positions.append({
                'title': title,
                'company': company,
                'start': f"{start_month}/{start_year}" if start_month and start_year else start_year,
                'end': f"{end_month}/{end_year}" if end_month and end_year else end_year,
                'description': description
            })

        # 7. Skills (list of strings)
        skills_expr = parse('$..profileSkills.elements[*].multiLocaleName.en_US')
        skills = [match.value for match in skills_expr.find(data)]

        # 8. Certifications (list of dicts)
        certifications_expr = parse('$..profileCertifications.elements[*]')
        certs_raw = [match.value for match in certifications_expr.find(data)]
        certifications = []
        for cert in certs_raw:
            name_matches = parse('multiLocaleName.en_US').find(cert)
            name = name_matches[0].value if name_matches else None

            authority_matches = parse('authority').find(cert)
            authority = authority_matches[0].value if authority_matches else None

            certifications.append({'name': name, 'authority': authority})

        # 9. Languages (list of dicts)
        languages_expr = parse('$..profileLanguages.elements[*]')
        langs_raw = [match.value for match in languages_expr.find(data)]
        languages = []
        for lang in langs_raw:
            name_matches = parse('multiLocaleName.en_US').find(lang)
            name = name_matches[0].value if name_matches else None

            proficiency_matches = parse('proficiency').find(lang)
            proficiency = proficiency_matches[0].value if proficiency_matches else None

            languages.append({'name': name, 'proficiency': proficiency})

        # 10. Volunteer Experiences (list of dicts)
        volunteer_expr = parse('$..profileVolunteerExperiences.elements[*]')
        vol_raw = [match.value for match in volunteer_expr.find(data)]
        volunteers = []
        for vol in vol_raw:
            role_matches = parse('multiLocaleRole.en_US').find(vol)
            role = role_matches[0].value if role_matches else None

            company_matches = parse('multiLocaleCompanyName.en_US').find(vol)
            company = company_matches[0].value if company_matches else None

            volunteers.append({'role': role, 'company': company})

        # 11. Profile Picture URL (example 200x200)
        pic_root_url_expr = parse('$..profilePicture.displayImageReference.vectorImage.rootUrl')
        pic_root_matches = pic_root_url_expr.find(data)
        pic_root_url = pic_root_matches[0].value if pic_root_matches else None

        pic_path_expr = parse(
            '$..profilePicture.displayImageReference.vectorImage.artifacts[0].fileIdentifyingUrlPathSegment')
        pic_path_matches = pic_path_expr.find(data)
        pic_path = pic_path_matches[0].value if pic_path_matches else None

        profile_pic_url = pic_root_url + pic_path if pic_root_url and pic_path else None

        # 12. Volunteer Causes (list)
        causes_expr = parse('$..volunteerCauses[*]')
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
            'volunteer_causes': causes,
        }

        return extracted_info, data