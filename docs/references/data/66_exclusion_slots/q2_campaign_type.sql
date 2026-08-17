.headers on
.mode column
.width 40 20

SELECT '== excluded rows joined to campaign_type (via campaign entity) ==' AS label;
SELECT COALESCE(ce.campaign_type,'(no campaign entity match)') AS campaign_type, COUNT(*) AS n_rows,
       COUNT(DISTINCT e.adgroup_id) AS n_groups
FROM naver_search_term_exclusion e
LEFT JOIN naver_entity ce ON ce.entity_type='campaign' AND ce.entity_id = e.campaign_id
WHERE e.status='excluded'
GROUP BY campaign_type
ORDER BY n_rows DESC;

SELECT '== cross: campaign_type x source ==' AS label;
SELECT COALESCE(ce.campaign_type,'(no match)') AS campaign_type, COALESCE(e.source,'(NULL=ours)') AS source, COUNT(*) AS n_rows, COUNT(DISTINCT e.adgroup_id) AS n_groups
FROM naver_search_term_exclusion e
LEFT JOIN naver_entity ce ON ce.entity_type='campaign' AND ce.entity_id = e.campaign_id
WHERE e.status='excluded'
GROUP BY campaign_type, source
ORDER BY campaign_type, n_rows DESC;

SELECT '== cross: campaign_type x live_note pattern (3880-note vs other) ==' AS label;
SELECT COALESCE(ce.campaign_type,'(no match)') AS campaign_type,
  CASE WHEN e.live_note LIKE '%저장된 id가 없어%' THEN 'auto-recon-note(3880-batch)'
       WHEN e.live_note IS NULL OR e.live_note='' THEN '(empty note)'
       ELSE 'other-note' END AS note_bucket,
  COUNT(*) AS n_rows, COUNT(DISTINCT e.adgroup_id) AS n_groups
FROM naver_search_term_exclusion e
LEFT JOIN naver_entity ce ON ce.entity_type='campaign' AND ce.entity_id = e.campaign_id
WHERE e.status='excluded'
GROUP BY campaign_type, note_bucket
ORDER BY campaign_type, n_rows DESC;

SELECT '== naver_entity adgroup counts by campaign_type (all statuses) ==' AS label;
SELECT campaign_type, status, COUNT(*) AS n
FROM naver_entity WHERE entity_type='adgroup'
GROUP BY campaign_type, status ORDER BY campaign_type, n DESC;

SELECT '== naver_entity total distinct adgroup rows ==' AS label;
SELECT COUNT(*) AS n FROM naver_entity WHERE entity_type='adgroup';
