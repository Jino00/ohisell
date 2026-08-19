-- ⓐ 라이브 행 확인 — 행수·창·그룹수를 같이 낸다(창 없는 숫자는 축의 정체가 아니다, 교훈 #316)
.mode list
.headers on
SELECT 'current' AS tbl, COUNT(*) AS rows_, COUNT(DISTINCT adgroup_id) AS grps,
       SUM(probe_status=200) AS ok, SUM(probe_status<>200) AS not_ok,
       MIN(observed_at) AS obs_min, MAX(observed_at) AS obs_max
  FROM naver_adgroup_target_current;
SELECT 'media_black' AS tbl, COUNT(*) AS rows_, COUNT(DISTINCT adgroup_id) AS grps,
       COUNT(DISTINCT media_code) AS codes, MIN(observed_at) AS obs_min, MAX(observed_at) AS obs_max
  FROM naver_adgroup_media_black;
SELECT 'change' AS tbl, COUNT(*) AS rows_ FROM naver_adgroup_target_change;
-- A6 퇴화 판정(ⓓ) — 분모를 같이 낸다
SELECT pc, mobile, COUNT(*) AS grps FROM naver_adgroup_target_current
 WHERE probe_status=200 GROUP BY pc, mobile ORDER BY grps DESC;
