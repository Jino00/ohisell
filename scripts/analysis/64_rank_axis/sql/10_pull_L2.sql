.headers on
.mode csv
SELECT ad_date, campaign_id, adgroup_id, keyword_id, imp, clk, cost, rank_sum,
       conv_direct_cnt, conv_indirect_cnt, conv_direct_amt, conv_indirect_amt
FROM naver_ad_daily
WHERE keyword_id <> '' AND adgroup_id <> '__backfill__' AND campaign_type <> '';
