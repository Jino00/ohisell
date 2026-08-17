.headers on
.mode csv
SELECT source, campaign_id, adgroup_id, search_term,
  SUM(imp) AS imp, SUM(clk) AS clk, SUM(cost) AS cost,
  SUM(conv_purchase_cnt) AS conv_purchase_cnt, SUM(conv_direct_cnt) AS conv_direct_cnt,
  SUM(conv_purchase_amt) AS conv_purchase_amt
FROM naver_search_term_daily
WHERE ad_date >= '2026-05-20' AND ad_date <= '2026-08-17'
GROUP BY source, campaign_id, adgroup_id, search_term;
