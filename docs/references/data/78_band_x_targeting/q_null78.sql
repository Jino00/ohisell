.mode list
.headers on
-- A6 미설정 9그룹의 정체
SELECT e.campaign_type, COUNT(*) AS grps
  FROM naver_adgroup_target_current c
  LEFT JOIN naver_entity e ON e.entity_id = c.adgroup_id
 WHERE c.pc IS NULL GROUP BY 1;
-- 미설정 9그룹의 블랙 보유 여부
SELECT c.adgroup_id, c.black_media_count, e.campaign_type
  FROM naver_adgroup_target_current c
  LEFT JOIN naver_entity e ON e.entity_id = c.adgroup_id
 WHERE c.pc IS NULL;
