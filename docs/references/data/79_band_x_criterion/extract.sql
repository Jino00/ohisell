-- D-NAO-203 · 밴드 × 연령·성별·관심사 교차 원료 추출 (재현 SQL)
-- 창: 2025-08-19 ~ 2026-08-18 (365일, 계정 활동일 355일)
-- ★축을 가로질러 합산하지 않는다 — AG/GN/AD는 각각 «같은 성과의 독립 분해»다.
--   반드시 criterion_type 하나로 좁혀서 집계할 것(안 그러면 3중 계상).
-- ★`__backfill__` 센티널 배제: naver_criterion_daily의 adgroup_id는 네이버 리포트에서
--   직접 오므로 센티널이 원리적으로 없다(공용 필터 없음 — 새 집계마다 다시 판단해야 한다).
--   대조군인 naver_ad_daily 쪽에는 반드시 붙인다.
.mode list
.separator |

-- (1) 밴드 × 코드 × 캠페인유형 (유형 통제 — ref 78 F20: 유형 혼합 분모는 서사를 구성 산술로 만든다)
select 'CELL', b.band, b.campaign_type, c.criterion_type, c.criterion_code,
       count(distinct c.adgroup_id), sum(c.imp), sum(c.clk), sum(c.cost)
from naver_criterion_daily c join band b on b.adgroup_id = c.adgroup_id
group by b.band, b.campaign_type, c.criterion_type, c.criterion_code;

-- (2) 전환(총이익에 닿는 축) — 구매만(add_to_cart는 매출이 아니다)
select 'CONV', b.band, b.campaign_type, v.criterion_type, v.criterion_code,
       count(distinct v.adgroup_id), sum(v.conv_cnt), sum(v.conv_amt)
from naver_criterion_conv_daily v join band b on b.adgroup_id = v.adgroup_id
where v.conv_kind = 'purchase'
group by b.band, b.campaign_type, v.criterion_type, v.criterion_code;

-- (3) 홀드아웃 2분할 — 그룹 md5 짝/홀 (ref 78이 세운 방법. ★시간 분할은 쓰지 않는다:
--     여긴 성과 시계열이라 가능하지만, ref 78과 같은 분할을 써야 결과를 비교할 수 있다)
select 'HOLD', b.band, b.campaign_type, c.criterion_type, c.criterion_code, h.half,
       sum(c.clk), sum(c.cost)
from naver_criterion_daily c join band b on b.adgroup_id = c.adgroup_id
     join half h on h.adgroup_id = c.adgroup_id
group by b.band, b.campaign_type, c.criterion_type, c.criterion_code, h.half;

-- (4) 기기(P/M) × 밴드 — CRITERION만 주는 축
-- ★★초판 결함(2026-08-19, Fable이 잡음): `criterion_type`을 통제하지 않아 **전 축 합산**이
--   나왔다. SHOPPING은 정확히 2.00배(AG+GN), WEB_SITE는 3.41배(AG+GN+AD+SD)였다.
--   이 파일 머리말이 「축을 가로질러 합산하지 말라」고 스스로 적어 놓고 (4)절에서 어겼다 —
--   교차 집계에서 축 통제를 빠뜨리는 것이 이 축의 기본 함정이다.
--   ⇒ AG축으로 좁힌다(AG는 계정 100%를 덮으므로 기기 분해의 분모로 옳다).
select 'DEV', b.band, b.campaign_type, c.device, '', count(distinct c.adgroup_id),
       sum(c.clk), sum(c.cost)
from naver_criterion_daily c join band b on b.adgroup_id = c.adgroup_id
where c.criterion_type = 'AG'
group by b.band, b.campaign_type, c.device;
