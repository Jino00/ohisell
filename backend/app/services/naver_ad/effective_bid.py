# effective_bid.py — effective_bid_sa (스프린트 B Phase 2, D-NAO-65)
# 역할(SA·단일책임): NaverAdgroupProduct(B1이 적재한 소재-레벨 입찰)만 읽어 광고그룹의
#   "실효 입찰" 단일값을 파생한다. 순수 파생(DB 읽기만 — 쓰기·제어·API 콜 없음. 소재-레벨
#   제어는 B3 몫). useGroupBidAmt=false 소재는 소재 개별 bidAmt가 실효 입찰이고 그룹입찰을
#   무시한다(공식 apidoc, D-NAO-65 B 선행 실측). 그룹 단일 실효값 = 소재 실효입찰들의 max
#   (보수 — 임계=실효×10을 높여 과-pause 방지, 실측 CPC 상한과 정합).
#
# ★D-NAO-218(M2-b2, ref 65 정정 #2 소비처): 위 파생은 그룹입찰/소재입찰 **어느 쪽이 레버인가**
#   (ad vs group 축)만 가린다 — **기기(PC/모바일) 축**은 원래 이 모듈의 소관이 아니었다. 그런데
#   두 값(group_bid·ad_bid_amt) 모두 네이버 콘솔의 "명목" 입찰가이고, 실제로 과금되는 CPC는
#   여기에 기기별 입찰가중치(pcNetworkBidWeight/mobileNetworkBidWeight, 10~500·기본 100)가
#   곱해진 값이다. 즉 이 모듈이 지금까지 "effective_bid"라 불러온 값은 (ad-vs-group 축에서는
#   맞지만) 기기 축에서는 여전히 명목이었다 — ref 65가 지목한 "명목 입찰을 실효 입찰의 대리로
#   쓰는 전진 판단" 그 자체다. 아래 device_weight_multiplier가 그 축을 마저 닫는다.
from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct, NaverEntity

log = logging.getLogger(__name__)

_ID_CHUNK = 400  # IN 절 분할(SQLite 바인딩 상한 방어 — bm_diff._ID_CHUNK 관례)

_DEFAULT_DEVICE_WEIGHT = 100  # 네이버 기본값(공식 Swagger 확정) — NULL(미관측)의 취급값


def device_weight_multiplier(pc_weight: int | None, mobile_weight: int | None) -> Decimal:
    """기기별 입찰가중치 → 실효 배율(단일값). 실효 입찰가 = 명목 × 이 배율.

    ★이 함수의 호출부(effective_bid·rank_servo 소비 경로)는 PC/모바일을 **구분하지 않는**
    단일 숫자 하나로 그룹의 "실효 입찰"을 표현한다 — 그 설계 자체를 이번 슬라이스에서
    새로 짜지 않는다(범위 밖: PC/모바일 이원화는 시장가·서보 판정 전체의 재설계). 구분하지
    않는 자리에 기기별로 다른 두 값을 하나로 접어야 하므로, **둘 중 큰 쪽**(=더 높은 실효
    입찰=더 보수적 비용 가정)을 택한다. 이유: 이 값이 흘러가는 곳(경제성 상한 비교·서보 스텝
    캡)은 전부 "실제로 얼마가 나갈지"를 과소평가하면 과다입찰로 이어지는 자리다 — 모듈
    헤더의 기존 관례("그룹 단일 실효값 = 소재 max, 보수")와 같은 방향의 선택이다.
    ⚠️`bid_weight=130`처럼 100 초과 상향도 실재한다(D-NAO-216 §8-Q2 각주 라이브 실측) —
    100을 상한으로 자르지 않는다.

    NULL은 100(가중치 미설정)으로 취급한다 — **NULL과 100을 구분해 로깅하는 책임은
    호출부**에 있다(이 함수는 순수 계산이라 로그를 남기지 않는다, bid_ceiling_calculator의
    "순수 계산부는 SA간 직접 호출 금지·부수효과 0" 관례와 같은 결)."""
    pc = pc_weight if pc_weight is not None else _DEFAULT_DEVICE_WEIGHT
    mobile = mobile_weight if mobile_weight is not None else _DEFAULT_DEVICE_WEIGHT
    return Decimal(max(pc, mobile)) / Decimal(_DEFAULT_DEVICE_WEIGHT)


