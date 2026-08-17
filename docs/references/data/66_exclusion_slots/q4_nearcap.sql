.headers on
.mode csv

CREATE TEMP VIEW shopping_rows AS
SELECT e.*, ce.campaign_type AS c_type, ce.name AS campaign_name
FROM naver_search_term_exclusion e
JOIN naver_entity ce ON ce.entity_type='campaign' AND ce.entity_id = e.campaign_id
WHERE e.status='excluded' AND ce.campaign_type='SHOPPING';

CREATE TEMP VIEW shopping_group_counts AS
SELECT adgroup_id, campaign_id, campaign_name,
       COUNT(*) AS n_excl,
       SUM(CASE WHEN source='console_import' THEN 1 ELSE 0 END) AS n_console,
       SUM(CASE WHEN source IS NULL THEN 1 ELSE 0 END) AS n_ours
FROM shopping_rows
GROUP BY adgroup_id;

.once /tmp/near_cap_all.csv
SELECT g.adgroup_id, COALESCE(ne.name,'(no entity match)') AS group_name,
       g.campaign_id, g.campaign_name, ne.status AS group_status,
       g.n_excl, g.n_console, g.n_ours
FROM shopping_group_counts g
LEFT JOIN naver_entity ne ON ne.entity_type='adgroup' AND ne.entity_id = g.adgroup_id
WHERE g.n_excl >= 50
ORDER BY g.n_excl DESC;

.print "wrote near_cap_all.csv"

.headers on
.mode column
.width 20 20
SELECT '== count of near-cap rows by bucket with distinct check ==' AS label;
SELECT COUNT(*) FROM shopping_group_counts WHERE n_excl>=70;
SELECT COUNT(*) FROM shopping_group_counts WHERE n_excl>=60;
SELECT COUNT(*) FROM shopping_group_counts WHERE n_excl>=50;

SELECT '== full histogram export to csv (all 116 groups) ==' AS label;
.once /tmp/all_group_counts.csv
.mode csv
SELECT g.adgroup_id, COALESCE(ne.name,'') AS group_name, g.campaign_id, g.campaign_name,
       COALESCE(ne.status,'') AS group_status, g.n_excl, g.n_console, g.n_ours
FROM shopping_group_counts g
LEFT JOIN naver_entity ne ON ne.entity_type='adgroup' AND ne.entity_id = g.adgroup_id
ORDER BY g.n_excl DESC;
