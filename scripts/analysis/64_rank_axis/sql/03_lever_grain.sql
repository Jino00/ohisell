.headers on
.mode csv
SELECT 'ad_daily_SHOPPING_keyword_id_dist' AS label,
       CASE WHEN keyword_id='' THEN 'empty_sentinel' ELSE 'nonempty' END AS kw_kind,
       COUNT(*) AS n, COUNT(DISTINCT campaign_id) AS distinct_campaigns
FROM naver_ad_daily WHERE campaign_type='SHOPPING' GROUP BY kw_kind;

SELECT 'entity_keyword_by_campaign_type' AS label, e.campaign_type, COUNT(*) AS n,
       COUNT(DISTINCT e.campaign_id) AS distinct_campaigns
FROM naver_entity e WHERE e.entity_type='keyword' GROUP BY e.campaign_type;

SELECT 'entity_keyword_shopping_detail' AS label, e.entity_id, e.campaign_id, e.adgroup_id_placeholder
FROM naver_entity e WHERE e.entity_type='keyword' AND e.campaign_type='SHOPPING' LIMIT 5;
