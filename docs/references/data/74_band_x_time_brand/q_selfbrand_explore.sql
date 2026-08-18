.headers on
.mode csv
-- 자사키워드 adgroup(핸드폰필름/골프필름) 검색어 상위 (클릭순)
SELECT adgroup_id, search_term, SUM(clk) clk, SUM(cost) cost, COUNT(*) n
FROM naver_search_term_daily
WHERE adgroup_id IN ('grp-a001-01-000000031116306','grp-a001-01-000000043935093')
GROUP BY adgroup_id, search_term
ORDER BY clk DESC
LIMIT 60;
