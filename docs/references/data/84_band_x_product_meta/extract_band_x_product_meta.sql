-- ⓒ 원료 추출 (D-NAO-212 · C10) — prod 읽기 전용
--   ssh -o BatchMode=yes sellc.ohitech.co.kr \
--     "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < extract_band_x_product_meta.sql
--
-- ★밴드는 prod DB에 없다 — 정본은 CSV(`docs/references/data/63_band_decomposition/band_group_total.csv`,
--   grain=adgroup_id, 854행)다. 그래서 여기선 «광고 상품 ↔ 상품 메타»만 뽑고 밴드 결합은 로컬에서 한다.
-- ★`naver_ad_daily`를 읽지 않으므로 `__backfill__` 배제 3컬럼은 이 SQL에 해당 없음(공용 필터 부재 규율).
.mode list
.headers off
.separator |

-- [1] 조인율 양방향의 분모·분자 (숫자 4개)
SELECT 'ad_products_distinct', COUNT(DISTINCT mall_product_id) FROM naver_adgroup_product;
SELECT 'ad_products_matched',  COUNT(DISTINCT p.mall_product_id)
  FROM naver_adgroup_product p
  JOIN naver_product_meta_current m ON m.channel_product_no = p.mall_product_id;
SELECT 'meta_rows',            COUNT(*) FROM naver_product_meta_current;
SELECT 'meta_in_ads',          COUNT(*)
  FROM naver_product_meta_current m
 WHERE EXISTS (SELECT 1 FROM naver_adgroup_product p WHERE p.mall_product_id = m.channel_product_no);

-- [2] 상태 분포 (계약 §8 [미상] ⑤ — 무필터 호출에 DELETE/SUSPENSION이 섞이는가)
SELECT 'status|' || COALESCE(status_type,'(null)'), COUNT(*)
  FROM naver_product_meta_current GROUP BY 1 ORDER BY 2 DESC;

-- [3] 밴드 결합용 원료 (adgroup 단위 — 로컬에서 CSV와 조인한다)
.headers on
SELECT p.adgroup_id                      AS adgroup_id,
       p.mall_product_id                 AS mall_product_id,
       m.channel_product_no              AS channel_product_no,
       COALESCE(m.status_type,'')        AS status_type,
       COALESCE(m.whole_category_name,'')AS whole_category_name,
       COALESCE(m.sale_price,'')         AS sale_price,
       COALESCE(m.discounted_price,'')   AS discounted_price,
       COALESCE(m.stock_quantity,'')     AS stock_quantity
  FROM naver_adgroup_product p
  LEFT JOIN naver_product_meta_current m ON m.channel_product_no = p.mall_product_id;
