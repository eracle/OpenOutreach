SELECT
    id AS stage_id,
    name AS stage_name,
    index_number,
    "default" AS is_default,
    success_stage AS is_success
FROM crm.crm_stage
