# diag_rg_probe.py — 로켓그로스(RG) 라이브 실태 진단 (읽기 전용, GET만)
# 목적: P3 설계 전 "RG 상품/사이즈/재고/주문이 실제로 존재하는가"를 라이브로 확정(원칙22, 추정금지).
#   트랙은 "RG 판매 0"이라 했으나 판매0 ≠ 상품0. 상품·사이즈·재고가 있으면 P3가 작업할 거리가 있음.
# 실행: 서버에서  cd /home/ubuntu/ohisell/backend && .venv/bin/python scripts/diag_rg_probe.py
#   (쿠팡 Open API는 IP 화이트리스트 — 서버에서만. 로컬은 403.)
# 명세: docs/references/05_coupang_rocketgrowth_api_specs.md
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.clients.coupang._base import CoupangBaseClient
from app.config import get_coupang_config

ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]
_SELLER_BASE = "/v2/providers/seller_api/apis/api/v1/marketplace"
_RG_BASE = "/v2/providers/rg_open_api/apis/api/v1/vendors"


def line(t=""):
    print(t, flush=True)


def probe_account(key: str) -> None:
    line("\n" + "=" * 64)
    line(f"계정 {key}")
    line("=" * 64)
    cfg = get_coupang_config(key)
    if cfg is None:
        line("  config_missing (env 미설정)")
        return
    cli = CoupangBaseClient(cfg)
    vid = cfg.vendor_id
    line(f"  vendor_id={vid}")

    # ── 1) RG 상품 목록 (businessTypes=rocketGrowth) — 상품 존재 여부 ──
    line("\n[1] RG 상품 목록 (businessTypes=rocketGrowth)")
    rg_count = 0
    sample_spids: list = []
    next_token = ""
    pages = 0
    while pages < 20:  # 안전 상한
        params = {"vendorId": vid, "maxPerPage": 100, "businessTypes": "rocketGrowth"}
        if next_token:
            params["nextToken"] = next_token
        resp = cli._request("GET", f"{_SELLER_BASE}/seller-products", params)
        if resp is None:
            line("    응답 None (인증/IP/네트워크 실패 — 서버에서 실행했는지 확인)")
            return
        data = resp.get("data") or []
        rg_count += len(data)
        for d in data:
            if len(sample_spids) < 5:
                sample_spids.append(d.get("sellerProductId"))
        next_token = resp.get("nextToken") or ""
        pages += 1
        if not next_token:
            break
    line(f"    RG/하이브리드 상품 수: {rg_count}  (페이지 {pages})")
    line(f"    표본 sellerProductId: {sample_spids}")

    # ── 2) 표본 상품 단건조회 → rocketGrowthItemData.skuInfo(사이즈) 존재 여부 ──
    line("\n[2] 표본 상품 사이즈(skuInfo) 실태")
    size_present = 0
    size_filled = 0
    opt_total = 0
    vii_sample: list = []
    for spid in sample_spids[:3]:
        if spid is None:
            continue
        detail = cli._request("GET", f"{_SELLER_BASE}/seller-products/{spid}")
        if not detail or not detail.get("data"):
            line(f"    spid={spid}: 단건조회 실패/빈값")
            continue
        items = detail["data"].get("items") or []
        for it in items:
            opt_total += 1
            rg = it.get("rocketGrowthItemData")
            if not rg:
                continue
            size_present += 1
            vii = rg.get("vendorItemId")
            if vii and len(vii_sample) < 5:
                vii_sample.append(vii)
            sku = rg.get("skuInfo") or {}
            dims = [sku.get("width"), sku.get("length"), sku.get("height"), sku.get("weight")]
            if any(v for v in dims):
                size_filled += 1
        line(f"    spid={spid}: items={len(items)}")
    line(f"    옵션 합계 {opt_total} / rocketGrowthItemData 있음 {size_present} / 사이즈 채워짐 {size_filled}")
    line(f"    표본 vendorItemId: {vii_sample}")

    # ── 3) 로켓창고 재고 summaries — 재고 행 존재 여부 ──
    line("\n[3] 로켓창고 재고 summaries")
    inv_count = 0
    inv_sample: list = []
    next_token = ""
    pages = 0
    while pages < 20:
        params = {}
        if next_token:
            params["nextToken"] = next_token
        resp = cli._request("GET", f"{_RG_BASE}/{vid}/rg/inventory/summaries", params or None)
        if resp is None:
            line("    응답 None (인증/IP/네트워크 실패)")
            break
        data = resp.get("data") or []
        inv_count += len(data)
        for d in data:
            if len(inv_sample) < 3:
                inv_sample.append({
                    "vii": d.get("vendorItemId"),
                    "orderable": (d.get("inventoryDetails") or {}).get("totalOrderableQuantity"),
                    "sold30d": (d.get("salesCountMap") or {}).get("SALES_COUNT_LAST_THIRTY_DAYS"),
                })
        next_token = resp.get("nextToken") or ""
        pages += 1
        if not next_token:
            break
    line(f"    재고 요약 행 수: {inv_count}  (페이지 {pages})")
    line(f"    표본: {inv_sample}")


def main() -> None:
    line("로켓그로스 라이브 실태 진단 (읽기 전용) — 원칙22")
    for key in ACCOUNTS:
        probe_account(key)
    line("\n완료. 위 수치로 P3 설계(사이즈/재고 적재 가치) 판단.")


if __name__ == "__main__":
    main()
