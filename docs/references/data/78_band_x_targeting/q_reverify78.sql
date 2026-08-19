.mode list
.headers on
SELECT 'current' AS tbl, COUNT(*) AS rows_, COUNT(DISTINCT adgroup_id) AS grps,
       SUM(probe_status=200) AS ok FROM naver_adgroup_target_current;
SELECT 'media_black' AS tbl, COUNT(*) AS rows_, COUNT(DISTINCT adgroup_id) AS grps,
       COUNT(DISTINCT media_code) AS codes FROM naver_adgroup_media_black;
SELECT 'change' AS tbl, COUNT(*) AS rows_ FROM naver_adgroup_target_change;
SELECT 'dim_m' AS tbl, COUNT(*) AS rows_, COUNT(DISTINCT ad_date) AS days,
       MIN(ad_date) AS d0, MAX(ad_date) AS d1,
       COUNT(DISTINCT adgroup_id) AS grps, COUNT(DISTINCT dim_value) AS codes
  FROM naver_search_term_dim_daily WHERE dim_type='m' AND adgroup_id <> '__backfill__';
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
