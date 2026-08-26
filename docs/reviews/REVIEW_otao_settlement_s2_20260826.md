# 적대 리뷰 — PR #486 (계약 §4 S2)

- 대상: `feat/po-forecast-n7` @ `888f050e` · base `origin/main` · 11파일 +1,556/−2
- 워크트리: `/Users/jino/.claude-worktrees/ohiselling/po-forecast-n7`
- 리뷰 일시: 2026-08-26 23:4x ~ 2026-08-27 00:0x KST
- 기준선(변이 전): 백엔드 `6799 passed` (276.7s) · 프론트 `972 passed / 68 files` (9.7s) · `tsc --noEmit` 0 · `eslint` 0
- prod 접근: 읽기 전용 조회 3회(`mode=ro`). **쓰기·배포·마이그레이션 0건.**

## 판정: FAIL (P1 2건)

두 건 다 재현 절차가 있고, 둘 다 **계약이 지목한 그 표면**(`/otao-po` 정산 창 섹션)에 산다.
숫자 층(창 배정·집계·대조 3상태)은 prod 실측으로 전건 검증됐고 변이 19개 중 17개가 죽었다 —
**남은 구멍은 전부 「값은 맞는데 사람에게 안 닿는다」 쪽**이다.

---

## P1

### P1-1 — `line_type='unknown'` 금액이 화면 어디에도 안 뜨는데 «픽업 합계»에는 들어간다

**무엇이 문제인가.** 서비스층은 미분류를 `other_quantity`/`other_amount_cny`로 **일부러 갈라
두고**(`settlement.py:140-141`, 「미분류를 product로 접지 않는다」) 라우터도 그 두 키를 body에
싣는다(`otao_po.py`). 그런데

- `otaoSettlementPanel.tsx`의 표에는 **미분류 칸이 없다** (열: 상품 수량·상품 CNY·부자재 수량·
  부자재 CNY·픽업 합계·실제 지급액·대조)
- `settlement.py`의 `notes`에도 **미분류 문장이 없다** (`notes.append` 8곳 전수 확인 —
  원장구간·통화·부자재·경계·draft·미배정·대조 뿐)

⇒ 미분류 금액은 `total_amount_cny`에만 들어가므로, 화면에서 **보이는 칸의 합 ≠ 픽업 합계**가
되고 그 차이를 설명하는 글자가 한 자도 없다. 이 화면의 유일한 용도가 「이 숫자를 OTAO 지급액과
맞춰 본다」인데, 맞지 않을 때 원인 후보 하나가 **화면에서 통째로 비어 있다.**

**왜 「언젠가의 가정」이 아닌가.** `ImportInvoiceLine.line_type`의 **기본값이 `"unknown"`**이고
(`models.py:4490`), 적재 라우터도 `"unknown"`으로 넣는다(`import_cost.py:392`). 분류는
**사람이 나중에 확정한다**(모델 docstring, 계약 §2-4). 즉 **새 선적이 적재된 직후는 항상
`unknown`이고**, 누군가 분류하기 전까지 그 상태다. prod가 지금 0건인 것은 158줄이 이미 전부
분류됐기 때문이지 구조가 그걸 막아서가 아니다(prod 실측: `product` 150 / `material` 8 /
`unknown` 0).

**재현.** `frontend/src/pages/` 아래 임시 테스트로 `other_quantity: 800, other_amount_cny: 9600,
total_amount_cny: 20000` (상품 10,000 + 부자재 400 + 미분류 9,600) 페이로드를 `OtaoSettlementPanel`에
렌더:

```
npx vitest run src/pages/<임시>.test.tsx
  × 미분류 금액 9,600이 화면 어딘가에 뜬다        → AssertionError: expected 0 to be greater than 0
  × 다르면 설명이 있다                            → textContent가 /미분류|분류|unknown/ 에 매치 안 됨
```

렌더 결과는 상품 10,000 · 부자재 400 · **픽업 합계 20,000** — 9,600이 어디서 왔는지 화면이
말하지 않는다.

**처방 방향(권고, 구현은 저자 몫)**: 미분류 열을 하나 더 세우거나, 최소한 `other_amount_cny > 0`
일 때 백엔드 `notes` 한 줄 + 행 옆 자백 마크. 부자재를 가른 것과 **같은 이유·같은 모양**이면 된다.

---

### P1-2 — ★표면 절단 변이 SURVIVED: prod가 실제로 그리는 분기의 정산 섹션을 지워도 972/972 초록

