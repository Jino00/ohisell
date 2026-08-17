.headers on
.mode csv
SELECT action, dry_run, COUNT(*) AS n, MIN(changed_at) AS min_d, MAX(changed_at) AS max_d,
       COUNT(DISTINCT campaign_id) AS distinct_campaigns
FROM naver_change_log GROUP BY action, dry_run ORDER BY n DESC;
