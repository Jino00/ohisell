.headers on
.mode csv
SELECT channel_id, channel_product_id, product_name, has_cost, contribution_margin
FROM naver_product_bep
WHERE channel_id = 6;
