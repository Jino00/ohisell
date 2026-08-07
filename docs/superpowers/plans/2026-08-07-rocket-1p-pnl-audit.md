# 로켓1P 손익 근거 화면(pnl-audit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로켓1P 판매 축 손익이 맞는지 Jino가 스스로 확인하는 화면 — 산술 검사 9종(A1~A7·B1·B2) + 「날짜×옵션」 원자를 원천 행까지 드릴다운.

**Architecture:** 계산은 새로 하지 않는다 — `compute_rocket_1p_revenue`(화면과 같은 함수)를 호출하고 그 응답의 **폴드들(일별·옵션별)이 사다리 타일과 일치하는지**를 검사한다. 원자 상세를 위해 그 함수의 원자 파생 루프를 `day_option_atoms()`로 추출한다(단일 출처 유지). 새 라우터+서비스+프론트 페이지 각 1파일.

**Tech Stack:** FastAPI + SQLAlchemy(text SQL) / React + vitest / pytest.

**스펙:** `docs/superpowers/specs/2026-08-07-rocket-1p-pnl-audit-design.md` (승인 완료)

**⚠️계획 단계 실측 (2026-08-07, prod 7일 창):** 광고비 총 6,968,457원 중 `ad_no_sales`(창 내 무판매 옵션)=253,091원 외에 **435,916원이 «판매행이 있는 옵션의 판매 없는 날» 광고비**로, 현행 엔진의 어느 버킷에도 귀속되지 않는다. **A7은 라이브에서 fail이 정상**이며 그것이 이 화면의 존재 이유다(실제 결손 관측). 엔진 수리는 이 계약 스코프 밖 — 이월.

---

## File Structure

| 파일 | 역할 | 신규/수정 |
|---|---|---|
| `backend/app/services/coupang/rocket_1p_revenue.py` | `day_option_atoms()` 추출(원자 파생의 단일 출처) | 수정 |
| `backend/app/services/coupang/rocket_1p_channel_pnl.py` | `promo_window_counts()` 추가 | 수정 |
| `backend/app/services/coupang/rocket_1p_pnl_audit.py` | 검사·원자목록·원자상세 계산 | 신규 |
| `backend/app/routers/rocket_1p_pnl_audit.py` | 얇은 라우터 3개 | 신규 |
| `backend/app/main.py` | 라우터 등록 1줄 | 수정 |
| `backend/tests/test_rocket_1p_pnl_audit.py` | 검사·원자 테스트 | 신규 |
| `frontend/src/lib/api.ts` | 타입+페처 3개 | 수정 |
| `frontend/src/pages/Rocket1PPnlAudit.tsx` | 근거 화면 | 신규 |
| `frontend/src/pages/rocket1pPnlAudit.test.tsx` | 렌더 테스트 | 신규 |
| `frontend/src/App.tsx` | 라우트 1줄 | 수정 |
| `frontend/src/pages/Rocket1PRevenue.tsx` | 「근거 보기」 버튼 | 수정 |

**공통 명령** (모든 백엔드 테스트는 `backend/`에서):
```bash
cd backend && python -m pytest tests/test_rocket_1p_pnl_audit.py -v
cd backend && python -m pytest tests/test_rocket_1p_revenue.py -q   # 리팩터 회귀 가드
cd frontend && npx tsc --noEmit && npm test
```

---

### Task 1: `day_option_atoms()` 추출 (원자 파생의 단일 출처)

**Files:**
- Modify: `backend/app/services/coupang/rocket_1p_revenue.py` (파생 루프 추출)
- Test: `backend/tests/test_rocket_1p_pnl_audit.py` (신규 — Σ원자 = 타일)

**왜:** 근거 화면의 3·4단이 원자를 필요로 하는데, `compute_rocket_1p_revenue`는 폴드만 반환한다. SQL을 복제하면 두 계산이 생긴다(스펙 §7 금지). 파생만 추출하고 기존 함수가 그것을 소비하게 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_rocket_1p_pnl_audit.py` 신규 생성. 픽스처는 `test_rocket_1p_revenue.py`와 같은 형(그대로 복사):

```python
# test_rocket_1p_pnl_audit.py — 손익 «근거 화면» SA (2026-08-07 설계 승인)
#
# 이 파일이 지키는 것:
#   ① 원자(day_option_atoms)의 합 = 화면 타일 — 원자는 파생의 단일 출처다
#   ② 검사는 «같은 함수의 다른 그레인»을 비교한다 — 재계산이 아니다
#   ③ B1은 절대 pass가 되지 않는다 — 판정할 수 없는 검사를 초록으로 칠하면 거짓 초록
#   ④ A5·A6·A7은 조용한 결손(INNER JOIN 탈락·분담금 모름·광고 미귀속)을 드러낸다
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text as _t
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (Channel, CoupangAdOptionDaily, CoupangAdReport,
                        CoupangRocketPurchaseOrderItem, CoupangRocketSalesDaily)
from app.services.coupang import rocket_1p_channel_pnl as pnl
from app.services.coupang.rocket_1p_revenue import (
    compute_rocket_1p_revenue, day_option_atoms)

VENDOR = pnl.ROCKET_1P_VENDOR_ID
ZERO_D = Decimal("0")
D = date(2026, 8, 4)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Channel(id=5, code="COUPANG_ROCKET", name="쿠팡 로켓배송", platform="coupang",
                  channel_type="consignment", company="주식회사 오하이테크"))
    s.commit()
    yield s
    s.close()


def _sale(s, option_id, sku, qty, consumer, *, d=D):
    s.add(CoupangRocketSalesDaily(
        vendor_id=VENDOR, option_id=option_id, sku_id=sku, date=d,
        qty=qty, revenue=Decimal(consumer),
        product_name=f"상품 {option_id}", source="sales_analysis"))


def _price(s, sku, unit_price, seq):
    s.add(CoupangRocketPurchaseOrderItem(
        purchase_order_seq=seq, vendor_id=VENDOR, product_number=sku,
        unit_purchase_price=Decimal(unit_price), order_qty=1))


def _cost(s, sku, cost_price, internal_sku=None, match_method=None):
    isku = internal_sku or f"OHI-{sku}"
    s.execute(_t("INSERT INTO product_master (internal_sku, product_name, cost_price) "
                 "VALUES (:i, :n, :c)"), {"i": isku, "n": isku, "c": cost_price})
    s.execute(_t("INSERT INTO rocket_product_cost_map "
                 "(product_number, internal_sku, status, match_method) "
                 "VALUES (:p, :i, 'confirmed', :m)"),
              {"p": str(sku), "i": isku, "m": match_method})


def _ad_option(s, option_id, spend, d=D):
    s.add(CoupangAdOptionDaily(
        report_date=d, vendor_id=VENDOR, sell_type="Retail",
        ad_option_id=option_id, conv_option_id=option_id,
        impressions=0, clicks=0, ad_spend=Decimal(spend),
        orders=0, sales_qty=0, conversion_revenue=Decimal("0")))


def _ad_account(s, spend, d=D):
    s.add(CoupangAdReport(report_date=d, sell_type="Retail", vendor_id=VENDOR,
                          impressions=0, clicks=0, ad_spend=Decimal(spend),
                          orders=0, sales_qty=0, conversion_revenue=Decimal("0")))


# ═══ ① 원자의 합 = 화면 타일 (원자는 파생의 단일 출처) ═══


def test_atoms_sum_to_screen_tile(db):
    """Σ원자 순이익 = compute_rocket_1p_revenue의 pnl 타일. 원자를 따로 계산하지 않았다는 증거."""
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)
    _ad_option(db, "A", "10000")
    _ad_account(db, "10000")
    db.commit()
    ctx = day_option_atoms(db, D, D)
    r = compute_rocket_1p_revenue(db, D, D)
    atom_sum = sum((a["net_profit"] for a in ctx["atoms"] if a["net_profit"] is not None), ZERO_D)
    assert str(atom_sum) == r["pnl"]["net_profit"]
    assert ctx["burden_known"] is True
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && python -m pytest tests/test_rocket_1p_pnl_audit.py -v`
Expected: FAIL — `ImportError: cannot import name 'day_option_atoms'`

- [ ] **Step 3: `day_option_atoms` 구현**

`rocket_1p_revenue.py`의 `compute_rocket_1p_revenue` **바로 위**에 추가:

```python
def day_option_atoms(
    db: Session, date_from: date, date_to: date, vendor_id: str | None = None
) -> dict:
    """「날짜×옵션」 원자와 그 파생 — **이 함수가 원자 파생의 단일 출처다.**

    `compute_rocket_1p_revenue`가 이것을 소비해 폴드(일별·옵션별·사다리)를 만들고,
    손익 근거 화면(pnl-audit)이 같은 것을 소비해 원자 목록·원자 상세를 만든다.
    두 화면이 같은 원자를 보므로 «근거가 화면과 다른 계산»이 원리적으로 불가능하다.

    반환: {"atoms": [원자…], "burden_known": bool,
           "ad_by_day_option": {(날짜,옵션): 광고비}, "ad_by_option": {옵션: 광고비}}
    원자 필드의 None 의미는 화면과 같다 — 모름이지 0이 아니다.
    """
    vendor = vendor_id or ROCKET_1P_VENDOR_ID
    params = {"vendor": vendor, "since": date_from.isoformat(), "until": date_to.isoformat()}
    rows = db.execute(text(_DAY_OPTION_SQL), params).fetchall()

    ad_by_day_option: dict[tuple[str, str], Decimal] = {}
    ad_by_option: dict[str, Decimal] = {}
    for d_, oid_, amt in db.execute(text(_DAY_OPTION_AD_SQL), params).fetchall():
        key, val = (str(d_)[:10], str(oid_)), _d(amt)
        ad_by_day_option[key] = val
        ad_by_option[str(oid_)] = ad_by_option.get(str(oid_), ZERO) + val

    burden_by_day_option = promo_burden_by_day_option(db, date_from, date_to, vendor)

    atoms: list[dict] = []
    for (day, option_id, sku_id, product_name, qty, consumer, ours,
         unit_price, visitors, cost, unit_cost) in rows:
        day_key = str(day)[:10]
        oid = str(option_id)
        q = int(qty or 0)
        consumer_d = _d(consumer)
        ours_d = _dn(ours)
        cost_d = _dn(cost)
        ad = ad_by_day_option.get((day_key, oid))
        # 광고 행이 없는 옵션·날 = 광고를 안 돌린 것 → 손익에선 0원이 맞다(표시는 None="—").
        ad_for_pnl = ad if ad is not None else ZERO
        burden = (None if burden_by_day_option is None
                  else burden_by_day_option.get((day_key, oid), ZERO))

        # ★순이익 공식·반올림 지점은 종전 인라인 파생과 **문자 그대로 같다** — 옮긴 것이지
        #   고친 게 아니다. 바꾸면 test_rocket_1p_revenue.py 30여 건이 잡는다.
        net = None
        net_upper = None
        if ours_d is not None and cost_d is not None and burden is not None:
            vat = payable_vat(ours_d, cost_d, burden, ad_for_pnl)
            net = _money(ours_d - cost_d - burden - ad_for_pnl - vat)
        elif cost_d is None and ours_d is not None and burden is not None:
            bound = _money(ours_d - burden - ad_for_pnl
                           - payable_vat(ours_d, ZERO, burden, ad_for_pnl))
            if bound < ZERO:
                net_upper = bound

        atoms.append({
            "date": day_key, "option_id": oid,
            "sku_id": sku_id, "product_name": product_name,
            "qty": q, "consumer_revenue": consumer_d, "our_revenue": ours_d,
            "unit_price": _dn(unit_price), "cost": cost_d, "unit_cost": _dn(unit_cost),
            "visitors": None if visitors is None else int(visitors),
            "ad_spend": ad, "ad_for_pnl": ad_for_pnl, "promo_burden": burden,
            "net_profit": net, "net_profit_upper": net_upper,
        })
    return {"atoms": atoms, "burden_known": burden_by_day_option is not None,
            "ad_by_day_option": ad_by_day_option, "ad_by_option": ad_by_option}
