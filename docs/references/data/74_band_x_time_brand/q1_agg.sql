.headers on
.mode csv
SELECT adgroup_id, search_term, source,
       SUM(clk) clk, SUM(cost) cost, SUM(imp) imp,
       SUM(conv_purchase_cnt) conv_cnt, SUM(conv_purchase_amt) conv_amt,
       COUNT(*) n_rows
FROM naver_search_term_daily
WHERE ad_date BETWEEN '2025-07-23' AND '2026-08-16' AND adgroup_id != '__backfill__'
GROUP BY adgroup_id, search_term, source;
