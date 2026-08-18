-- ★금지선 대응: 새 집계 SQL마다 `__backfill__` 센티널 배제를 다시 적는다(공용 필터 없음).
-- 먼저 «이 표에 센티널이 실재하는가»를 판정한다 — 없으면 배제절이 무의미하고, 있으면 필수다.
SELECT 'dim_daily' AS t, COUNT(*) AS sentinel_rows
  FROM naver_search_term_dim_daily WHERE adgroup_id = '__backfill__'
UNION ALL
SELECT 'dim_cell', COUNT(*)
  FROM naver_search_term_dim_cell_daily WHERE adgroup_id = '__backfill__'
UNION ALL
SELECT 'search_term_daily(대조군)', COUNT(*)
  FROM naver_search_term_daily WHERE adgroup_id = '__backfill__';
