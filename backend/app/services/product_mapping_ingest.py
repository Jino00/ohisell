# product_mapping_ingest.py — 상품 연관맵 매핑 적재 Harness (엑셀 마스터 → upsert)
#       트랙 docs/tracks/active/track_product-connection-map.md S1.
#       흐름: 엑셀 파서(SA) → 채널 라벨 리졸버(SA) → 상품/매핑 upsert(SA) → 무결성 검사(SA).
#       마스터 시트 grain = 옵션 단위(D-1), 채널명 마커 컬럼 뒤 옵션ID 컬럼들이 채널블록.
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal

from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session

from app.models import Channel, ProductChannelMapping, ProductMaster
from app.services.cost_price_history import (
    PATH_MAPPING_INGEST,
    record_cost_price_change,
)
from app.services.cost_truth_audit import screen_cost, screen_reason, try_load_truth

# 라이브 실측(2026-07-03, ohisell_mapping_template.xlsx "원가 매핑" 시트) 라벨→account_key 대조.
# D-2 트랙 대조표와 동일한 축이나, 엑셀에 실제 저장된 문자열 그대로를 키로 둔다.
# 미등록 라벨은 절대 추측하지 않고 무결성 에러로 표면화한다(CLAUDE.md 추정 금지 원칙).
_LABEL_TO_ACCOUNT_KEY = {
    "자사몰 (cafe24)": "CAFE24",
    "네이버 스마트스토어": "NAVER",
    "COUPANG_ohitech_로켓배송": "COUPANG_ROCKET",
    "COUPANG_ohitech_판매자": "COUPANG_WING2",
    "COUPANG_ohitech_로켓그로스": "COUPANG_RG2",
    "COUPANG_ofixohi_로켓그로스": "COUPANG_RG1",
    "COUPANG_ofixohi_판매자": "COUPANG_WING1",
}

_CHANNEL_MARKER_HEADER = "채널명"
# 자동 채번 SKU의 형태. 이 형태가 아닌 SKU(수동 명명)는 채번 계산에서 제외한다.
_SKU_NUM_RE = re.compile(r"^OHI-(\d+)$")
MAPPING_SOURCE_EXCEL = "excel_master"


@dataclass
class MasterRow:
    row_idx: int
    product_name: str
    cost_price: Decimal
    # {엑셀 라벨(원문): [channel_product_id, ...]}
    channel_ids: dict[str, list[str]] = field(default_factory=dict)
    # 블록 고정라벨과 이 행의 실제 라벨이 다른 경우("마커컬럼, 고정라벨, 행라벨") 목록. codex P1.
    label_mismatches: list[tuple[int, str, str]] = field(default_factory=list)


@dataclass
class IntegrityReport:
    unknown_labels: list[str] = field(default_factory=list)  # "행N: 라벨 'X' 미등록"
    duplicate_product_names: list[str] = field(default_factory=list)
    duplicate_channel_ids: list[str] = field(default_factory=list)  # "채널 X 옵션ID Y 중복(행 a,b)"
    mapping_conflicts: list[str] = field(default_factory=list)  # upsert 단계에서 채워짐
    label_mismatches: list[str] = field(default_factory=list)  # 블록 고정라벨과 실제 행 라벨 불일치(codex P1)
    # 버퍼가 얹힌 원가라 **쓰지 않은** 행들(D-CPP-35). 매핑은 정상 처리된다.
    cost_buffers: list[str] = field(default_factory=list)
    # 정본 스냅샷이 없어 원가를 검사하지 못했을 때의 한 줄. ★검사 못 함과 이상 없음을 가른다.
    cost_guard_unavailable: str | None = None


