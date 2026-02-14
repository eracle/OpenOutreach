-- One row per profile that had a connection request sent (reached PENDING).
-- target: 1 = accepted (reached CONNECTED+), 0 = not accepted (stuck at PENDING)
-- 3 mechanical features + profile_text for keyword extraction

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
        l.summary,
        l.location,
        l.industry_name,
        l.connection_degree,
        l.positions_json,
        l.educations_json

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
        MAX(is_current) AS is_currently_employed,
        -- Years experience: earliest start to latest end
        CASE WHEN MIN(start_frac) IS NOT NULL AND MAX(end_frac) IS NOT NULL
             THEN MAX(end_frac) - MIN(start_frac)
             ELSE 0 END AS years_experience,
        -- Concatenated text for keyword matching
        string_agg(
            coalesce(pos_title, '') || ' ' || coalesce(pos_company, '') || ' ' || coalesce(pos_location, '') || ' ' || coalesce(pos_description, ''),
            ' '
        ) AS positions_text
    FROM positions_unnested
    GROUP BY deal_id
),

-- Unnest educations and aggregate text for keyword matching
education_aggs AS (
    SELECT
        p.deal_id,
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

    -- Mechanical features
    p.connection_degree,
    coalesce(pa.is_currently_employed, 0) AS is_currently_employed,
    coalesce(pa.years_experience, 0) AS years_experience,

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
