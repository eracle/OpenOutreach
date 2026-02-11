SELECT
    d.id AS deal_id,
    d.lead_id,
    d.name,
    d.ticket,
    s.name AS current_stage,
    d.stages_dates,
    d.active,
    d.creation_date,
    d.update_date,
    d.win_closing_date
FROM crm.crm_deal d
LEFT JOIN crm.crm_stage s ON d.stage_id = s.id
