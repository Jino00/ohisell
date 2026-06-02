# coupang 패키지 — 쿠팡 Open API 클라이언트 (도메인별 SA).
# 하위호환: 기존 `from app.clients.coupang import CoupangClient` 그대로 작동.
from app.clients.coupang._base import CoupangBaseClient
from app.clients.coupang.channel import CoupangClient
from app.clients.coupang.products import CoupangProductClient

__all__ = ["CoupangBaseClient", "CoupangClient", "CoupangProductClient"]
