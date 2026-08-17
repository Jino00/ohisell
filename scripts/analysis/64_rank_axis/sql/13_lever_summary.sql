.headers on
.mode csv
SELECT 'ad_daily_SHOPPING_keyword_nonempty' AS metric,
       (SELECT COUNT(*) FROM naver_ad_daily WHERE campaign_type='SHOPPING' AND keyword_id<>'') AS n;
SELECT 'ad_daily_SHOPPING_keyword_empty' AS metric,
       (SELECT COUNT(*) FROM naver_ad_daily WHERE campaign_type='SHOPPING' AND keyword_id='') AS n;
SELECT 'ad_daily_SHOPPING_distinct_campaigns' AS metric,
       (SELECT COUNT(DISTINCT campaign_id) FROM naver_ad_daily WHERE campaign_type='SHOPPING') AS n;
SELECT 'ad_daily_WEB_SITE_keyword_nonempty' AS metric,
       (SELECT COUNT(*) FROM naver_ad_daily WHERE campaign_type='WEB_SITE' AND keyword_id<>'') AS n;
SELECT 'entity_keyword_WEB_SITE' AS metric,
       (SELECT COUNT(*) FROM naver_entity WHERE entity_type='keyword' AND campaign_type='WEB_SITE') AS n;
SELECT 'entity_keyword_WEB_SITE_distinct_campaigns' AS metric,
       (SELECT COUNT(DISTINCT campaign_id) FROM naver_entity WHERE entity_type='keyword' AND campaign_type='WEB_SITE') AS n;
SELECT 'entity_keyword_SHOPPING' AS metric,
       (SELECT COUNT(*) FROM naver_entity WHERE entity_type='keyword' AND campaign_type='SHOPPING') AS n;
SELECT 'entity_keyword_BRAND_SEARCH' AS metric,
       (SELECT COUNT(*) FROM naver_entity WHERE entity_type='keyword' AND campaign_type='BRAND_SEARCH') AS n;
SELECT 'adgroup_product_total' AS metric, (SELECT COUNT(*) FROM naver_adgroup_product) AS n;
SELECT 'adgroup_product_distinct_adgroups' AS metric, (SELECT COUNT(DISTINCT adgroup_id) FROM naver_adgroup_product) AS n;
SELECT 'adgroup_product_distinct_campaigns' AS metric, (SELECT COUNT(DISTINCT campaign_id) FROM naver_adgroup_product) AS n;
SELECT 'use_group_bid_amt_false_individual' AS metric, (SELECT COUNT(*) FROM naver_adgroup_product WHERE use_group_bid_amt=0) AS n;
SELECT 'use_group_bid_amt_false_individual_distinct_adgroups' AS metric,
       (SELECT COUNT(DISTINCT adgroup_id) FROM naver_adgroup_product WHERE use_group_bid_amt=0) AS n;
SELECT 'use_group_bid_amt_true_group' AS metric, (SELECT COUNT(*) FROM naver_adgroup_product WHERE use_group_bid_amt=1) AS n;
SELECT 'ad_user_lock_true' AS metric, (SELECT COUNT(*) FROM naver_adgroup_product WHERE ad_user_lock=1) AS n;
