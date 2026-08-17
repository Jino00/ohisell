.headers on
.mode csv
SELECT 'g0_14d_shopping' AS k, COUNT(*) AS v FROM (
  SELECT 1 FROM naver_search_term_daily WHERE source='shopping' AND ad_date BETWEEN '2026-08-04' AND '2026-08-17'
  GROUP BY campaign_id, adgroup_id, search_term
);
SELECT 'g0_14d_expkeyword' AS k, COUNT(*) AS v FROM (
  SELECT 1 FROM naver_search_term_daily WHERE source='expkeyword' AND ad_date BETWEEN '2026-08-04' AND '2026-08-17'
  GROUP BY campaign_id, adgroup_id, search_term
);
SELECT 'g0_90d_shopping' AS k, COUNT(*) AS v FROM (
  SELECT 1 FROM naver_search_term_daily WHERE source='shopping' AND ad_date BETWEEN '2026-05-20' AND '2026-08-17'
  GROUP BY campaign_id, adgroup_id, search_term
);
SELECT 'g0_90d_expkeyword' AS k, COUNT(*) AS v FROM (
  SELECT 1 FROM naver_search_term_daily WHERE source='expkeyword' AND ad_date BETWEEN '2026-05-20' AND '2026-08-17'
  GROUP BY campaign_id, adgroup_id, search_term
);