```

- [ ] **Step 4: `compute_rocket_1p_revenue`가 원자를 소비하게 교체**

같은 파일에서 다음을 교체한다. **찾을 코드** (원자 관련 준비부, `rows = db.execute(text(_DAY_OPTION_SQL)…` 부터 `burden_by_day_option = …` 까지 — 사이의 `covered`·`sales_span` 부분은 유지):

교체 전 (해당 줄들만):
```python
    rows = db.execute(text(_DAY_OPTION_SQL), params).fetchall()
    …
    covered = len(rows) > 0
    …
    ad_by_day_option: dict[tuple[str, str], Decimal] = {}
    ad_by_option: dict[str, Decimal] = {}
    for d_, oid, amt in db.execute(text(_DAY_OPTION_AD_SQL), params).fetchall():
        key, val = (str(d_)[:10], str(oid)), _d(amt)
        ad_by_day_option[key] = val
        ad_by_option[str(oid)] = ad_by_option.get(str(oid), ZERO) + val
    …
    burden_by_day_option = promo_burden_by_day_option(db, date_from, date_to, vendor)
```

교체 후:
```python
    ctx = day_option_atoms(db, date_from, date_to, vendor)
    covered = len(ctx["atoms"]) > 0
    ad_by_day_option = ctx["ad_by_day_option"]
    ad_by_option = ctx["ad_by_option"]
    burden_known = ctx["burden_known"]
```

그리고 메인 루프 헤더를 교체한다:

교체 전:
```python
    for (day, option_id, sku_id, product_name, qty, consumer, ours,
         unit_price, visitors, cost, unit_cost) in rows:
        day_key = str(day)[:10]
        oid = str(option_id)
        q = int(qty or 0)
        consumer_d = _d(consumer)
        ours_d = _dn(ours)
        cost_d = _dn(cost)
        consumer_total += consumer_d
        qty_total += q
        if ours_d is not None:
            priced_qty += q

        ad = ad_by_day_option.get((day_key, oid))
        ad_for_pnl = ad if ad is not None else ZERO
        burden = (None if burden_by_day_option is None
                  else burden_by_day_option.get((day_key, oid), ZERO))

        net = None
        net_upper = None
        if ours_d is not None and cost_d is not None and burden is not None:
            vat = payable_vat(ours_d, cost_d, burden, ad_for_pnl)
            net = _money(ours_d - cost_d - burden - ad_for_pnl - vat)
            pnl_qty += q
            pnl_revenue += ours_d
            pnl_cost += cost_d
            pnl_burden += burden
            pnl_ad += ad_for_pnl
            pnl_net += net
        if ours_d is not None and cost_d is not None:
            costed_revenue += ours_d
        elif cost_d is None:
            if ours_d is not None and burden is not None:
                bound = _money(ours_d - burden - ad_for_pnl
                               - payable_vat(ours_d, ZERO, burden, ad_for_pnl))
                if bound < ZERO:
                    net_upper = bound
```

교체 후 (파생은 원자에서 읽고, **집계·uncosted 장부는 그대로**):
```python
    for a in ctx["atoms"]:
        day_key, oid = a["date"], a["option_id"]
        sku_id, product_name = a["sku_id"], a["product_name"]
        q = a["qty"]
        consumer_d, ours_d, cost_d = a["consumer_revenue"], a["our_revenue"], a["cost"]
        unit_price, unit_cost, visitors = a["unit_price"], a["unit_cost"], a["visitors"]
        ad, ad_for_pnl, burden = a["ad_spend"], a["ad_for_pnl"], a["promo_burden"]
        net, net_upper = a["net_profit"], a["net_profit_upper"]
        consumer_total += consumer_d
        qty_total += q
        if ours_d is not None:
            priced_qty += q

        # net is not None ⟺ 매출·원가·분담금 셋 다 앎 — 종전 인라인 조건과 동치다.
        if net is not None:
            pnl_qty += q
            pnl_revenue += ours_d
            pnl_cost += cost_d
            pnl_burden += burden
            pnl_ad += ad_for_pnl
            pnl_net += net
        if ours_d is not None and cost_d is not None:
            costed_revenue += ours_d
        elif cost_d is None:
```

`elif cost_d is None:` 블록 내부의 `if ours_d is not None and burden is not None: bound = … net_upper = bound` 부분은 **삭제**한다(원자가 이미 계산). `uncosted` 장부(`key = str(sku_id) …` 이하)는 그대로 두되, 종전과 같은 위치에서 `if net_upper is not None: u["loss_confirmed"] = True`가 동작하는지 확인.

루프 이후 `burden_by_day_option is None` 참조 2곳(옵션 폴드의 `burden = None if …`, blocked 판정의 `elif burden_by_day_option is None:`)을 `not burden_known`으로 교체.

`unit_price`/`unit_cost` 폴드 대입도 원자 값이 이미 Decimal이므로 `o["unit_price"] = _d(unit_price)` → `o["unit_price"] = unit_price`, `o["unit_cost"]`도 동일하게. (`options` 직렬화부의 `str(_d(unit_price))`는 `str(unit_price)`로.)

- [ ] **Step 5: 신규+기존 테스트 전부 통과 확인**

Run: `cd backend && python -m pytest tests/test_rocket_1p_pnl_audit.py tests/test_rocket_1p_revenue.py -q`
Expected: 전부 PASS. **기존 파일에서 하나라도 깨지면 리팩터가 의미를 바꾼 것이다 — 신규 코드를 고치지 기존 테스트를 고치지 마라.**

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/coupang/rocket_1p_revenue.py backend/tests/test_rocket_1p_pnl_audit.py
git commit -m "refactor(rocket-1p): 「날짜×옵션」 원자 파생을 day_option_atoms()로 추출 — 근거 화면과 단일 출처 공유"
```

---

### Task 2: `promo_window_counts()` — A6의 좌·우변

**Files:**
- Modify: `backend/app/services/coupang/rocket_1p_channel_pnl.py` (`_promo_burden_by_day` 아래에 추가)
- Test: `backend/tests/test_rocket_1p_pnl_audit.py`

- [ ] **Step 1: 실패하는 테스트 추가**

```python
# ═══ ② A6의 원료 — 창에 걸친 프로모션 수와 할인액 없는 수 ═══


def test_promo_window_counts(db):
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('P1', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"), {"v": VENDOR})
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('P2', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"), {"v": VENDOR})
    db.execute(_t("INSERT INTO coupang_promo_discount_item "
                  "(request_id, product_number, discount_type, discount_value) "
                  "VALUES ('P1', 'S1', 'FIXED', 1500)"))
    db.commit()
    c = pnl.promo_window_counts(db, D, D)
    assert c == {"promos": 2, "unpriced": 1}
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && python -m pytest tests/test_rocket_1p_pnl_audit.py::test_promo_window_counts -v`
Expected: FAIL — `AttributeError: … has no attribute 'promo_window_counts'`

- [ ] **Step 3: 구현**

`rocket_1p_channel_pnl.py`의 `_PROMO_UNPRICED_SQL` 아래에 추가:

```python
_PROMO_TOTAL_SQL = """
SELECT COUNT(*) FROM coupang_rocket_promotion pr
WHERE pr.vendor_id = :vendor
  AND date(pr.start_at) <= :until AND date(pr.end_at) >= :since
"""


def promo_window_counts(db: Session, date_from: date, date_to: date,
                        vendor_id: str | None = None) -> dict | None:
    """창에 걸친 프로모션 수와 그중 할인액 원천이 없는 수. 테이블이 없으면 None(모름).

    손익 근거 화면의 A6 검사가 쓴다 — «분담금 0»과 «분담금 모름»을 가르는 판정자
    (`_promo_burden_by_day`의 unpriced 가드)를 **개수로** 보여 검사의 좌·우변을 만든다.
    """
    insp = sa_inspect(db.get_bind())
    if not (insp.has_table("coupang_rocket_promotion")
            and insp.has_table("coupang_promo_discount_item")):
        return None
    params = {"vendor": vendor_id or ROCKET_1P_VENDOR_ID,
              "since": date_from.isoformat(), "until": date_to.isoformat()}
    promos = int(db.execute(text(_PROMO_TOTAL_SQL), params).scalar() or 0)
    unpriced = int(db.execute(text(_PROMO_UNPRICED_SQL), params).scalar() or 0)
    return {"promos": promos, "unpriced": unpriced}
```

- [ ] **Step 4: 통과 확인** — 위 명령. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/coupang/rocket_1p_channel_pnl.py backend/tests/test_rocket_1p_pnl_audit.py
git commit -m "feat(rocket-1p): promo_window_counts — 분담금 «모름» 판정자를 개수로 노출 (A6 원료)"
```

---

### Task 3: 검사 서비스 `compute_pnl_audit_checks`

**Files:**
- Create: `backend/app/services/coupang/rocket_1p_pnl_audit.py`
- Test: `backend/tests/test_rocket_1p_pnl_audit.py`

- [ ] **Step 1: 실패하는 테스트 추가**

```python
# ═══ ③ 검사 — 같은 함수의 다른 그레인 비교, 재계산 아님 ═══


def _full_fixture(db):
    """A1~A7 전부 판정 가능한 최소 데이터."""
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)
    _ad_option(db, "A", "10000")
    _ad_account(db, "10000")
    db.commit()


def test_checks_pass_and_ladder_matches_screen(db):
    from app.services.coupang.rocket_1p_pnl_audit import compute_pnl_audit_checks
    _full_fixture(db)
    r = compute_pnl_audit_checks(db, D, D)
    by = {c["id"]: c for c in r["checks"]}
    for cid in ("A1", "A2", "A3", "A4", "A5", "A7"):
        assert by[cid]["verdict"] == "pass", (cid, by[cid])
        # ★통과해도 좌·우변 숫자를 싣는다 — 발견 0건과 실행 안 됨은 같은 숫자로 보인다(교훈 #123)
        assert by[cid]["left"] is not None and by[cid]["right"] is not None
    # 사다리 = 화면과 같은 함수 산출값(원 단위 일치의 근거)
    scr = compute_rocket_1p_revenue(db, D, D)
    assert r["ladder"]["net_profit"] == scr["pnl"]["net_profit"]


def test_b1_never_passes_even_when_equal(db):
    """★판정할 수 없는 검사를 초록으로 칠하지 않는다 — 값이 우연히 같아도."""
    from app.services.coupang.rocket_1p_pnl_audit import compute_pnl_audit_checks
    _full_fixture(db)
    r = compute_pnl_audit_checks(db, D, D)
    b1 = next(c for c in r["checks"] if c["id"] == "B1")
    assert b1["verdict"] == "undetermined"


