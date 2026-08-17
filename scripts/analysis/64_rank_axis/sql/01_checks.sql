.headers on
.mode csv
SELECT 'L3_backfill_adgroup' AS label, COUNT(*) AS n FROM naver_search_term_daily WHERE adgroup_id='__backfill__';
.schema naver_search_term_daily
SELECT 'L2_imp0_cost_pos' AS label, COUNT(*) AS n, SUM(cost) AS sum_cost
FROM naver_ad_daily WHERE keyword_id<>'' AND adgroup_id<>'__backfill__' AND campaign_type<>'' AND imp=0 AND cost>0;
SELECT 'L2_imp0_total' AS label, COUNT(*) AS n, SUM(cost) AS sum_cost
FROM naver_ad_daily WHERE keyword_id<>'' AND adgroup_id<>'__backfill__' AND campaign_type<>'' AND imp=0;
SELECT 'L3_imp0_cost_pos' AS label, COUNT(*) AS n, SUM(cost) AS sum_cost
FROM naver_search_term_daily WHERE source='shopping' AND imp=0 AND cost>0;
SELECT 'L3_imp0_total' AS label, COUNT(*) AS n, SUM(cost) AS sum_cost
FROM naver_search_term_daily WHERE source='shopping' AND imp=0;
SELECT DISTINCT campaign_type FROM naver_ad_daily;