**재현.**

```
# OtaoPurchaseOrders.tsx 의 «발주 원장이 비어있지 않은» 분기(line 238)에서
#   {settlementSection}
# 한 줄을 지운 뒤
cd frontend && npx vitest run
  → Test Files 68 passed (68) / Tests 972 passed (972)     ← 아무도 안 죽는다
```

**왜 이게 아픈가.** prod 실측: `otao_purchase_order` 95행(정본 66) · `otao_purchase_order_line`
1,205행 ⇒ **Jino가 여는 `/otao-po`는 `ledger_empty=false` 분기**다. 그런데 새로 만든 표면
테스트 `otaoSettlementReachesTheUser.test.tsx`는 `fetchOtaoRoster`를 **`EMPTY_ROSTER`로 목**해
`ledger_empty=true` 분기(line 120)만 렌더한다. 나머지 두 파일(`otaoPoReachesTheUser`,
`otaoSalesReachesTheUser`)은 정산 축을 **일부러 throw**시켜 격리하므로 이 섹션을 단언하지 않는다.

⇒ **「prod 상태를 렌더하는 테스트가 0건」이라는 n=6 P1-3이 그대로 재발**했다. 이 파일 헤더가
*"n=6 적대 리뷰 P1-3이 남긴 것이 이 파일의 존재 이유"*라고 적고 있는데, 닫았다고 적은 그 구멍이
**한 칸 옆에서 그대로 열려 있다.**

**출하 결함은 아니다 — 회귀 방어 결함이다.** 나는 그 분기를 직접 렌더해 반증했다: 임시 테스트에서
`FULL_ROSTER`(정본 66/라인 1,205 모양)로 `App`을 `/otao-po`에 렌더하니 「정산 창 (OTAO 지급)」
heading·`2026-08`·`33,920`이 **정상적으로 뜬다**(1 passed). 즉 코드는 맞고, **그 사실을 지키는
테스트가 없다.** §4가 표면 절단 변이를 의무화한 이유가 정확히 이 자리다.

**처방 방향**: `otaoSettlementReachesTheUser.test.tsx`에 케이스 하나 — 발주 원장이 «있는»
상태에서도 섹션이 뜬다(SUR-S8). 목 하나 바꾸고 `it` 하나 더 쓰는 일이다.

---

## P2 (트리아지)

| # | 내용 | 처분 | 근거 1줄 |
|---|---|---|---|
| P2-1 | `_window_bounds`의 1월 연도 롤백이 **미테스트** — `(year-1,12)`를 `(year,12)`로 바꿔도 39 passed (M6 SURVIVED). 그러면 「2026-01」창의 시작이 **끝보다 뒤**(2026-12-20 ~ 2026-01-19)가 된다 | **채택** | 저자 스스로 의심한 자리이고 12월 하순 픽업이 실재하면 곧 밟는다 — `assert _window_bounds("2027-01") == (date(2026,12,20), date(2027,1,19))` 한 줄 |
| P2-2 | 라우터 `totals`의 `if not isinstance(v, Decimal)` 필터 — `"material_amount_cny"` 재기입 줄을 지워도 39 passed (M7 SURVIVED). HTTP 경계에서 `totals` 키를 단언하는 테스트가 0건 | **채택** | 지금은 7개 Decimal 키를 전부 명시 재기입해 **실결함 없음**(전수 확인)이나, 「조용히 사라지는 키」가 교훈 #321의 그 모양이다 |
| P2-3 | `_next_key` 연도 롤오버 미테스트 (M16 SURVIVED). 창 채우기 `while True`가 연말을 넘는 시나리오가 테스트에 없다 | **이월** | 실패 모드는 **무한 루프가 아니다** — 실측으로 반증했다(아래 §반증). `_window_bounds`가 `ValueError: month must be in 1..12`로 끊고 화면은 「정산 창을 불러오지 못했습니다」로 말한다 |
| P2-4 | `payments` 인자를 라우터가 안 넘긴다 — 테스트에서만 도는 경로 | **기각** | 계약이 「읽기 전용·지급액 입력 표면 안 연다」로 **이미 결정**했다(§1 의도된 설계). 단 그 귀결은 아래 「확인 못 한 것」에 사실로 남긴다 |
| P2-5 | 프론트 `cny()`가 `Math.round(v)` — 소수 금액이 생기면 창별 표시값의 합과 표시 합계가 1 CNY 어긋날 수 있다 | **이월** | prod 12/12 선적 전건 정수라 지금은 무해(실측: `declared_inv_value` 37555·58702·… 전부 정수) |
| P2-6 | 문구 `창 경계 ±2일` vs 코드 `< _BOUNDARY_DAYS(=2)` — 실효는 경계일 ±1일(18·19·20·21일 지목) | **기각** | 명시된 근거(B/L 발행일이 신고일보다 **최대 2일** 이르다)에 대해 임계는 옳다 — 22일 신고분은 B/L 20일이라 창이 안 밀린다. 문구만 느슨하다 |
| P2-7 | 창 개수 상한이 없다 — 오염된 `declaration_date` 한 건(예: 1926년)이면 창이 1,200개 넘게 만들어져 표가 통째로 무너진다 | **이월** | 현 원장은 2026-01-27~2026-08-18로 건전(실측). 방어는 「무한 루프」가 아니라 「표가 길어짐」 급 |

