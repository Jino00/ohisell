.headers on
.mode csv
SELECT 'WEB_SITE_keyword_empty' AS label, COUNT(*) AS n, SUM(cost) AS sum_cost,
       COUNT(DISTINCT adgroup_id) AS distinct_adgroups, MIN(ad_date) AS min_d, MAX(ad_date) AS max_d
FROM naver_ad_daily WHERE campaign_type='WEB_SITE' AND keyword_id='' AND adgroup_id<>'__backfill__';
SELECT 'WEB_SITE_keyword_empty_by_adgroup_sample' AS label, adgroup_id, COUNT(*) AS n, SUM(cost) AS sum_cost
FROM naver_ad_daily WHERE campaign_type='WEB_SITE' AND keyword_id='' AND adgroup_id<>'__backfill__'
GROUP BY adgroup_id ORDER BY sum_cost DESC LIMIT 10;
SELECT 'WEB_SITE_backfill' AS label, COUNT(*) AS n, SUM(cost) AS sum_cost
FROM naver_ad_daily WHERE campaign_type='WEB_SITE' AND adgroup_id='__backfill__';
