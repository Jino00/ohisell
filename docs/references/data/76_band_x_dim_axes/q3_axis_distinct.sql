-- 축별 distinct 코드 수 (D-NAO-182 실측 시간 24·지역 19·매체 22와 대조)
SELECT dim_type, COUNT(DISTINCT dim_value) AS n_codes,
       SUM(imp) AS imp, SUM(clk) AS clk, SUM(cost) AS cost
  FROM naver_search_term_dim_daily GROUP BY dim_type;
