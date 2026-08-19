-- ⓑ 교차 원료 1: 그룹별 블랙리스트 보유 + 그 그룹의 172일 성과 총계.
-- ★밴드 결합은 로컬에서 band_group_total.csv(ref 63 정본)와 adgroup_id로 조인한다
--   (밴드는 DB·코드에 없고 CSV가 정본 — D-NAO-195 정정).
-- ★`__backfill__` 센티널 배제: 이 저장소엔 공용 필터가 없고 43개 파일이 각자 인라인한다.
--   여기서도 다시 적는다(D-NAO-198 규율).
.mode list
.headers on
SELECT c.adgroup_id,
       c.probe_status,
       c.black_media_count,
       c.pc, c.mobile,
       c.media_edit_tm,
       COALESCE(p.imp,0) AS imp, COALESCE(p.clk,0) AS clk, COALESCE(p.cost,0) AS cost,
       COALESCE(p.n_days,0) AS n_days
  FROM naver_adgroup_target_current c
  LEFT JOIN (
       SELECT adgroup_id, SUM(imp) imp, SUM(clk) clk, SUM(cost) cost,
              COUNT(DISTINCT ad_date) n_days
         FROM naver_search_term_dim_daily
        WHERE dim_type='m' AND adgroup_id <> '__backfill__'
        GROUP BY adgroup_id
  ) p ON p.adgroup_id = c.adgroup_id
 WHERE c.adgroup_id <> '__backfill__';
