.headers on
.mode csv
-- as_of hardcoded to 2026-08-17 (KST) to match kst_now().date() exactly, not sqlite UTC 'now'.
SELECT 'distinct_source' AS k, source AS v, COUNT(*) AS n FROM naver_search_term_daily GROUP BY source;
SELECT 'min_ad_date' AS k, MIN(ad_date) AS v, NULL AS n FROM naver_search_term_daily;
SELECT 'max_ad_date' AS k, MAX(ad_date) AS v, NULL AS n FROM naver_search_term_daily;
SELECT 'total_rows' AS k, COUNT(*) AS v, NULL AS n FROM naver_search_term_daily;
SELECT 'max_ad_date_shopping' AS k, MAX(ad_date) AS v, NULL AS n FROM naver_search_term_daily WHERE source='shopping';
SELECT 'max_ad_date_expkeyword' AS k, MAX(ad_date) AS v, NULL AS n FROM naver_search_term_daily WHERE source='expkeyword';
SELECT 'distinct_dates_14d_shopping' AS k, COUNT(DISTINCT ad_date) AS v, NULL AS n FROM naver_search_term_daily WHERE source='shopping' AND ad_date >= '2026-08-04' AND ad_date <= '2026-08-17';
SELECT 'distinct_dates_90d_shopping' AS k, COUNT(DISTINCT ad_date) AS v, NULL AS n FROM naver_search_term_daily WHERE source='shopping' AND ad_date >= '2026-05-20' AND ad_date <= '2026-08-17';
SELECT 'sum_clk_cost_14d_shopping' AS k, SUM(clk) AS v, SUM(cost) AS n FROM naver_search_term_daily WHERE source='shopping' AND ad_date >= '2026-08-04' AND ad_date <= '2026-08-17';
SELECT 'sum_clk_cost_90d_shopping' AS k, SUM(clk) AS v, SUM(cost) AS n FROM naver_search_term_daily WHERE source='shopping' AND ad_date >= '2026-05-20' AND ad_date <= '2026-08-17';
SELECT 'sum_convpurchase_14d_shopping' AS k, SUM(conv_purchase_cnt) AS v, SUM(conv_purchase_amt) AS n FROM naver_search_term_daily WHERE source='shopping' AND ad_date >= '2026-08-04' AND ad_date <= '2026-08-17';
SELECT 'sum_convpurchase_90d_shopping' AS k, SUM(conv_purchase_cnt) AS v, SUM(conv_purchase_amt) AS n FROM naver_search_term_daily WHERE source='shopping' AND ad_date >= '2026-05-20' AND ad_date <= '2026-08-17';
SELECT 'nonzero_convpurchase_rows_14d_shopping' AS k, COUNT(*) AS v, NULL AS n FROM naver_search_term_daily WHERE source='shopping' AND ad_date >= '2026-08-04' AND ad_date <= '2026-08-17' AND conv_purchase_cnt > 0;
