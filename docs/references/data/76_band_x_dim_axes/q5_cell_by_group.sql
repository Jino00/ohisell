-- 유료 결합 칸(축 간 상호작용) 롤업 — «비용이 난 자리»만 담긴 표라는 것을 잊지 말 것
SELECT adgroup_id, hour_code, region_code, media_code,
       SUM(imp) AS imp, SUM(clk) AS clk, SUM(cost) AS cost, COUNT(DISTINCT ad_date) AS n_days
  FROM naver_search_term_dim_cell_daily
 GROUP BY adgroup_id, hour_code, region_code, media_code;