def apply_device_weight(nominal_bid: int, pc_weight: int | None, mobile_weight: int | None) -> int:
    """명목 입찰 → 기기가중치 적용 실효 입찰(원 단위 반올림, 항상 int)."""
    mult = device_weight_multiplier(pc_weight, mobile_weight)
    return int((Decimal(nominal_bid) * mult).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def nominal_ceiling_for_device(db: Session, adgroup_id: str, effective_ceiling: int) -> tuple[int, dict]:
    """실효(원가) 스케일 경제성 상한 → 이 그룹 기기가중치로 나눈 **명목** 스케일 상한.

    ★rank_servo:49 소비처(ref 65 정정 #2)를 위한 전용 변환. rank_servo.decide_servo_step은
    current_bid·target_bid·economic_ceiling을 **같은 스케일**로 비교하는데(순수 함수라
    그 전제를 스스로 검증하지 않는다), 호출부(auto_operator)에서 current_bid·target_bid는
    항상 **명목**(네이버 bidAmt 필드에 그대로 쓰는 값)이고 economic_ceiling은 RPC·BEP로 낸
    **실효**(원가) 값이다. 가중치≠100이면 두 스케일이 어긋난다 — 특히 가중치>100(실재:
    AG5054=130)이면 명목 그대로 비교했을 때 상한을 조용히 넘겨 과다입찰이 된다. 이 함수는
    rank_servo.py를 손대지 않고 **진입 직전** 상한만 명목 스케일로 환산한다(서보 신설이
    아니라 정정 — 앵커 「순위 서보 구현 안 함」 경계 준수).

    반환: (명목 스케일 상한, {"pc":.., "mobile":..}) — 후자는 호출부가 NULL/100 구분
    로깅에 재사용."""
    w = adgroup_device_weights(db, [adgroup_id]).get(adgroup_id, {"pc": None, "mobile": None})
    mult = device_weight_multiplier(w["pc"], w["mobile"])
    if effective_ceiling <= 0 or mult == Decimal(1):
        return effective_ceiling, w
    nominal = int((Decimal(effective_ceiling) / mult).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return nominal, w


def adgroup_device_weights(db: Session, adgroup_ids) -> dict[str, dict]:
    """광고그룹별 기기 입찰가중치 배치 조회(N+1 방지, GATE P3-2와 같은 관례).

    반환: {adgroup_id: {"pc": int|None, "mobile": int|None}} — 행이 없거나(entity sync
    전) 값이 NULL이면 None(호출부가 NULL/100을 구분해 로깅할 수 있게 원값을 보존한다)."""
    ids = [a for a in dict.fromkeys(adgroup_ids) if a]
    if not ids:
        return {}
    out: dict[str, dict] = {}
    for i in range(0, len(ids), _ID_CHUNK):
        rows = (
            db.query(NaverEntity.entity_id, NaverEntity.pc_bid_weight, NaverEntity.mobile_bid_weight)
            .filter(NaverEntity.entity_type == "adgroup", NaverEntity.entity_id.in_(ids[i:i + _ID_CHUNK]))
            .all()
        )
        for adgroup_id, pc, mobile in rows:
            out[adgroup_id] = {"pc": pc, "mobile": mobile}
    return out


def _superseded_ad_ids(db: Session, ad_ids: list[str]) -> set[tuple[str, str]]:
    """(ad_id, adgroup_id) 중 **더 최근 관측이 다른 곳에 있는** 조합 = 버려야 할 옛 문맥.

    ★왜 필요한가(codex 4R P1-1): `naver_adgroup_product`는 삭제 없는 누적 upsert이고 unique
    키가 (adgroup_id, mall_product_id)뿐이라, **소재가 그룹 A→B로 옮겨가면 두 행이 남는다.**
    그대로 두면 A를 조회할 때 그 소재가 A의 소재로 보이고, `_derive`가 그 옛 행을
    `max_ad_id`(=실효 레버)로 내보낸다. 그 값은 auto_operator·proposal_writer를 거쳐
    **실제 입찰 대상**이 되므로, 지금은 B에 있는 소재를 A의 문맥으로 조작하게 된다.
    삭제를 없앤 결과 생긴 유일한 실질 위험이라 여기서 닫는다.

    ★판정은 `synced_at` **상대 비교**뿐 — 신선도 '창'을 쓰지 않는다. 창을 쓰면 커서 한 바퀴가
    며칠 걸릴 때 살아 있는 매핑까지 밀려나는데(그 주기 실측이 아직 없다), 상대 비교는 그
    위험이 없다: 중복이 없으면 아무것도 버리지 않고, 있으면 더 최근 쪽만 남긴다.
    동률이면 `id`가 큰 쪽(나중에 삽입된 행)을 채택해 판정을 결정적으로 만든다.
    """
    uniq = [a for a in dict.fromkeys(ad_ids) if a]
    if not uniq:
        return set()
    rows: list[tuple] = []
    for i in range(0, len(uniq), _ID_CHUNK):
        rows.extend(
            db.query(
                NaverAdgroupProduct.ad_id,
                NaverAdgroupProduct.adgroup_id,
                NaverAdgroupProduct.synced_at,
                NaverAdgroupProduct.id,
            )
            .filter(NaverAdgroupProduct.ad_id.in_(uniq[i:i + _ID_CHUNK]))
            .all()
        )
    best: dict[str, tuple] = {}
    for ad_id, adgroup_id, synced_at, row_id in rows:
        key = (synced_at, row_id)
        if ad_id not in best or key > best[ad_id][0]:
            best[ad_id] = (key, adgroup_id)
    return {
        (ad_id, adgroup_id)
        for ad_id, adgroup_id, _synced, _rid in rows
        if best[ad_id][1] != adgroup_id
    }


def _derive(rows: list[tuple], group_bid: int) -> dict:
    """소재 행 목록 → 실효입찰 파생(순수 계산부 — 단건/배치가 공유, GATE P3-2).

    rows: [(ad_id, ad_bid_amt, use_group_bid_amt, ad_user_lock), ...] (해당 그룹 소재들).
    규칙(공식 apidoc·B1 실측):
    - 정지 소재(ad_user_lock=True)는 서빙하지 않으므로 실효입찰 계산에서 제외.
    - useGroupBidAmt=False ∧ ad_bid_amt 존재 소재 → 그 소재 bidAmt가 그 소재의 실효 입찰.
    - useGroupBidAmt=True 소재 → 그룹입찰(group_bid)이 그 소재의 실효 입찰.
    - 그룹 단일 실효값 = 위 실효입찰들의 max(보수적 — 과-pause 방지·최대 CPC 정합, Q2 결정).
    - 유효한 소재-레벨 데이터가 하나도 없으면(sync 전 NULL·비쇼핑·전 소재 정지) → group_bid
      폴백(fail-safe·하위호환 — 소재를 아직 모르던 종전 그룹입찰 기준 동작 보존)."""
    max_ad_bid: int | None = None   # useGroupBidAmt=false 활성 소재들의 최대 bidAmt
    max_ad_id: str | None = None
    has_group_ad = False            # useGroupBidAmt=true 활성 소재 존재(그룹입찰이 실효인 소재)
    ad_count = 0

    for ad_id, ad_bid_amt, use_group, user_lock in rows:
        if user_lock is True:
            continue  # 정지 소재 제외(서빙 안 함)
        if use_group is True:
            has_group_ad = True
            ad_count += 1
        elif use_group is False and ad_bid_amt is not None:
            ad_count += 1
            if max_ad_bid is None or ad_bid_amt > max_ad_bid:
                max_ad_bid = ad_bid_amt
                max_ad_id = ad_id
        # 그 외(use_group None·ad_bid_amt None = sync 전/부분응답) → 데이터 없음(기여 없음)

    group_contrib = group_bid if has_group_ad else None
    candidates = [v for v in (max_ad_bid, group_contrib) if v is not None]

    if not candidates:
        # 유효 소재-레벨 데이터 없음 → 그룹입찰 폴백(fail-safe·하위호환)
        return {
            "effective_bid": group_bid, "group_bid": group_bid,
            "has_ad_data": False, "source": "group", "max_ad_id": None, "ad_count": 0,
        }

    effective = max(candidates)
    # 소재 bidAmt가 그룹입찰 기여 이상이면 source='ad'(소재-레벨 입찰이 실효 레버 = 그룹입찰 우회).
    if max_ad_bid is not None and (group_contrib is None or max_ad_bid >= group_contrib):
        source, src_ad_id = "ad", max_ad_id
    else:
        source, src_ad_id = "group", None
    return {
        "effective_bid": effective, "group_bid": group_bid,
        "has_ad_data": True, "source": source, "max_ad_id": src_ad_id, "ad_count": ad_count,
    }


def adgroup_effective_bids(db: Session, group_bids: dict[str, int]) -> dict[str, dict]:
    """여러 광고그룹의 실효 입찰을 **1쿼리 배치**로 파생 (GATE P3-2 — 보드 루프 N+1 제거).

    group_bids: {adgroup_id: 그룹입찰} — 파생에 그룹입찰이 필요하므로(혼합 그룹의 max 비교·
    폴백값) id 집합이 아니라 id→그룹입찰 매핑을 받는다. 반환: {adgroup_id:
    adgroup_effective_bid와 동일 형태 dict}(입력 전 키 보장 — 소재 행 없는 그룹은 그룹입찰
    폴백)."""
    if not group_bids:
        return {}
    rows = (
        db.query(
            NaverAdgroupProduct.adgroup_id,
            NaverAdgroupProduct.ad_id,
            NaverAdgroupProduct.ad_bid_amt,
            NaverAdgroupProduct.use_group_bid_amt,
            NaverAdgroupProduct.ad_user_lock,
            NaverAdgroupProduct.synced_at,
        )
        .filter(NaverAdgroupProduct.adgroup_id.in_(group_bids))
        .all()
    )
    stale_ad_ids = _superseded_ad_ids(db, [r[1] for r in rows if r[1]])
    by_adgroup: dict[str, list[tuple]] = {}
    for adgroup_id, ad_id, ad_bid_amt, use_group, user_lock, synced_at in rows:
        if ad_id and (ad_id, adgroup_id) in stale_ad_ids:
            continue  # 이 소재는 다른 그룹에서 더 최근에 관측됐다 — 옛 문맥 행은 버린다
        by_adgroup.setdefault(adgroup_id, []).append((ad_id, ad_bid_amt, use_group, user_lock))
    derived = {
        adgroup_id: _derive(by_adgroup.get(adgroup_id, []), group_bid)
        for adgroup_id, group_bid in group_bids.items()
    }

    # ── D-NAO-218(M2-b2) — 기기 축 적용 ──────────────────────────────────────
    # 위 _derive는 ad-vs-group 축(어느 레버가 실효인가)만 가린다. source·max_ad_id·
    # has_ad_data는 그 축의 판정이라 **명목 기준 그대로 둔다**(레버 라우팅은 기기와 무관).
    # 기기 축은 여기서 마지막에 곱해 최종 effective_bid를 낸다 — 원래 값은
    # effective_bid_nominal로 보존(하위 소비처가 배선 전 값과 대조할 수 있게, 이 계약 합격②).
    weights = adgroup_device_weights(db, group_bids.keys())
    for adgroup_id, out in derived.items():
        w = weights.get(adgroup_id, {"pc": None, "mobile": None})
        nominal = out["effective_bid"]
        mult = device_weight_multiplier(w["pc"], w["mobile"])
        out["effective_bid_nominal"] = nominal
        out["effective_bid"] = int((Decimal(nominal) * mult).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        out["device_pc_weight"] = w["pc"]
        out["device_mobile_weight"] = w["mobile"]
        if w["pc"] is None and w["mobile"] is None:
            # ★스펙 요구: NULL(미관측)과 100(진짜 가중치 없음)을 구분해 로깅한다.
            log.info("effective_bid: adgroup=%s 기기가중치 미관측(NULL) — 100 취급", adgroup_id)
        else:
            log.debug(
                "effective_bid: adgroup=%s 기기가중치 pc=%s mobile=%s 배율=%s 명목=%s→실효=%s",
                adgroup_id, w["pc"], w["mobile"], mult, nominal, out["effective_bid"],
            )
    return derived


def adgroup_effective_bid(db: Session, adgroup_id: str, group_bid: int) -> dict:
    """광고그룹 1개의 실효 입찰 파생 (B2, D-NAO-65 설계질문 Q2) — 배치의 단건 래퍼.

    반환 dict(진단·사유문이 소비 — Q2 근거 표면화):
    - effective_bid: 파생 실효 입찰(항상 int — 데이터 없으면 group_bid).
    - group_bid:     입력 그룹입찰(그대로 — 폴백/레버연결 판정용).
    - has_ad_data:   유효 소재-레벨 입찰이 하나라도 있었는지(False=폴백).
    - source:        'ad'(실효값이 소재 bidAmt에서 옴=그룹입찰 우회=레버 미연결) /
                     'group'(그룹입찰이 실효 레버·폴백 포함).
    - max_ad_id:     source='ad'일 때 실효값을 만든 소재 nccAdId(사유문용), 아니면 None.
    - ad_count:      실효값 계산에 기여한 유효(활성) 소재 수."""
    return adgroup_effective_bids(db, {adgroup_id: group_bid})[adgroup_id]
