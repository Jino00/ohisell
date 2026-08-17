.headers on
.mode column
.width 40 20

SELECT '== total rows in naver_search_term_exclusion ==' AS label;
SELECT COUNT(*) AS total_rows FROM naver_search_term_exclusion;

SELECT '== rows by status ==' AS label;
SELECT status, COUNT(*) AS n FROM naver_search_term_exclusion GROUP BY status ORDER BY n DESC;

SELECT '== rows by source (NULL vs console_import) ==' AS label;
SELECT COALESCE(source,'(NULL=ours)') AS source, COUNT(*) AS n FROM naver_search_term_exclusion GROUP BY source ORDER BY n DESC;

SELECT '== rows by live_state ==' AS label;
SELECT COALESCE(live_state,'(NULL=never checked)') AS live_state, COUNT(*) AS n FROM naver_search_term_exclusion GROUP BY live_state ORDER BY n DESC;

SELECT '== status x source cross tab ==' AS label;
SELECT status, COALESCE(source,'(NULL=ours)') AS source, COUNT(*) AS n
FROM naver_search_term_exclusion GROUP BY status, source ORDER BY status, n DESC;

SELECT '== status x live_state cross tab ==' AS label;
SELECT status, COALESCE(live_state,'(NULL)') AS live_state, COUNT(*) AS n
FROM naver_search_term_exclusion GROUP BY status, live_state ORDER BY status, n DESC;

SELECT '== distinct adgroup_id count (any status) ==' AS label;
SELECT COUNT(DISTINCT adgroup_id) AS n FROM naver_search_term_exclusion;

SELECT '== distinct adgroup_id count (status=excluded) ==' AS label;
SELECT COUNT(DISTINCT adgroup_id) AS n FROM naver_search_term_exclusion WHERE status='excluded';

SELECT '== live_note sample - has type= pattern? count ==' AS label;
SELECT COUNT(*) AS n FROM naver_search_term_exclusion WHERE live_note LIKE '%type=%';

SELECT '== live_note type breakdown ==' AS label;
SELECT
  CASE
    WHEN live_note LIKE '%type=1%' THEN 'type=1(exact)'
    WHEN live_note LIKE '%type=2%' THEN 'type=2(phrase)'
    WHEN live_note LIKE '%type=미상%' THEN 'type=미상'
    WHEN live_note LIKE '%type=%' THEN 'type=other'
    ELSE '(no type info in note)'
  END AS type_bucket,
  COUNT(*) AS n
FROM naver_search_term_exclusion
GROUP BY type_bucket ORDER BY n DESC;

SELECT '== sample live_note distinct values (first 20) ==' AS label;
SELECT live_note, COUNT(*) AS n FROM naver_search_term_exclusion GROUP BY live_note ORDER BY n DESC LIMIT 20;

SELECT '== created_at date range ==' AS label;
SELECT MIN(created_at) AS min_created, MAX(created_at) AS max_created FROM naver_search_term_exclusion;

SELECT '== console_excluded_at non-null count ==' AS label;
SELECT COUNT(*) AS n FROM naver_search_term_exclusion WHERE console_excluded_at IS NOT NULL;
