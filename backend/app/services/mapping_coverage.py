# mapping_coverage.py — 상품 연관맵 커버리지 리포트 (트랙 S2)
#       채널별 매핑 보유율 + 주문에는 있으나 연관맵에 없는 옵션ID를 집계한다.
#       주문↔마스터 연결(백필)은 기존 sync_service.relink_unlinked_orders를 재사용한다(신규 SA 불필요).
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Channel, Order, ProductChannelMapping


@dataclass
class ChannelCoverage:
    channel_id: int
    channel_code: str
    channel_name: str
    mapped_option_count: int  # 연관맵에 매핑된 옵션 수(is_active)
    order_option_count: int  # 주문에서 관측된 distinct 옵션ID 수
    unmapped_order_options: list[dict] = field(default_factory=list)  # [{option_id, order_count}]
    total_orders: int = 0
    unlinked_orders: int = 0  # product_id IS NULL
    blank_option_id_orders: int = 0  # platform_product_id=""(옵션ID 자체가 없는 주문, codex P2)

    @property
    def order_option_coverage(self) -> float:
        """주문에서 관측된 옵션ID 중 연관맵에 매핑된 비율(0~1).

        분모(order_option_count)가 0이면 1.0을 반환하지만, 이는 "옵션ID가 있는 주문이 하나도
        없다"는 뜻이지 그 채널에 문제가 없다는 보장은 아니다(codex P2) — 옵션ID 자체가 비어있는
        (platform_product_id="") 주문이 있을 수 있으므로 별도 blank_option_id_orders로 확인할 것.
        """
        if self.order_option_count == 0:
            return 1.0
        return round(1 - len(self.unmapped_order_options) / self.order_option_count, 4)


def compute_mapping_coverage(db: Session) -> list[ChannelCoverage]:
    channels = db.query(Channel).order_by(Channel.id).all()
    result: list[ChannelCoverage] = []

    for ch in channels:
        mapped_count = (
            db.query(ProductChannelMapping)
            .filter(
                ProductChannelMapping.channel_id == ch.id,
                ProductChannelMapping.is_active.is_(True),
            )
            .count()
        )
        mapped_ids = {
            r[0]
            for r in db.query(ProductChannelMapping.channel_product_id)
            .filter(
                ProductChannelMapping.channel_id == ch.id,
                ProductChannelMapping.is_active.is_(True),
            )
            .all()
        }

        order_rows = (
            db.query(
                Order.platform_product_id,
                func.count(Order.id).label("order_count"),
            )
            .filter(Order.channel_id == ch.id, Order.platform_product_id != "")
            .group_by(Order.platform_product_id)
            .all()
        )

        unmapped = [
            {"option_id": pid, "order_count": cnt}
            for pid, cnt in order_rows
            if pid not in mapped_ids
        ]
        unmapped.sort(key=lambda x: -x["order_count"])

        total_orders = db.query(Order).filter(Order.channel_id == ch.id).count()
        unlinked_orders = (
            db.query(Order)
            .filter(Order.channel_id == ch.id, Order.product_id.is_(None))
            .count()
        )
        blank_option_id_orders = (
            db.query(Order)
            .filter(Order.channel_id == ch.id, Order.platform_product_id == "")
            .count()
        )

        result.append(
            ChannelCoverage(
                channel_id=ch.id,
                channel_code=ch.code,
                channel_name=ch.name,
                mapped_option_count=mapped_count,
                order_option_count=len(order_rows),
                unmapped_order_options=unmapped,
                total_orders=total_orders,
                unlinked_orders=unlinked_orders,
                blank_option_id_orders=blank_option_id_orders,
            )
        )

    return result
