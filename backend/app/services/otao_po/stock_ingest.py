"""ECOUNT 창고별 재고 응답 → `otao_stock_snapshot` 적재. **네트워크를 모른다.**

계약 `docs/contracts/CONTRACT_inventory_unified.md` §4 S4 · 체인 `발주예측` n=8.

실행 스크립트는 `backend/scripts/ecount_stock_export.py`(Mac — ECOUNT를 부르는 유일한 자리)와
`backend/scripts/otao_stock_import.py`(prod — DB만 있으면 된다)다. 발주 원장이 쓰는
`otao_po_export.py`/`otao_po_import.py` 짝과 **같은 통로**다: prod 서버는 ECOUNT 허용목록 IP가
아니고, Mac은 prod DB에 직접 못 쓴다.

★이 모듈이 네트워크를 모르는 것이 설계다. n=5 사고(교훈: 격리 성공 ≠ 라이브)에서 배운 것은
「가짜 클라이언트 주입으로 테스트가 `client=None` 경로를 한 번도 안 밟았다」였다. 그래서 여기엔
클라이언트가 아예 없다 — 응답 **행 목록(dict)**만 받는다. 네트워크는 export 스크립트에만 있고,
그 경계가 얇을수록 테스트가 못 보는 자리가 줄어든다.

## ECOUNT 응답 필드 (공식 스펙 `AI_office/docs/references/10_ecount-openapi-integration-spec_20260616.md` §5b)

    POST /OAPI/V2/InventoryBalance/GetListInventoryBalanceStatusByLocation?SESSION_ID=…
    요청  BASE_DATE(필수, YYYYMMDD) · WH_CD(빈값=전체) · PROD_CD(빈값=전체)
    응답  Data.Result[] : WH_CD · WH_DES · PROD_CD · PROD_DES · PROD_SIZE_DES · BAL_QTY

## ★수량은 문자열로 올 수 있다 — 그리고 «못 읽은 것»을 0으로 만들지 않는다

`BAL_QTY`가 `"836.00"`·`"1,391"` 꼴로 올 수 있다. 파싱에 실패하면 그 행을 **버리고 사유와 함께
리포트에 싣는다.** 0으로 대체하면 「그 창고에 0개 있다」가 되어 재고가 조용히 사라진다 —
계약 §2-8이 겨눈 자리다(§3-4의 판매 축 판박이).

## ★같은 키가 두 번 오면 합치되 «합쳤다고 말한다»

`(창고, 품목)`이 응답에 두 번 나오면 수량을 더한다. 그런데 **조용히 더하면 안 된다** — n=6
적대 리뷰 P1-1이 정확히 그 모양이었다(중복 매핑 55키가 outerjoin에서 펼쳐져 판매를 6% 부풀렸고
픽스처가 키당 1행뿐이라 테스트 0건이 잡았다). 그래서 `duplicate_keys`로 세어 리포트에 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OtaoStockSnapshot

_ECOUNT_SOURCE = "ecount_api"
_MANUAL_SOURCE = "manual"


@dataclass
class StockIngestReport:
    snapshot_at: datetime | None = None
    source: str = _ECOUNT_SOURCE
    rows_in: int = 0
    inserted: int = 0
    unchanged: int = 0  # 이미 같은 키가 있었다 (멱등)
    skipped: list[dict] = field(default_factory=list)  # {"reason":…, "row":…}
    duplicate_keys: list[str] = field(default_factory=list)
    warehouses: dict[str, int] = field(default_factory=dict)  # 창고명 → 행수

    def as_dict(self) -> dict:
        return {
            "snapshot_at": self.snapshot_at.isoformat() if self.snapshot_at else None,
            "source": self.source,
            "rows_in": self.rows_in,
            "inserted": self.inserted,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "duplicate_keys": self.duplicate_keys,
            "warehouses": self.warehouses,
        }


def _decimal(raw) -> Decimal | None:
    """`"1,391.00"` → Decimal. 못 읽으면 **None**이지 0이 아니다."""
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        return Decimal(str(raw))
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _base_date(raw) -> date | None:
    """`"20260827"` → date. 형식이 아니면 None — `snapshot_at`으로 대체하지 않는다."""
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def build_stock_payload(
    result_rows: list[dict],
    *,
    snapshot_at: datetime,
    base_date_raw=None,
    source: str = _ECOUNT_SOURCE,
) -> dict:
    """ECOUNT `Data.Result[]` → 적재 페이로드(JSON 직렬화 가능).

    ★페이로드 파일 자체가 근거 보존물이다 — 무엇을 심었는지 나중에 파일로 되짚는다
    (`otao_po_export.py`와 같은 이유).
    """
    return {
        "snapshot_at": snapshot_at.isoformat(timespec="seconds"),
        "base_date": (_base_date(base_date_raw).isoformat() if _base_date(base_date_raw) else None),
        "source": source,
        "rows": [
            {
                "warehouse_code": str(r.get("WH_CD") or "").strip(),
                "warehouse_name": (str(r.get("WH_DES")).strip() if r.get("WH_DES") else None),
                "product_code": str(r.get("PROD_CD") or "").strip(),
                "product_name": (str(r.get("PROD_DES")).strip() if r.get("PROD_DES") else None),
                "quantity": (str(r.get("BAL_QTY")) if r.get("BAL_QTY") is not None else None),
                "raw": r,
            }
            for r in result_rows
        ],
    }


def ingest_stock_payload(
    session: Session, payload: dict, *, dry_run: bool = False
) -> StockIngestReport:
    """페이로드 → `otao_stock_snapshot`. 멱등이다.

    같은 `(snapshot_at, warehouse_code, product_code)`가 이미 있으면 **건드리지 않는다** —
    스냅샷은 「그 시각에 그렇게 보였다」는 관측 기록이라 추후 정정 대상이 아니다. 값이 달라졌다면
    그건 새 관측이므로 새 `snapshot_at`으로 들어와야 한다.
    """
    import json as _json

    source = str(payload.get("source") or _ECOUNT_SOURCE)
    rep = StockIngestReport(source=source)

    snapshot_at = payload.get("snapshot_at")
    if not snapshot_at:
        raise ValueError("payload에 snapshot_at이 없다 — 시각 없는 스냅샷은 t0가 될 수 없다")
    at = datetime.fromisoformat(str(snapshot_at))
    rep.snapshot_at = at
    bdate = payload.get("base_date")
    base = date.fromisoformat(str(bdate)) if bdate else None

    rows = list(payload.get("rows") or [])
    rep.rows_in = len(rows)

    # ── 키별로 접는다. 합치되 «합쳤다»고 말한다. ──────────────────────────
    folded: dict[tuple[str, str], dict] = {}
    for r in rows:
        wh = str(r.get("warehouse_code") or "").strip()
        code = str(r.get("product_code") or "").strip()
        if not code:
            # 품목코드가 없으면 이 행은 키를 가질 수 없다. 버리되 사유를 남긴다.
            rep.skipped.append({"reason": "product_code 없음", "row": r})
            continue
        qty = _decimal(r.get("quantity"))
        if qty is None:
            # ★0으로 대체하지 않는다 — 「못 읽었다」와 「0개다」는 다르다.
            rep.skipped.append({"reason": f"수량 파싱 실패({r.get('quantity')!r})", "row": r})
            continue
        if not wh:
            # 창고코드가 비면 이름을 키로 쓴다. 빈 문자열로 접으면 서로 다른 창고가 한 줄이 된다.
            wh = str(r.get("warehouse_name") or "").strip() or "(창고미상)"
        key = (wh, code)
        if key in folded:
            folded[key]["quantity"] += qty
            label = f"{wh}/{code}"
            if label not in rep.duplicate_keys:
                rep.duplicate_keys.append(label)
        else:
            folded[key] = {
                "warehouse_code": wh,
                "warehouse_name": r.get("warehouse_name"),
                "product_code": code,
                "product_name": r.get("product_name"),
                "quantity": qty,
                "raw": r.get("raw"),
            }
        name = str(r.get("warehouse_name") or wh)
        rep.warehouses[name] = rep.warehouses.get(name, 0) + 1

    existing = {
        (s.warehouse_code, s.product_code)
        for s in session.scalars(
            select(OtaoStockSnapshot).where(OtaoStockSnapshot.snapshot_at == at)
        ).all()
    }

    for key, row in folded.items():
        if key in existing:
            rep.unchanged += 1
            continue
        session.add(
            OtaoStockSnapshot(
                snapshot_at=at,
                base_date=base,
                warehouse_code=row["warehouse_code"],
                warehouse_name=row["warehouse_name"],
                product_code=row["product_code"],
                product_name=row["product_name"],
                quantity=row["quantity"],
                source=source,
                raw_json=_json.dumps(row["raw"], ensure_ascii=False) if row["raw"] else None,
            )
        )
        rep.inserted += 1

    if dry_run:
        session.rollback()
    else:
        session.commit()
    return rep


def build_manual_count_payload(
    counts: dict[str, str | int | float],
    *,
    snapshot_at: datetime,
    warehouse_name: str = "본사",
    warehouse_code: str = "(실사)",
) -> dict:
    """사람이 센 값 → 같은 테이블의 `source='manual'` 행.

    ★`manual`은 스냅샷 축에서 **빠진다**(`stock.py` `_MANUAL_SOURCE`) — 대조의 상대편이지
    「시스템이 말한 재고」가 아니기 때문이다. 섞으면 ECOUNT 값을 자기 자신과 대조하게 되어
    오차가 항상 0으로 나온다.
    """
    return {
        "snapshot_at": snapshot_at.isoformat(timespec="seconds"),
        "base_date": snapshot_at.date().isoformat(),
        "source": _MANUAL_SOURCE,
        "rows": [
            {
                "warehouse_code": warehouse_code,
                "warehouse_name": warehouse_name,
                "product_code": str(code).strip(),
                "product_name": None,
                "quantity": str(qty),
                "raw": {"counted_by": "human", "product_code": code, "quantity": qty},
            }
            for code, qty in counts.items()
        ],
    }