---

## 변이 주입 결과

★ = 사용자에게 닿는 마지막 표면을 끊는 변이. `error`(문법 파손)는 사망으로 세지 않았다 —
1차 시도의 M6가 실제로 `2 errors`로 나왔고, 주석이 `else` 절을 삼킨 문법 오류였으므로
**무효 처리 후 재실행**했다(재실행 결과가 아래 표의 M6이다).

백엔드 판정 명령: `cd backend && python3 -m pytest -q tests/test_otao_po_settlement.py tests/test_otao_po_http.py` (기준선 **39 passed**)
프론트 판정 명령: `cd frontend && npx vitest run` (기준선 **972 passed**)

| # | 변이 | 파일 | 결과 | 죽인 테스트 |
|---|---|---|---|---|
| ★M1 | 발주 원장 «있는» 분기에서 `{settlementSection}` 렌더 제거 | `OtaoPurchaseOrders.tsx:238` | **SURVIVED** (972 passed) | — → **P1-2** |
| ★M1b | 발주 원장 «빈» 분기에서 `{settlementSection}` 렌더 제거 | `OtaoPurchaseOrders.tsx:120` | KILLED (14 failed) | `SUR-S1`~`SUR-S7` 외 |
| ★M2 | 라우터 응답에서 `reconciled` 키 제거 | `otao_po.py` | KILLED (2 failed) | `test_settlement_body_carries_every_confession_field`, `..._keeps_reconciled_null_not_false` |
| ★M3 | `ReconciledCell`이 `null`을 「일치」로 그림 | `otaoSettlementPanel.tsx` | KILLED (2 failed) | `SUR-S5`, `지급액을 받으면 「일치」로 바뀌고…` |
| ★M4 | 패널이 `windows.slice(0,1)`만 그림 | `otaoSettlementPanel.tsx` | KILLED (8 failed) | `SUR-S2`·`S3`·`S4`·`S5` 외 |
| M5 | 창 배정 `day >= 20` → `>= 21` | `settlement.py:98` | KILLED (3 failed) | `test_window_boundary_is_the_twentieth`, `test_december_twentieth_rolls_into_next_january` |
| M6 | `_window_bounds` 1월에 연도 안 뺌 `(year-1,12)`→`(year,12)` | `settlement.py:116` | **SURVIVED** (39 passed) | — → **P2-1** |
| ★M7 | 라우터 `totals`에서 `material_amount_cny` 재기입 제거 | `otao_po.py` | **SURVIVED** (39 passed) | — → **P2-2** |
| M8 | `material`을 `product`에 접음 | `settlement.py:234` | KILLED (5 failed) | `test_material_is_split_out…`, `test_prod_shaped_ledger_reproduces_every_window`, `test_payment_side_and_s1_pickup_side_differ_by_exactly_the_material` 외 |
| M9 | `_BOUNDARY_DAYS 2 → 0` (경계 선적 지목 안 함) | `settlement.py:87` | KILLED (1 failed) | `test_boundary_shipments_are_named_because_pickup_date_is_missing` |
| M10 | 신고일 없는 라인 카운트 삭제 | `settlement.py:221` | KILLED (1 failed) | `test_missing_declaration_date_is_reported_not_zeroed` |
| M11 | `draft` 자백 삭제 (`status != "confirmed"` → `False`) | `settlement.py:232` | KILLED (2 failed) | `test_draft_shipment_is_included_but_named`, `test_settlement_body_confesses_draft_and_boundary_shipments` |
| M14 | 빈 창 채우기 삭제 (`else:` → `elif False:`) | `settlement.py:257` | KILLED (1 failed) | `test_empty_interior_window_is_listed_as_zero_not_dropped` |
| M15 | 지급액 없을 때 `reconciled = None` → `False` | `settlement.py:270` | KILLED (5 failed) | `test_without_payments_reconciliation_is_impossible_not_failed`, `test_one_supplied_payment_satisfies_the_contract` 외 |
| M16 | `_next_key`의 13월 롤오버 무력화 | `settlement.py:121` | **SURVIVED** (39 passed) | — → **P2-3** |
| M17 | 선적 중복 가드 제거 (`if sid not in …` → `if True`) | `settlement.py:229` | KILLED (5 failed) | `test_prod_shaped_ledger_reproduces_every_window`, `test_draft_shipment_is_included_but_named` 외 |
| ★M12 | 부자재를 상품 칸에 합쳐 렌더 | `otaoSettlementPanel.tsx` | KILLED (1 failed) | `SUR-S4` |
| ★M13 | 창 기간 셀(`{w.start} ~ {w.end}`) 제거 | `otaoSettlementPanel.tsx` | KILLED (1 failed) | `SUR-S3` |
| ★M18 | `draft`·창 경계 자백 마크 제거 | `otaoSettlementPanel.tsx` | KILLED (1 failed) | `SUR-S6` |
| ★M19 | 정산 로드 실패 시 조용히 빈 자리 | `OtaoPurchaseOrders.tsx` | KILLED (1 failed) | `정산을 못 불러오면 화면이 말한다` |

