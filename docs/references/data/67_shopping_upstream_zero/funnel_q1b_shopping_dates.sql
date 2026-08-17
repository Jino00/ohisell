.headers on
.mode csv
SELECT 'min_ad_date_shopping' AS k, MIN(ad_date) AS v FROM naver_search_term_daily WHERE source='shopping';
SELECT ad_date, COUNT(*) AS rows, SUM(clk) AS clk, SUM(cost) AS cost
FROM naver_search_term_daily
WHERE source='shopping' AND ad_date >= '2026-05-20' AND ad_date <= '2026-08-17'
GROUP BY ad_date ORDER BY ad_date;