def test_a5_surfaces_silent_inner_join_loss(db):
    """발주 이력 없는 SKU는 손익 매출에서 조용히 빠진다 — A5가 그 수량을 드러낸다."""
    from app.services.coupang.rocket_1p_pnl_audit import compute_pnl_audit_checks
    _full_fixture(db)
    _sale(db, "B", "S2", 5, "500000")   # 발주 이력 없음 → INNER JOIN 탈락
    db.commit()
    r = compute_pnl_audit_checks(db, D, D)
    a5 = next(c for c in r["checks"] if c["id"] == "A5")
    assert a5["verdict"] == "fail"
    assert a5["left"] == "10" and a5["right"] == "15"


def test_a6_unpriced_promo_fails_and_a1_undetermined(db):
    """분담금 모름 → A6 fail, 손익 자체가 없으므로 A1~A3은 undetermined."""
    from app.services.coupang.rocket_1p_pnl_audit import compute_pnl_audit_checks
    _full_fixture(db)
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('686180', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"),
               {"v": VENDOR})
    db.commit()
    r = compute_pnl_audit_checks(db, D, D)
    by = {c["id"]: c for c in r["checks"]}
    assert by["A6"]["verdict"] == "fail"
    assert by["A1"]["verdict"] == "undetermined"
    assert by["A3"]["verdict"] == "undetermined"


def test_a7_catches_ad_on_no_sales_day_of_sold_option(db):
    """★창 내 판매행이 있는 옵션이 «판매 없는 날»에 쓴 광고비 — 원자에도 ad_no_sales에도
    귀속되지 않는다(prod 실측 7일 창 435,916원). A7이 이 결손을 드러낸다."""
    from app.services.coupang.rocket_1p_pnl_audit import compute_pnl_audit_checks
    _full_fixture(db)
    _ad_option(db, "A", "5000", d=date(2026, 8, 5))   # 8/5 광고, 그날 판매행 없음
    db.commit()
    r = compute_pnl_audit_checks(db, D, date(2026, 8, 5))
    a7 = next(c for c in r["checks"] if c["id"] == "A7")
    assert a7["verdict"] == "fail"
    assert Decimal(a7["right"]) - Decimal(a7["left"]) == Decimal("5000")
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && python -m pytest tests/test_rocket_1p_pnl_audit.py -v -k "checks or b1 or a5 or a6 or a7"`
Expected: FAIL — `ModuleNotFoundError: … rocket_1p_pnl_audit`

- [ ] **Step 3: 서비스 구현**

Create `backend/app/services/coupang/rocket_1p_pnl_audit.py`:

```python
# rocket_1p_pnl_audit.py — 로켓1P 손익 «근거 화면» SA (트랙: 쿠팡 손익 정합)
#
# 왜 있나 (Jino, 2026-08-07): *"우리 손익(납품가 축)이 정말 실수 없이 나오는지 어떻게 확신할
#   수 있는지가 궁금해."* — 산술 검사(A1~A7·B1·B2)와 원천 행까지의 근거 추적을 준다.
#
# ★★가장 중요한 제약: **계산을 새로 하지 않는다.** 검사는 `compute_rocket_1p_revenue`(화면과
#   같은 함수)의 응답에서 «폴드들이 사다리 타일과 일치하는가»를 본다. 원자는
#   `day_option_atoms`(그 함수가 소비하는 것과 같은 출처)에서 온다. 근거 창이 자기 계산을
#   하면 «화면과 근거가 다른 두 계산»이 되어 검사 자체가 무의미해진다(스펙 §7).
#
# ★판정은 셋이다: pass / fail / undetermined. 판정할 수 없는 검사(B1: 1P 재고 데이터가 없어
#   두 축 차이를 설명할 수 없다)를 pass로 칠하면 그게 거짓 초록이다(교훈 #123).
#   같은 이유로 **통과해도 좌·우변 숫자를 항상 싣는다** — 발견 0건과 실행 안 됨은 같은
#   숫자로 보인다.
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

# _COST_COVERAGE_MIN·_settlement_window는 모듈 내부 이름이지만 여기서 그대로 쓴다 —
# 공개 별칭을 만들면 정의가 둘로 보이고, 이 파일은 같은 앱의 자매 SA다.
from app.services.coupang.rocket_1p_channel_pnl import (  # noqa: PLC2701
    ROCKET_1P_VENDOR_ID, ZERO, _COST_COVERAGE_MIN, _settlement_window,
    promo_window_counts)
from app.services.coupang.rocket_1p_revenue import (  # noqa: PLC2701
    _money, compute_rocket_1p_revenue, day_option_atoms)

# 옵션 표를 자르지 않기 위한 한도 — 잘리면 A2·A7의 합을 낼 수 없다(그땐 undetermined).
_ATOM_LIMIT = 1_000_000

_LADDER_KEYS = ("basis", "qty", "revenue", "cost", "promo_burden", "ad_spend", "vat",
                "net_profit", "profit_rate", "ad_no_sales", "ad_no_sales_included",
                "cost_coverage", "revenue_priced", "blocked")


def _check(cid: str, label: str, left, right, *, verdict: str,
           note: str | None = None, unit: str = "원") -> dict:
    diff = None
    if left is not None and right is not None:
        try:
            diff = str(Decimal(str(left)) - Decimal(str(right)))
        except ArithmeticError:
            diff = None
    return {"id": cid, "label": label,
            "left": None if left is None else str(left),
            "right": None if right is None else str(right),
            "diff": diff, "unit": unit, "verdict": verdict, "note": note}


def compute_pnl_audit_checks(db: Session, date_from: date, date_to: date) -> dict:
    r = compute_rocket_1p_revenue(db, date_from, date_to, None, _ATOM_LIMIT)
    p = r["pnl"]
    checks: list[dict] = []
    net = None if p["net_profit"] is None else Decimal(p["net_profit"])
    all_options = r["shown"] == r["option_count"]

    # ── A1·A2·A3 — 손익이 있어야 검사할 대상이 있다 ────────────────────
    if net is None:
        reason = p["blocked"]["reason"] if p["blocked"] else "손익 없음"
        for cid, label in (("A1", "일별 합 = 사다리 순이익"),
                           ("A2", "옵션별 합 = 사다리 순이익"),
                           ("A3", "매출−원가−분담금−광고−VAT = 순이익")):
            checks.append(_check(cid, label, None, None, verdict="undetermined",
                                 note=f"손익이 없어 검사 대상이 없습니다 — {reason}"))
    else:
        # ★사다리는 basis=full일 때 «판매 없는 옵션 광고비»를 세후로 추가 차감하는데,
        #   일별·옵션별 폴드에는 그 차감이 없다(귀속할 날·옵션이 없는 돈이라서). 비교하려면
        #   우변에 그 차감을 되돌린다 — 이건 재계산이 아니라 응답이 공개한 값의 역산이다.
        adj = (_money(Decimal(p["ad_no_sales"]) * Decimal("100") / Decimal("110"))
               if p["ad_no_sales_included"] else ZERO)
        folds_net = net + adj
        adj_note = (None if adj == ZERO else
                    f"사다리는 판매 없는 옵션 광고비 세후 {adj}원을 추가 차감(basis=full) — "
                    "우변에 되더해 비교")
        daily_sum = sum((Decimal(d["net_profit"]) for d in r["daily"]
                         if d["net_profit"] is not None), ZERO)
        checks.append(_check("A1", "일별 합 = 사다리 순이익", daily_sum, folds_net,
                             verdict="pass" if daily_sum == folds_net else "fail",
                             note=adj_note))
        if not all_options:
            checks.append(_check("A2", "옵션별 합 = 사다리 순이익", None, None,
                                 verdict="undetermined",
                                 note=f"옵션 표가 잘렸습니다({r['shown']}/{r['option_count']}) — 합을 낼 수 없습니다"))
        else:
            opt_sum = sum((Decimal(o["net_profit"]) for o in r["options"]
                           if o["net_profit"] is not None), ZERO)
            checks.append(_check("A2", "옵션별 합 = 사다리 순이익", opt_sum, folds_net,
                                 verdict="pass" if opt_sum == folds_net else "fail",
                                 note=adj_note))
        lhs = (Decimal(p["revenue"]) - Decimal(p["cost"]) - Decimal(p["promo_burden"])
               - Decimal(p["ad_spend"]) - Decimal(p["vat"]))
        checks.append(_check("A3", "매출−원가−분담금−광고−VAT = 순이익", lhs, net,
                             verdict="pass" if lhs == net else "fail"))

    # ── A4 — 원가 커버리지 ─────────────────────────────────────────
    if p["cost_coverage"] is None:
        checks.append(_check("A4", f"원가 커버리지 ≥ {_COST_COVERAGE_MIN}", None, None,
                             verdict="undetermined", unit="비율",
                             note="판매분석 미수집 창 — 커버리지 자체가 없습니다"))
    else:
        cov = Decimal(p["cost_coverage"])
        checks.append(_check("A4", f"원가 커버리지 ≥ {_COST_COVERAGE_MIN}", cov,
                             _COST_COVERAGE_MIN, unit="비율",
                             verdict="pass" if cov >= _COST_COVERAGE_MIN else "fail",
                             note=f"원가 확인 매출 {p['revenue']} / 납품단가 확인 매출 {p['revenue_priced']}"))

    # ── A5 — 수량 결합(조용한 INNER JOIN 탈락) ──────────────────────
    c = r["coverage"]
    if not c["sales_data_covered"]:
        checks.append(_check("A5", "발주단가 결합 수량 = 전체 판매수량", None, None,
                             verdict="undetermined", unit="개", note="판매분석 미수집 창"))
    else:
        checks.append(_check("A5", "발주단가 결합 수량 = 전체 판매수량",
                             c["qty_priced"], c["qty_all"], unit="개",
                             verdict="pass" if c["qty_priced"] == c["qty_all"] else "fail",
                             note="발주 이력이 없는 SKU는 손익 매출에서 조용히 빠집니다"
                                  "(INNER JOIN) — 차이가 그 수량입니다"))

    # ── A6 — 분담금 원천 ───────────────────────────────────────────
    pc = promo_window_counts(db, date_from, date_to)
    if pc is None:
        checks.append(_check("A6", "창에 걸친 프로모션 전건에 할인액 원천", None, None,
                             verdict="undetermined", unit="건", note="분담금 원천 테이블 없음"))
    elif pc["promos"] == 0:
        checks.append(_check("A6", "창에 걸친 프로모션 전건에 할인액 원천", 0, 0,
                             verdict="pass", unit="건",
                             note="창에 프로모션 없음 — 분담금 0은 추정이 아니라 사실입니다"))
    else:
        priced = pc["promos"] - pc["unpriced"]
        checks.append(_check("A6", "창에 걸친 프로모션 전건에 할인액 원천",
                             priced, pc["promos"], unit="건",
                             verdict="pass" if pc["unpriced"] == 0 else "fail",
                             note=None if pc["unpriced"] == 0 else
                             f"제안서 미수집 프로모션 {pc['unpriced']}건 — 분담금이 «모름»이라 손익이 막힙니다"))

    # ── A7 — 광고비 귀속(원자 + 무판매 옵션 = 창 전체) ────────────────
    if not all_options:
        checks.append(_check("A7", "원자 광고비 + 무판매 옵션 광고비 = 창 전체", None, None,
                             verdict="undetermined", note="옵션 표가 잘려 합을 낼 수 없습니다"))
    else:
        atoms_ad = sum((Decimal(o["ad_spend"]) for o in r["options"]
                        if o["ad_spend"] is not None), ZERO)
        left = atoms_ad + Decimal(p["ad_no_sales"])
        right = Decimal(p["ad_option_total"])
        checks.append(_check("A7", "원자 광고비 + 무판매 옵션 광고비 = 창 전체", left, right,
                             verdict="pass" if left == right else "fail",
                             note="차이 = 창 안에 판매행이 있는 옵션이 «판매 없는 날»에 쓴 광고비 — "
                                  "원자에도 ad_no_sales에도 귀속되지 않습니다(실측 2026-08-07 "
                                  "7일 창 435,916원). fail은 검사 오류가 아니라 실제 결손 관측입니다."))

    # ── B1 — 두 축 대사. **절대 pass로 칠하지 않는다** ─────────────────
    checks.append(_check("B1", "두 축 대사 (계산서 ↔ 판매)",
                         r["totals"]["settlement_revenue"], r["totals"]["our_revenue"],
                         verdict="undetermined",
                         note="차이는 쿠팡 창고 재고 증감으로 설명되어야 하나, 1P 재고 데이터가 "
                              "없어 판정하지 않습니다. 값이 같아도 pass가 아닙니다."))

    # ── B2 — 계산서 라인 완결성 ─────────────────────────────────────
    sw = _settlement_window(db, date_from, date_to)
    total_inv = sw["line_invoices"] + sw["fallback_invoices"]
    checks.append(_check("B2", "계산서 라인 완결성 (라인/전체)",
                         sw["line_invoices"], total_inv, unit="건",
                         verdict="pass" if sw["fallback_invoices"] == 0 else "undetermined",
                         note=None if sw["fallback_invoices"] == 0 else
                         f"라인 없는 계산서 {sw['fallback_invoices']}건은 작성일 폴백 — "
                         "오류가 아니라 날짜 귀속 정밀도만 낮습니다"))

    return {
        "period": r["period"],
        "ladder": {k: p[k] for k in _LADDER_KEYS},
        "checks": checks,
    }
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && python -m pytest tests/test_rocket_1p_pnl_audit.py -v`
Expected: 전부 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/coupang/rocket_1p_pnl_audit.py backend/tests/test_rocket_1p_pnl_audit.py
git commit -m "feat(rocket-1p): 손익 근거 검사 9종 — 같은 함수의 폴드 대 타일 비교, B1은 영구 «판정 안 함»"
```

