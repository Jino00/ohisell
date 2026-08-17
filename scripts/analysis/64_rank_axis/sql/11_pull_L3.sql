.headers on
.mode csv
SELECT ad_date, campaign_id, adgroup_id, search_term, imp, clk, cost, rank_sum,
       conv_purchase_cnt, conv_direct_cnt, conv_purchase_amt
FROM naver_search_term_daily
WHERE source='shopping';
