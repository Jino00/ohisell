# coupang 패키지 — 쿠팡 Open API 클라이언트 (도메인별 SA).
# 하위호환: 기존 `from app.clients.coupang import CoupangClient` 그대로 작동.
from app.clients.coupang._base import CoupangBaseClient
from app.clients.coupang.brand import CoupangBrandClient
from app.clients.coupang.category import CoupangCategoryClient
from app.clients.coupang.channel import CoupangClient
from app.clients.coupang.coupons import CoupangCouponClient
from app.clients.coupang.cs import CoupangCsClient
from app.clients.coupang.exchanges import CoupangExchangeClient
from app.clients.coupang.inbound import CoupangInboundClient
from app.clients.coupang.logistics import CoupangLogisticsClient
from app.clients.coupang.products import CoupangProductClient
from app.clients.coupang.returns import CoupangReturnClient
from app.clients.coupang.rocketgrowth import CoupangRocketGrowthClient
from app.clients.coupang.settlement import CoupangSettlementClient

__all__ = [
    "CoupangBaseClient",
    "CoupangBrandClient",
    "CoupangCategoryClient",
    "CoupangClient",
    "CoupangCouponClient",
    "CoupangCsClient",
    "CoupangExchangeClient",
    "CoupangInboundClient",
    "CoupangLogisticsClient",
    "CoupangProductClient",
    "CoupangReturnClient",
    "CoupangRocketGrowthClient",
    "CoupangSettlementClient",
]
