.headers on
.mode column
SELECT adgroup_id,
       COUNT(*) AS rows,
       SUM(cost) AS cost_90d,
       SUM(imp) AS imp_90d,
       SUM(clk) AS clk_90d,
       MIN(ad_date) AS min_d,
       MAX(ad_date) AS max_d
FROM naver_ad_daily
WHERE adgroup_id IN (
  'grp-a001-02-000000045687119',
  'grp-a001-02-000000054536931',
  'grp-a001-02-000000044490484',
  'grp-a001-01-000000070512941'
)
AND ad_date >= date('now','-90 day')
GROUP BY adgroup_id;

SELECT 'TOTAL_4_GROUPS' AS label, SUM(cost) AS cost_90d
FROM naver_ad_daily
WHERE adgroup_id IN (
  'grp-a001-02-000000045687119',
  'grp-a001-02-000000054536931',
  'grp-a001-02-000000044490484',
  'grp-a001-01-000000070512941'
)
AND ad_date >= date('now','-90 day');

SELECT 'TOTAL_ALL_SHOPPING_WEB' AS label, SUM(cost) AS cost_90d
FROM naver_ad_daily
WHERE ad_date >= date('now','-90 day');
