-- ⓑ 교차 원료 2(정정판): 「차단 쪽 분모」는 블랙리스트 표에서 온다.
-- ★초판(q3)의 함정: 성과축 dim에는 «송출된 칸»만 있어서, 차단된 매체는 정의상 행이 없다
--   → LEFT JOIN 기준으로 세면 grps_blocked가 언제나 0에 가깝게 나온다(인과의 결과를
--   분모로 착각한 것). 두 표면을 각각 집계해 코드 합집합으로 붙인다.
-- ★`__backfill__` 배제(공용 필터 없음 — 파일마다 다시 적는 규율).
.mode list
.headers on
WITH blk AS (
  SELECT media_code AS code, COUNT(DISTINCT adgroup_id) AS grps_blocking
    FROM naver_adgroup_media_black WHERE adgroup_id <> '__backfill__' GROUP BY 1),
perf AS (
  SELECT dim_value AS code, COUNT(DISTINCT adgroup_id) AS grps_delivered,
         SUM(imp) AS imp, SUM(clk) AS clk, SUM(cost) AS cost,
         MIN(ad_date) AS d0, MAX(ad_date) AS d1
    FROM naver_search_term_dim_daily
   WHERE dim_type='m' AND adgroup_id <> '__backfill__' GROUP BY 1),
codes AS (SELECT code FROM blk UNION SELECT code FROM perf)
SELECT c.code,
       COALESCE(b.grps_blocking, 0)  AS grps_blocking,
       COALESCE(p.grps_delivered, 0) AS grps_delivered,
       COALESCE(p.imp, 0) AS imp, COALESCE(p.clk, 0) AS clk, COALESCE(p.cost, 0) AS cost,
       p.d0, p.d1
  FROM codes c LEFT JOIN blk b ON b.code = c.code LEFT JOIN perf p ON p.code = c.code
 ORDER BY COALESCE(p.cost,0) DESC, COALESCE(b.grps_blocking,0) DESC;
