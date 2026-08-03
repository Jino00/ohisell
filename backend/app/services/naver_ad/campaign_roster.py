# campaign_roster.py — 캠페인 명부 SA (D-NAO-48). 단일 책임: "우리 계정의 캠페인은 뭐가
# 있고, 각각 누가(우리/원본MOP/수동) 돌리며, 최근 성과는 어떤가"를 한 번에 답한다.
#
# 왜 필요한가(실측): 관리주체 스위치를 광고 옆에 달려면 **캠페인 이름**이 있어야 하는데
# API 어디에도 없었다 — 콘솔은 report(grain=campaign)+campaign-settings+diagnosis 3-call을
# 병합하는데 report가 이름을 주지 않아 화면에 `cmp-a001-02-000000008492582`가 그대로
# 노출된다(MOP UX 리뷰에서 "베끼면 안 되는 것"으로 꼽은 내부 ID 노출을 우리가 하고 있었음).
# 이름·광고종류·상태는 naver_entity에 있다.
#
# 쓰기 없음(읽기 전용). 관리주체 변경은 기존 PUT /campaign-settings가 유일 경로이고
# 실행 게이트는 naver_execution_harness의 optimizer=='ours' 하드체크다(D-NAO-13).
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverCampaignSettings, NaverEntity
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_today

DEFAULT_WINDOW_DAYS = 30

# 관측 스코프의 광고비 창 — "지금 돈을 쓰고 있는가"를 판정하는 기간(D-0 포함).
OBSERVATION_COST_WINDOW_DAYS = 7