**19개 중 3개 SURVIVED** (M1★·M6·M7★). 원복 확인: `git status --short` = 체인 등록부 1건(내 것
아님·리뷰 전부터 변경돼 있던 파일)뿐, `git diff --stat HEAD -- backend frontend` **빈 출력**,
`grep -rn MUTATION backend/app frontend/src` **0건**.

---

## 내가 반증한 것 / 확인 못 한 것

### 실측으로 확인한 것 (저자의 자기채점을 독립 재계산으로 대조)

prod `file:/home/ubuntu/ohisell/backend/ohisell.db?mode=ro` 를 별도 스크립트로 **다시 집계**해
픽스처와 대조했다. **전건 일치**:

- `import_shipment` **12건** / `import_invoice_line` **158줄** (`product` 150 / `material` 8 /
  `unknown` 0) · 신고일 없는 라인 **0건**
- 창 7개 · 창별 `(선적, 상품수량, 상품CNY, 부자재수량, 부자재CNY, 합계CNY)`가
  `_PROD_WINDOWS`와 **한 자리도 안 틀리고 동일** — `2026-02 (2, 7060, 91057, 6500, 5200, 96257)` …
  `2026-08 (2, 1750, 22400, 14400, 11520, 33920)`
- 총계 **310,742 CNY** (상품 282,662 / 부자재 28,080) · 상품수량 21,760 / 부자재수량 35,100
- `draft` 1건 = id 9(`SETR2601250319`, 2026-01-27) → **2026-02 창** — 픽스처와 동일
- 헤더 `declared_inv_value` == Σ(`unit_price_foreign × quantity`), **12/12 차이 0.00** —
  docstring의 「라인에서 다시 쌓아도 된다」 근거가 참
- `remittance_fx_rate` **12/12 NULL** — 「원화 환산 안 한다」 근거가 참
- OTAO 지급 원장 부재: `settlements` **0행**, 123개 테이블 중 지급·송금 성격 **0건** —
  `reconciled=null`이 정직한 값이라는 주장이 참
- **S1↔S2 등식이 prod에서 성립**: roster `picked` 18,970 + `unmapped_qty` 2,790 = **21,760**
  = settlement `product_quantity`. 테스트가 등식으로 잠근 관계가 라이브에서도 참이다

### 반증한 것

1. **「`while True`가 무한 루프가 될 수 있다」 — 실측으로 반증.** `_next_key`의 롤오버를 실제로
   깨고 2026-12창 ~ 2027-02창 원장을 넣어 5초 알람으로 재봤더니 매달리지 않고
   `ValueError: month must be in 1..12, not 13`으로 즉시 끊긴다(`_window_bounds`가 방어벽).
   요청 핸들러가 걸리는 시나리오는 없다 — P2-3으로 강등.
