.headers on
.mode column
.separator " | "

SELECT '=== total ops_diary_entries rows ===' AS section;
SELECT COUNT(*) FROM ops_diary_entries;

SELECT '=== date range ===' AS section;
SELECT MIN(created_at), MAX(created_at) FROM ops_diary_entries;

SELECT '=== rows with action=search_term_exclude, event_type=observe, last 60d ===' AS section;
SELECT id, created_at, event_type, actor, action, substr(rationale,1,150) AS rationale_head
FROM ops_diary_entries
WHERE action = 'search_term_exclude'
ORDER BY created_at DESC
LIMIT 20;

SELECT '=== distinct action values in ops_diary_entries (last 90d) ===' AS section;
SELECT action, COUNT(*) AS n
FROM ops_diary_entries
WHERE created_at >= date('now', '-90 day')
GROUP BY action
ORDER BY n DESC
LIMIT 30;

SELECT '=== search_term_judge output check: rows in naver_search_term_daily by source, last 30d ===' AS section;
SELECT source, COUNT(*) AS n, COUNT(DISTINCT search_term) AS distinct_terms, SUM(cost) AS total_cost
FROM naver_search_term_daily
WHERE ad_date >= date('now', '-30 day')
GROUP BY source;

SELECT '=== naver_search_term_daily campaign_id -> campaign_type join, source counts last 30d ===' AS section;
SELECT d.campaign_type, COUNT(*) AS n_rows, SUM(t.cost) AS total_cost
FROM naver_search_term_daily t
LEFT JOIN (SELECT DISTINCT campaign_id, campaign_type FROM naver_ad_daily) d
  ON d.campaign_id = t.campaign_id
WHERE t.ad_date >= date('now', '-30 day')
GROUP BY d.campaign_type;