def observation_campaign_ids(
    db: Session, *, days: int = OBSERVATION_COST_WINDOW_DAYS, today: date | None = None,
) -> set[str]:
    """★관측(수집·진단·리포트) 대상 캠페인 — **관측 스코프의 단일 진실 원천**.

    ══ 왜 optimizer로 스코프하면 안 되는가 (읽기 전에 이 문단부터) ══
    **D-NAO-13 원문**: "진단·리포트·이상 알림은 **상태 무관 전 캠페인**(읽기는 무해)".
    optimizer/auto_operate는 **실행 게이트**의 입력이지 관측 게이트의 입력이 아니다.
    관측에까지 그 조건을 걸면 "정지 스위치를 내리는 순간 눈까지 감는" 구조가 되는데,
    정지는 바로 **뭔가 잘못돼서 더 잘 봐야 할 때** 내리는 스위치다.

    ══ 같은 클래스의 사고가 두 번 났다 ══
    ① 2026-07-24 (D-NAO-92): 03 캠페인을 optimizer='none'으로 내리자, `bm_diff`가
       optimizer='ours' 행만 change_log와 대조하던 탓에 **우리 자신의 변경 7건이 전부
       "대행사 조작"으로 오기록**됐다. 그래서 bm_diff는 "optimizer와 무관하게" 검사하도록
       고쳐졌다 — `bm_diff.py:10-13`(★echo 필터 주석)에 그 경위가 남아 있다.
       그러나 **그 교훈이 나머지 모듈로 퍼지지 않았다.**
    ② 2026-07-30 10:50 KST (D-NAO-132 긴급정지): 계정의 모든 캠페인이 optimizer='none' ·
       auto_operate=0이 되자, 관측 스코프를 optimizer='ours'로 잡고 있던 **관측·수집 5곳이
       함께 죽었다**(shopping_ad_product_sync / ad_external_change / flight_loop /
       profit_scorecard / probe_learning_loop). 라이브 증거: prod 로그
       `naver_adgroup_product sync: 그룹 0개 ... 정리 276행`(2026-07-31 07:45 KST) —
       수집을 안 한 게 아니라 기존 매핑 276행을 **능동적으로 지웠다**.

    그래서 관측 스코프를 이 한 곳으로 모은다. 다음 사람에게: 여기를 `optimizer == 'ours'`로
    되돌리면 위 두 사고가 세 번째로 재현된다. 실행을 막고 싶다면
    `naver_execution_harness`의 optimizer 하드체크(D-NAO-13)와 auto_operate 킬스위치
    (D-NAO-49)를 쓰면 되고, 그 둘은 이 함수와 무관하게 이미 작동한다.

    ══ 스코프 기준(합집합) ══
      · **최근 `days`일(D-0 포함) 광고비 > 0인 캠페인** — 지금 돈을 쓰고 있는 것은 우리
        스위치와 무관하게 다 본다. 2026-07-30 사고의 증상("01·03이 돈을 쓰는데 우리가 못
        본다")을 직접 겨냥한다.
      · **`naver_campaign_settings`에 행이 존재하는 캠페인**(optimizer 값 **무관**) —
        우리가 관리했던(또는 관리 중인) 캠페인은 **정지 중에도 계속 본다.** 정지 기간의
        관측치가 없으면 재개 판단의 근거가 없다.

    계정 전체(naver_entity의 모든 캠페인)로 넓히지 않는 이유는 순전히 비용이다 —
    소재 수집은 그룹당 1 API 콜이고 계정 전체 활성 SHOPPING 그룹은 325개다(2026-08-03 실측).
    관측 목적상 계정 전체가 더 정확하지만, 돈도 안 쓰고 우리 설정도 없는 캠페인은 관측
    가치가 가장 낮은 구간이라 여기서 잘라낸다.

    ★sentinel(`__backfill__`) 행을 **제외하지 않는다**(다른 SA와 의도적으로 다름): 이
    함수는 광고비를 **합산하지 않고 존재만 판정**하므로 2배 함정이 성립하지 않는다.
    반대로 제외하면 캠페인 롤업 백필만 있고 실단위 행이 아직 없는 캠페인이 스코프에서
    빠져 **관측 사각**이 생긴다 — 관측 스코프는 넓은 쪽이 안전 방향이다.

    ══ ★★한계: `today`는 **반쪽 리플레이**다(codex 1R P1-2, 2026-08-03) ══
    `today`를 과거 날짜로 주면 **광고비 축만** 되감긴다. settings 축(`managed`)은
    `naver_campaign_settings`의 **현재 상태**를 읽으며, 이 테이블에는 **멤버십 히스토리가
    없다.** 따라서 과거 날짜 리플레이에서
      · 그 날짜 이후에 추가된 settings 캠페인은 **잘못 포함**되고,
      · 그 사이 제거된 settings 캠페인은 **누락**된다.
    → **리플레이 결과를 "그날의 관측 스코프"라고 믿으면 안 된다.** `today`가 보장하는 것은
      "실행 시각에 의존하지 않는 결정적 계산"(멱등·테스트 가능성)까지이지, 시점 정확한
      과거 재현이 아니다. 정확한 과거 재현이 필요하면 settings 멤버십 스냅샷 테이블이
      선행돼야 하고 그것은 DB 마이그레이션 사안이다(별도 항목으로 남김).
    이 한계는 이번 변경이 만든 것이 아니라 원래부터 그랬다 — `today` 파라미터가 "완전한
    리플레이가 된다"는 인상을 주므로 여기 명시한다.

    ★이 집합을 **삭제 판단의 근거로 쓰지 말 것.** 2026-08-03(codex 2R) 이후
    `shopping_ad_product_sync`는 매핑을 지우지 않는다 — 스코프가 좁아진 이유가 "정말
    이탈했다"인지 "수집 축이 침묵한다"인지 이 집합만으로는 영영 구분되지 않기 때문이다.
    그 판정을 시도했던 가드 계층은 전부 제거됐고, 되살리려면 그 구분을 가능하게 하는
    증거부터 확보해야 한다.

    반환: campaign_id 집합. 각 소비자는 자기 고유 필터(SHOPPING만·활성 그룹만 등)를
    이 집합 **위에** 얹는다.
    """
    today = today or kst_today()
    date_from = today - timedelta(days=days - 1)

    spending = {
        r[0]
        for r in db.query(NaverAdDaily.campaign_id)
        .filter(
            NaverAdDaily.ad_date >= date_from,
            NaverAdDaily.ad_date <= today,
            NaverAdDaily.cost > 0,
        )
        .distinct()
        .all()
        if r[0]
    }
    managed = {r[0] for r in db.query(NaverCampaignSettings.campaign_id).all() if r[0]}
    return spending | managed