2. **「M1이 출하 결함이다」 — 반증.** 발주 원장이 «있는» 모양(`FULL_ROSTER`)으로 `App`을
   `/otao-po`에 렌더하니 heading·창키·금액이 정상 표시(1 passed). 코드는 맞고 **테스트가 없는 것**이다.
3. **`w.shipments` 중복 계산 우려 — 반증.** `shipment_ids` 가드가 `draft_shipment_ids`·
   `boundary_shipment_ids` append를 함께 감싸므로 라인 순서와 무관하다(M17이 5건을 죽여 확인).
4. **`totals`의 Decimal 필터가 키를 조용히 삼킨다 — 오늘은 반증.** `s.totals`의 Decimal 키
   7개(product/material/other × 수량·금액 + total)가 라우터에서 **전부 명시 재기입**된다. 실결함
   없음. 다만 그 사실을 지키는 테스트가 0건이라 P2-2로 남긴다.

### 확인 못 한 것 (PASS의 근거로 쓰면 안 되는 자리)

- **라이브 배포 검증 0건.** 이 브랜치는 prod에 없고 배포는 금지 범위였다. 따라서
  「Jino가 브라우저 `https://sellc.ohitech.co.kr/otao-po`에서 정산 창을 **눈으로 본다**」는
  **아직 관측되지 않았다.** 여기 판정은 워크트리 코드·테스트·prod **데이터** 대조까지다 —
  배포 후 완료 QA가 라이브 표면을 따로 재야 한다.
- **계약 §4 S2 합격기준의 후반은 어떤 라이브 경로로도 달성 불가다.** *"실제 19일 OTAO 지급액과
  1개 창 이상 대조 일치"* — `build_settlement(payments=…)`는 **라우터가 안 부르고** 지급액을
  받는 표면도 (의도적으로) 없다. 즉 실행 중인 시스템에서 `reconciliation.source`는 영구히
  `"none"`이다. 이것은 §1이 「결정된 것」으로 못 박은 설계이므로 P1으로 올리지 않는다 —
  그러나 **합격기준 대비 «부분»이라는 사실 자체**는 판정 재료로 완료 QA에 넘긴다.
- 백엔드 전수(6,799건)는 **변이 없는 기준선에서만** 돌렸다. 변이별로는 정산 2파일(39건)만
  돌렸으므로, SURVIVED 3건이 «저장소 어딘가의 다른 테스트»에 잡힐 가능성은 완전히는 못 배제한다.
  다만 `grep -rln "build_settlement\|otao-po/settlement" backend/tests` = 그 2파일뿐이라
  가능성은 낮다.

### 금지선 검사 (계약 §3)

- 마이그레이션 없는 스키마 변경 — **없음** (diff에 `alembic/` 0파일, `models.py` 미변경)
- ECOUNT API 호출 — **0건**
- 원장 쓰기 — **0건** (`settlement.py`에 `session.add`/`commit` 없음, `select`만)
- 3상태 합산 단일 숫자 — **없음** (상품/부자재를 갈라 싣고 합계를 «같이» 준다)
- 자동 발주 실행 — **없음** (읽기 전용 GET 하나)
- prod 쓰기·배포·마이그레이션(리뷰어 측) — **0건**

---

# 2R 재판정 — 수정 커밋 `d3213319` 하나의 diff만

- 범위: `git diff 888f050e..d3213319` (6파일 +428/−4 — 그중 202줄이 이 리뷰 문서 자체)
- 질문: **1R P1 2건이 해소됐는가.** 전체 브랜치 재리뷰 안 함. 새 지적 생산 안 함.
  이월 P2 3건(`_next_key`·`Math.round`·창 개수 상한)은 재검토 대상 아님.
- 판정 시각: 2026-08-27 00:1x~00:2x KST

## 판정: **PASS** (P1 = 0)

## 기준선 — 저자 주장치를 내가 직접 재현

| 항목 | 저자 주장 | 내 실측 | 일치 |
|---|---|---|---|
| 백엔드 전수 | 6,804 passed / 0 | **6,804 passed, 868 warnings in 278.61s** | ✅ |
| 프론트 전수 | 973 / 68파일 | **68 passed (68) / 973 passed (973)** | ✅ |
| `tsc -b` | 0 | **exit 0, 출력 없음** | ✅ |
| eslint 래칫 | 96 warnings, 0 errors | **✖ 96 problems (0 errors, 96 warnings)** | ✅ |
| 정산 2파일 타깃 | — | 39 → **44 passed** (+5) | — |