---

### Task 4: 원자 목록 `compute_pnl_audit_atoms`

**Files:**
- Modify: `backend/app/services/coupang/rocket_1p_pnl_audit.py`
- Test: `backend/tests/test_rocket_1p_pnl_audit.py`

> ★**`day_option_atoms`를 직접 import하지 마라 — 라우터가 주입한다** (Task 3에서 확정).
> `test_module_is_not_referenced_by_accounting_paths`(D-CPP-2 금지선)가 `app/services/` 아래의
> `rocket_1p_revenue` 참조를 금지한다. 라우터가 참조하는 것은 이미 승인된 패턴이다
> (`app/routers/overview.py:21`). 부수 효과가 본질이다 — 이 모듈은 **주입된 것 말고는 볼 수
> 없으므로** 「근거 창은 계산을 새로 하지 않는다」가 규칙이 아니라 구조가 된다.
> 시그니처: `compute_pnl_audit_atoms(db, date_from, date_to, ctx, *, sort, flt, option_id)`
> — `ctx`는 `day_option_atoms(db, date_from, date_to)`의 결과다.

> ★**`limit` 계약을 응답에 실어라** (Task 3 재검 P2-4). 라우터가 `ATOM_LIMIT`이 아닌 값으로
> 화면을 부르면 A2·A7이 조용히 `undetermined`가 되는데, 지금은 그걸 **응답만 보고는 알 수
> 없다**(화면 응답에 limit이 없다). 원자 목록 응답에 `option_count`/`shown` 같은 잘림 사실을
> 함께 실어 화면이 「검사 2개가 사라졌다」를 말할 수 있게 하라.

- [ ] **Step 1: 실패하는 테스트 추가**

```python
# ═══ ④ 원자 목록 — 신뢰도 배지 + Σ = 사다리 ═══


def test_atoms_badges_and_sum(db):
    from app.services.coupang.rocket_1p_pnl_audit import (
        compute_pnl_audit_atoms, compute_pnl_audit_checks)
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000, match_method="suggested")   # 이름 유사도 자동 확정
    _sale(db, "B", "S2", 5, "500000")
    _price(db, "S2", "50000", 2)
    _cost(db, "S2", 15000, match_method="manual")
    db.commit()
    r = compute_pnl_audit_atoms(db, D, D)
    by_opt = {a["option_id"]: a for a in r["atoms"]}
    assert by_opt["A"]["cost_source"] == "suggested"   # ← «사람이 확인 안 함» 배지의 근거
    assert by_opt["B"]["cost_source"] == "manual"
    ck = compute_pnl_audit_checks(db, D, D)
    assert r["totals"]["net_profit"] == ck["ladder"]["net_profit"]


def test_atoms_filter_suggested(db):
    from app.services.coupang.rocket_1p_pnl_audit import compute_pnl_audit_atoms
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000, match_method="suggested")
    _sale(db, "B", "S2", 5, "500000")
    _price(db, "S2", "50000", 2)
    _cost(db, "S2", 15000, match_method="manual")
    db.commit()
    r = compute_pnl_audit_atoms(db, D, D, flt="suggested")
    assert [a["option_id"] for a in r["atoms"]] == ["A"]
```

- [ ] **Step 2: 실패 확인** — `cd backend && python -m pytest tests/test_rocket_1p_pnl_audit.py -v -k atoms`. Expected: FAIL (ImportError).

- [ ] **Step 3: 구현** — `rocket_1p_pnl_audit.py`에 추가:

```python
def _s(v) -> str | None:
    return None if v is None else str(v)


def compute_pnl_audit_atoms(db: Session, date_from: date, date_to: date, ctx: dict,
                            sort: str = "revenue", flt: str = "all",
                            option_id: str | None = None) -> dict:
    """3단 — 「날짜×옵션」 원자 목록 + 신뢰도 배지.

    ★`ctx`는 `day_option_atoms`(화면이 소비하는 것과 같은 출처)의 결과이며 **라우터가
      주입한다** — 이 모듈은 D-CPP-2 가드 때문에 그 함수를 직접 부를 수 없고, 그 덕에
      화면이 낸 숫자를 **재도출할 길을 두지 않는다**(모듈 참조가 막혀 있다 — 「어떤 계산도
      못 한다」는 뜻이 아니다. db는 받고, 허용 모듈의 CTE·헬퍼는 쓸 수 있다).
    배지: cost_source = manual(수기 확인) | suggested(이름 유사도 자동 — 사람이 확인 안 함)
                        | excluded(원가 제외 결정) | none(다리 없음).
    """
    src = {str(pn): (st, mm) for pn, st, mm in db.execute(text(
        "SELECT product_number, status, match_method FROM rocket_product_cost_map"
    )).fetchall()}

    rows: list[dict] = []
    for a in ctx["atoms"]:
        st = src.get(str(a["sku_id"])) if a["sku_id"] is not None else None
        if a["cost"] is not None:
            source = (st[1] or "manual") if st else "manual"
        elif st and st[0] == "ignored":
            source = "excluded"
        else:
            source = "none"
        rows.append({
            "date": a["date"], "option_id": a["option_id"], "sku_id": a["sku_id"],
            "product_name": a["product_name"], "qty": a["qty"],
            "consumer_revenue": str(a["consumer_revenue"]),
            "our_revenue": _s(a["our_revenue"]), "unit_price": _s(a["unit_price"]),
            "cost": _s(a["cost"]), "unit_cost": _s(a["unit_cost"]),
            "ad_spend": _s(a["ad_spend"]), "promo_burden": _s(a["promo_burden"]),
            "net_profit": _s(a["net_profit"]),
            "net_profit_upper": _s(a["net_profit_upper"]),
            "cost_source": source,
        })

    if option_id:
        rows = [x for x in rows if x["option_id"] == option_id]
    elif flt == "loss":
        rows = [x for x in rows
                if (x["net_profit"] is not None and Decimal(x["net_profit"]) < 0)
                or x["net_profit_upper"] is not None]
    elif flt == "suggested":
        rows = [x for x in rows if x["cost_source"] == "suggested"]
    elif flt == "uncosted":
        rows = [x for x in rows if x["cost"] is None]
    elif flt == "unpriced":
        rows = [x for x in rows if x["our_revenue"] is None]

    if sort == "date":
        rows.sort(key=lambda x: (x["date"], x["option_id"]))
    elif sort == "net":
        # 적자 큰 순 — 모름(None)은 뒤로(모름을 0으로 끼워 순서를 속이지 않는다)
        rows.sort(key=lambda x: (x["net_profit"] is None,
                                 Decimal(x["net_profit"]) if x["net_profit"] is not None else ZERO))
    else:  # revenue(기본): 우리 매출 큰 순, 단가 미상은 뒤로
        rows.sort(key=lambda x: (x["our_revenue"] is None,
                                 -(Decimal(x["our_revenue"]) if x["our_revenue"] is not None else ZERO)))

    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "burden_known": ctx["burden_known"],
        "count": len(rows),
        "totals": {
            "qty": sum(x["qty"] for x in rows),
            "net_profit": str(sum((Decimal(x["net_profit"]) for x in rows
                                   if x["net_profit"] is not None), ZERO)),
        },
        "atoms": rows,
    }
```

- [ ] **Step 4: 통과 확인** — `cd backend && python -m pytest tests/test_rocket_1p_pnl_audit.py -v`. Expected: 전부 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/coupang/rocket_1p_pnl_audit.py backend/tests/test_rocket_1p_pnl_audit.py
git commit -m "feat(rocket-1p): 근거 화면 3단 — 원자 목록 + 원가 출처 배지(suggested=사람 미확인)"
```

---

### Task 5: 원자 상세 `compute_pnl_audit_atom_detail` (다섯 갈래 원천 행)

**Files:**
- Modify: `backend/app/services/coupang/rocket_1p_pnl_audit.py`
- Test: `backend/tests/test_rocket_1p_pnl_audit.py`

> ★**창을 좁히지 마라** (Task 1 코드 품질 리뷰 C1). `day_option_atoms`의 `net_profit`·
> `burden_known`은 **창 종속**이다 — 분담금 가드가 창에 걸친 프로모션 전체를 본다. 하루
> 창으로 부르면 화면이 «—»로 그린 행에 숫자가 찍힌다(리뷰어 실측: wide 8/1–8/4 → net=None,
> narrow 8/4–8/4 → net=363,636.36). 그래서 이 함수는 **화면과 같은 창**(`date_from`·`date_to`)을
> 받아 `day_option_atoms`를 부르고, 결과를 `date_`로 **거른다**.

- [ ] **Step 1: 실패하는 테스트 추가**

```python
# ═══ ⑤ 원자 상세 — 다섯 갈래 원천 행 ═══


