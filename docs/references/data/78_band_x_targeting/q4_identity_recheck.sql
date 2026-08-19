-- 동일성 인과 검정을 «배포된 표»로 재현(ref 77은 로컬 원자료로 했다 — 여기선 DB끼리).
.mode list
.headers on
SELECT 'blocked_pairs_with_delivery' AS k, COUNT(*) AS pairs,
       SUM(d.imp) AS imp, SUM(d.clk) AS clk, SUM(d.cost) AS cost
  FROM naver_search_term_dim_daily d
  JOIN naver_adgroup_media_black b
    ON b.adgroup_id = d.adgroup_id AND b.media_code = d.dim_value
 WHERE d.dim_type='m' AND d.adgroup_id <> '__backfill__';
SELECT 'open_pairs' AS k, COUNT(*) AS pairs,
       SUM(d.imp) AS imp, SUM(d.clk) AS clk, SUM(d.cost) AS cost
  FROM naver_search_term_dim_daily d
  LEFT JOIN naver_adgroup_media_black b
    ON b.adgroup_id = d.adgroup_id AND b.media_code = d.dim_value
 WHERE d.dim_type='m' AND d.adgroup_id <> '__backfill__' AND b.adgroup_id IS NULL
   AND d.adgroup_id IN (SELECT DISTINCT adgroup_id FROM naver_adgroup_media_black);