## 저자의 사고(같은 파일 수리분 유실) — 커밋에서 살아 있는지 직접 확인

`git checkout -- settlement.py`가 P1-1 수리분까지 지웠다는 자백에 대해, **커밋된 트리**를 직접 봤다.

```
grep -c "아직 분류되지 않은" backend/app/services/otao_po/settlement.py   → 1   (note 살아 있음, :340)
grep -c "미분류"            frontend/src/pages/otaoSettlementPanel.tsx    → 5   (열머리·셀·주석)
git diff --stat HEAD -- backend frontend                                  → 빈 출력
```

⇒ **유실 없음.** 그리고 이 자백 자체는 §2의 사후 가시성을 제대로 지킨 것이다 — 커밋 메시지에
남겼고, 나에게 「내 자기채점이니 다시 재라」고 넘겼다. 판정에 감점 요소로 반영하지 않는다.

## 변이 재주입 — 저자 표를 믿지 않고 내가 다시 쟀다

`error`(문법 파손)와 `failed`를 구분했다: 백엔드는 매 변이마다 `ast.parse`를 먼저 돌려
`syntax_ok=True`를 확인했고, 프론트는 `Transform failed`/`Failed to parse`/`SyntaxError`를
따로 검사해 전건 `err=-`였다. **아래는 전부 진짜 `failed`다.**

| # | 변이 | 결과 | 죽인 테스트 |
|---|---|---|---|
| ★R1 | `OtaoPurchaseOrders.tsx` **원장 «있음» 분기**의 `{settlementSection}` 제거 — 1R P1-2 그 변이 | **KILLED** 14 failed / 567 passed | `SUR-S1`·`S2`·`S3`·`S4`·`S5` 외 14건 |
| ★R2 | 창 행에서 **미분류 CNY 셀** 제거 — 1R P1-1 그 표면 | **KILLED** 3 failed | `SUR-S8`, `SUR-S4`, `SUR-S5` |
| ★R3 | 창 행에서 **미분류 수량** 값 제거 | **KILLED** 1 failed | `SUR-S8` |
| ★R4 | **합계 줄**에서 미분류 두 칸 제거 | **SURVIVED** 581 passed | — → 2R-P2-1 |
| ★R5 | 미분류 **열 머리(`Th`) 2개** 제거 | **KILLED** 1 failed | `SUR-S8` |
| ★R6 | 서비스: 미분류 자백 `note` 삭제 | **KILLED** 2 failed | `test_unclassified_lines_are_confessed_in_notes`, `test_settlement_body_shows_unclassified_lines` |
| ★R7 | 서비스: 미분류 금액을 갈라 싣지 않고 **조용히 누락**(합계엔 남김) = 1R P1-1의 서비스층판 | **KILLED** 4 failed | 위 2건 + `test_a_freshly_ingested_shipment_is_entirely_unclassified`, `test_unknown_line_type_is_not_folded_into_product` |
| R8 | `_window_bounds` 1월 연도 롤백 제거 (1R P2-1) | **KILLED** 1 failed | `test_january_window_rolls_back_into_the_previous_december` |
| R9 | 라우터 `totals`의 `material_amount_cny` 재기입 제거 (1R P2-2) | **KILLED** 1 failed | `test_settlement_body_carries_every_totals_key` |
| R10 | 라우터 창 body에서 `other_amount_cny` 키 제거 | **KILLED** 2 failed | `test_settlement_body_carries_every_confession_field`, `test_settlement_body_shows_unclassified_lines` |

원복 확인: `git status --short` = 체인 등록부 1건(리뷰 전부터 변경돼 있던, 내 것 아닌 파일)뿐 ·
`git diff --stat HEAD -- backend frontend` **빈 출력** · `MUTATION`/`{/* r */}` 마커 **0건**.

**저자 표와 어긋난 곳 1건(무해)**: 저자는 R9(M7)를 「2 failed」로 적었으나 내 실측은 **1 failed**
(`test_settlement_body_carries_every_totals_key` 단독)이다. 죽었다는 결론은 같다.

## 1R P1 2건 판정

### P1-1 (미분류가 화면에 안 닿는다) → **해소**

1R의 결함 문장은 셋이었고 셋 다 상환됐다.

