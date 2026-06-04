# product_write.py — 쿠팡 상품 쓰기 Harness (트랙 D-16, W3).
# W3a 단순쓰기 9개(재고·가격·할인율기준가·판매중지/재개·자동생성옵션 활성/비활성) 구현.
# (W3b 상품 생성/수정/삭제는 추후 같은 파일에 추가 — 트랙 §5 product_write.py.)
# 핵심: dry_run 게이트는 여기서만(_write_guard). SA는 dry_run 모름(원칙18-1 단일책임).
# W3a는 전부 request body 없음(명세 02 §4) — path/query만. payload는 변경 파라미터 미리보기.
# 진입부에서 path 정수값을 검증(dry-run에서도 통과해야 — W2 codex 교훈: dry검증 우회 방지).
from __future__ import annotations

import logging
from urllib.parse import urlencode

from app.clients.coupang._base import CoupangWriteValidationError
from app.clients.coupang.products import (
    CoupangProductClient,
    _SELLER_BASE as _SELLER_BASE_PATH,
    auto_option_item_optin_path,
    auto_option_item_optout_path,
    auto_option_seller_optin_path,
    auto_option_seller_optout_path,
    item_base_price_path,
    item_price_path,
    item_price_query,
    item_quantity_path,
    item_resume_path,
    item_stop_path,
)
from app.config import CoupangAccountConfig
from app.services.coupang._write_guard import guarded_write

log = logging.getLogger(__name__)


def _client(account: CoupangAccountConfig) -> CoupangProductClient:
    return CoupangProductClient(account)


def _require_int(value: object, field: str, *, min_value: int) -> int:
    """필수 정수 검증(dry-run preview에서도 적용 — W2 codex[P2]: dry검증 우회 방지).

    dry_run은 SA를 호출하지 않으므로 SA._vid가 안 돌아간다. preview가 '라이브 가능 파라미터'
    이려면 Harness 진입부에서 dry/live 공통 검증해야 한다. SA._vid는 직접호출 대비 이중 방어.
    범위·10원단위 등 세부는 쿠팡이 검증(D-1) — 여기선 타입·하한만.
    검증 실패=클라이언트 입력 오류 → CoupangWriteValidationError(라우터 400, codex[P2] W3a).
    """
    try:
        v = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as e:
        raise CoupangWriteValidationError(
            f"상품 쓰기 필수 정수 필드 오류: {field}={value!r}"
        ) from e
    if v < min_value:
        raise CoupangWriteValidationError(f"상품 쓰기 {field} 범위 오류: {v} (최소 {min_value})")
    return v


def _as_bool(value: object, field: str) -> bool | None:
    """bool 정규화(Harness 경계 — 직접 호출자가 'false' 문자열 넘기는 버그 차단, codex[P2] W3a).

    라우터는 bool 타입을 강제하지만 Harness 직접 호출(테스트·스크립트)은 문자열 가능.
    'false'는 truthy라 정규화 없이는 SA가 "true"로 전송(라이브 오변경). None은 미지정(통과).
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes"):
            return True
        if v in ("false", "0", "no", ""):
            return False
    if isinstance(value, int):  # bool은 위에서 처리됨 — 순수 int는 0/1만(codex[P2] R2)
        if value in (0, 1):
            return bool(value)
        # ap_active=2·-1 같은 비정상 정수가 조용히 True 되는 것 차단(라이브 오변경 방지)
    raise CoupangWriteValidationError(f"상품 쓰기 bool 필드 오류: {field}={value!r}")


# ════════════════════════════════════════════════════════════════════
# W3a 옵션 단위 쓰기 (vendorItemId 대상)
# ════════════════════════════════════════════════════════════════════

def update_item_quantity(
    account: CoupangAccountConfig,
    vendor_item_id: int,
    quantity: int,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#14 아이템 재고수량 변경. dry_run 기본(라이브 미변경). 재고 0=품절(허용)."""
    vid = _require_int(vendor_item_id, "vendorItemId", min_value=1)
    qty = _require_int(quantity, "quantity", min_value=0)
    client = _client(account)
    return guarded_write(
        operation=f"재고변경(vii={vid}→{qty})",
        method="PUT",
        path=item_quantity_path(vid, qty),
        payload={"vendorItemId": vid, "quantity": qty, "_note": "path segment, body 없음"},
        sa_call=lambda: client.update_item_quantity(vendor_item_id=vid, quantity=qty),
        dry_run=dry_run,
        confirm=confirm,
    )


