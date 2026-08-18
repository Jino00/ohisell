.headers on
.mode csv
SELECT COUNT(*) FROM (
  SELECT adgroup_id, search_term, source
  FROM naver_search_term_daily
  WHERE ad_date BETWEEN '2025-07-23' AND '2026-08-16' AND adgroup_id != '__backfill__'
  GROUP BY adgroup_id, search_term, source
);
