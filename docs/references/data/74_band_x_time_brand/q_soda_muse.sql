-- 탐색용(회색 토큰 「소다케이스」·자사 브랜드 후보 판정 근거). ★__backfill__ 센티널 배제.
.headers on
.mode csv
SELECT search_term, SUM(clk) clk, SUM(cost) cost, COUNT(*) n
FROM naver_search_term_daily
WHERE (search_term LIKE '%소다%' OR search_term LIKE '%아이뮤즈%' OR search_term LIKE '%뮤패드%')
  AND adgroup_id <> '__backfill__'
GROUP BY search_term
ORDER BY clk DESC
LIMIT 30;
