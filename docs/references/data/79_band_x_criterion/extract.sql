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
--
-- ★★D-NAO-207: **임프레션·전환수·전환매출을 붙였다.** 초판은 클릭·비용만 줘서
--   「효율(ROAS/CPA/CTR)이 밴드마다 다른가」를 **원리적으로 검정할 수 없었다** — ref 79의
--   강등 5건이 전부 그 이유였고(§8-1), ref 80이 「A+B=4/36의 병목」으로 지목한 자리다.
--   ★구매만 센다(`conv_kind='purchase'`) — 장바구니는 매출이 아니다((2)절과 같은 규약).
--
-- ★★**조인을 «집계 키 수준»에서 한다 — 일자별 행 단위로 하면 전환이 샌다.**
--   첫 시도는 (일자,그룹,코드,기기)로 좌측 조인했는데 CONV 대비 **139건(SHOPPING 88·
--   WEB_SITE 51)이 떨어졌다.** 원인: **광고비 0인 날에 붙은 전환**(전환은 «발생일»에
--   귀속되므로 이전 클릭의 전환이 노출·클릭 0인 날에 붙는다)은 stat 쪽에 매칭 상대가 없다.
--   그 행들이 빠지면 **ROAS가 과소평가**된다. ⇒ 양쪽을 각각 키까지 집계한 뒤 합친다
--   (stat만 있는 칸·conv만 있는 칸 **둘 다 남긴다**).
with s as (
    select b.band, b.campaign_type, c.criterion_type, c.criterion_code, h.half,
           sum(c.clk) as clk, sum(c.cost) as cost, sum(c.imp) as imp
    from naver_criterion_daily c
         join band b on b.adgroup_id = c.adgroup_id
         join half h on h.adgroup_id = c.adgroup_id
    group by 1,2,3,4,5
), v as (
    select b.band, b.campaign_type, v0.criterion_type, v0.criterion_code, h.half,
           sum(v0.conv_cnt) as conv_cnt, sum(v0.conv_amt) as conv_amt
    from naver_criterion_conv_daily v0
         join band b on b.adgroup_id = v0.adgroup_id
         join half h on h.adgroup_id = v0.adgroup_id
    where v0.conv_kind = 'purchase'
    group by 1,2,3,4,5
)
select 'HOLD', s.band, s.campaign_type, s.criterion_type, s.criterion_code, s.half,
       s.clk, s.cost, s.imp, coalesce(v.conv_cnt,0), coalesce(v.conv_amt,0)
from s left join v
  on v.band=s.band and v.campaign_type=s.campaign_type and v.criterion_type=s.criterion_type
 and v.criterion_code=s.criterion_code and v.half=s.half
union all
-- conv만 있는 칸(그 조합에 노출·클릭이 0인 경우) — 버리면 매출이 샌다
select 'HOLD', v.band, v.campaign_type, v.criterion_type, v.criterion_code, v.half,
       0, 0, 0, v.conv_cnt, v.conv_amt
from v where not exists (
    select 1 from s
    where s.band=v.band and s.campaign_type=v.campaign_type and s.criterion_type=v.criterion_type
      and s.criterion_code=v.criterion_code and s.half=v.half);

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
