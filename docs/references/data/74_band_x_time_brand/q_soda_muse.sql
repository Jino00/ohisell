.headers on
.mode csv
SELECT search_term, SUM(clk) clk, SUM(cost) cost, COUNT(*) n
FROM naver_search_term_daily
WHERE search_term LIKE '%소다%' OR search_term LIKE '%아이뮤즈%' OR search_term LIKE '%뮤패드%'
GROUP BY search_term
ORDER BY clk DESC
LIMIT 30;
