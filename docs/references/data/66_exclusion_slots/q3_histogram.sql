.headers on
.mode column
.width 40 20

CREATE TEMP VIEW shopping_group_counts AS
SELECT e.adgroup_id AS adgroup_id, COUNT(*) AS n_excl
FROM naver_search_term_exclusion e
JOIN naver_entity ce ON ce.entity_type='campaign' AND ce.entity_id = e.campaign_id
WHERE e.status='excluded' AND ce.campaign_type='SHOPPING'
GROUP BY e.adgroup_id;

SELECT '== sqlite version ==' AS label;
SELECT sqlite_version();

SELECT '== SHOPPING: groups-with-exclusion count, sum, avg, max ==' AS label;
SELECT COUNT(*) AS n_groups, SUM(n_excl) AS total_excl, AVG(n_excl) AS avg_excl, MAX(n_excl) AS max_excl, MIN(n_excl) AS min_excl
FROM shopping_group_counts;

SELECT '== SHOPPING: histogram buckets (denominator = 116 groups w/ >=1 exclusion) ==' AS label;
SELECT
  CASE
    WHEN n_excl >= 70 THEN '70+ (at cap)'
    WHEN n_excl >= 60 THEN '60-69'
    WHEN n_excl >= 50 THEN '50-59'
    WHEN n_excl >= 40 THEN '40-49'
    WHEN n_excl >= 30 THEN '30-39'
    WHEN n_excl >= 20 THEN '20-29'
    WHEN n_excl >= 10 THEN '10-19'
    WHEN n_excl >= 1  THEN '1-9'
    ELSE '0'
  END AS bucket,
  COUNT(*) AS n_groups
FROM shopping_group_counts
GROUP BY bucket
ORDER BY MIN(n_excl);

-- percentiles via ordered offset (0-indexed), n=116
SELECT '== SHOPPING percentiles (n=116 groups w/ exclusion) ==' AS label;
WITH ordered AS (
  SELECT n_excl, ROW_NUMBER() OVER (ORDER BY n_excl) AS rn, COUNT(*) OVER () AS cnt
  FROM shopping_group_counts
)
SELECT
  (SELECT n_excl FROM ordered WHERE rn = CAST(0.50*(cnt-1) AS INTEGER)+1 LIMIT 1) AS p50,
  (SELECT n_excl FROM ordered WHERE rn = CAST(0.75*(cnt-1) AS INTEGER)+1 LIMIT 1) AS p75,
  (SELECT n_excl FROM ordered WHERE rn = CAST(0.90*(cnt-1) AS INTEGER)+1 LIMIT 1) AS p90,
  (SELECT n_excl FROM ordered WHERE rn = CAST(0.95*(cnt-1) AS INTEGER)+1 LIMIT 1) AS p95,
  (SELECT MAX(n_excl) FROM ordered) AS max;

SELECT '== SHOPPING: full per-group list, top 30 by count (for near-cap identification) ==' AS label;
SELECT adgroup_id, n_excl FROM shopping_group_counts ORDER BY n_excl DESC LIMIT 30;

SELECT '== SHOPPING: groups >=70 (at/over cap) ==' AS label;
SELECT COUNT(*) AS n FROM shopping_group_counts WHERE n_excl >= 70;

SELECT '== SHOPPING: groups 60-69 ==' AS label;
SELECT COUNT(*) AS n FROM shopping_group_counts WHERE n_excl BETWEEN 60 AND 69;

SELECT '== SHOPPING: groups 50-59 ==' AS label;
SELECT COUNT(*) AS n FROM shopping_group_counts WHERE n_excl BETWEEN 50 AND 59;
