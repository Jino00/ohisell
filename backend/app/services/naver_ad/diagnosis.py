# diagnosis.py — diagnosis Harness (P2-S2)
# 역할: account_diagnosis_sa(보드 6개) + campaign_target_resolver(계정 BEP/목표ROAS) +
#   actual_revenue_sa(D-NAO-21 보정계수)를 조합해 GET /diagnosis 응답 하나로 정리.
#   SA간 직접 호출 금지 원칙(원칙18) — 이 Harness가 유일한 정보 유통 허브.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.naver_ad import account_diagnosis as diag
from app.services.naver_ad import accel_gate_view, campaign_target_resolver, metrics_aggregator
from app.services.naver_ad.actual_revenue import naver_order_revenue

_CORRECTION_LOOKBACK_DAYS = 30  # D-NAO-21: 계정 보정계수 산출 창(30일 고정)


def _as_interval(point: Decimal, **rest) -> dict:
    """점추정 하나를 안3 «구간 자»로 펼친다 (D-NAO-230).

    하한 = min(1, 점추정) · 상한 = max(1, 점추정). 1.0은 「보정 없음」(네이버 convAmt를
    그대로 믿는 것)이고, 그것도 하나의 가정이므로 구간의 한쪽 끝일 뿐이다(계약 §4 안3 ④).
    min/max로 감싸는 이유: 점추정이 1 미만으로 재확정되어도 「하한이 항상 상향(액셀) 쪽
    보수값, 상한이 항상 하향(브레이크) 쪽 보수값」이라는 불변식이 깨지지 않게 하기 위해서다.

    `factor`(기존 키)는 **상한**으로 둔다 — 라이브 점추정이 1보다 커서(1.31) 상한 == 현행값이라,
    **명시적으로 `factor_low`를 고른 소비처만** 동작이 바뀐다(그 목록은 D-NAO-231 결정대로
    「실쓰기의 크기」 층뿐이다 — `build_diagnosis` docstring 참조).
    """
    low = min(Decimal("1"), point)
    high = max(Decimal("1"), point)
    return {"factor": high, "factor_low": low, "factor_high": high, "factor_point": point, **rest}


def _factor_payload(correction: dict) -> dict:
    """API 응답용 — 구간 양끝을 float로 펼친다 (D-NAO-230 표면 계약, 계약 §5-5).

    ★`factor` 하나만 내보내면 화면이 다시 점추정으로 돌아간다 — 「구간 양끝 병기」가
    이 계약의 표면 요건이므로 세 값(하한·상한·점추정)을 **전부** 내보낸다.
    """
    return {
        **correction,
        "factor": float(correction["factor"]),
        "factor_low": float(correction["factor_low"]),
        "factor_high": float(correction["factor_high"]),
        "factor_point": float(correction["factor_point"]),
    }


