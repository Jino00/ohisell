.headers on
.mode csv
SELECT 'L2_total' AS label, COUNT(*) AS n, MIN(ad_date) AS min_d, MAX(ad_date) AS max_d,
       COUNT(DISTINCT ad_date) AS distinct_dates
FROM naver_ad_daily WHERE keyword_id <> '';
SELECT 'L2_after_filters' AS label, COUNT(*) AS n, MIN(ad_date) AS min_d, MAX(ad_date) AS max_d,
       COUNT(DISTINCT ad_date) AS distinct_dates
FROM naver_ad_daily
WHERE keyword_id <> '' AND adgroup_id <> '__backfill__' AND campaign_type <> '';
SELECT 'L3_total' AS label, COUNT(*) AS n, MIN(ad_date) AS min_d, MAX(ad_date) AS max_d,
       COUNT(DISTINCT ad_date) AS distinct_dates
FROM naver_search_term_daily WHERE source='shopping';
SELECT 'L3_expkeyword' AS label, COUNT(*) AS n, MIN(ad_date) AS min_d, MAX(ad_date) AS max_d,
       COUNT(DISTINCT ad_date) AS distinct_dates
FROM naver_search_term_daily WHERE source='expkeyword';
SELECT 'change_log_total' AS label, COUNT(*) AS n, MIN(changed_at) AS min_d, MAX(changed_at) AS max_d
FROM naver_change_log;
