.headers on
.mode csv
SELECT platform_product_id, SUM(selling_price) AS rev
FROM orders
WHERE channel_id = 6 AND selling_price > 0
GROUP BY platform_product_id;