def correction_factor(db: Session, date_to: date) -> dict:
    """D-NAO-21 보정계수 = (실단위 데이터가 실제로 존재하는 창의) 실주문매출 ÷ 네이버 convAmt.

    ★★공식은 **방향 중립**이다 — 창의 데이터가 방향을 정한다 (D-NAO-230 산출물 B, 계약 §1-4).
    분자(실주문매출)가 분모(네이버 convAmt)보다 크면 계수>1이라 «올려» 보정하고, 반대면
    계수<1이라 «내려» 보정한다. 이 함수는 어느 쪽으로도 못 박지 않는다.
      · D-NAO-7(2026-06)이 실증한 「convAmt가 ~2.6배 과대」는 **우리 원장이 아니라 MOP 화면**과
        비교한 값이었다 — 이 공식의 분모/분자 관계를 규정하지 않는다.
      · 라이브(2026-08-23) 실측은 반대 방향이다: 계수 **1.31**(>1, 올려 보정). 즉 「곱해 줄인다」는
        옛 주석은 특정 시점 데이터를 한 번 찍은 화석이었지 공식의 성질이 아니었다.
    ★그리고 분자 `actual_revenue.naver_order_revenue`에는 **광고 귀속 조인이 없다**
    (`channel_id==6` 필터뿐) — 따라서 이 계수는 「네이버 채널 매출 100%를 광고가 견인했다」는
    가정과 **수학적으로 동치**다. 그 가정을 못 지우기 때문에 D-NAO-230이 점추정을 버리고
    **구간 [하한, 상한]**으로 바꿨다(안3). 소비처별로 어느 끝을 쓰는지는
    `docs/references/93_correction_factor_consumer_census_20260823.md` §3 표가 정본이다.

    창은 최대 30일이지만, naver_ad_daily 실단위(P0) 수집이 아직 30일 안 쌓였으면(파이프라인
    가동 초기) 실제 데이터가 있는 구간으로 양쪽(매출·convAmt)을 똑같이 좁힌다 — 그렇지 않으면
    "매출은 30일치, convAmt는 3일치"처럼 창이 어긋나 계수가 왜곡된다(원칙22, 라이브 검증 중 발견).
    naver convAmt=0이면(실단위 데이터 자체가 없음) 계수 산출 불가(1.0 폴백, no-op 보정 + 사유 명시).

    X1b T4(naver_execution_harness 가드레일 컨텍스트)도 재사용 — 진단·실행 양쪽이 같은
    보정계수 산출 로직을 공유해야 판정 기준이 어긋나지 않는다(공개 함수로 승격, 원래
    비공개였음).

    반환 키(D-NAO-230에서 확장, 기존 `factor`는 하위호환 유지):
      factor      — 상한(하위호환 키). 기존 소비처가 그대로 쓰면 현행 동작.
      factor_high — 상한. **모든 «후보 선정» 판정**(브레이크·액셀 양쪽)이 쓴다 — D-NAO-231.
      factor_low  — 하한. **«실쓰기의 크기»를 정하는 층만** 쓴다: 입찰 크기(`bid_simulator`),
                    증액 가드(`naver_execution_harness`), 확장 배분(`expansion_allocator`·
                    `expansion_pressure`), 진입 게이트(`auto_operator`).
      factor_point— 보정 전 점추정 원값(표면 병기·감사용).
    """
    earliest_real = diag.earliest_real_data_date(db, date_to, _CORRECTION_LOOKBACK_DAYS)
    if earliest_real is None:
        return _as_interval(Decimal("1"), source="unavailable", window_revenue=0, window_conv_amt=0)

    date_from = earliest_real
    revenue = naver_order_revenue(db, date_from, date_to)["revenue"]
    totals = metrics_aggregator.aggregate(db, date_from, date_to, grain="date")["totals"]
    naver_conv_amt = totals["conv_amt"]
    if not naver_conv_amt:
        return _as_interval(
            Decimal("1"), source="unavailable", window_revenue=revenue, window_conv_amt=0,
        )
    factor = (Decimal(revenue) / Decimal(naver_conv_amt)).quantize(Decimal("0.0001"))
    return _as_interval(
        factor, source="actual_revenue_ratio",
        window_from=date_from.isoformat(), window_to=date_to.isoformat(),
        window_revenue=revenue, window_conv_amt=naver_conv_amt,
    )


def _target_roas_resolver(db: Session, account_target_roas: Decimal):
    """campaign_id → Decimal target_roas 리졸버(override > 계정기본값) — 회차 내 캐싱.

    resume_candidates 전용(codex[P2], X1b T3) — proposal_pipeline._make_target_roas_resolver와
    동일 원리(계정 단일 target_roas만 쓰면 캠페인 override가 실제 판정에 반영되지 않는 재발
    버그 패턴, 2026-07-07 라이브검증 이력)를 diagnosis Harness 쪽에서도 적용한다.
    """
    cache: dict[str, Decimal] = {}

    def _resolve(campaign_id: str) -> Decimal:
        if campaign_id not in cache:
            resolved = campaign_target_resolver.resolve_target_roas(db, campaign_id)
            cache[campaign_id] = (
                resolved["target_roas"] if resolved["target_roas"] is not None else account_target_roas
            )
        return cache[campaign_id]

    return _resolve


