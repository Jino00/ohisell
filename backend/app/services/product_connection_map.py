# product_connection_map.py — 상품 연결맵 매트릭스 (트랙 S4, D-12)
#   내부옵션(행) × 채널(열) 그리드를 조립하는 읽기전용 SA. 부작용 없음.
#   채널옵션ID 충돌(같은 채널에서 1옵션ID가 2개 이상 마스터에 걸림 = 이중귀속 위험)을 표면화한다.
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
    conflict: bool = False  # 같은 채널에서 이 옵션ID가 2개 이상 마스터에 active로 걸림


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
    conflict_option_count: int  # 충돌한 (채널,옵션ID) 조합 수(전역, 필터 무관)


def build_connection_map(
    db: Session, q: str | None = None, limit: int | None = None
) -> ConnectionMap:
    """내부옵션×채널 매트릭스를 조립. `q`=internal_sku/상품명 부분일치 필터, `limit`=행 상한."""
    channels = db.query(Channel).order_by(Channel.id).all()
    ch_infos = [
        ConnectionChannel(c.id, c.code, c.name, c.platform, c.sell_type)
        for c in channels
    ]

    # 충돌 판정: (channel_id, channel_product_id) → distinct product_id 집합.
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
    conflict_keys = {k for k, prods in owner.items() if len(prods) > 1}

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
        for m in p.channel_mappings:
            conf = m.is_active and (m.channel_id, m.channel_product_id) in conflict_keys
            if conf:
                has_conf = True
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
            )
        )

    return ConnectionMap(
        channels=ch_infos,
        rows=rows,
        total_products=total,
        shown_products=len(rows),
        conflict_option_count=len(conflict_keys),
    )
