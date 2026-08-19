-- ⓑ 교차 원료 2: 「차단한 매체가 (차단하지 않은 그룹에서는) 어떤 성과였나」.
-- 이게 이번 적재가 연 유일한 질문이다 — 차단 그룹 안에서는 정의상 성과가 0이므로,
-- 대조군은 «같은 매체를 차단하지 않은 그룹»이다.
.mode list
.headers on
SELECT d.dim_value AS media_code,
       COUNT(DISTINCT CASE WHEN b.adgroup_id IS NOT NULL THEN d.adgroup_id END) AS grps_blocked,
       COUNT(DISTINCT CASE WHEN b.adgroup_id IS NULL     THEN d.adgroup_id END) AS grps_open,
       SUM(CASE WHEN b.adgroup_id IS NULL THEN d.imp  ELSE 0 END) AS imp_open,
       SUM(CASE WHEN b.adgroup_id IS NULL THEN d.clk  ELSE 0 END) AS clk_open,
       SUM(CASE WHEN b.adgroup_id IS NULL THEN d.cost ELSE 0 END) AS cost_open,
       SUM(CASE WHEN b.adgroup_id IS NOT NULL THEN d.imp  ELSE 0 END) AS imp_blocked,
       SUM(CASE WHEN b.adgroup_id IS NOT NULL THEN d.cost ELSE 0 END) AS cost_blocked,
       MIN(d.ad_date) AS d0, MAX(d.ad_date) AS d1
  FROM naver_search_term_dim_daily d
  LEFT JOIN naver_adgroup_media_black b
         ON b.adgroup_id = d.adgroup_id AND b.media_code = d.dim_value
 WHERE d.dim_type='m' AND d.adgroup_id <> '__backfill__'
 GROUP BY d.dim_value
 ORDER BY cost_open DESC;
