SELECT
    id AS lead_id,
    website AS linkedin_url,
    first_name,
    last_name,
    title AS headline,
    city_name,
    company_name,
    email,
    description AS profile_json,
    -- Parsed JSON fields for features (guard against empty/null description)
    CASE WHEN description IS NOT NULL AND description != ''
         THEN json_extract_string(description, '$.summary')
         ELSE NULL END AS summary,
    CASE WHEN description IS NOT NULL AND description != ''
         THEN json_extract_string(description, '$.location_name')
         ELSE NULL END AS location,
    CASE WHEN description IS NOT NULL AND description != ''
         THEN json_extract(description, '$.positions')
         ELSE NULL END AS positions_json,
    CASE WHEN description IS NOT NULL AND description != ''
         THEN json_extract(description, '$.educations')
         ELSE NULL END AS educations_json,
    CASE WHEN description IS NOT NULL AND description != ''
         THEN json_extract_string(description, '$.industry.name')
         ELSE NULL END AS industry_name,
    CASE WHEN description IS NOT NULL AND description != ''
         THEN json_extract_string(description, '$.geo.defaultLocalizedNameWithoutCountryName')
         ELSE NULL END AS geo_name,
    CASE WHEN description IS NOT NULL AND description != ''
         THEN json_extract(description, '$.connection_degree')
         ELSE NULL END AS connection_degree,
    CASE WHEN description IS NOT NULL AND description != ''
              AND json_array_length(json_extract(description, '$.positions')) IS NOT NULL
         THEN json_array_length(json_extract(description, '$.positions'))
         ELSE 0 END AS num_positions,
    CASE WHEN description IS NOT NULL AND description != ''
              AND json_array_length(json_extract(description, '$.educations')) IS NOT NULL
         THEN json_array_length(json_extract(description, '$.educations'))
         ELSE 0 END AS num_educations,
    disqualified,
    contact_id,
    creation_date,
    update_date
FROM crm.crm_lead
