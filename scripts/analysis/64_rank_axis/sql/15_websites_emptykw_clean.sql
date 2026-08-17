.headers on
.mode csv
SELECT 'WEB_SITE_keyword_empty_nonbackfill_n' AS metric,
       (SELECT COUNT(*) FROM naver_ad_daily WHERE campaign_type='WEB_SITE' AND keyword_id='' AND adgroup_id<>'__backfill__') AS n;
SELECT 'WEB_SITE_keyword_empty_nonbackfill_sum_cost' AS metric,
       (SELECT SUM(cost) FROM naver_ad_daily WHERE campaign_type='WEB_SITE' AND keyword_id='' AND adgroup_id<>'__backfill__') AS n;
SELECT 'WEB_SITE_keyword_empty_nonbackfill_distinct_adgroups' AS metric,
       (SELECT COUNT(DISTINCT adgroup_id) FROM naver_ad_daily WHERE campaign_type='WEB_SITE' AND keyword_id='' AND adgroup_id<>'__backfill__') AS n;
SELECT 'WEB_SITE_backfill_n' AS metric,
       (SELECT COUNT(*) FROM naver_ad_daily WHERE campaign_type='WEB_SITE' AND adgroup_id='__backfill__') AS n;
SELECT 'WEB_SITE_backfill_sum_cost' AS metric,
       (SELECT SUM(cost) FROM naver_ad_daily WHERE campaign_type='WEB_SITE' AND adgroup_id='__backfill__') AS n;
