-- One row per profile that had a connection request sent (reached PENDING).
-- target: 1 = accepted (reached CONNECTED+), 0 = not accepted (stuck at PENDING)
-- 24 mechanical features + profile_text for keyword extraction

WITH profiles AS (
    SELECT
        d.deal_id,
        d.lead_id,
        d.current_stage,
        d.stages_dates,
        d.creation_date AS deal_created,
        d.update_date AS deal_updated,

        -- Target: accepted if current stage is CONNECTED or beyond
        CASE WHEN d.current_stage IN ('Connected', 'Completed')
             THEN 1 ELSE 0
        END AS accepted,

        -- Lead features
        l.headline,
        l.city_name,
        l.company_name,
        l.location,
        l.industry_name,
        l.geo_name,
        l.connection_degree,
        l.num_positions,
        l.num_educations,
        l.summary,
        l.positions_json,
        l.educations_json,
        l.profile_json

    FROM {{ ref('stg_deals') }} d
    JOIN {{ ref('stg_leads') }} l ON d.lead_id = l.lead_id
    WHERE d.current_stage IN ('Pending', 'Connected', 'Completed')
),

-- Unnest positions array and extract per-position fields
positions_unnested AS (
    SELECT
        p.deal_id,
        s.title AS pos_title,
        s.company_name AS pos_company,
        s.location AS pos_location,
        s.description AS pos_description,
        s.start_year,
        s.start_month,
        s.end_year,
        s.end_month,
        CASE WHEN s.end_year IS NULL THEN 1 ELSE 0 END AS is_current,
        -- Fractional start date
        CASE WHEN s.start_year IS NOT NULL
             THEN s.start_year + coalesce(s.start_month, 1) / 12.0
             ELSE NULL END AS start_frac,
        -- Fractional end date (NULL end = current)
        CASE WHEN s.end_year IS NOT NULL
             THEN s.end_year + coalesce(s.end_month, 1) / 12.0
             ELSE EXTRACT(YEAR FROM CURRENT_DATE) + EXTRACT(MONTH FROM CURRENT_DATE) / 12.0
             END AS end_frac
    FROM profiles p,
    LATERAL (
        SELECT UNNEST(
            from_json(p.positions_json,
                '[{"title":"VARCHAR","company_name":"VARCHAR","location":"VARCHAR","description":"VARCHAR","start_year":"INT","start_month":"INT","end_year":"INT","end_month":"INT"}]'
            )
        )
    ) AS t(s)
    WHERE p.positions_json IS NOT NULL
      AND json_array_length(p.positions_json) > 0
),

-- Aggregate position features per deal
position_aggs AS (
    SELECT
        deal_id,
        COUNT(DISTINCT pos_company) AS num_distinct_companies,
        CASE WHEN COUNT(*) > 0
             THEN SUM(CASE WHEN pos_description IS NOT NULL AND length(pos_description) > 0 THEN 1 ELSE 0 END)::FLOAT / COUNT(*)
             ELSE 0 END AS positions_with_description_ratio,
        COUNT(DISTINCT pos_location) FILTER (WHERE pos_location IS NOT NULL) AS num_position_locations,
        SUM(length(coalesce(pos_description, ''))) AS total_description_length,
        CASE WHEN SUM(CASE WHEN pos_description IS NOT NULL AND length(pos_description) > 0 THEN 1 ELSE 0 END) > 0
             THEN 1 ELSE 0 END AS has_position_descriptions,
        MAX(is_current) AS is_currently_employed,
        AVG(length(coalesce(pos_title, ''))) AS avg_title_length,
        -- Years experience: earliest start to latest end
        CASE WHEN MIN(start_frac) IS NOT NULL AND MAX(end_frac) IS NOT NULL
             THEN MAX(end_frac) - MIN(start_frac)
             ELSE 0 END AS years_experience,
        -- Current position tenure (months) — latest current position
        CASE WHEN MAX(is_current) = 1
             THEN (MAX(end_frac) FILTER (WHERE is_current = 1) - MAX(start_frac) FILTER (WHERE is_current = 1)) * 12
             ELSE 0 END AS current_position_tenure_months,
        -- Avg tenure across all positions (months)
        AVG((end_frac - start_frac) * 12) FILTER (WHERE start_frac IS NOT NULL) AS avg_position_tenure_months,
        -- Longest single tenure (months)
        MAX((end_frac - start_frac) * 12) FILTER (WHERE start_frac IS NOT NULL) AS longest_tenure_months,
        -- Concatenated text for keyword matching
        string_agg(
            coalesce(pos_title, '') || ' ' || coalesce(pos_company, '') || ' ' || coalesce(pos_location, '') || ' ' || coalesce(pos_description, ''),
            ' '
        ) AS positions_text
    FROM positions_unnested
    GROUP BY deal_id
),