def test_atom_detail_five_sources(db):
    from app.services.coupang.rocket_1p_pnl_audit import compute_pnl_audit_atom_detail
    _sale(db, "A", "S1", 10, "1000000")
    _sale(db, "A2", "S1", 3, "300000")          # 같은 sku를 쓰는 형제 옵션
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000, match_method="suggested")
    _ad_option(db, "A", "10000")
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('686180', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"),
               {"v": VENDOR})
    db.execute(_t("INSERT INTO coupang_promo_discount_item "
                  "(request_id, product_number, discount_type, discount_value) "
                  "VALUES ('686180', 'S1', 'FIXED', 1500)"))
    db.commit()
    r = compute_pnl_audit_atom_detail(db, D, D, D, "A")
    assert r["sales"]["qty"] == 10                       # ① 판매행
    assert r["unit_price"]["unit_purchase_price"] == "60000"   # ② 납품단가(최근 발주)
    assert r["unit_price"]["sibling_option_count"] == 2  # 같은 상품번호를 쓰는 옵션 수
    assert r["cost"]["map"]["match_method"] == "suggested"     # ③ 원가 다리
    assert r["cost"]["master"]["cost_price"] == "20000"
    assert r["ad"]["ad_spend"] == "10000"                # ④ 광고비
    assert len(r["promos"]) == 1                         # ⑤ 분담금 제안서
    assert r["atom"]["net_profit"] is not None           # 원자 자신(같은 출처에서 재조립)


def test_atom_detail_missing_rows_are_null_not_zero(db):
    from app.services.coupang.rocket_1p_pnl_audit import compute_pnl_audit_atom_detail
    _sale(db, "A", "S1", 10, "1000000")   # 발주도 원가도 광고도 없음
    db.commit()
    r = compute_pnl_audit_atom_detail(db, D, D, D, "A")
    assert r["unit_price"] is None
    assert r["cost"]["map"] is None
    assert r["ad"] is None


def test_atom_detail_uses_screen_window_not_the_single_day(db):
    """★C1 회귀 가드 — 원자 상세는 **화면과 같은 창**으로 판정해야 한다.

    창 안 어딘가에 제안서 없는 프로모션이 있으면 분담금은 «모름»이고 화면은 그 행을 «—»로
    그린다. 상세를 하루 창으로 좁혀 부르면 그 가드를 빠져나가 숫자가 찍힌다 — 근거 화면이
    화면과 다른 답을 내는 것이라 이 설계가 막으려던 실패 그 자체다.
    """
    from app.services.coupang.rocket_1p_pnl_audit import compute_pnl_audit_atom_detail
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)
    # 8/1~8/2 프로모션 — 할인액 원천 없음(제안서 미수집). 8/4 판매와 겹치지 않는다.
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('686180', :v, '2026-08-01 00:00:00', '2026-08-02 23:59:59')"),
               {"v": VENDOR})
    db.commit()
    wide = compute_pnl_audit_atom_detail(db, date(2026, 8, 1), date(2026, 8, 4), D, "A")
    assert wide["atom"]["burden_known"] is False
    assert wide["atom"]["net_profit"] is None    # 화면이 «—»로 그리는 것과 같다
```

- [ ] **Step 2: 실패 확인** — `-k atom_detail`. Expected: FAIL (ImportError).

- [ ] **Step 3: 구현** — `rocket_1p_pnl_audit.py`에 추가:

```python
from sqlalchemy import inspect as sa_inspect  # 파일 상단 import에 추가

_ATOM_SALES_SQL = """
SELECT date, option_id, sku_id, product_name, qty, revenue, visitors, source, synced_at
FROM coupang_rocket_sales_daily
WHERE vendor_id = :vendor AND option_id = :oid AND date = :d
"""

# ★LATEST_UNIT_PRICE_CTE(rn=1, PO 최신순)와 **같은 의미**의 단건 조회 — 정렬 키가 같다.
_ATOM_PO_SQL = """
SELECT i.purchase_order_seq, i.unit_purchase_price, i.order_qty, o.po_created_at
FROM coupang_rocket_purchase_order_item i
LEFT JOIN coupang_rocket_purchase_order o ON o.purchase_order_seq = i.purchase_order_seq
WHERE i.product_number = :pn
ORDER BY i.purchase_order_seq DESC LIMIT 1
"""

_ATOM_SIBLING_SQL = """
SELECT COUNT(DISTINCT option_id) FROM coupang_rocket_sales_daily
WHERE vendor_id = :vendor AND sku_id = :pn
"""

_ATOM_COST_SQL = """
SELECT m.product_number, m.internal_sku, m.status, m.match_method, m.note, m.updated_at,
       pm.product_name, pm.cost_price
FROM rocket_product_cost_map m
LEFT JOIN product_master pm ON pm.internal_sku = m.internal_sku
WHERE m.product_number = :pn
"""

_ATOM_AD_SQL = """
SELECT report_date, ad_option_id, ad_spend, impressions, clicks, orders, sales_qty
FROM coupang_ad_option_daily
WHERE vendor_id = :vendor AND sell_type = 'Retail'
  AND ad_option_id = :oid AND report_date = :d
"""

_ATOM_PROMO_SQL = """
SELECT pr.request_id, pr.start_at, pr.end_at, d.discount_type, d.discount_value
FROM coupang_rocket_promotion pr
LEFT JOIN coupang_promo_discount_item d
  ON d.request_id = pr.request_id AND d.product_number = :pn
WHERE pr.vendor_id = :vendor AND date(pr.start_at) <= :d AND date(pr.end_at) >= :d
"""


def compute_pnl_audit_atom_detail(db: Session, date_from: date, date_to: date,
                                  date_: date, option_id: str, ctx: dict) -> dict:
    """4단 — 원자 1개의 다섯 갈래 원천 행. **행을 그대로 보인다** — 가공하면 근거가 아니다.

    ★없는 행은 null이다. 0으로 접으면 «수집 안 됨»이 «0원»으로 둔갑한다(원칙22).
    ★★`date_from`·`date_to`는 **화면이 보고 있는 창**이다. 원자를 그 창으로 뽑은 뒤
      `date_`로 거른다 — 하루 창으로 좁혀 부르면 분담금 «모름» 가드를 빠져나가, 화면이
      «—»로 그린 행에 숫자가 찍힌다(Task 1 코드 품질 리뷰 C1).
    """
    vendor = ROCKET_1P_VENDOR_ID
    d_iso = date_.isoformat()

    sales_row = db.execute(text(_ATOM_SALES_SQL),
                           {"vendor": vendor, "oid": option_id, "d": d_iso}).fetchone()
    sales = None if sales_row is None else {
        "date": str(sales_row[0])[:10], "option_id": str(sales_row[1]),
        "sku_id": sales_row[2], "product_name": sales_row[3],
        "qty": int(sales_row[4] or 0), "consumer_revenue": _s(sales_row[5]),
        "visitors": sales_row[6], "source": sales_row[7], "synced_at": _s(sales_row[8]),
    }
    sku = sales["sku_id"] if sales else None

    unit_price = None
    cost = {"map": None, "master": None}
    promos = None
    if sku is not None:
        po = db.execute(text(_ATOM_PO_SQL), {"pn": str(sku)}).fetchone()
        if po is not None:
            siblings = int(db.execute(text(_ATOM_SIBLING_SQL),
                                      {"vendor": vendor, "pn": str(sku)}).scalar() or 0)
            unit_price = {
                "purchase_order_seq": po[0], "unit_purchase_price": _s(po[1]),
                "order_qty": po[2], "po_created_at": _s(po[3]),
                "sibling_option_count": siblings,
                "note": "가장 최근 발주의 단가입니다 — 단가가 바뀌면 과거 판매의 매출도 "
                        "소급해서 바뀝니다. 원가·단가는 상품번호(sku) 기준으로 붙으므로 "
                        "같은 상품번호를 쓰는 옵션 전부에 같은 값이 적용됩니다.",
            }
        cm = db.execute(text(_ATOM_COST_SQL), {"pn": str(sku)}).fetchone()
        if cm is not None:
            cost["map"] = {"product_number": cm[0], "internal_sku": cm[1], "status": cm[2],
                           "match_method": cm[3], "note": cm[4], "updated_at": _s(cm[5])}
            if cm[1] is not None:
                cost["master"] = {"internal_sku": cm[1], "product_name": cm[6],
                                  "cost_price": _s(cm[7])}
        insp = sa_inspect(db.get_bind())
        if insp.has_table("coupang_rocket_promotion"):
            promos = [{"request_id": row[0], "start_at": _s(row[1]), "end_at": _s(row[2]),
                       "discount_type": row[3], "discount_value": _s(row[4])}
                      for row in db.execute(text(_ATOM_PROMO_SQL),
                                            {"vendor": vendor, "pn": str(sku), "d": d_iso})]

    ad_row = db.execute(text(_ATOM_AD_SQL),
                        {"vendor": vendor, "oid": option_id, "d": d_iso}).fetchone()
    ad = None if ad_row is None else {
        "report_date": str(ad_row[0])[:10], "ad_option_id": str(ad_row[1]),
        "ad_spend": _s(ad_row[2]), "impressions": ad_row[3], "clicks": ad_row[4],
        "orders": ad_row[5], "sales_qty": ad_row[6],
    }

    # 원자 자신 — 라우터가 **화면과 같은 창**으로 뽑아 준 `ctx`에서 이 날짜로 거른다
    # (창을 좁혀 부르면 분담금 가드가 달라진다 — 위 docstring ★★. 그래서 창을 이 모듈이
    # 정하지 않고 받는다.) 계산식의 숫자가 위 원천 행들과 같은 값임을 화면이 나란히 보인다.
    atom = next((a for a in ctx["atoms"]
                 if a["option_id"] == option_id and a["date"] == d_iso), None)
    atom_out = None if atom is None else {
        "date": atom["date"], "option_id": atom["option_id"], "qty": atom["qty"],
        "our_revenue": _s(atom["our_revenue"]), "cost": _s(atom["cost"]),
        "promo_burden": _s(atom["promo_burden"]), "ad_spend": _s(atom["ad_spend"]),
        "net_profit": _s(atom["net_profit"]),
        "net_profit_upper": _s(atom["net_profit_upper"]),
        "burden_known": ctx["burden_known"],
    }

    return {"date": d_iso, "option_id": option_id, "atom": atom_out,
            "sales": sales, "unit_price": unit_price, "cost": cost,
            "ad": ad, "promos": promos}
```

- [ ] **Step 4: 통과 확인** — `cd backend && python -m pytest tests/test_rocket_1p_pnl_audit.py -v`. Expected: 전부 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/coupang/rocket_1p_pnl_audit.py backend/tests/test_rocket_1p_pnl_audit.py
git commit -m "feat(rocket-1p): 근거 화면 4단 — 원자 1개의 다섯 갈래 원천 행(없으면 null, 0 아님)"
```

---

### Task 6: 라우터 + 등록

