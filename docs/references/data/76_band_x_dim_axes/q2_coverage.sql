-- 적재 커버리지: 창·일수·행수·축별 distinct 코드 수
SELECT 'dim_daily' AS t, COUNT(*) AS rows_, COUNT(DISTINCT ad_date) AS days_,
       MIN(ad_date) AS d_min, MAX(ad_date) AS d_max,
       COUNT(DISTINCT adgroup_id) AS groups_
  FROM naver_search_term_dim_daily
UNION ALL
SELECT 'dim_cell', COUNT(*), COUNT(DISTINCT ad_date), MIN(ad_date), MAX(ad_date),
       COUNT(DISTINCT adgroup_id)
  FROM naver_search_term_dim_cell_daily;
