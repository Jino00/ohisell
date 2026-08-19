.mode list
.headers on
-- 성과축(dim m) 307그룹 중 current(1013)에 없는 것의 정체
SELECT d.adgroup_id, e.status, e.campaign_id
  FROM (SELECT DISTINCT adgroup_id FROM naver_search_term_dim_daily
         WHERE dim_type='m' AND adgroup_id <> '__backfill__') d
  LEFT JOIN naver_adgroup_target_current c ON c.adgroup_id = d.adgroup_id
  LEFT JOIN naver_entity e ON e.entity_id = d.adgroup_id
 WHERE c.adgroup_id IS NULL;
-- current 1013의 캠페인 유형 구성(naver_entity 기준) — 밴드 조인 없는 190 포함 전수
SELECT COALESCE(e.campaign_type,'(entity없음)') AS ct, COUNT(*) AS grps
  FROM naver_adgroup_target_current c
  LEFT JOIN naver_entity e ON e.entity_id = c.adgroup_id
 GROUP BY 1 ORDER BY 2 DESC;