# ── SA 1: 엑셀 파서 (순수 함수, DB/HTTP 없음) ──
def parse_master_sheet(ws: Worksheet) -> list[MasterRow]:
    """"원가 매핑" 시트 1행=옵션 1개를 파싱한다.

    row1(헤더)에서 "채널명"인 컬럼을 블록 마커로 찾고, 마커 다음 컬럼들(다음 마커 전까지)을
    그 블록의 옵션ID 컬럼으로 취급한다. 블록의 실제 채널 라벨은 헤더가 아니라 데이터 행에
    상수로 들어있으므로, 첫 비어있지 않은 데이터 행에서 읽어 블록 전체에 고정 적용한다.
    """
    rows_iter = ws.iter_rows(min_row=1, values_only=True)
    header = next(rows_iter, None)
    if header is None:
        return []

    marker_cols = [i for i, v in enumerate(header) if v == _CHANNEL_MARKER_HEADER]
    blocks: list[tuple[int, int, int]] = []  # (marker_col, id_col_start, id_col_end_exclusive)
    for bi, marker_col in enumerate(marker_cols):
        id_start = marker_col + 1
        id_end = marker_cols[bi + 1] if bi + 1 < len(marker_cols) else len(header)
        blocks.append((marker_col, id_start, id_end))

    block_labels: dict[int, str] = {}  # marker_col -> 라벨(첫 데이터 행에서 확정)
    result: list[MasterRow] = []

    for row_idx, row in enumerate(rows_iter, start=2):
        if not row or not row[0]:
            continue
        name = str(row[0]).strip()
        cost_raw = row[1] if len(row) > 1 else None
        try:
            cost = Decimal(str(cost_raw)) if cost_raw not in (None, "") else Decimal("0")
        except (InvalidOperation, ValueError):
            cost = Decimal("0")

        mrow = MasterRow(row_idx=row_idx, product_name=name, cost_price=cost)
        for marker_col, id_start, id_end in blocks:
            label_val = row[marker_col] if marker_col < len(row) else None
            if label_val is None:
                continue
            raw_label = str(label_val).strip()
            # 블록의 라벨은 첫 비어있지 않은 데이터 행에서 고정(D-1 설계, 실측상 열 전체가 상수).
            # 이후 행에서 값이 달라지면 그 행의 이 블록 ID는 절대 고정라벨로 귀속시키지 않고
            # (codex P1: 잘못된 채널로 조용히 귀속되는 것 방지) 불일치로 표면화만 한다.
            fixed_label = block_labels.setdefault(marker_col, raw_label)
            if raw_label != fixed_label:
                mrow.label_mismatches.append((marker_col, fixed_label, raw_label))
                continue
            label = fixed_label

            ids = [
                str(row[c]).strip()
                for c in range(id_start, min(id_end, len(row)))
                if row[c] not in (None, "")
            ]
            if ids:
                mrow.channel_ids.setdefault(label, []).extend(ids)

        result.append(mrow)

    return result


# ── SA 2: 채널 라벨 리졸버 ──
def resolve_channel_label(label: str) -> str | None:
    """엑셀 라벨 → account_key(Channel.code). 미등록이면 None(추측 금지)."""
    return _LABEL_TO_ACCOUNT_KEY.get(label)


# ── SA 4: 무결성 검사 (순수 함수) ──
def check_integrity(rows: list[MasterRow]) -> IntegrityReport:
    report = IntegrityReport()

    name_rows: dict[str, list[int]] = {}
    channel_id_rows: dict[tuple[str, str], list[int]] = {}

    for r in rows:
        name_rows.setdefault(r.product_name, []).append(r.row_idx)
        for marker_col, fixed_label, raw_label in r.label_mismatches:
            report.label_mismatches.append(
                f"행{r.row_idx}: 마커컬럼 {marker_col} 라벨 불일치 — 블록 고정라벨 '{fixed_label}' vs "
                f"이 행 '{raw_label}' (이 행의 해당 블록 옵션ID는 귀속하지 않음)"
            )
        for label, ids in r.channel_ids.items():
            if resolve_channel_label(label) is None:
                report.unknown_labels.append(f"행{r.row_idx}: 라벨 '{label}' 미등록")
                continue
            for cid in ids:
                channel_id_rows.setdefault((label, cid), []).append(r.row_idx)

    for name, idxs in name_rows.items():
        if len(idxs) > 1:
            report.duplicate_product_names.append(f"상품명 '{name}' 중복(행 {idxs})")

    for (label, cid), idxs in channel_id_rows.items():
        if len(idxs) > 1:
            report.duplicate_channel_ids.append(f"채널 '{label}' 옵션ID '{cid}' 중복(행 {idxs})")

    return report


