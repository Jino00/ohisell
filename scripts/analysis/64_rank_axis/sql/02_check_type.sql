.headers on
.mode csv
SELECT campaign_type, COUNT(*) AS n FROM naver_ad_daily WHERE keyword_id<>'' GROUP BY campaign_type;
SELECT 'change_log_by_action' AS label, action, dry_run, COUNT(*) AS n, MIN(changed_at) AS min_d, MAX(changed_at) AS max_d, COUNT(DISTINCT campaign_id) AS distinct_campaigns
FROM naver_change_log GROUP BY action, dry_run ORDER BY n DESC;
