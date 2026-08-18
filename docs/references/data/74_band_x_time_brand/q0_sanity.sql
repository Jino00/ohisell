.headers on
.mode csv
SELECT 'total_rows', COUNT(*) FROM naver_search_term_daily;
SELECT 'distinct_adgroup', COUNT(DISTINCT adgroup_id) FROM naver_search_term_daily;
SELECT 'min_date', MIN(ad_date) FROM naver_search_term_daily;
SELECT 'max_date', MAX(ad_date) FROM naver_search_term_daily;
SELECT 'backfill_sentinel_rows', COUNT(*) FROM naver_search_term_daily WHERE adgroup_id='__backfill__';
SELECT 'blank_search_term', COUNT(*) FROM naver_search_term_daily WHERE search_term='' OR search_term IS NULL;
SELECT 'source', source, COUNT(*) FROM naver_search_term_daily GROUP BY source;
SELECT 'window_rows_20250723_20260816', COUNT(*) FROM naver_search_term_daily WHERE ad_date BETWEEN '2025-07-23' AND '2026-08-16' AND adgroup_id != '__backfill__';
