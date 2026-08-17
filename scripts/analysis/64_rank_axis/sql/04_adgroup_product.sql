.headers on
.mode csv
SELECT 'adgroup_product_total' AS label, COUNT(*) AS n, COUNT(DISTINCT adgroup_id) AS distinct_adgroups,
       COUNT(DISTINCT campaign_id) AS distinct_campaigns
FROM naver_adgroup_product;

SELECT 'adgroup_product_campaign_type' AS label, e.campaign_type, COUNT(*) AS n
FROM naver_adgroup_product p
LEFT JOIN naver_entity e ON e.entity_type='campaign' AND e.entity_id=p.campaign_id
GROUP BY e.campaign_type;

SELECT 'use_group_bid_amt_dist' AS label,
       CASE WHEN use_group_bid_amt IS NULL THEN 'null'
            WHEN use_group_bid_amt=0 THEN 'false_individual_bid'
            ELSE 'true_group_bid' END AS bucket,
       COUNT(*) AS n, COUNT(DISTINCT adgroup_id) AS distinct_adgroups
FROM naver_adgroup_product GROUP BY bucket;

SELECT 'ad_user_lock_dist' AS label,
       CASE WHEN ad_user_lock IS NULL THEN 'null'
            WHEN ad_user_lock=1 THEN 'true_locked_off'
            ELSE 'false' END AS bucket,
       COUNT(*) AS n
FROM naver_adgroup_product GROUP BY bucket;