# ── SA 3: 상품/매핑 upsert ──
def upsert_product_by_name(
    db: Session,
    name: str,
    cost_price: Decimal,
    next_sku_num: list[int],
    cost_blocked: bool = False,
    cost_reason: str | None = None,
) -> tuple[ProductMaster, Literal["created", "updated"]]:
    """상품명으로 product_master를 upsert한다(기존 upload-by-name 로직 추출·재사용).

    next_sku_num은 길이1 리스트로 감싼 카운터(호출 간 공유, 커밋 전 flush로 채번 충돌 방지).

    `cost_blocked`면 **원가만 쓰지 않는다**(D-CPP-35). 이 시트의 행은 존재 이유가 매핑이라
    원가 하나 때문에 행 전체를 버리면 매핑이 사라지고 주문이 미연결로 남는다 —
    상품 원가표 시트(`/upload`)가 행 전체를 건너뛰는 것과 처분이 다른 이유가 이것이다.
    ★신규 상품이면 **0(미입력)으로 만든다.** 버퍼 값을 넣으면 «틀린 값»이고, 0은 «모르는 값»이라
      손익 엔진이 이미 미상으로 다룬다. 틀린 값보다 모르는 값이 낫다.

    ★`cost_price`가 실제로 움직이면 이력 1행을 같은 트랜잭션에 남긴다(계약 D-CPP-64 §4 S1-①).
      `cost_reason`은 그 행의 근거 좌표다 — 안 주면 「근거 없음」이 화면에 그대로 뜬다.
    """
    product = db.query(ProductMaster).filter_by(product_name=name).first()
    if product:
        if cost_price and not cost_blocked:
            # ★값을 바꾸기 «전에» 기록한다 — 옛 값이 필요하고, 같은 커밋이라 이력만 남고
            #   값이 안 바뀌는(또는 그 반대) 상태가 원리적으로 없다.
            record_cost_price_change(
                db,
                internal_sku=product.internal_sku,
                old_value=product.cost_price,
                new_value=cost_price,
                path=PATH_MAPPING_INGEST,
                actor="excel",
                reason=cost_reason or f"매핑 시트 업로드 — 상품명 「{name}」",
                product_id=product.id,
            )
            product.cost_price = cost_price
        return product, "updated"

    new_cost = Decimal("0") if cost_blocked else cost_price
    product = ProductMaster(
        internal_sku=_allocate_sku(db, next_sku_num),
        product_name=name,
        cost_price=new_cost,
    )
    db.add(product)
    db.flush()
    # 신규는 옛 값이 **없다**(0이 아니라 None). 0으로 만든 경우도 사건이다 —
    # 「0으로 태어났다」는 것 자체가 나중에 「왜 이 SKU만 원가가 없나」의 답이 된다.
    record_cost_price_change(
        db,
        internal_sku=product.internal_sku,
        old_value=None,
        new_value=new_cost,
        path=PATH_MAPPING_INGEST,
        actor="excel",
        reason=(
            cost_reason or f"매핑 시트 업로드 — 신규 등록 「{name}」"
        )
        + ("" if not cost_blocked else " · 버퍼 차단으로 0(미입력)에서 시작"),
        product_id=product.id,
    )
    return product, "created"


def upsert_mapping(
    db: Session, product_id: int, channel: Channel, channel_product_id: str
) -> Literal["created", "updated", "conflict"]:
    """(channel_id, channel_product_id) 키로 매핑을 upsert. 다른 product면 덮어쓰지 않고 conflict."""
    existing = (
        db.query(ProductChannelMapping)
        .filter_by(channel_id=channel.id, channel_product_id=channel_product_id)
        .first()
    )
    if existing is None:
        db.add(
            ProductChannelMapping(
                product_id=product_id,
                channel_id=channel.id,
                channel_product_id=channel_product_id,
                is_active=True,
                mapping_source=MAPPING_SOURCE_EXCEL,
            )
        )
        return "created"

    if existing.product_id != product_id:
        return "conflict"

    existing.is_active = True
    existing.mapping_source = MAPPING_SOURCE_EXCEL
    return "updated"


@dataclass
class IngestResult:
    products_created: int = 0
    products_updated: int = 0
    mappings_created: int = 0
    mappings_updated: int = 0
    mappings_conflicted: int = 0
    orders_linked: int = 0
    integrity: IntegrityReport = field(default_factory=IntegrityReport)


