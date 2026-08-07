# product_connection_map.py — 상품 연결맵 매트릭스 (트랙 S4, D-12)
#   내부옵션(행) × 채널(열) 그리드를 조립하는 읽기전용 SA. 부작용 없음.
#   한 채널옵션ID에 마스터가 2개 이상 걸린 상태를 **원가로 갈라서** 표면화한다:
#     «공유»  = 그 마스터들의 원가가 전부 같다 → 어느 쪽에 귀속돼도 적용 원가가 같아 금액 영향 0.
#     «충돌»  = 원가가 다르다 → 이중귀속으로 손익이 갈린다(진짜 위험).
#   ★왜 갈랐나(2026-08-07 라이브 실측): 46건 전부 그룹 내 원가가 동일했다 — 원인은 채널의
#     리스팅 1개(네이버는 platform_product_id가 옵션ID가 아니라 상품번호다)에 기종별 우리 SKU가
#     여러 개 묶여 있는 구조라서지 오적재가 아니었다. 이걸 전부 빨간 「충돌」로 부르면 상시
#     빨강이 되어 **진짜 충돌이 생겼을 때 묻힌다**(이 repo의 GFA 배너 63일 거짓 빨강과 같은 실패).
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from app.models import Channel, ProductChannelMapping, ProductMaster


@dataclass
class CellMapping:
    mapping_id: int
    channel_product_id: str
    channel_product_name: str | None
    channel_sku: str | None
    selling_price: Decimal
    is_active: bool
    mapping_source: str
    conflict: bool = False  # 이 옵션ID를 나눠 가진 마스터들의 원가가 다름 = 이중귀속 위험
    shared: bool = False  # 나눠 가졌지만 원가가 같음 = 금액 영향 없음(리스팅 공유)


@dataclass
class ConnectionRow:
    product_id: int
    internal_sku: str
    product_name: str
    cost_price: Decimal
    # channel_id -> list[CellMapping] (한 채널에 옵션ID가 여러 개일 수 있음)
    cells: dict[int, list[CellMapping]] = field(default_factory=dict)
    mapped_channel_count: int = 0
    has_conflict: bool = False
    has_shared: bool = False


@dataclass
class ConnectionChannel:
    channel_id: int
    channel_code: str
    channel_name: str
    platform: str
    sell_type: str | None


@dataclass
class ConnectionMap:
    channels: list[ConnectionChannel]
    rows: list[ConnectionRow]
    total_products: int  # 필터(q) 적용 후 전체 상품 수
    shown_products: int  # 실제 반환된 행 수(limit 적용 시 total보다 작을 수 있음)
    conflict_option_count: int  # 원가가 갈리는 (채널,옵션ID) 조합 수(전역, 필터 무관)
    shared_option_count: int = 0  # 원가가 같아 금액 영향이 없는 공유 조합 수(전역)


def build_connection_map(
    db: Session, q: str | None = None, limit: int | None = None
) -> ConnectionMap:
    """내부옵션×채널 매트릭스를 조립. `q`=internal_sku/상품명 부분일치 필터, `limit`=행 상한."""
    channels = db.query(Channel).order_by(Channel.id).all()
    ch_infos = [
        ConnectionChannel(c.id, c.code, c.name, c.platform, c.sell_type)
        for c in channels
    ]

    # 판정: (channel_id, channel_product_id) → distinct product_id 집합.
    # is_active만 대상 — 비활성 매핑은 매출/원가 이중귀속을 유발하지 않음(커버리지 SA와 일관).
    owner: dict[tuple[int, str], set[int]] = {}
    for cid, cpid, prod in (
        db.query(
            ProductChannelMapping.channel_id,
            ProductChannelMapping.channel_product_id,
            ProductChannelMapping.product_id,
        )
        .filter(ProductChannelMapping.is_active.is_(True))
        .all()
    ):
        owner.setdefault((cid, cpid), set()).add(prod)

    # 원가가 같으면 «공유», 다르면 «충돌».
    # ★«알 수 있는 원가»의 정의를 손익 엔진과 같은 축에 둔다: `_cost_of_line`은 falsy 원가
    #   (0·없음)를 «미상»으로 보고 그 라인 원가를 아예 빼 버린다(cost_price가 NOT NULL
    #   default 0이라 미입력과 실제 0을 구분할 수 없기 때문). 그래서 여기서도 0·None이 끼면
    #   «공유»로 접지 않는다 — **엔진이 원가를 못 셈하는 상태**와 **원가가 같은 상태**는 다르고,
    #   전자를 회색 「금액 영향 없음」으로 칠하면 그 결손이 안심 문구 뒤로 숨는다.
    #   None이 나오는 실제 경우 = product_id가 마스터에 없는 고아 매핑(2026-08-07 라이브에
    #   product_id=2628로 실재했다. 상품 최대 id는 949였다).
    cost_of = dict(db.query(ProductMaster.id, ProductMaster.cost_price).all())
    conflict_keys: set[tuple[int, str]] = set()
    shared_keys: set[tuple[int, str]] = set()
    for k, prods in owner.items():
        if len(prods) < 2:
            continue
        distinct_costs = {cost_of.get(pid) for pid in prods}
        if len(distinct_costs) == 1 and all(distinct_costs):
            shared_keys.add(k)
        else:
            conflict_keys.add(k)

    query = (
        db.query(ProductMaster)
        .options(joinedload(ProductMaster.channel_mappings))
        .order_by(ProductMaster.id)
    )
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            ProductMaster.internal_sku.ilike(like)
            | ProductMaster.product_name.ilike(like)
        )
    all_products = query.all()
    total = len(all_products)
    shown = all_products if limit is None else all_products[: max(0, limit)]

    rows: list[ConnectionRow] = []
    for p in shown:
        cells: dict[int, list[CellMapping]] = {}
        has_conf = False
        has_shar = False
        for m in p.channel_mappings:
            key = (m.channel_id, m.channel_product_id)
            conf = m.is_active and key in conflict_keys
            shar = m.is_active and key in shared_keys
            if conf:
                has_conf = True
            if shar:
                has_shar = True
            cells.setdefault(m.channel_id, []).append(
                CellMapping(
                    mapping_id=m.id,
                    channel_product_id=m.channel_product_id,
                    channel_product_name=m.channel_product_name,
                    channel_sku=m.channel_sku,
                    selling_price=m.selling_price,
                    is_active=m.is_active,
                    mapping_source=m.mapping_source,
                    conflict=conf,
                    shared=shar,
                )
            )
        rows.append(
            ConnectionRow(
                product_id=p.id,
                internal_sku=p.internal_sku,
                product_name=p.product_name,
                cost_price=p.cost_price,
                cells=cells,
                mapped_channel_count=len(cells),
                has_conflict=has_conf,
                has_shared=has_shar,
            )
        )

    return ConnectionMap(
        channels=ch_infos,
        rows=rows,
        total_products=total,
        shown_products=len(rows),
        conflict_option_count=len(conflict_keys),
        shared_option_count=len(shared_keys),
    )
