.headers on
.mode column
.width 40 20

SELECT '== WEB_SITE: group counts (denominator = 15 groups w/exclusion) ==' AS label;
CREATE TEMP VIEW ws_rows AS
SELECT e.*, ce.campaign_type AS c_type, ce.name AS campaign_name
FROM naver_search_term_exclusion e
JOIN naver_entity ce ON ce.entity_type='campaign' AND ce.entity_id = e.campaign_id
WHERE e.status='excluded' AND ce.campaign_type='WEB_SITE';

SELECT adgroup_id, campaign_id, campaign_name, COUNT(*) AS n_excl,
       SUM(CASE WHEN source='console_import' THEN 1 ELSE 0 END) AS n_console,
       SUM(CASE WHEN source IS NULL THEN 1 ELSE 0 END) AS n_ours
FROM ws_rows GROUP BY adgroup_id ORDER BY n_excl DESC;

SELECT '== WEB_SITE: min/max/avg ==' AS label;
SELECT MIN(n) AS min_n, MAX(n) AS max_n, AVG(n) AS avg_n FROM (
  SELECT COUNT(*) AS n FROM ws_rows GROUP BY adgroup_id
);

SELECT '== denominator B: all live SHOPPING adgroups by status ==' AS label;
SELECT status, COUNT(*) AS n FROM naver_entity WHERE entity_type='adgroup' AND campaign_type='SHOPPING' GROUP BY status;

SELECT '== denominator B: all live WEB_SITE adgroups by status ==' AS label;
SELECT status, COUNT(*) AS n FROM naver_entity WHERE entity_type='adgroup' AND campaign_type='WEB_SITE' GROUP BY status;

SELECT '== 48% check: recompute 33.4483/70 ==' AS label;
SELECT (33.4482758620690/70.0)*100 AS pct;

SELECT '== SHOPPING coverage: 116 groups-with-excl vs total SHOPPING adgroups (all statuses) ==' AS label;
SELECT
  (SELECT COUNT(*) FROM naver_entity WHERE entity_type='adgroup' AND campaign_type='SHOPPING') AS total_shopping_groups,
  116 AS groups_with_excl,
  ROUND(116.0 / (SELECT COUNT(*) FROM naver_entity WHERE entity_type='adgroup' AND campaign_type='SHOPPING') * 100, 1) AS pct_groups_with_excl;

SELECT '== SHOPPING coverage: on-only denominator ==' AS label;
SELECT
  (SELECT COUNT(*) FROM naver_entity WHERE entity_type='adgroup' AND campaign_type='SHOPPING' AND status='on') AS total_shopping_on,
  116 AS groups_with_excl,
  ROUND(116.0 / (SELECT COUNT(*) FROM naver_entity WHERE entity_type='adgroup' AND campaign_type='SHOPPING' AND status='on') * 100, 1) AS pct;

SELECT '== avg slot usage over ALL live SHOPPING groups (incl 0-exclusion groups), on-only ==' AS label;
SELECT
  3880.0 / (SELECT COUNT(*) FROM naver_entity WHERE entity_type='adgroup' AND campaign_type='SHOPPING' AND status='on') AS avg_over_all_on_groups,
  ROUND(3880.0 / (SELECT COUNT(*) FROM naver_entity WHERE entity_type='adgroup' AND campaign_type='SHOPPING' AND status='on') / 70.0 * 100,1) AS pct_of_cap_over_all_on_groups;

SELECT '== status machine check: any probation/restored rows anywhere (all types) ==' AS label;
SELECT status, COUNT(*) FROM naver_search_term_exclusion GROUP BY status;