def _next_sku_counter(db: Session) -> list[int]:
    """다음 채번 시작값 = **숫자형 `OHI-####` 중 최댓값 + 1**.

    ★문자열 정렬 최댓값을 쓰면 안 된다(2026-08-05 라이브 500의 원인):
      손으로 만든 비숫자 SKU(`OHI-Z-PRIVACY-FOLD8-WIDE`)가 정렬상 `OHI-0895`보다 위에 오고,
      `int("Z")`가 터지면 옛 코드는 **조용히 1부터** 다시 셌다 → 이미 있는 `OHI-0001`과
      UNIQUE 충돌 → 신규 상품이 하나라도 든 업로드는 전부 500. 정렬이 아니라 형태로 거른다.
    """
    nums = [
        int(m.group(1))
        for (sku,) in db.query(ProductMaster.internal_sku)
        .filter(ProductMaster.internal_sku.like("OHI-%"))
        .all()
        if sku and (m := _SKU_NUM_RE.match(sku))
    ]
    return [max(nums) + 1 if nums else 1]


def _allocate_sku(db: Session, counter: list[int]) -> str:
    """비어 있는 `OHI-####`를 찾아 반환한다(이미 쓰인 번호는 건너뛴다).

    카운터만 믿지 않는 이유: 수동 SKU가 또 생기거나 과거 채번이 어긋나 있으면 UNIQUE 충돌로
    **업로드 전체가 500**이 된다 — 한 건의 번호 충돌이 900행짜리 적재를 통째로 죽이게 두지 않는다.
    """
    while True:
        sku = f"OHI-{counter[0]:04d}"
        counter[0] += 1
        if db.query(ProductMaster.id).filter_by(internal_sku=sku).first() is None:
            return sku


# ── Harness ──
def ingest_master_sheet(db: Session, ws: Worksheet) -> IngestResult:
    rows = parse_master_sheet(ws)
    integrity = check_integrity(rows)
    result = IngestResult(integrity=integrity)

    channel_cache: dict[str, Channel | None] = {}
    next_sku_num = _next_sku_counter(db)

    # ★버퍼 유입 차단 가드(D-CPP-35). 옛 엑셀 재업로드가 버퍼 177건을 되돌리는 것을 여기서 막는다.
    truth = try_load_truth()
    if truth is None:
        integrity.cost_guard_unavailable = (
            "정본 스냅샷을 못 읽어 원가를 검사하지 않았다 — 이 업로드의 원가는 «검사 통과»가 아니다"
        )

    for r in rows:
        if not r.product_name:
            continue
        hit = screen_cost(r.cost_price, truth)
        if hit:
            integrity.cost_buffers.append(screen_reason(f"행{r.row_idx}", hit))
        product, action = upsert_product_by_name(
            db,
            r.product_name,
            r.cost_price,
            next_sku_num,
            cost_blocked=bool(hit),
            # 근거 좌표 — 「어느 시트 몇 행이 이 값을 넣었나」. 이력 패널이 이 문장을 그대로 쓴다.
            cost_reason=f"매핑 시트 업로드 「원가 매핑」 행 {r.row_idx} — 상품명 「{r.product_name}」",
        )
        if action == "created":
            result.products_created += 1
        else:
            result.products_updated += 1

        for label, ids in r.channel_ids.items():
            account_key = resolve_channel_label(label)
            if account_key is None:
                continue  # 이미 integrity.unknown_labels에 기록됨

            if account_key not in channel_cache:
                channel_cache[account_key] = db.query(Channel).filter_by(code=account_key).first()
            channel = channel_cache[account_key]
            if channel is None:
                integrity.unknown_labels.append(
                    f"행{r.row_idx}: account_key '{account_key}' 채널 미시드"
                )
                continue

            for cid in ids:
                outcome = upsert_mapping(db, product.id, channel, cid)
                if outcome == "created":
                    result.mappings_created += 1
                elif outcome == "updated":
                    result.mappings_updated += 1
                else:
                    result.mappings_conflicted += 1
                    integrity.mapping_conflicts.append(
                        f"행{r.row_idx}: 채널 '{label}' 옵션ID '{cid}'가 이미 다른 상품에 매핑됨"
                    )

    db.commit()

    from app.services.sync_service import relink_unlinked_orders

    result.orders_linked = relink_unlinked_orders(db)
    return result