-- Unnest educations and aggregate
education_aggs AS (
    SELECT
        p.deal_id,
        MAX(CASE WHEN edu.degree IS NOT NULL AND length(edu.degree) > 0 THEN 1 ELSE 0 END) AS has_education_degree,
        MAX(CASE WHEN edu.field_of_study IS NOT NULL AND length(edu.field_of_study) > 0 THEN 1 ELSE 0 END) AS has_field_of_study,
        CASE WHEN SUM(CASE WHEN edu.degree IS NOT NULL AND length(edu.degree) > 0 THEN 1 ELSE 0 END) > 1
             THEN 1 ELSE 0 END AS has_multiple_degrees,
        string_agg(
            coalesce(edu.school_name, '') || ' ' || coalesce(edu.degree, '') || ' ' || coalesce(edu.field_of_study, ''),
            ' '
        ) AS educations_text
    FROM profiles p,
    LATERAL (
        SELECT UNNEST(
            from_json(p.educations_json,
                '[{"school_name":"VARCHAR","degree":"VARCHAR","field_of_study":"VARCHAR"}]'
            )
        )
    ) AS t(edu)
    WHERE p.educations_json IS NOT NULL
      AND json_array_length(p.educations_json) > 0
    GROUP BY p.deal_id
)

SELECT
    p.deal_id,
    p.lead_id,
    p.accepted,

    -- Basic profile features
    p.headline,
    p.city_name,
    p.company_name,
    p.location,
    p.industry_name,
    p.geo_name,
    p.connection_degree,
    p.num_positions,
    p.num_educations,

    -- Derived boolean/length features
    CASE WHEN p.summary IS NOT NULL AND length(p.summary) > 0
         THEN 1 ELSE 0
    END AS has_summary,
    length(coalesce(p.headline, '')) AS headline_length,
    length(coalesce(p.summary, '')) AS summary_length,
    CASE WHEN p.industry_name IS NOT NULL AND length(p.industry_name) > 0
         THEN 1 ELSE 0
    END AS has_industry,
    CASE WHEN p.geo_name IS NOT NULL AND length(p.geo_name) > 0
         THEN 1 ELSE 0
    END AS has_geo,
    CASE WHEN p.location IS NOT NULL AND length(p.location) > 0
         THEN 1 ELSE 0
    END AS has_location,
    CASE WHEN p.company_name IS NOT NULL AND length(p.company_name) > 0
         THEN 1 ELSE 0
    END AS has_company,

    -- Position-derived features
    coalesce(pa.num_distinct_companies, 0) AS num_distinct_companies,
    coalesce(pa.positions_with_description_ratio, 0) AS positions_with_description_ratio,
    coalesce(pa.num_position_locations, 0) AS num_position_locations,
    coalesce(pa.total_description_length, 0) AS total_description_length,
    coalesce(pa.has_position_descriptions, 0) AS has_position_descriptions,
    coalesce(pa.is_currently_employed, 0) AS is_currently_employed,
    coalesce(pa.avg_title_length, 0) AS avg_title_length,
    coalesce(pa.years_experience, 0) AS years_experience,
    coalesce(pa.current_position_tenure_months, 0) AS current_position_tenure_months,
    coalesce(pa.avg_position_tenure_months, 0) AS avg_position_tenure_months,
    coalesce(pa.longest_tenure_months, 0) AS longest_tenure_months,

    -- Education-derived features
    coalesce(ea.has_education_degree, 0) AS has_education_degree,
    coalesce(ea.has_field_of_study, 0) AS has_field_of_study,
    coalesce(ea.has_multiple_degrees, 0) AS has_multiple_degrees,

    -- Timestamps
    p.deal_created,
    p.deal_updated,
    p.stages_dates,

    -- Text for keyword feature extraction in Python
    lower(
        coalesce(p.headline, '') || ' ' ||
        coalesce(p.summary, '') || ' ' ||
        coalesce(p.location, '') || ' ' ||
        coalesce(p.industry_name, '') || ' ' ||
        coalesce(pa.positions_text, '') || ' ' ||
        coalesce(ea.educations_text, '')
    ) AS profile_text,

    -- Raw JSON kept for backward compatibility
    p.positions_json,
    p.educations_json

FROM profiles p
LEFT JOIN position_aggs pa ON p.deal_id = pa.deal_id
LEFT JOIN education_aggs ea ON p.deal_id = ea.deal_id