**Files:**
- Create: `backend/app/routers/rocket_1p_pnl_audit.py`
- Modify: `backend/app/main.py` (import 1줄 + include 1줄)

- [ ] **Step 1: 라우터 작성**

```python
# rocket_1p_pnl_audit.py — 손익 «근거 화면» 라우터.
#
# ★이 라우터는 «얇다»보다 «합성한다»가 맞다: 검사·원자 서비스는 D-CPP-2 가드(app/services/
#   아래에서 rocket_1p_revenue 참조 금지) 때문에 화면 함수를 부를 수 없어, **여기서 부른 결과를
#   주입**한다. `app/routers/overview.py:21`이 같은 참조를 하는 것과 같은 패턴이다.
#   부수 효과가 본질이다 — 서비스가 화면 모듈을 참조할 수 없으므로 「근거 창은 화면이 낸
#   숫자를 재도출하지 않는다」가 문서 규칙이 아니라 구조가 된다(서비스가 아무 계산도 못
#   한다는 뜻은 아니다 — db는 받는다).
# ★창을 정하는 곳도 여기 하나다. 세 엔드포인트가 **같은 창**으로 화면 함수와 원자를 뽑는다 —
#   창이 갈리면 분담금 «모름» 판정이 갈려 근거가 화면과 다른 답을 낸다(Task 1 리뷰 C1).
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.coupang.rocket_1p_pnl_audit import (
    ATOM_LIMIT, compute_pnl_audit_atom_detail, compute_pnl_audit_atoms,
    compute_pnl_audit_checks)
from app.services.coupang.rocket_1p_revenue import (
    compute_rocket_1p_revenue, day_option_atoms)
from app.utils.kst import kst_today

router = APIRouter(prefix="/api/coupang/ops/rocket/pnl-audit", tags=["rocket-1p-pnl-audit"])


def _parse(s: str | None, default: date) -> date:
    if not s:
        return default
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"날짜 형식이 아닙니다: {s}")


def _window(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    dto = _parse(date_to, kst_today())
    dfrom = _parse(date_from, dto - timedelta(days=6))
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="date_from이 date_to보다 늦습니다")
    return dfrom, dto


@router.get("/checks")
def pnl_audit_checks(date_from: str | None = Query(None), date_to: str | None = Query(None),
                     db: Session = Depends(get_db)):
    """1단 — 산술 검사. 통과해도 좌·우변을 싣고, 판정 불가는 pass가 아니라 undetermined다."""
    dfrom, dto = _window(date_from, date_to)
    return compute_pnl_audit_checks(db, dfrom, dto)


@router.get("/atoms")
def pnl_audit_atoms(date_from: str | None = Query(None), date_to: str | None = Query(None),
                    sort: str = Query("revenue", pattern="^(revenue|net|date)$"),
                    flt: str = Query("all", pattern="^(all|loss|suggested|uncosted|unpriced)$"),
                    option_id: str | None = Query(None),
                    db: Session = Depends(get_db)):
    """3단 — 「날짜×옵션」 원자 목록(신뢰도 배지 포함)."""
    dfrom, dto = _window(date_from, date_to)
    return compute_pnl_audit_atoms(db, dfrom, dto, sort=sort, flt=flt, option_id=option_id)


@router.get("/atom")
def pnl_audit_atom(date_: str = Query(..., alias="date"), option_id: str = Query(...),
                   date_from: str | None = Query(None), date_to: str | None = Query(None),
                   db: Session = Depends(get_db)):
    """4단 — 원자 1개의 다섯 갈래 원천 행.

    ★`date_from`·`date_to`는 **화면이 보고 있는 창**이다(생략하면 최근 7일). 이 창으로
      원자를 뽑아 `date`로 거른다 — 하루로 좁히면 분담금 «모름» 판정이 달라진다.
    """
    try:
        d = date.fromisoformat(date_)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"날짜 형식이 아닙니다: {date_}")
    dfrom, dto = _window(date_from, date_to)
    return compute_pnl_audit_atom_detail(db, dfrom, dto, d, option_id)
```

- [ ] **Step 2: `main.py` 등록**

`from app.routers import …` 부근에 import를 추가하고(기존 스타일에 맞춰), `app.include_router(naver_ops.router)` 근처에:

```python
from app.routers import rocket_1p_pnl_audit
app.include_router(rocket_1p_pnl_audit.router)
```

- [ ] **Step 3: 앱이 뜨는지 확인**

Run: `cd backend && python -c "from app.main import app; print([r.path for r in app.routes if 'pnl-audit' in r.path])"`
Expected: 경로 3개 출력.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/rocket_1p_pnl_audit.py backend/app/main.py
git commit -m "feat(rocket-1p): /api/coupang/ops/rocket/pnl-audit 라우터 3종"
```

---

### Task 7: api.ts 타입 + 페처

**Files:**
- Modify: `frontend/src/lib/api.ts` (`fetchRocket1PRevenue` 아래에 추가)

- [ ] **Step 1: 추가**

```typescript
// ── 로켓1P 손익 «근거 화면» (2026-08-07 설계) ──
// ★verdict 3값: 판정할 수 없는 검사(B1)는 pass가 아니라 undetermined다 — 거짓 초록 금지.
export interface PnlAuditCheck {
  id: string; label: string;
  left: string | null; right: string | null; diff: string | null;
  unit: string; verdict: "pass" | "fail" | "undetermined"; note: string | null;
}
export interface PnlAuditLadder {
  basis: string | null; qty: number | null;
  revenue: string | null; cost: string | null; promo_burden: string | null;
  ad_spend: string | null; vat: string | null; net_profit: string | null;
  profit_rate: string | null; ad_no_sales: string; ad_no_sales_included: boolean;
  cost_coverage: string | null; revenue_priced: string | null;
  blocked: { code: string; reason: string } | null;
}
export interface PnlAuditChecks {
  period: { from: string; to: string; vendor_id: string };
  ladder: PnlAuditLadder;
  checks: PnlAuditCheck[];
}
export type PnlAuditCostSource = "manual" | "suggested" | "excluded" | "none";
export interface PnlAuditAtom {
  date: string; option_id: string; sku_id: string | null; product_name: string | null;
  qty: number; consumer_revenue: string; our_revenue: string | null;
  unit_price: string | null; cost: string | null; unit_cost: string | null;
  ad_spend: string | null; promo_burden: string | null;
  net_profit: string | null; net_profit_upper: string | null;
  cost_source: PnlAuditCostSource;
}
export interface PnlAuditAtoms {
  period: { from: string; to: string };
  burden_known: boolean; count: number;
  totals: { qty: number; net_profit: string };
  atoms: PnlAuditAtom[];
}
export interface PnlAuditAtomDetail {
  date: string; option_id: string;
  atom: {
    date: string; option_id: string; qty: number;
    our_revenue: string | null; cost: string | null; promo_burden: string | null;
    ad_spend: string | null; net_profit: string | null; net_profit_upper: string | null;
    burden_known: boolean;
  } | null;
  sales: {
    date: string; option_id: string; sku_id: string | null; product_name: string | null;
    qty: number; consumer_revenue: string | null; visitors: number | null;
    source: string; synced_at: string | null;
  } | null;
  unit_price: {
    purchase_order_seq: number; unit_purchase_price: string | null; order_qty: number | null;
    po_created_at: string | null; sibling_option_count: number; note: string;
  } | null;
  cost: {
    map: { product_number: string; internal_sku: string | null; status: string;
           match_method: string | null; note: string | null; updated_at: string | null } | null;
    master: { internal_sku: string; product_name: string | null; cost_price: string | null } | null;
  };
  ad: { report_date: string; ad_option_id: string; ad_spend: string | null;
        impressions: number | null; clicks: number | null; orders: number | null;
        sales_qty: number | null } | null;
  promos: Array<{ request_id: string; start_at: string | null; end_at: string | null;
                  discount_type: string | null; discount_value: string | null }> | null;
}

const _auditQ = (p: { from: string; to: string }) =>
  new URLSearchParams({ date_from: p.from, date_to: p.to });

export function fetchPnlAuditChecks(p: { from: string; to: string }): Promise<PnlAuditChecks> {
  return fetchApi<PnlAuditChecks>(`/api/coupang/ops/rocket/pnl-audit/checks?${_auditQ(p)}`);
}
export function fetchPnlAuditAtoms(p: {
  from: string; to: string; sort?: string; flt?: string; optionId?: string;
}): Promise<PnlAuditAtoms> {
  const q = _auditQ(p);
  if (p.sort) q.set("sort", p.sort);
  if (p.flt) q.set("flt", p.flt);
  if (p.optionId) q.set("option_id", p.optionId);
  return fetchApi<PnlAuditAtoms>(`/api/coupang/ops/rocket/pnl-audit/atoms?${q}`);
}
// ★from/to는 **화면이 보고 있는 창**이다 — 생략하면 안 된다. 창을 좁히면 분담금 «모름»
//   판정이 달라져 화면이 «—»로 그린 행에 숫자가 찍힌다(Task 1 리뷰 C1).
export function fetchPnlAuditAtom(p: {
  date: string; optionId: string; from: string; to: string;
}): Promise<PnlAuditAtomDetail> {
  const q = new URLSearchParams({
    date: p.date, option_id: p.optionId, date_from: p.from, date_to: p.to,
  });
  return fetchApi<PnlAuditAtomDetail>(`/api/coupang/ops/rocket/pnl-audit/atom?${q}`);
}
```

- [ ] **Step 2: 타입 확인** — `cd frontend && npx tsc --noEmit`. Expected: 에러 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(rocket-1p): pnl-audit API 타입·페처 3종"
```

---

### Task 8: 페이지 + 라우트 + 「근거 보기」 버튼

**Files:**
- Create: `frontend/src/pages/Rocket1PPnlAudit.tsx`
- Modify: `frontend/src/App.tsx` (import + 라우트 1줄)
- Modify: `frontend/src/pages/Rocket1PRevenue.tsx` (버튼)

- [ ] **Step 1: 페이지 작성**

