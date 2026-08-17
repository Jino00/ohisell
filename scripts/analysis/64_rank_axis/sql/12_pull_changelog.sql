.headers on
.mode csv
SELECT id, changed_at, entity_type, entity_id, campaign_id, action, dry_run, occurred_at
FROM naver_change_log;
