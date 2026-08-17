.headers on
.mode column
.separator " | "

SELECT '=== 1. naver_proposals type=search_term_exclude, last 30d, joined to campaign_type via naver_ad_daily ===' AS section;
SELECT
  d.campaign_type,
  p.status,
  COUNT(*) AS n
FROM naver_proposals p
LEFT JOIN (
  SELECT DISTINCT campaign_id, campaign_type FROM naver_ad_daily
) d ON d.campaign_id = p.campaign_id
WHERE p.proposal_type = 'search_term_exclude'
  AND p.created_at >= date('now', '-30 day')
GROUP BY d.campaign_type, p.status
ORDER BY d.campaign_type, p.status;

SELECT '=== 1b. same, all-time (no window) ===' AS section;
SELECT
  d.campaign_type,
  p.status,
  COUNT(*) AS n,
  MIN(p.created_at) AS earliest,
  MAX(p.created_at) AS latest
FROM naver_proposals p
LEFT JOIN (
  SELECT DISTINCT campaign_id, campaign_type FROM naver_ad_daily
) d ON d.campaign_id = p.campaign_id
WHERE p.proposal_type = 'search_term_exclude'
GROUP BY d.campaign_type, p.status
ORDER BY d.campaign_type, p.status;

SELECT '=== 2. ops_diary_entries mentioning shopping briefing, last 30d ===' AS section;
SELECT
  date(created_at) AS d,
  COUNT(*) AS n,
  MAX(created_at) AS latest_ts
FROM ops_diary_entries
WHERE rationale LIKE '%쇼핑 손실 검색어%'
  AND created_at >= date('now', '-30 day')
GROUP BY date(created_at)
ORDER BY d DESC;

SELECT '=== 2b. same diary text, most recent 5 rows overall (any date) ===' AS section;
SELECT id, created_at, substr(rationale,1,140) AS rationale_head
FROM ops_diary_entries
WHERE rationale LIKE '%쇼핑 손실 검색어%'
ORDER BY created_at DESC
LIMIT 5;

SELECT '=== 3. ops_diary_entries with phrase "콘솔 수동 제외 대상" (claimed at scheduler_service.py:832) ===' AS section;
SELECT id, created_at, substr(rationale,1,140) AS rationale_head
FROM ops_diary_entries
WHERE rationale LIKE '%콘솔 수동 제외 대상%'
ORDER BY created_at DESC
LIMIT 5;

SELECT '=== 4. BRAND_SEARCH campaigns: distinct campaign_id from naver_ad_daily, all-time ===' AS section;
SELECT DISTINCT campaign_id, campaign_type
FROM naver_ad_daily
WHERE campaign_type = 'BRAND_SEARCH';

SELECT '=== 4b. naver_campaign_settings row count for those BRAND_SEARCH campaign_ids ===' AS section;
SELECT s.campaign_id, s.optimizer, s.auto_operate
FROM naver_campaign_settings s
WHERE s.campaign_id IN (
  SELECT DISTINCT campaign_id FROM naver_ad_daily WHERE campaign_type = 'BRAND_SEARCH'
);

SELECT '=== 4c. total distinct campaigns by type (all-time) vs settings row count ===' AS section;
SELECT
  d.campaign_type,
  COUNT(DISTINCT d.campaign_id) AS n_campaigns,
  SUM(CASE WHEN s.campaign_id IS NOT NULL THEN 1 ELSE 0 END) AS n_with_settings
FROM (SELECT DISTINCT campaign_id, campaign_type FROM naver_ad_daily) d
LEFT JOIN naver_campaign_settings s ON s.campaign_id = d.campaign_id
GROUP BY d.campaign_type;

SELECT '=== 5. optimizer value distribution, naver_campaign_settings (all rows) ===' AS section;
SELECT optimizer, COUNT(*) AS n
FROM naver_campaign_settings
GROUP BY optimizer;

SELECT '=== 5b. optimizer distribution restricted to campaigns with nonzero cost in last 90 days (active roster) ===' AS section;
SELECT s.optimizer, COUNT(*) AS n
FROM naver_campaign_settings s
WHERE s.campaign_id IN (
  SELECT DISTINCT campaign_id FROM naver_ad_daily
  WHERE ad_date >= date('now', '-90 day')
)
GROUP BY s.optimizer;

SELECT '=== 5c. full row listing of naver_campaign_settings (small table expected) ===' AS section;
SELECT campaign_id, optimizer, auto_operate, updated_at
FROM naver_campaign_settings
ORDER BY campaign_id;