```tsx
// Rocket1PPnlAudit.tsx — 로켓1P 손익 «근거 화면» (2026-08-07 설계, Jino 승인)
//
// Jino 원문: "우리 손익(납품가 축)이 정말 실수 없이 나오는지 어떻게 확신할 수 있는지"
// 구조: 1단 산술 검사 → 2단 사다리(클릭=필터) → 3단 원자 목록 → 4단 원천 행 5갈래.
// ★이 화면은 계산하지 않는다 — 백엔드가 화면과 같은 함수를 다른 그레인으로 불러 비교한 것을
//   그대로 보인다. 검사 verdict 3값: pass(초록)/fail(빨강)/undetermined(회색 «판정 안 함»).
//   B1은 영구 회색이다 — 1P 재고 데이터가 없어 두 축 차이를 판정할 수 없다(거짓 초록 금지).
import { useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { Card, Table, Th, Td, Loading, EmptyState, Badge } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import { kstDate } from "../lib/periodRange";
import {
  fetchPnlAuditChecks, fetchPnlAuditAtoms, fetchPnlAuditAtom,
  type PnlAuditCheck, type PnlAuditAtom, type PnlAuditAtomDetail,
} from "../lib/api";

const NO_DATA = "—";
const won = (v: string | number | null | undefined) => {
  if (v == null) return NO_DATA;
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? `${Math.round(n).toLocaleString("ko-KR")}원` : NO_DATA;
};
const num = (v: number | null | undefined) => (v == null ? NO_DATA : v.toLocaleString("ko-KR"));
const pct = (v: string | null | undefined) => {
  if (v == null) return NO_DATA;
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : NO_DATA;
};

/** 판정 칩 — undetermined는 «판정 안 함»이다. 회색이지 초록이 아니다. */
function Verdict({ v }: { v: PnlAuditCheck["verdict"] }) {
  if (v === "pass") return <span className="rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">통과</span>;
  if (v === "fail") return <span className="rounded bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800">불일치</span>;
  return <span className="rounded bg-gray-200 px-2 py-0.5 text-xs font-medium text-gray-600">판정 안 함</span>;
}

const SOURCE_LABEL: Record<PnlAuditAtom["cost_source"], { text: string; cls: string }> = {
  manual: { text: "수기 확인", cls: "bg-green-50 text-green-700" },
  suggested: { text: "이름 유사도 — 사람 미확인", cls: "bg-amber-100 text-amber-800" },
  excluded: { text: "원가 제외 결정", cls: "bg-gray-100 text-gray-600" },
  none: { text: "다리 없음", cls: "bg-red-50 text-red-700" },
};

function DetailBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-gray-200 p-2">
      <div className="mb-1 text-xs font-semibold text-gray-500">{title}</div>
      <div className="text-xs leading-relaxed text-gray-800">{children}</div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-2"><span className="text-gray-400">{k}</span><span className="tabular-nums">{v}</span></div>;
}

/** 4단 — 원자 1개의 다섯 갈래 원천 행. 없는 행은 «없음»으로 그대로 말한다(0이 아니다).
 *  ★from/to(화면이 보고 있는 창)를 반드시 함께 넘긴다 — 창을 좁히면 분담금 «모름» 판정이
 *    달라져 화면이 «—»로 그린 행에 숫자가 찍힌다(Task 1 리뷰 C1). */
function AtomDetail({ date, optionId, from, to }: {
  date: string; optionId: string; from: string; to: string;
}) {
  const { data, error } = useAsyncData<PnlAuditAtomDetail>(
    () => fetchPnlAuditAtom({ date, optionId, from, to }), [date, optionId, from, to]);
  if (error) return <div className="p-2 text-xs text-red-600">원천 조회 실패: {String(error)}</div>;
  if (!data) return <div className="p-2"><Loading /></div>;
  const cm = data.cost.map;
  return (
    <div className="grid gap-2 bg-gray-50 p-3 md:grid-cols-5">
      <DetailBlock title="① 판매행 (판매분석)">
        {data.sales ? (<>
          <KV k="수량" v={num(data.sales.qty)} />
          <KV k="소비자 실현가" v={won(data.sales.consumer_revenue)} />
          <KV k="수집 시각" v={data.sales.synced_at ?? NO_DATA} />
        </>) : "행 없음"}
      </DetailBlock>
      <DetailBlock title="② 납품단가 (최근 발주)">
        {data.unit_price ? (<>
          <KV k="발주번호" v={String(data.unit_price.purchase_order_seq)} />
          <KV k="발주일" v={data.unit_price.po_created_at?.slice(0, 10) ?? NO_DATA} />
          <KV k="단가" v={won(data.unit_price.unit_purchase_price)} />
          <KV k="공유 옵션" v={`${data.unit_price.sibling_option_count}개`} />
          <p className="mt-1 text-[11px] text-amber-700">⚠️ {data.unit_price.note}</p>
        </>) : "발주 이력 없음 — 이 판매는 손익 매출에서 빠져 있습니다(A5)"}
      </DetailBlock>
      <DetailBlock title="③ 원가 (다리 → 등록원가)">
        {cm ? (<>
          <KV k="내부 SKU" v={cm.internal_sku ?? NO_DATA} />
          <KV k="상태" v={`${cm.status}${cm.match_method ? ` · ${cm.match_method}` : ""}`} />
          <KV k="등록원가" v={won(data.cost.master?.cost_price)} />
          {cm.note && <p className="mt-1 text-[11px] text-gray-500">{cm.note}</p>}
          {cm.match_method === "suggested" && (
            <p className="mt-1 text-[11px] font-medium text-amber-800">
              이름 유사도로 자동 확정 — 사람이 확인하지 않았습니다.{" "}
              <Link className="underline" to="/command-center">원가 매핑 화면으로</Link>
            </p>
          )}
        </>) : "다리 없음 — 원가를 등록해도 붙지 않습니다(연결부터)"}
      </DetailBlock>
      <DetailBlock title="④ 광고비 (옵션×일)">
        {data.ad ? (<>
          <KV k="광고비" v={won(data.ad.ad_spend)} />
          <KV k="노출/클릭" v={`${num(data.ad.impressions)}/${num(data.ad.clicks)}`} />
        </>) : "행 없음 — 그날 이 옵션에 광고 없음(손익에선 0원이 맞음)"}
      </DetailBlock>
      <DetailBlock title="⑤ 분담금 (프로모션 제안서)">
        {data.promos == null ? "원천 테이블 없음(모름)" : data.promos.length === 0
          ? "그날 걸린 프로모션 없음 — 분담금 0은 사실"
          : data.promos.map((p, i) => (
            <div key={i}>
              <KV k={p.request_id} v={p.discount_value == null
                ? "할인액 모름(제안서 미수집)"
                : `${p.discount_type} ${p.discount_value}`} />
              <div className="text-[11px] text-gray-400">
                {p.start_at?.slice(0, 10)} ~ {p.end_at?.slice(0, 10)}
              </div>
            </div>
          ))}
      </DetailBlock>
    </div>
  );
}

export default function Rocket1PPnlAudit() {
  const [sp] = useSearchParams();
  const from = sp.get("from") ?? kstDate(-6);
  const to = sp.get("to") ?? kstDate(0);
  const [flt, setFlt] = useState("all");
  const [sort, setSort] = useState("revenue");
  const [open, setOpen] = useState<string | null>(null);   // `${date}|${option_id}`

  const checks = useAsyncData(() => fetchPnlAuditChecks({ from, to }), [from, to]);
  const atoms = useAsyncData(
    () => fetchPnlAuditAtoms({ from, to, sort, flt }), [from, to, sort, flt]);

  const ladder = checks.data?.ladder;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-gray-900">로켓1P 손익 — 근거</h1>
        <p className="mt-1 text-sm text-gray-500">
          기간 {from} ~ {to} · 이 화면은 계산하지 않습니다 — 손익 화면과 같은 함수의 결과를
          다른 그레인으로 대조한 것입니다. URL을 공유하면 같은 것이 재현됩니다.
        </p>
      </div>

      {/* ── 1단: 산술 검사 ── */}
      <Card title="산술 검사">
        {checks.error ? <EmptyState reason="검사 조회 실패" hint={String(checks.error)} />
          : !checks.data ? <Loading />
          : (
            <Table>
              <thead><tr><Th>검사</Th><Th right>좌변</Th><Th right>우변</Th><Th right>차이</Th><Th>판정</Th></tr></thead>
              <tbody>
                {checks.data.checks.map((c) => (
                  <tr key={c.id} className="align-top hover:bg-gray-50">
                    <Td>
                      <span className="font-medium">{c.id}</span> {c.label}
                      {c.note && <p className="mt-0.5 max-w-lg text-[11px] text-gray-500">{c.note}</p>}
                    </Td>
                    <Td right>{c.unit === "원" ? won(c.left) : c.left ?? NO_DATA}</Td>
                    <Td right>{c.unit === "원" ? won(c.right) : c.right ?? NO_DATA}</Td>
                    <Td right>
                      <span className={c.diff && Number(c.diff) !== 0 ? "font-medium text-red-700" : ""}>
                        {c.unit === "원" ? won(c.diff) : c.diff ?? NO_DATA}
                      </span>
                    </Td>
                    <Td><Verdict v={c.verdict} /></Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
      </Card>

      {/* ── 2단: 사다리(참고 표시 — 손익 화면과 같은 값) ── */}
      {ladder && (
        <Card title="손익 사다리 (손익 화면과 같은 함수 산출)"
              right={ladder.basis === "costed_subset"
                ? <Badge tone="alert">원가 확인 {pct(ladder.cost_coverage)}분만</Badge>
                : ladder.basis === "full" ? <Badge tone="neutral">기간 전체</Badge> : undefined}>
          {ladder.blocked ? (
            <div className="px-4 py-3"><EmptyState reason="손익 없음" hint={ladder.blocked.reason} /></div>
          ) : (
            <div className="grid grid-cols-2 gap-x-6 gap-y-1 px-4 py-3 text-sm md:grid-cols-3">
              <KV k="우리 매출(납품가)" v={won(ladder.revenue)} />
              <KV k="− 원가" v={won(ladder.cost)} />
              <KV k="− 분담금" v={won(ladder.promo_burden)} />
              <KV k="− 광고비" v={won(ladder.ad_spend)} />
              <KV k="− 납부세액" v={won(ladder.vat)} />
              <KV k="= 순이익" v={<b>{won(ladder.net_profit)}</b>} />
            </div>
          )}
        </Card>
      )}

      {/* ── 3단: 원자 목록 ── */}
      <Card title={`계산 원자 — 날짜×옵션 (${num(atoms.data?.count)}건, Σ순이익 ${won(atoms.data?.totals.net_profit)})`}
            right={
              <div className="flex items-center gap-2 text-xs">
                <select value={flt} onChange={(e) => setFlt(e.target.value)}
                        className="rounded border-gray-300 py-1 text-xs">
                  <option value="all">전체</option>
                  <option value="loss">적자만</option>
                  <option value="suggested">원가=이름유사도만</option>
                  <option value="uncosted">원가 없음만</option>
                  <option value="unpriced">단가 없음만</option>
                </select>
                <select value={sort} onChange={(e) => setSort(e.target.value)}
                        className="rounded border-gray-300 py-1 text-xs">
                  <option value="revenue">매출순</option>
                  <option value="net">적자순</option>
                  <option value="date">날짜순</option>
                </select>
              </div>
            }>
        {atoms.error ? <EmptyState reason="원자 조회 실패" hint={String(atoms.error)} />
          : !atoms.data ? <Loading />
          : atoms.data.atoms.length === 0 ? <EmptyState reason="조건에 맞는 원자가 없습니다" />
          : (
            <Table>
              <thead><tr>
                <Th>날짜</Th><Th>옵션</Th><Th right>수량</Th><Th right>우리 매출</Th>
                <Th right>원가</Th><Th right>분담금</Th><Th right>광고비</Th><Th right>순이익</Th><Th>원가 출처</Th>
              </tr></thead>
              <tbody>
                {atoms.data.atoms.map((a) => {
                  const key = `${a.date}|${a.option_id}`;
                  const src = SOURCE_LABEL[a.cost_source];
                  return (
                    <>
                      <tr key={key} className="cursor-pointer hover:bg-gray-50"
                          onClick={() => setOpen(open === key ? null : key)}>
                        <Td>{a.date}</Td>
                        <Td>
                          <div className="max-w-xs truncate" title={a.product_name ?? a.option_id}>
                            {a.product_name ?? a.option_id}
                          </div>
                          <div className="text-[11px] text-gray-400">옵션 {a.option_id}</div>
                        </Td>
                        <Td right>{num(a.qty)}</Td>
                        <Td right>{won(a.our_revenue)}</Td>
                        <Td right>{won(a.cost)}</Td>
                        <Td right>{won(a.promo_burden)}</Td>
                        <Td right>{won(a.ad_spend)}</Td>
                        <Td right>
                          {a.net_profit != null ? (
                            <span className={Number(a.net_profit) >= 0 ? "text-judge-good" : "font-medium text-judge-bad"}>
                              {won(a.net_profit)}
                            </span>
                          ) : a.net_profit_upper != null ? (
                            <span className="font-medium text-judge-bad">≤ {won(a.net_profit_upper)}</span>
                          ) : NO_DATA}
                        </Td>
                        <Td><span className={`rounded px-1.5 py-0.5 text-[11px] ${src.cls}`}>{src.text}</span></Td>
                      </tr>
                      {open === key && (
                        <tr key={`${key}-detail`}>
                          <td colSpan={9}>
                            <AtomDetail date={a.date} optionId={a.option_id} from={from} to={to} />
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </Table>
          )}
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: 라우트 추가** — `App.tsx`:

```tsx
import Rocket1PPnlAudit from "./pages/Rocket1PPnlAudit";
// rocket-1p-funnel 라우트 아래에:
<Route path="rocket-1p/pnl-audit" element={<Rocket1PPnlAudit />} />
```

- [ ] **Step 3: 「근거 보기」 버튼** — `Rocket1PRevenue.tsx`의 「우리 손익 (납품가 축)」 Card의 `right` prop을 다음으로 교체(기존 배지 로직은 그대로 안에 둔다):

```tsx
right={
  <div className="flex items-center gap-2">
    {data.pnl.basis === "full"
      ? <Badge tone="neutral">원가 확인 100% · 기간 전체</Badge>
      : data.pnl.cost_coverage == null
        ? undefined
        : <Badge tone="alert">원가 확인 {pct(data.pnl.cost_coverage)}분만</Badge>}
    {/* ★새 창 — 기간을 URL에 실어 본 것을 그대로 재현·공유한다 */}
    <a href={`/rocket-1p/pnl-audit?from=${from}&to=${to}`} target="_blank" rel="noreferrer"
       className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50">
      근거 보기 ↗
    </a>
  </div>
}
```

- [ ] **Step 4: 확인** — `cd frontend && npx tsc --noEmit`. Expected: 에러 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Rocket1PPnlAudit.tsx frontend/src/App.tsx frontend/src/pages/Rocket1PRevenue.tsx
git commit -m "feat(rocket-1p): 손익 근거 화면 — 검사·사다리·원자·원천 행 4단 + 「근거 보기」 진입"
```

