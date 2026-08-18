-- ★교차의 원료: 광고그룹 × 축 × 코드 롤업(전 창). 밴드 결합은 로컬에서
--   band_group_total.csv(ref 63 정본)와 adgroup_id로 조인해 수행한다.
SELECT adgroup_id, dim_type, dim_value,
       SUM(imp) AS imp, SUM(clk) AS clk, SUM(cost) AS cost, SUM(rank_sum) AS rank_sum,
       COUNT(DISTINCT ad_date) AS n_days
  FROM naver_search_term_dim_daily
 GROUP BY adgroup_id, dim_type, dim_value;