def build(db: Session, *, days: int = DEFAULT_WINDOW_DAYS, today: date | None = None) -> list[dict]:
    """캠페인 명부 — entity(이름·종류·상태) ⨝ ad_daily(최근 N일 성과) ⨝ settings(관리주체).

    창 관례(다른 SA와 동일 — 어긋나면 화면끼리 숫자가 안 맞는다):
      · **오늘(D-0) 제외**: naver_ad_daily가 아직 확정 적재 전일 수 있다. ad_report·
        diagnosis·dashboard_overview._optimizer_coverage 전부 D-1 확정치 기준.
      · **BACKFILL_SENTINEL_ADGROUP 제외**: campaign_backfill이 심는 그룹 롤업 행이라
        실단위 행과 같이 합치면 **광고비가 이중집계**된다(2배 함정 — proposal_pipeline·
        account_diagnosis 등도 동일하게 제외).
      · **설정 행이 없으면 optimizer='none'**: naver_execution_harness._resolve_optimizer와
        동일 시맨틱. 여기서 다르게 굴면 화면이 "우리가 돌린다"는데 실행 게이트는 막는
        모순이 생긴다(단일 진실).

    광고비 0인 캠페인도 포함한다 — 0은 '없는 것'이 아니다. 정지됐거나 아직 집행 전인
    캠페인에도 관리주체를 지정할 수 있어야 카나리를 확대한다(D-47-h와 같은 정신).
    roas_naver는 광고비 0이면 **None**(0.0이 아니다 — '알 수 없음'이지 'ROAS 0배'가 아님).

    ★P1-1(D-NAO-104) additive 확장: `auto_operate`·`status_reason` 2개를 추가로 싣는다.
    기존 키는 하나도 바뀌지 않으므로 기존 소비자(커맨드 센터 `/campaigns`)는 무영향이다.
    """
    today = today or kst_today()
    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=days - 1)

    perf = {
        r.campaign_id: r
        for r in db.query(
            NaverAdDaily.campaign_id.label("campaign_id"),
            func.sum(NaverAdDaily.cost).label("cost"),
            func.sum(NaverAdDaily.clk).label("clk"),
            func.sum(NaverAdDaily.conv_direct_amt).label("conv_direct_amt"),
            func.sum(NaverAdDaily.conv_indirect_amt).label("conv_indirect_amt"),
        )
        .filter(
            NaverAdDaily.ad_date >= date_from,
            NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.campaign_id)
        .all()
    }

    settings_rows = db.query(NaverCampaignSettings).all()
    optimizer_by_campaign = {s.campaign_id: s.optimizer for s in settings_rows}
    # P1-1(D-NAO-104): 성과 뷰가 "우리가 자동으로 돌리는가"를 말하려면 optimizer만으로는
    # 부족하다 — optimizer='ours'인데 auto_operate=0인 상태가 실재한다(D-NAO-92의 03이
    # 정확히 그 모양이었다: 우리 소유인데 자동 레인은 정지). 설정 행이 없으면 False
    # (auto_operate의 모델 기본값과 동일 시맨틱 — 없는 것은 꺼진 것).
    auto_operate_by_campaign = {s.campaign_id: bool(s.auto_operate) for s in settings_rows}
    # UI2(D-NAO-65): loss 대응 정책. NULL/미설정은 그대로 None으로 실어 보낸다 —
    # 프론트가 '기본(고삐)'로 해석한다(전역 기본값 leash 불변식, 여기서 임의 정규화 금지:
    # 화면은 '미설정'과 '명시 leash'를 구분할 필요가 없고, NULL=leash 정규화는 쓰기 경로
    # (PUT /campaign-settings/loss-policy)의 책임이지 조회 SA가 할 일이 아니다).
    loss_policy_by_campaign = {s.campaign_id: s.loss_policy for s in settings_rows}

    rows: list[dict] = []
    campaigns = (
        db.query(NaverEntity)
        .filter(NaverEntity.entity_type == "campaign", NaverEntity.status != "deleted")
        .all()
    )
    for c in campaigns:
        p = perf.get(c.entity_id)
        cost = int(p.cost or 0) if p else 0
        # 네이버 기준 ROAS = (직접+간접 전환매출)/광고비 — D-NAO-7의 1열과 동일 정의.
        # (직접만·실주문 대조 2열은 이 명부의 책임이 아니다 — ad_report가 3열을 담당.)
        conv_amt = (int(p.conv_direct_amt or 0) + int(p.conv_indirect_amt or 0)) if p else 0
        rows.append({
            "campaign_id": c.entity_id,
            "name": c.name,
            "campaign_type": c.campaign_type,
            "status": c.status,
            # D-NAO-97 statusReason 원문. status(on/off)는 사람의 On/Off 스위치만 반영하므로
            # "켜져 있는데 왜 안 도는가"(예산 소진·상위 캠페인 OFF·검수 중)는 이 필드에만 있다.
            # 한글화는 소비자(성과 뷰) 몫 — 명부는 원문을 그대로 실어 보낸다(번역 레이어 단일화).
            "status_reason": c.status_reason,
            "cost": cost,
            "clk": int(p.clk or 0) if p else 0,
            "conv_amt": conv_amt,
            "roas_naver": round(conv_amt / cost, 4) if cost else None,
            "optimizer": optimizer_by_campaign.get(c.entity_id, "none"),
            "auto_operate": auto_operate_by_campaign.get(c.entity_id, False),
            "loss_policy": loss_policy_by_campaign.get(c.entity_id),  # NULL=콘솔이 '기본(고삐)'로 해석
            "window_days": days,
        })

    rows.sort(key=lambda r: r["cost"], reverse=True)
    return rows