---

### Task 9: 프론트 렌더 테스트

**Files:**
- Create: `frontend/src/pages/rocket1pPnlAudit.test.tsx`

- [ ] **Step 1: 테스트 작성** (부분 목 패턴은 `rocketReconRefresh.test.tsx`와 동일)

```tsx
// @vitest-environment jsdom
//
// rocket1pPnlAudit.test.tsx — 근거 화면이 지켜야 하는 것:
//  ① B1(판정 불가)은 «판정 안 함»으로 그린다 — 값이 무엇이든 «통과»가 아니다(거짓 초록 금지).
//  ② 통과한 검사도 좌·우변 숫자를 보인다 — 발견 0건과 실행 안 됨은 같은 숫자로 보인다(교훈 #123).
//  ③ suggested 원가 배지는 «사람 미확인»을 말한다.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchPnlAuditChecks: () => Promise.resolve({
    period: { from: "2026-08-01", to: "2026-08-07", vendor_id: "A01029796" },
    ladder: {
      basis: "costed_subset", qty: 100, revenue: "1000000", cost: "300000",
      promo_burden: "0", ad_spend: "200000", vat: "45455", net_profit: "454545",
      profit_rate: "0.4545", ad_no_sales: "0", ad_no_sales_included: false,
      cost_coverage: "0.97", revenue_priced: "1030000", blocked: null,
    },
    checks: [
      { id: "A1", label: "일별 합 = 사다리 순이익", left: "454545", right: "454545",
        diff: "0", unit: "원", verdict: "pass", note: null },
      { id: "B1", label: "두 축 대사 (계산서 ↔ 판매)", left: "44567166", right: "47048828",
        diff: "-2481662", unit: "원", verdict: "undetermined",
        note: "1P 재고 데이터가 없어 판정하지 않습니다." },
    ],
  }),
  fetchPnlAuditAtoms: () => Promise.resolve({
    period: { from: "2026-08-01", to: "2026-08-07" },
    burden_known: true, count: 1,
    totals: { qty: 10, net_profit: "454545" },
    atoms: [{
      date: "2026-08-04", option_id: "A", sku_id: "S1", product_name: "상품 A",
      qty: 10, consumer_revenue: "1000000", our_revenue: "600000",
      unit_price: "60000", cost: "200000", unit_cost: "20000",
      ad_spend: "10000", promo_burden: "0", net_profit: "354545",
      net_profit_upper: null, cost_source: "suggested" as const,
    }],
  }),
  fetchPnlAuditAtom: () => new Promise<never>(() => {}),
}));

import Rocket1PPnlAudit from "./Rocket1PPnlAudit";

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/rocket-1p/pnl-audit?from=2026-08-01&to=2026-08-07"]}>
      <Routes><Route path="/rocket-1p/pnl-audit" element={<Rocket1PPnlAudit />} /></Routes>
    </MemoryRouter>,
  );
}

describe("Rocket1PPnlAudit", () => {
  it("B1은 «판정 안 함»으로 그린다 — 통과가 아니다", async () => {
    renderPage();
    expect(await screen.findByText("판정 안 함")).toBeTruthy();
    // 판정 불가여도 좌·우변은 보인다
    expect(await screen.findByText("44,567,166원")).toBeTruthy();
  });

  it("통과한 검사도 좌·우변 숫자를 보인다", async () => {
    renderPage();
    expect(await screen.findByText("통과")).toBeTruthy();
    expect((await screen.findAllByText("454,545원")).length).toBeGreaterThan(0);
  });

  it("suggested 원가는 «사람 미확인» 배지", async () => {
    renderPage();
    expect(await screen.findByText("이름 유사도 — 사람 미확인")).toBeTruthy();
  });
});
```

- [ ] **Step 2: 실행** — `cd frontend && npm test`. Expected: 신규 3건 포함 전부 PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/rocket1pPnlAudit.test.tsx
git commit -m "test(rocket-1p): 근거 화면 — B1 판정 안 함·좌우변 상시 표시·suggested 배지"
```

---

### Task 10: 전체 검증

- [ ] **Step 1: 백엔드 전체** — `cd backend && python -m pytest -q`. Expected: 전부 PASS (main 기준 기존 실패가 있으면 **그 목록이 착수 전과 같은지**만 확인 — 새 실패 0).
- [ ] **Step 2: 프론트 전체** — `cd frontend && npx tsc --noEmit && npm test && npm run build`. Expected: 에러 0.
- [ ] **Step 3: lint 래칫** — CI와 같은 lint 명령 실행(에러 상한 54 유지 — `.github/workflows` 참조). 신규 파일에서 에러 0.

---

### Task 11: PR → 리뷰 → 병합 → 배포 → 라이브 합격기준

- [ ] **Step 1: push + PR 생성** (본문에 스펙 링크·검사 표·라이브 실측 계획 포함)

```bash
git push -u origin claude/rocket-1p-pnl-audit
gh pr create --title "feat(rocket-1p): 손익 근거 화면 — 산술 검사 9종 + 원자→원천 행 드릴다운" --body "스펙: docs/superpowers/specs/2026-08-07-rocket-1p-pnl-audit-design.md ..."
```

- [ ] **Step 2: PR 경계 리뷰** — `/codex-panel` (전역 §4 의무). P1=0까지, 최대 3라운드.
- [ ] **Step 3: 병합** — `scripts/safe_merge.sh <PR번호>` (직접 `gh pr merge` 금지).
- [ ] **Step 4: 배포** — main 병합 후:

```bash
scripts/safe_deploy.sh backend/app/services/coupang/rocket_1p_revenue.py backend/app/services/coupang/rocket_1p_channel_pnl.py backend/app/services/coupang/rocket_1p_pnl_audit.py backend/app/routers/rocket_1p_pnl_audit.py backend/app/main.py --restart
(cd frontend && npm run build) && scripts/safe_deploy.sh --frontend
```

- [ ] **Step 5: 라이브 합격기준 검증** (스펙 §8 — 전부 서버 안 127.0.0.1:8011):

```bash
# ① A1·A2·A3 차이 0원 + 화면 순이익과 원 단위 일치
ssh sellc.ohitech.co.kr 'curl -s "localhost:8011/api/coupang/ops/rocket/pnl-audit/checks?date_from=2026-08-01&date_to=2026-08-07"' | python3 -c "
import json,sys; r=json.load(sys.stdin)
by={c['id']:c for c in r['checks']}
for cid in ('A1','A2','A3'): print(cid, by[cid]['verdict'], by[cid]['diff'])
print('ladder net:', r['ladder']['net_profit'])"
ssh sellc.ohitech.co.kr 'curl -s "localhost:8011/api/overview/rocket-1p-revenue?from=2026-08-01&to=2026-08-07"' | python3 -c "
import json,sys; print('screen net:', json.load(sys.stdin)['pnl']['net_profit'])"
# → 두 net이 문자 그대로 같아야 한다

# ② 원자 상세 5갈래 + suggested 배지 (atoms에서 suggested 1건 골라 atom 호출)
# ③ B1 verdict == "undetermined" 확인 (①의 스크립트에 B1 추가)
# ④ A5 좌·우변 숫자가 응답에 실려 있는지 확인
# ⑤ 프론트: /rocket-1p/pnl-audit?from=…&to=… 접속 → 검사 표·회색 B1·원자 드릴다운 눈 확인
```

- [ ] **Step 6: 문서** — 트랙 파일에 D-CPP-N(번호는 `scripts/next_ids.sh`) 기록, `claude-progress.txt` 갱신. **A7 라이브 fail 값(광고 미귀속 결손)을 이월 항목으로 HANDOFF에 기록** — 엔진 수리는 별건.

---

## Self-Review 결과 (작성 시 반영 완료)

- 스펙 §4의 검사 9종 전부 태스크 3에 구현·테스트됨. §5의 4단 드릴다운은 태스크 4·5·8. §3 진입점은 태스크 8. §8 합격기준은 태스크 11 Step 5가 1:1 대응.
- A1·A2의 우변 보정(ad_no_sales 세후 되더하기)은 basis=full일 때만 발생 — 현재 라이브는 costed_subset이라 보정 0. 보정이 있으면 note로 화면에 드러남.
- `_settlement_window`·`_COST_COVERAGE_MIN`·`_money` private import는 의도된 것(단일 정의 유지) — noqa 주석 포함.
- 타입 일치: `day_option_atoms` 반환 키 ↔ 태스크 4 직렬화 ↔ api.ts `PnlAuditAtom` ↔ 페이지 사용 필드 대조 완료.