1. **「미분류 금액이 화면 어디에도 안 뜬다」** → 창 행에 미분류 수량·미분류 CNY **두 칸이 상품·
   부자재와 같은 격으로** 섰다(값이 있으면 amber + ⚠ + 툴팁). R2·R3·R5 세 변이가 전부 `SUR-S8`에
   죽는다.
2. **「보이는 칸의 합 ≠ 픽업 합계」** → `SUR-S8`이 셀 인덱스로 못 박는다: `cells[7]`=5,000 ·
   `cells[8]`=9,600 · `cells[9]`=**43,520**(22,400+11,520+9,600). 인덱스가 밀리면 즉시 빨개진다
   (실제로 기존 `SUR-S4`·`SUR-S5`의 인덱스 7→9, 8→10 갱신이 이 커밋에 같이 들어 있다).
3. **「차이를 설명하는 글자가 없다」** → 백엔드 `notes`에 미분류 문장 추가. R6(note 삭제)이 2건에
   죽고, R7(서비스층에서 조용히 누락)이 4건에 죽는다.

★특히 **`test_a_freshly_ingested_shipment_is_entirely_unclassified`가 `line_type`을 «지정하지
않는»** 것으로 적재 직후 실제 모양을 재현한 것이 옳다. 1R이 P1으로 올린 근거가 정확히
「`line_type` 기본값이 `unknown`이고 분류는 사람이 나중에 한다」였고, 테스트가 그 **기본값
자체**를 밟는다 — 상수를 베껴 적은 테스트가 아니다.

### P1-2 (표면 절단 변이 생존) → **해소**

1R에서 **972 passed**로 살아남았던 바로 그 변이(원장 «있음» 분기의 `{settlementSection}` 제거)를
같은 방식으로 다시 넣었더니 **14 failed / 567 passed**. 기본 목이 `EMPTY_ROSTER` →
`FULL_ROSTER`로 바뀌었고, 빈 분기는 `SUR-S7`이 자기 안에서 `roster = EMPTY_ROSTER`로 덮어
**두 분기를 다 밟는다.** 목을 모듈 스코프 `let roster`로 빼고 `beforeEach`에서 되돌리는 모양이라
케이스 간 누수도 없다(`SUR-S7` 뒤에 오는 케이스들이 그대로 통과하는 것으로 확인).

## 2R P2 (트리아지 — 라운드를 늘리지 않는다)

| # | 내용 | 처분 | 근거 1줄 |
|---|---|---|---|
| 2R-P2-1 | ★R4 SURVIVED — **합계 줄**의 미분류 두 칸을 지워도 581 passed. 열 머리는 12개인데 합계 줄이 10칸이 되어 「픽업 합계」가 미분류 열 아래로 밀리는데 아무도 안 잡는다 | **채택 권고(비차단)** | 출하 코드는 **정상**이고(합계 줄에 두 칸이 실재) 1R P1-1 문장은 「창 행·설명」이라 이건 잔여 회귀 공백이다 — 기존 「합계 줄이 prod 총액을 그대로 그린다」에 `cells[7]`/`cells[8]` 인덱스 단언 두 줄이면 닫힌다 |

**이건 P1이 아니다**: 재현되는 것은 «테스트가 안 지킨다»이지 «화면이 틀리다»가 아니고, 1R이 P1으로
못 박은 세 문장은 전부 상환됐다. 이 한 건 때문에 3라운드를 열지 않는다(§4 라운드 증식 차단).

## 2R에서 확인 못 한 것 — 1R과 동일하게 유효하다

- **라이브 배포 검증 0건.** 여전히 브랜치는 prod에 없다. 「Jino가 브라우저에서 정산 창을 본다」는
  아직 관측되지 않았다 — 배포 후 완료 QA가 라이브 표면을 따로 재야 한다.
- **계약 §4 S2 합격기준 후반은 여전히 어떤 라이브 경로로도 달성 불가.** 이 커밋은 그 축을 건드리지
  않았다(`payments`를 라우터가 부르지 않는 것은 계약이 결정한 설계). 판정 재료로 완료 QA에 넘긴다.
- 금지선 재검사: 이 커밋 diff에 마이그레이션 0 · `models.py` 미변경 · ECOUNT 호출 0 · 원장 쓰기 0
  (`settlement.py`에 `add`/`commit` 없음) · 자동 발주 없음. **리뷰어 측 prod 쓰기·배포 0건**
  (2R에서는 prod 조회조차 안 했다 — 범위가 커밋 diff이므로).
