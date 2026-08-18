-- 탐색용(회색 토큰 「소다케이스」 판정 근거). ★__backfill__ 센티널 배제 —
-- 이 저장소는 공용 필터가 없어 판정 경로마다 다시 적어야 하고, 잊으면 조용히 틀린다.
-- (이 테이블엔 현재 센티널 행이 0건이라 값은 안 바뀌지만, 규율은 값이 아니라 «모양»이다.)
.headers on
.mode csv
SELECT search_term, SUM(clk) clk, SUM(cost) cost, COUNT(*) n
FROM naver_search_term_daily
WHERE search_term LIKE '%소다%' AND adgroup_id <> '__backfill__'
GROUP BY search_term ORDER BY clk DESC LIMIT 20;
SELECT 'total_soda_rows', COUNT(*) FROM naver_search_term_daily
WHERE search_term LIKE '%소다%' AND adgroup_id <> '__backfill__';
