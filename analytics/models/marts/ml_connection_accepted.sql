-- One row per profile that had a connection request sent (reached PENDING).
-- target: 1 = accepted (reached CONNECTED+), 0 = not accepted (stuck at PENDING)

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
)

SELECT
    deal_id,
    lead_id,
    accepted,

    -- Profile features
    headline,
    city_name,
    company_name,
    location,
    connection_degree,
    num_positions,
    num_educations,

    -- Derived features
    CASE WHEN summary IS NOT NULL AND length(summary) > 0
         THEN 1 ELSE 0
    END AS has_summary,
    length(coalesce(headline, '')) AS headline_length,

    -- Timestamps
    deal_created,
    deal_updated,
    stages_dates,

    -- Raw JSON for advanced feature extraction in Python
    positions_json,
    educations_json

FROM profiles