def build_diagnosis(db: Session, date_from: date, date_to: date) -> dict:
    """진단 보드 6개 + 보정계수 + 계정 BEP/목표ROAS를 조립. 읽기 전용(D-3, 제안 없음).

    ★D-NAO-230(안3 «구간 자»): 이 Harness가 **소비처별로 어느 끝을 주입할지 결정하는 유일한
    지점**이다(SA간 직접호출 금지 원칙18 — 판정 함수들은 자기가 어느 끝을 받았는지 모른다).
    ★★**D-NAO-231 (Jino 결정 2026-08-23) — 안3의 「액셀=하한」은 «후보 선정»이 아니라
    «실쓰기의 크기»에 적용한다.** 그래서 **이 Harness의 보드는 브레이크·액셀 가릴 것 없이
    전부 상한**이고, 하한은 실제 쓰기를 만드는 층만 쓴다(`bid_simulator` 입찰 크기 ·
    `naver_execution_harness` 증액 가드 · `expansion_allocator`/`expansion_pressure` 확장 배분 ·
    `auto_operator` 진입 게이트).

    왜냐하면 액셀 «선정»까지 하한으로 좁히면 **브레이크는 그대로인데 액셀만 줄어** 비대칭이
    벌어지기 때문이다 — 라이브 실측(2026-08-23, `/api/naver/ad/diagnosis` 원본 universe 재구성):
    액셀 220 → 195건(−11%) · 브레이크 665건 불변 ⇒ 3.02:1 → **3.41:1**. 그것이 곧 북극성 §7이
    상습 실패 모드로 지목한 「ROAS 방어로의 표류」이고 D-NAO-85(ROAS +7%·매출 −52%)의 볼륨판
    재현이다. 계약 §6 금지선 2(자 교정을 액셀 재조정과 «한 배포 단위»로)의 이행이 이 배정이다 —
    **선정을 상한에 두는 것이 곧 「액셀 판정 불변」이고, 보수화는 실쓰기 크기에서 실제로 일어난다.**

    ⇒ **이 배포로 보드 판정 건수는 하나도 변하지 않는다**(브레이크 665 · 액셀 220 불변).
    바뀌는 것은 ①표면(구간 양끝 병기, 계약 §5-5) ②실쓰기 층의 보수화 둘뿐이다.
    census `docs/references/93_correction_factor_consumer_census_20260823.md` §3 표는 이 결정으로
    개정됐다(2b·2i·2f·2j의 「하한」 배정 → 「상한 — 선정이므로」).

    ★`floor_wait_units`가 `shopping_pause_candidates`와 **같은 끝**이어야 「무액션 여집합」
    정합이 성립한다는 제약도 그대로 만족된다(둘 다 상한).
    """
    correction = correction_factor(db, date_to)
    # D-NAO-231: 진단 보드는 «선정»이므로 전부 상한. 하한(`factor_low`)은 이 Harness가 쓰지 않는다.
    factor = correction["factor_high"]

    bep_roas = campaign_target_resolver.account_default_bep_roas(db)
    target_roas = campaign_target_resolver.account_default_target_roas(db)

    if bep_roas is None or target_roas is None:
        return {
            "window": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
            "correction_factor": _factor_payload(correction),
            "account_bep_roas": float(bep_roas) if bep_roas is not None else None,
            "account_target_roas": float(target_roas) if target_roas is not None else None,
            "error": "계정 BEP/목표ROAS 산출 불가 — naver_product_bep에 has_cost=True 상품 없음",
            "boards": None,
            "accel_gate": None,  # D-NAO-232: 응답 모양을 두 분기에서 같게 유지(키 부재 ≠ 값 없음)
        }

    boards = {
        # 2a 브레이크(bid_down만) → 상한
        "bleeding_keywords": diag.bleeding_keywords(db, date_from, date_to, bep_roas, factor),
        # 2b 액셀 — «선정»이므로 상한(D-NAO-231). 실쓰기 보수화는 bid_simulator/harness가 한다
        "starving_winners": diag.starving_winners(db, date_from, date_to, target_roas, factor),
        # 2c 기록값만(임계 비교 없음) → 현행 유지
        "expansion_bucket": diag.expansion_bucket(db, date_from, date_to, factor),
        # 2d 브레이크(bid_down만, adgroup grain) → 상한
        "shopping_group_bep": diag.shopping_group_bep(db, date_from, date_to, bep_roas, factor),
        # X1b-S S3(D-NAO-43 성장 확장): shopping_group_bep(적자, down)의 반대 — 수익 그룹
        # 성장 후보(bid_up). 같은 캠페인별 target_roas 리졸버(override>계정기본값) 재사용.
        # 2i 액셀 — «선정»이므로 상한(D-NAO-231)
        "shopping_group_growth": diag.shopping_group_growth(
            db, date_from, date_to, _target_roas_resolver(db, target_roas), factor,
        ),
        "exclusion_candidates": diag.exclusion_candidates(db, date_from, date_to),
        "keyword_triage": diag.keyword_triage(db, as_of=date_to),
        # 2e 경보판 — 어떤 제안 액션에도 연결 안 됨(비집행) → 현행 유지
        "vicious_cycle": diag.vicious_cycle_flags(db, date_to, target_roas, factor),
        "pause_candidates": diag.pause_candidates(db, date_from, date_to),
        # 2f 액셀(재개) — «선정»이므로 상한(D-NAO-231)
        "resume_candidates": diag.resume_candidates(
            db, date_to, _target_roas_resolver(db, target_roas), factor,
        ),
        # X1b-S S1(D-NAO-43): pause_candidates/resume_candidates(WEB_SITE 키워드)의 SHOPPING
        # adgroup 대칭 확장 — 04 등 쇼핑 캠페인 스톱로스 정지·재개.
        # D-NAO-64(A): account BEP·보정계수 주입 → 무전환뿐 아니라 바닥그룹 저ROAS도 정지 후보.
        # 2g 브레이크(터미널 pause, lever_broken 경로) → 상한
        "shopping_pause_candidates": diag.shopping_pause_candidates(
            db, date_from, date_to, bep_roas=bep_roas, correction_factor=factor,
        ),
        # 2j 액셀(재개) — «선정»이므로 상한(D-NAO-231)
        "shopping_resume_candidates": diag.shopping_resume_candidates(
            db, date_to, _target_roas_resolver(db, target_roas), factor,
        ),
        # B4(D-NAO-65): 이미 pause된 레버끊김(MO형) 그룹의 소재-레벨 재개 배선 — 소재 실효입찰
        # vs 입찰 바닥(70) 비교로 재개 준비(소재 bid_down)/바닥 재개(resume) 분기(GATE P2-1)
        # + 재pause 3일 쿨다운(GATE P2-3①). 카나리 스코프·ML 제외·순서강제는
        # proposal_writer.build()가 적용(이 보드는 후보 판정만).
        "shopping_lever_resume_candidates": diag.shopping_lever_resume_candidates(
            db, date_to=date_to,
        ),
        # UI3(D-NAO-65, DL2 GATE P3① 후속): 바닥 대기(at-floor 무액션) 관찰 보드 — 관찰 전용
        # (제안·실행 경로에 절대 연결 안 됨, §0 금지선). shopping_pause_candidates와 같은
        # date_from/date_to/bep_roas/factor를 주입해 창·임계를 일치시킨다(무액션 여집합 정합).
        # 2h 관찰 전용 — ★shopping_pause_candidates와 **같은 끝(상한)**이어야 여집합이 성립한다.
        "floor_wait_units": diag.floor_wait_units(
            db, date_from, date_to, bep_roas=bep_roas, correction_factor=factor,
        ),
    }

    return {
        "window": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "correction_factor": _factor_payload(correction),
        "account_bep_roas": float(bep_roas),
        "account_target_roas": float(target_roas),
        "boards": boards,
        # D-NAO-232(계약 §4-④): 「액셀이 실행 게이트에서 얼마나 죽는가」 관측 표면.
        # ★이 값이 `factor_low`를 읽는 것은 D-NAO-231 위반이 아니다 — 보드 «선정»은 위에서
        #   전부 상한(`factor`)으로 이미 끝났고, 여기는 **그 선정 결과가 실행 게이트에서
        #   어떻게 되는지 «관측»**만 한다. 판정·필터를 바꾸지 않는다(accel_gate_view 머리 주석).
        #   게이트가 실제로 하한을 쓰므로(`naver_execution_harness.py:926`), 하한을 안 보면
        #   화면이 실제 동작과 다른 것을 그리게 된다 — 그게 세션 39 적대 리뷰 P1-1이었다.
        #   ★적대 리뷰 1R P1-1: 목표ROAS는 **캠페인별**로 넘긴다 — 실제 게이트가
        #   `_resolve_target_roas_float(db, campaign_id)`를 쓰므로, 계정 기본값 하나로 재면
        #   화면이 실제 게이트와 «다른 그룹»을 지목한다(라이브에서 3그룹 교체·하한 총이익 66% 어긋남).
        "accel_gate": accel_gate_view.build(
            boards,
            factor_low=float(correction["factor_low"]),
            factor_high=float(correction["factor_high"]),
            target_roas=float(target_roas),
            bep_roas=float(bep_roas),
            resolve_target_roas=_target_roas_resolver(db, target_roas),
        ),
    }
