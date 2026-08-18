.headers on
.mode csv
SELECT search_term, SUM(clk) clk, SUM(cost) cost, COUNT(*) n
FROM naver_search_term_daily
WHERE search_term LIKE '%소다%'
GROUP BY search_term ORDER BY clk DESC LIMIT 20;
SELECT 'total_soda_rows', COUNT(*) FROM naver_search_term_daily WHERE search_term LIKE '%소다%';
SELECT adgroup_id, COUNT(*) FROM naver_search_term_daily WHERE adgroup_id IN (
 SELECT adgroup_id FROM (SELECT DISTINCT adgroup_id FROM naver_search_term_daily) x
) AND 1=0;