def update_item_price(
    account: CoupangAccountConfig,
    vendor_item_id: int,
    price: int,
    *,
    force_sale_price_update: bool = False,
    ap_min_sale_price: int | None = None,
    ap_active: bool | None = None,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#15 아이템 판매가격 변경. dry_run 기본.

    apMinSalePrice/apActive는 자동가격조정 — 둘 다 함께 전달해야 함(명세 02 §4). 하나만 주면 거부.
    """
    vid = _require_int(vendor_item_id, "vendorItemId", min_value=1)
    prc = _require_int(price, "price", min_value=1)
    force = _as_bool(force_sale_price_update, "force_sale_price_update") or False
    ap_on = _as_bool(ap_active, "ap_active")  # None=미지정
    ap_min = (
        _require_int(ap_min_sale_price, "apMinSalePrice", min_value=1)
        if ap_min_sale_price is not None
        else None
    )
    # 자동가격조정은 apMinSalePrice·apActive를 함께 전달(명세 제약 — 하나만이면 쿠팡 400 전에 차단)
    if (ap_min is None) != (ap_on is None):
        raise CoupangWriteValidationError(
            "가격변경: apMinSalePrice와 apActive는 함께 전달해야 합니다(자동가격조정)."
        )
    if ap_min is not None and ap_min >= prc:
        raise CoupangWriteValidationError(
            f"가격변경: apMinSalePrice({ap_min})는 price({prc})보다 작아야 합니다."
        )
    # preview path = 실제 호출될 path+query(no-body API — codex[P2]: SA와 같은 item_price_query)
    query = item_price_query(
        force_sale_price_update=force, ap_min_sale_price=ap_min, ap_active=ap_on
    )
    preview_path = item_price_path(vid, prc)
    if query:
        preview_path += "?" + urlencode(query)
    client = _client(account)
    return guarded_write(
        operation=f"가격변경(vii={vid}→{prc}원)",
        method="PUT",
        path=preview_path,
        payload={
            "vendorItemId": vid,
            "price": prc,
            "forceSalePriceUpdate": force,
            "apMinSalePrice": ap_min,
            "apActive": ap_on,
            "query": query,
            "_note": "path+query, body 없음",
        },
        sa_call=lambda: client.update_item_price(
            vendor_item_id=vid,
            price=prc,
            force_sale_price_update=force,
            ap_min_sale_price=ap_min,
            ap_active=ap_on,
        ),
        dry_run=dry_run,
        confirm=confirm,
    )


def update_item_base_price(
    account: CoupangAccountConfig,
    vendor_item_id: int,
    original_price: int,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#16 아이템 할인율 기준가격 변경. dry_run 기본. originalPrice 0원부터 가능(명세)."""
    vid = _require_int(vendor_item_id, "vendorItemId", min_value=1)
    op = _require_int(original_price, "originalPrice", min_value=0)
    client = _client(account)
    return guarded_write(
        operation=f"할인율기준가 변경(vii={vid}→{op}원)",
        method="PUT",
        path=item_base_price_path(vid, op),
        payload={"vendorItemId": vid, "originalPrice": op, "_note": "path segment, body 없음"},
        sa_call=lambda: client.update_item_base_price(
            vendor_item_id=vid, original_price=op
        ),
        dry_run=dry_run,
        confirm=confirm,
    )


def resume_item_sale(
    account: CoupangAccountConfig,
    vendor_item_id: int,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#17 아이템 판매 재개. dry_run 기본."""
    vid = _require_int(vendor_item_id, "vendorItemId", min_value=1)
    client = _client(account)
    return guarded_write(
        operation=f"판매재개(vii={vid})",
        method="PUT",
        path=item_resume_path(vid),
        payload={"vendorItemId": vid, "_note": "path segment, body 없음"},
        sa_call=lambda: client.resume_item_sale(vendor_item_id=vid),
        dry_run=dry_run,
        confirm=confirm,
    )


def stop_item_sale(
    account: CoupangAccountConfig,
    vendor_item_id: int,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#18 아이템 판매 중지. dry_run 기본."""
    vid = _require_int(vendor_item_id, "vendorItemId", min_value=1)
    client = _client(account)
    return guarded_write(
        operation=f"판매중지(vii={vid})",
        method="PUT",
        path=item_stop_path(vid),
        payload={"vendorItemId": vid, "_note": "path segment, body 없음"},
        sa_call=lambda: client.stop_item_sale(vendor_item_id=vid),
        dry_run=dry_run,
        confirm=confirm,
    )


def enable_auto_option_item(
    account: CoupangAccountConfig,
    vendor_item_id: int,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#19 자동생성옵션 활성화(옵션 단위). dry_run 기본."""
    vid = _require_int(vendor_item_id, "vendorItemId", min_value=1)
    client = _client(account)
    return guarded_write(
        operation=f"자동옵션 활성화(vii={vid})",
        method="POST",
        path=auto_option_item_optin_path(vid),
        payload={"vendorItemId": vid, "_note": "path segment, body 없음"},
        sa_call=lambda: client.enable_auto_option_item(vendor_item_id=vid),
        dry_run=dry_run,
        confirm=confirm,
    )


def disable_auto_option_item(
    account: CoupangAccountConfig,
    vendor_item_id: int,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#21 자동생성옵션 비활성화(옵션 단위). dry_run 기본."""
    vid = _require_int(vendor_item_id, "vendorItemId", min_value=1)
    client = _client(account)
    return guarded_write(
        operation=f"자동옵션 비활성화(vii={vid})",
        method="POST",
        path=auto_option_item_optout_path(vid),
        payload={"vendorItemId": vid, "_note": "path segment, body 없음"},
        sa_call=lambda: client.disable_auto_option_item(vendor_item_id=vid),
        dry_run=dry_run,
        confirm=confirm,
    )


# ════════════════════════════════════════════════════════════════════
# W3a 셀러 전체 단위 쓰기 (vendorItemId 없음 — HMAC키로 셀러 식별, 명세 02 §4)
# ⚠️ 전체 상품에 영향. 라이브 실행 시 셀러의 모든 적격 상품에 자동옵션 적용/해제.
# ════════════════════════════════════════════════════════════════════

def enable_auto_option_all(
    account: CoupangAccountConfig,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#20 자동생성옵션 활성화(전체 단위). ⚠️ 셀러 전체 상품 대상. dry_run 기본."""
    client = _client(account)
    return guarded_write(
        operation=f"자동옵션 활성화(전체, vendor={account.vendor_id})",
        method="POST",
        path=auto_option_seller_optin_path(),
        payload={"scope": "seller-all", "vendorId": account.vendor_id, "_note": "body 없음"},
        sa_call=lambda: client.enable_auto_option_all(),
        dry_run=dry_run,
        confirm=confirm,
    )


def disable_auto_option_all(
    account: CoupangAccountConfig,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#22 자동생성옵션 비활성화(전체 단위). ⚠️ 셀러 전체 상품 대상. dry_run 기본."""
    client = _client(account)
    return guarded_write(
        operation=f"자동옵션 비활성화(전체, vendor={account.vendor_id})",
        method="POST",
        path=auto_option_seller_optout_path(),
        payload={"scope": "seller-all", "vendorId": account.vendor_id, "_note": "body 없음"},
        sa_call=lambda: client.disable_auto_option_all(),
        dry_run=dry_run,
        confirm=confirm,
    )


# ════════════════════════════════════════════════════════════════════
# W3b 복잡 쓰기 (상품 생성·승인요청·수정·삭제 차단)
# #9·#11·#12는 body 있음(JSON dict). #10은 no-body. #13은 시스템 정책으로 영구 차단.
# ════════════════════════════════════════════════════════════════════

def _require_product_body(body: object, required_keys: tuple[str, ...], context: str) -> dict:
    """body가 dict이고 required_keys를 가졌는지 검증(dry-run에서도 적용).

    body writes는 SA._vid 이중방어가 없으므로 Harness 진입부에서 타입·필수키 검증한다.
    """
    if not isinstance(body, dict):
        raise CoupangWriteValidationError(f"{context}: body는 dict여야 합니다 (받은 타입: {type(body).__name__})")
    for k in required_keys:
        if k not in body:
            raise CoupangWriteValidationError(f"{context}: body 필수 필드 누락: '{k}'")
    return body


def _body_preview(body: dict, context: str) -> dict:
    """dry_run payload용 body 요약(대용량 body의 과도한 미리보기 방지)."""
    items = body.get("items") or []
    return {
        "_context": context,
        "sellerProductId": body.get("sellerProductId"),
        "sellerProductName": body.get("sellerProductName"),
        "vendorId": body.get("vendorId"),
        "item_count": len(items),
        "requested": body.get("requested"),
        "_note": "body 요약(상세는 라이브 실행 시 전달됨)",
    }


def create_product(
    account: CoupangAccountConfig,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#9 상품 생성 (POST, body 있음). dry_run 기본(라이브 미생성).

    body: 상품 전체 JSON dict. 필수: vendorId, sellerProductName, items[].
    dry_run=True면 body 요약 미리보기만 반환(API 미호출). requested=true로 생성 시 자동 승인요청.
    """
    _require_product_body(body, ("vendorId", "sellerProductName", "items"), "상품생성")
    client = _client(account)
    return guarded_write(
        operation=f"상품생성({body.get('sellerProductName', '')!r})",
        method="POST",
        path=f"{_SELLER_BASE_PATH}/seller-products",
        payload=_body_preview(body, "상품생성"),
        sa_call=lambda: client.create_product(body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


def request_approval(
    account: CoupangAccountConfig,
    seller_product_id: int,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#10 상품 승인 요청 (PUT, body 없음). dry_run 기본.

    '임시저장' 상태에서만 가능. create_product(requested=True)로 생성하면 자동이므로 별도 불필요.
    """
    spid = _require_int(seller_product_id, "sellerProductId", min_value=1)
    client = _client(account)
    return guarded_write(
        operation=f"상품승인요청(spid={spid})",
        method="PUT",
        path=f"{_SELLER_BASE_PATH}/seller-products/{spid}/approvals",
        payload={"sellerProductId": spid, "_note": "path param, body 없음"},
        sa_call=lambda: client.request_approval(seller_product_id=spid),
        dry_run=dry_run,
        confirm=confirm,
    )


def update_product(
    account: CoupangAccountConfig,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#11 상품 수정 (승인필요, PUT, body 있음). dry_run 기본.

    body: 상품 전체 JSON dict. 필수: sellerProductId, vendorId, items[].
    items[] 각 옵션에 sellerProductItemId 포함 시 수정, 없으면 신규 추가, 통째 제거하면 삭제.
    ⚠️ 승인완료 상품의 판매가·재고·판매상태는 #14~18(단순쓰기 SA)을 사용할 것.
    """
    _require_product_body(body, ("sellerProductId", "vendorId", "items"), "상품수정(승인필요)")
    spid = _require_int(body["sellerProductId"], "sellerProductId", min_value=1)
    client = _client(account)
    return guarded_write(
        operation=f"상품수정(승인필요, spid={spid})",
        method="PUT",
        path=f"{_SELLER_BASE_PATH}/seller-products",
        payload=_body_preview(body, "상품수정(승인필요)"),
        sa_call=lambda: client.update_product(body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


def update_product_partial(
    account: CoupangAccountConfig,
    seller_product_id: int,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#12 상품 수정 (승인불필요, PUT, body 있음). dry_run 기본.

    배송/반품지 정보만 승인 없이 수정. 필드 생략 가능(부분 전송).
    '임시저장·승인대기중'은 수정 불가(쿠팡 400).
    """
    spid = _require_int(seller_product_id, "sellerProductId", min_value=1)
    _require_product_body(body, ("sellerProductId",), "상품수정(승인불필요)")
    body_spid = _require_int(body["sellerProductId"], "body.sellerProductId", min_value=1)
    if spid != body_spid:
        raise CoupangWriteValidationError(
            f"상품수정(승인불필요): path sellerProductId({spid}) ≠ body sellerProductId({body_spid})"
        )
    client = _client(account)
    return guarded_write(
        operation=f"상품수정(승인불필요, spid={spid})",
        method="PUT",
        path=f"{_SELLER_BASE_PATH}/seller-products/{spid}/partial",
        payload={"sellerProductId": spid, "fields": list(body.keys()), "_note": "부분 body"},
        sa_call=lambda: client.update_product_partial(seller_product_id=spid, body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


def delete_product(*args, **kwargs) -> None:  # type: ignore[return]
    """#13 상품 삭제 — ⛔ 이 시스템에서 영구 차단(시스템 정책, 원칙 W3b 확정).

    실수로 라이브 상품을 삭제하는 사고를 원천 차단한다.
    삭제가 필요하면 Wing(coupang.com)에서 직접 수행할 것.
    """
    raise CoupangWriteValidationError(
        "상품 삭제는 이 시스템에서 허용하지 않습니다. Wing(coupang.com)에서 직접 수행하세요."
    )


# ════════════════════════════════════════════════════════════════════
# W5 RG 쓰기 (로켓그로스 상품 생성·수정 — seller_api 동일 경로)
# products.py #9·#11과 동일 경로이나 RG 전용 body(rocketGrowthItemData 포함).
# Harness 패턴 product_write.py 재사용(트랙 §5 W5 명시).
# ════════════════════════════════════════════════════════════════════

def _rg_client(account: CoupangAccountConfig):
    from app.clients.coupang.rocketgrowth import CoupangRocketGrowthClient
    return CoupangRocketGrowthClient(account)


def _require_rg_items(body: dict, context: str) -> None:
    """items[] 전체가 rocketGrowthItemData 키를 가지는지 검증(codex[P2] W5).

    RG·일반상품이 seller_api 동일 경로이므로, 일반 상품 body가 RG 라우트로 통과하는 것을 차단.
    skuInfo 내부 세부 구조는 D-1(추정금지) — 키 존재 여부만 확인.
    """
    items = body.get("items") or []
    if not items:
        return  # 빈 items는 _require_product_body에서 처리
    if not isinstance(items, list):
        raise CoupangWriteValidationError(f"{context}: items는 list여야 합니다")
    bad = [
        i for i, item in enumerate(items)
        if not isinstance(item, dict) or "rocketGrowthItemData" not in item
    ]
    if bad:
        raise CoupangWriteValidationError(
            f"{context}: items[{bad[0]}]에 rocketGrowthItemData 없음 "
            f"(RG 상품 필수 필드 — 명세 05 §6). {len(bad)}개 item 누락."
        )


def create_rg_product(
    account: CoupangAccountConfig,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#6 RG 상품 생성 (POST, body 있음). dry_run 기본(라이브 미등록).

    body 필수: vendorId, sellerProductName, items[](각 item에 rocketGrowthItemData.skuInfo 포함).
    경로·응답은 products.py create_product(#9)와 동일. RG 전용 body 구조.
    """
    _require_product_body(body, ("vendorId", "sellerProductName", "items"), "RG 상품 생성")
    _require_rg_items(body, "RG 상품 생성")
    client = _rg_client(account)
    return guarded_write(
        operation=f"RG 상품 생성({body.get('sellerProductName', '')!r})",
        method="POST",
        path=f"{_SELLER_BASE_PATH}/seller-products",
        payload=_body_preview(body, "RG 상품 생성"),
        sa_call=lambda: client.create_rg_product(body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


def update_rg_product(
    account: CoupangAccountConfig,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#7 RG 상품 수정 (PUT, body 있음). dry_run 기본.

    body 필수: sellerProductId, vendorId, items[]. 경로·응답은 products.py update_product(#11)과 동일.
    """
    _require_product_body(body, ("sellerProductId", "vendorId", "items"), "RG 상품 수정")
    _require_rg_items(body, "RG 상품 수정")
    spid = _require_int(body["sellerProductId"], "sellerProductId", min_value=1)
    client = _rg_client(account)
    return guarded_write(
        operation=f"RG 상품 수정(spid={spid})",
        method="PUT",
        path=f"{_SELLER_BASE_PATH}/seller-products",
        payload=_body_preview(body, "RG 상품 수정"),
        sa_call=lambda: client.update_rg_product(body=body),
        dry_run=dry_run,
        confirm=confirm,
    )
