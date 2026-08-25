# 적대 리뷰 — PR #460 「OTAO 발주 원장 + 발주 메뉴」 (2R)

> 대상 브랜치 `feat/po-forecast-n4` · HEAD **`b7723881`** · diff `git diff origin/main...HEAD` (7커밋 / 24파일 / +3,235)
> 리뷰 위치: 워크트리 `/Users/jino/.claude-worktrees/ohiselling/po-forecast-n4` (공유 메인 폴더 미사용)
> 계약 정본: `docs/contracts/CONTRACT_inventory_unified.md` (v0.2, 2026-08-25 Jino 승인)
> 리뷰어: 만든 쪽이 아닌 기 · 판정 규칙 전역 §4 (게이트는 P1으로만)
> 일시: 2026-08-26 KST

## 판정: **PASS** (P1 0건)

1R P1 2건 **모두 해소**됐고, 이번 세션이 새로 얹은 ~1,500줄에서 P1은 나오지 않았다.
변이 25종을 주입해 **21종 KILLED / 4종 SURVIVED**이며, 생존 4종은 전부 **테스트 그물의 갭**이지
출하 코드의 결함이 아니다(코드는 옳고 테스트가 그것을 안 지킨다). 그 4종은 P2로 처분한다.

---

## A. 1R P1 해소 여부

### P1-1: **해소** — `order_count`의 그레인이 「발주 건」으로 바뀌었다

`backend/app/services/otao_po/roster.py:113-128` 현재 코드:

```python
seen_orders: dict[str, set[int]] = {}
for code, qty, order_date, order_id in session.execute(q):
    ...
    seen_orders.setdefault(code, set()).add(int(order_id))

for code, order_ids in seen_orders.items():
    row(code).order_count = len(order_ids)
```

`set[date]` → `set[int]`(발주서 행 id)로 바뀌었다. 쿼리(`:98-107`)가 `OtaoPurchaseOrder.id`를
select 목록에 추가했고 정본 필터를 이미 통과한 행만 온다.

**회귀 잠금 실재**: `test_order_count_counts_orders_not_dates`
(`backend/tests/test_otao_po_ledger.py:338-349`) 가 같은 날 `20260107-1`·`20260107-2` 두 건을
심고 `order_count == 2`를 요구한다.

**재현(변이 M11 — 초판으로 되돌리기)**:
```
# roster.py: set[int] → set, add(int(order_id)) → add(order_date)
$ python3 -m pytest -q tests/test_otao_po_ledger.py tests/test_otao_po_ingest.py tests/test_otao_po_http.py
FAILED tests/test_otao_po_ledger.py::test_order_count_counts_orders_not_dates
1 failed, 44 passed
```
⇒ 초판 결함이 **다시 들어오면 죽는다.**

### P1-2: **해소** — 모델 ↔ 마이그 nullable 파리티가 맞고, 정적 가드까지 붙었다

마이그레이션 `backend/alembic/versions/otao1po4n4a_add_otao_purchase_order_ledger.py`:
- `:62` `parsed_at … nullable=False`
- `:104` `created_at … nullable=False`
- `:105` `updated_at … nullable=False`

모델 쪽 `Mapped[datetime]`(Optional 아님) ⇒ SQLAlchemy 2.0에서 NOT NULL. 세 컬럼 모두 일치.

**회귀 잠금 실재**: `test_migration_nullable_matches_model` + `test_migration_covers_every_model_column`
(`test_otao_po_ledger.py:459-485`). 마이그 파일을 **정적 파싱**해 `(table, column) → nullable`을
뽑아 모델과 대조한다(alembic 미설치 환경에서도 돈다).

**재현(변이 M12)**:
```
# 마이그 parsed_at nullable=False → True
FAILED tests/test_otao_po_ledger.py::test_migration_nullable_matches_model
1 failed, 44 passed
```

**부수 확인 — alembic head 단일**(마이그가 갈래를 만들면 prod `--migrate`가 죽는다):
```
$ python3 -m alembic heads
otao1po4n4a (head)
```
단일 head. `down_revision = "cst4pick59a"` → 실재. dangling 0건.
(`tests/test_alembic_revision_integrity.py::test_single_head_linear_chain`도 스위트에서 통과)

### P2-1(이월분): **이월 유지** — 처분되지 않았다

모델 자동 유도 이름 ↔ 마이그 명시 이름이 **4곳** 여전히 다르다(실측):

| 대상 | 모델(SQLAlchemy 자동) | 마이그 |
|---|---|---|
| `otao_purchase_order_line.order_id` 인덱스 | `ix_otao_purchase_order_line_order_id` | `ix_otao_po_line_order_id` |
| `…_line.product_code` 인덱스 | `ix_otao_purchase_order_line_product_code` | `ix_otao_po_line_product_code` |
| `…_line.name_en` 인덱스 | `ix_otao_purchase_order_line_name_en` | `ix_otao_po_line_name_en` |
| `otao_item_name_map.raw_name` unique | **이름 없음**(`unique=True` 인라인) | `uq_otao_item_name_map_raw` |

재현:
```
$ python3 -c "from app.models import OtaoPurchaseOrderLine as M; \
  print([i.name for i in M.__table__.indexes])"
['ix_otao_purchase_order_line_order_id', 'ix_otao_purchase_order_line_product_code', 'ix_otao_purchase_order_line_name_en']
$ grep -oE 'create_index\("[a-z_0-9]+"' alembic/versions/otao1po4n4a_*.py
create_index("ix_otao_po_line_order_id"   ...
```
영향은 1R 판정 그대로 — 기능 무해, `alembic revision --autogenerate`가 drop/create 노이즈를 낸다.
이번에 추가된 `test_migration_covers_every_model_column`은 **컬럼만** 세고 인덱스·제약 이름은 안 본다.
**새 P1으로 승격하지 않는다.** 처분: 이월 유지.

---

## B. 신규 코드 지적

### P1 (게이트) — **0건**

재현 절차를 적을 수 있는 정확성·계약 위반을 찾지 못했다.

### P2 (트리아지)

**P2-2 [`backend/app/services/otao_po/parser.py:76,106-118`] — `name_en`의 «맨 앞 숫자»가 잘린다 (근본 원인 확정)** — 처분 제안: **채택**

`TRAIL_FILLER = re.compile(r"[\s\d\]]*")`가 한글 상품명 뒤의 «공백+숫자»를 찌꺼기로 먹는다.
그래서 영문상품명이 숫자로 시작하면 그 숫자가 한글명 쪽으로 넘어간다.

재현:
```
$ python3 -c "
from app.services.otao_po.parser import _split_name
print(_split_name('강화유리 갤럭시S25울트라 2.5D Clear Glass '))"
('강화유리 갤럭시S25울트라 2', '.5D Clear Glass')      ← 기대 ('…울트라', '2.5D Clear Glass')
```
문서 전체로도 재현(발주서 텍스트 → 파싱 → 사전 → 대조):
```
code='GSAS25U' qty=100 name_en='.5D Clear Glass'
dictionary keys: {'.5dclearglass': {'GSAS25U'}, ...}
ledger '2.5D Clear Glass' -> None (unmatched)
```

- **트랙이 이미 알고 있는 건이다**(`track_inventory-management.md` 확인줄 23:4x: *"파서가 삼성 `2.5D`의 앞 `2`를 흘려…"*),
  `name_map.py` docstring도 미일치 22종 중 **삼성 `2.5D Clear Glass` 6종**을 명시한다.
- **왜 P1이 아닌가**: ①방향이 **안전 쪽**이다 — 오매핑이 아니라 `unmatched`가 되고,
  그건 계약 §2-9·§3-6이 요구하는 바로 그 상태(「매핑 필요」 표면화)다 ②**수량은 안 틀린다**
  (`qty=100` 정상, 헤더 검산 통과) ③빈 수량 행에서도 흘린 `2`가 유령 수량이 되지 않는다
  (실측 `qty=None, blank=True`) ④n=4로부터의 **이월 결함**이고 이번 PR이 만든 회귀가 아니다.
- 다만 원장의 `name_en`은 *"문서가 직접 적어 준다"*가 존재 이유인 컬럼이라 **문서와 다른 값이 저장된다.**
  `TRAIL_FILLER`를 `[\s\]]*` + 「닫는 괄호/공백만」으로 좁히면 낫는다. 회귀 테스트 1건 동반 권고.

**P2-3 [`roster.py:83-85`] — 창(window) 경계가 «선적 1건» 픽스처로만 검증된다** — 처분 제안: **채택(테스트 추가)**

`_ledger_window_start`의 `func.min(...)`을 `func.max(...)`로 바꾸는 변이(M18)가 **생존**했다:
```
=== M18: 창 시작일을 min → max 로 (MUTATION) ===
45 passed
```
원인은 로직이 아니라 픽스처다 — `test_otao_po_ledger.py`·`test_otao_po_http.py`의 모든
시나리오가 `import_shipment`를 **정확히 1건**만 심어 min ≡ max다. prod 원장은 **12선적**이므로
`max`였다면 창이 최신 통관일로 밀려 거의 모든 발주가 `out_of_window_ordered`로 떨어진다 —
`out_of_window_ordered`라는 칸의 존재 이유 자체가 안 지켜지는 상태인데 전건 초록이다.
선적 2건 이상 픽스처 1개면 닫힌다.

**P2-4 [`name_map.py:49` / `test_otao_po_ledger.py:175-181`] — 「2.5D 가드」 테스트가 실제로는 가드를 안 지킨다** — 처분 제안: **채택(테스트 보강)**

`_TWO_EA = re.compile(r"(?<![\d.])\b2\s*ea\b", re.I)`에서 **lookbehind만** 지우는 변이(M20b)가 생존:
```
=== M20b: lookbehind (?<![\d.]) 만 제거 (MUTATION) ===
45 passed
mutant   normalize("Glass 3.2ea") = 'glass3.'
original normalize("Glass 3.2ea") = 'glass3.2ea'
```
`test_normalize_keeps_2_5d_prefix`는 자기 docstring에 *"규칙 3의 반대편 가드다"*라고 적었지만,
그 두 단언은 `\b`만으로도 통과한다(`2.5D Clear Glass`엔 `2` 뒤에 `ea`가 없다). 즉 **lookbehind가
지키는 입력이 테스트에 하나도 없다.** `"Glass 3.2ea"`류 단언 1줄이면 닫힌다.
(주: 실제 prod 품목명에 이 패턴이 있는지는 확인 못 했으므로 영향은 낮다 — 그래서 P2다.)

**P2-5 [`frontend/src/pages/OtaoPurchaseOrders.tsx:77-85, 151-155`] — 근거 보존 표면 2개가 잠기지 않았다** — 처분 제안: **이월**

두 변이가 프론트 7건 전건 초록인 채 생존했다:
- **M24** 헤더의 `정본 발주서 N건 (대체됨 M건) · 최근 발주 …` 통째 제거 → 7 passed
- **M25** 「매핑 필요」 카드의 `붙음 N/M` 배지 제거 → 7 passed

둘 다 **백엔드는 이미 주고**(`source.orders_superseded`, `source.name_map_resolved/total`) HTTP
테스트도 단언하는데(`test_source_reports_authoritative_split`), **화면이 그린다는 것만** 아무도 안
잰다. 전자는 D-INV-3 「왜 이 숫자인가를 되짚는다」의 표면, 후자는 계약 §2-6 「매핑 신선도 병기」의
표면이다. 핵심 3칸·매핑필요 목록·ledger_empty는 전부 잠겨 있으므로 **P1은 아니다.**

**P2-6 [`ingest.py:270-309`] — 정본 판정의 mtime이 DB에 안 남아 페이로드 구성에 의존한다** — 처분 제안: **이월**

`_mark_authoritative`는 **DB의 모든** `OtaoPurchaseOrder`를 재판정하는데, 3순위 `mtime`은
`mtimes.get(po.id, 0.0)` — 즉 **이번 페이로드에 실린 파일만** 값을 갖는다. 부분 페이로드를 먹이면
①②로 안 갈리는 serial의 정본이 «이번에 실린 쪽»으로 조용히 넘어간다.
현재는 `otao_po_export.py`가 항상 루트 전체를 walk하므로 닫혀 있다 — 그래서 이월이다.
`mtime`을 행에 저장하거나, 재판정 대상을 이번 페이로드가 건드린 serial로 좁히면 구조로 닫힌다.

**P2-7 [`ingest.py:349-354`] — 사람이 확정했으나 코드가 비어 있는 매핑이 「매핑 필요」 출력에서 빠진다** — 처분 제안: **채택**

```python
if row is not None and row.match_kind == "manual":
    rep.map_manual_kept += 1
    if row.product_code:
        rep.map_resolved += 1
    continue           # ← product_code is None이어도 map_unresolved에 안 들어간다
```
`match_kind='manual'` + `product_code=None`(사람이 「아직 모르겠다」로 남긴 행)은
`map_unresolved`에 안 실려 `otao_po_import.py:49-50`의 `매핑 필요:` 목록에서 사라진다.
계약 §2-9 「조용히 빼면 발주 누락」의 결과 그 자체다. 한 줄이면 닫힌다.

**P2-8 [`roster.py:83-85` vs `:138-148`] — `reserved`의 두 변이 서로 다른 창을 쓸 수 있다** — 처분 제안: **이월**

`ImportShipment.declaration_date`는 **nullable=True**다(실측). `window_start`는 `min()`이라
NULL 선적을 무시하는데, 그 선적의 `import_invoice_line`은 `picked`에 **그대로 더해진다**.
즉 그런 행이 있으면 `ordered`(창 필터 적용)와 `picked`(무필터)가 다른 모집단이 되어 `reserved`가
음수로 밀린다. 화면은 그걸 「창이 어긋났다」로 설명하는데 실제 원인은 NULL 통관일이다.
prod에 그런 행이 실재하는지는 **이 리뷰에서 확인 못 했다**(prod 미접속) — 그래서 이월.
`ImportShipment`는 §3-8 A′ 소관이라 **수정하지 말고**, 로스터 쪽에서 NULL 선적 수량을 별도 칸으로
자백하는 것이 이 계약의 정당한 대응이다.

**P2-9 [`docs/tracks/active/track_inventory-management.md` 확인줄 23:4x]** — 처분 제안: **채택**

그 줄이 *"적재기(`ingest.py`)·API·화면·테스트는 **미작성**"* / *"prod 테이블은 비어 있고 로스터는
빈 값을 돌려준다"*라고 단언하는데, **같은 브랜치의 커밋 `34b80818`·`b7723881`이 정확히 그 넷을
만들었다.** 브랜치의 문서가 브랜치의 diff와 모순된다(n=5 확인줄 미추가). 세션 종료 시 확인줄을
추가하면 자연히 닫힌다.

**P2-10 [`frontend/src/pages/OtaoPurchaseOrders.tsx`] — 어느 SKU의 픽업 칸이 불완전한지 행 수준에서 안 보인다** — 처분 제안: **기각**

「매핑 필요」 수량은 원리적으로 SKU에 못 붙는다(붙었으면 매핑된 것이다). 총량+품목명 목록이
계약 §2-9가 요구하는 전부이고, 행 수준 표기는 만들 수 없다. 기각.

---

## C. 계약 금지선 검사

| 조항 | 판정 | 근거 |
|---|:---:|---|
| **§3-2** 자동 «실행» 금지(발주 발송·픽업 지시·재고 수정) | **O** | 라우터는 `@router.get("/roster")` **1개뿐**(`otao_po.py:33`) — POST/PUT/DELETE 0건. `api.ts` 추가분은 `fetchOtaoRoster()` 1개. 화면에 쓰기 컨트롤 0개. 적재는 사람이 실행하는 CLI 2본(`otao_po_export.py`·`otao_po_import.py`)이고 `--dry-run`을 갖췄다. |
| **§3-3** ECOUNT API 미등록 IP 호출 금지 | **O** | `grep -rniE "requests\.|httpx|urllib|aiohttp"` → 신규 코드 전체 **0건**. `ecount` 문자열은 전부 ①파일명 정규식 `^[0-9A-Z]{15}\.PDF$` ②`source_kind` 라벨 ③주석. 네트워크 호출 없음. 원천은 로컬 PDF다. |
| **§3-6** 매핑 미확정 상품의 발주 수량 산출 금지 | **O** | `name_map.resolve()`가 `unmatched`/`ambiguous`에서 **`product_code=None`을 반환**하고 다수결로 고르지 않는다(`name_map.py:113-143`). 규칙 2(공용≡단일)는 의도적으로 자동화 안 함 — `test_resolve_reports_unmatched_rather_than_dropping`이 잠금. 미확정분은 body `unmapped` + 화면 「매핑 필요」 카드로만 노출. |
| **§3-8** A′/B 소관 코드(수입 원장) 수정 금지 | **O** | 신규 코드에서 `ImportShipment`·`ImportInvoiceLine`은 **`select()` 4곳뿐**(`roster.py:85,138` · `ingest.py:338`). `session.add` 3곳은 전부 `Otao*` 모델. 마이그레이션은 **순수 `create_table` 3개**로 기존 테이블 무접촉(`git diff`로 확인). 사전은 `import_invoice_line.internal_sku`가 아니라 **별도 테이블** `otao_item_name_map`에 산다 — D-INV-1 경계 그대로. |
| **§3-9** 예약 잔량·운송중·현재고 합산 단일 숫자 금지 | **O** | 응답에 파생 총계 없음. `test_body_never_carries_a_merged_single_number`가 `{total, combined, on_hand_plus_reserved, grand_total}` 부재를 단언. 화면 합계도 `발주 X · 픽업 Y · 잔량 Z` **3분 표기**(`OtaoPurchaseOrders.tsx:104-106`). 변이 M3(픽업 열 제거)가 KILLED. |
| **§2-8** 「데이터 없음」 ≠ 「0」 | **O** | 3층에서 지킨다: ①파서 `qty=None`·`blank_qty=True` ②적재기가 그 라인을 **원장에 안 넣고** `blank_qty_lines`에 좌표와 함께 실음 ③화면이 `ledger_empty`로 「아직 안 심었다」를 0과 가름. 음수 `reserved`도 clamp 안 함. 변이 M14·M19·M5 전부 KILLED. |
| **§2-9** 매핑 미확정을 표면에 드러낸다 | **O** | body `unmapped: [{item_name, quantity}]` + `notes` + 화면 「매핑 필요」 카드(수량 병기). 변이 M4(카드 제거)·M5(body에서 제거) 둘 다 KILLED. |
| **D-INV-2** 매핑 규칙 3 | **O** | 규칙 1(`screen protector` 접미)·3(`2ea`)은 `normalize()`가 집행 · **규칙 2는 자동으로 안 붙이고 `unmatched`로 남긴다**(요구사항 그대로 — 문자열로 못 푸는 상품 지식). `test_normalize_folds_*` 3건 + `test_resolve_reports_unmatched_rather_than_dropping`이 잠금. ⚠️`2ea` 수량 단위 [미상]은 계약대로 미해소이고 합산도 안 한다. |
| **D-INV-3** 정본 규칙 ①ECOUNT ②Revise/후행 ③serial로 접기 | **O** | `_mark_authoritative`의 rank 튜플이 정확히 그 순서(`ingest.py:278-283`), 그룹핑 키가 `po.serial`. 변이 M7(순위 뒤집기) KILLED. mtime으로만 갈린 건은 `tie_broken_by_mtime`으로 자백. 진 행엔 `supersede_reason` 기록. 로스터·사전 둘 다 `is_authoritative.is_(True)` 필터(변이 M16·M21 KILLED). |

★**S1 합격기준의 나머지 절반** — *"ECOUNT 원본 대조 표본 10건 일치"* — 는 이 PR에 증거가 없다.
prod 원장이 아직 비어 있어(적재 미실행) 원리적으로 못 댄다. 계약 §4의 `- [ ] S1`이 **미체크로
남아 있는 것이 옳고**, 이 리뷰는 그것을 P1으로 세지 않는다(적대 리뷰=「코드가 옳은가」,
합격 판정=완료 QA 몫). ★리뷰어는 **prod에 접속하지 않았다** — prod 상태 주장은 하지 않는다.

---

## D. 변이 주입 결과 (25종 — 표면 절단 6종 포함)

기준 상태 = 커밋 `b7723881`. 백엔드는 `tests/test_otao_po_{ledger,ingest,http}.py`(**baseline 45 passed**),
프론트는 `src/pages/otaoPoReachesTheUser.test.tsx`(**baseline 7 passed**)로 판정.

| # | 변이 | 파일 | 내용 | 결과 | 죽인 테스트 |
|---|---|---|---|:---:|---|
| **M1** ★표면 | 라우트 절단 | `frontend/src/App.tsx` | `<Route path="otao-po">` 제거 | **KILLED** | SUR-1·2·3·3b·자백①·SUR-4·SUR-5 (7/7) |
| **M2** ★표면 | 메뉴 절단 | `frontend/src/components/Layout.tsx` | 「📦 발주 (OTAO)」 NAV 항목 제거 | **KILLED** | SUR-2 |
| **M3** ★표면 | 3칸 중 1칸 절단 | `OtaoPurchaseOrders.tsx` | 「픽업 누계」 `<Th>`+`<Td>` 제거 | **KILLED** | SUR-3, SUR-3b, 자백① |
| **M4** ★표면 | 매핑필요 카드 절단 | `OtaoPurchaseOrders.tsx` | 「매핑 필요」 `<Card>` 통째 제거 | **KILLED** | SUR-4 |
| **M5** ★표면 | 응답에서 자백 필드 제거 | `backend/app/routers/otao_po.py` | `ledger_empty`·`unmapped`·`notes` 삭제 | **KILLED** | `test_window_start_is_declared`, `test_unmapped_names_are_in_the_body_with_quantity`, `test_negative_reserved_is_not_clamped_in_the_body`, `test_empty_ledger_says_so_instead_of_showing_zeros` |
| **M6** ★표면 | 라우터 등록 절단 | `backend/app/main.py` | `include_router(otao_po.router)` 제거 | **KILLED** | http 7건 전건 |
| **M7** | 정본 순위 뒤집기 | `ingest.py:278-283` | ECOUNT·Revise가 **지도록** rank 반전 | **KILLED** | `test_same_serial_two_files_both_kept_one_authoritative`, `test_revise_wins_when_no_ecount_copy`, `test_sync_name_map_uses_only_authoritative_orders` |
| **M8** | manual 보호 제거 | `ingest.py:349-354` | 사람 확정 매핑을 재적재가 덮게 함 | **KILLED** | `test_sync_name_map_does_not_overwrite_manual_decisions` |
| **M9** | `_order_date` 항상 None | `ingest.py:119-127` | 발주일 판독 무력화 | **KILLED** | `test_order_date_comes_from_serial_prefix` |
| **M10** | sha 멱등 검사 제거 | `ingest.py:199` | `existing.get(sha)` → `None` | **KILLED** | `test_ingest_is_idempotent_on_file_content`, `test_moved_file_updates_path_without_duplicating` (UNIQUE 위반) |
| **M11** | ★P1-1 회귀 | `roster.py:113-128` | `order_count`를 다시 `set[date]`로 | **KILLED** | `test_order_count_counts_orders_not_dates` |
| **M12** | ★P1-2 회귀 | `alembic/…/otao1po4n4a_*.py:62` | `parsed_at nullable=False → True` | **KILLED** | `test_migration_nullable_matches_model` |
| **M13** ★표면 | 픽업 배선 절단 | `roster.py:148` | `row(code).picked += n` → `row(code)` | **KILLED** | `test_picked_is_wired_from_customs_ledger` 외 6건 |
| **M14** | 음수 잔량 clamp | `roster.py:152` | `reserved = max(0, …)` | **KILLED** | `test_reserved_may_go_negative_and_is_not_clamped`, `test_negative_reserved_is_not_clamped_in_the_body` |
| **M15** | 창 필터 제거 | `roster.py:116-118` | `in_window = True` | **KILLED** | `test_out_of_window_orders_are_separated_not_dropped` 외 3건 |
| **M16** | 정본 필터 제거 | `roster.py:106` | `where(is_authoritative)` 삭제 | **KILLED** | `test_non_authoritative_orders_are_excluded` 외 2건 |
| **M17** | `line_type` 필터 제거 | `roster.py:138-140` | 부자재가 픽업에 섞이게 | **KILLED** | `test_material_lines_are_not_counted_as_pickup` |
| **M18** | 창 시작일 `min`→`max` | `roster.py:85` | 원장 창의 정의를 뒤집음 | **SURVIVED** | — (픽스처가 전부 선적 1건 ⇒ min≡max) → **P2-3** |
| **M19** | 빈 수량 0채움 | `ingest.py:231-240` | `qty=None` 라인을 `0`으로 적재 | **KILLED** | `test_blank_quantity_line_is_reported_not_zero_filled` |
| **M20** | `_TWO_EA` 전체 완화 | `name_map.py:49` | `(?<![\d.])\b2\s*ea\b` → `2\s*ea` | *무효* | 테스트된 입력에서 **등가 변이** — M20b로 대체 |
| **M20b** | lookbehind만 제거 | `name_map.py:49` | `(?<![\d.])` 삭제 | **SURVIVED** | — (`Glass 3.2ea`→`glass3.` 로 동작이 실제로 바뀜) → **P2-4** |
| **M21** | 사전을 비정본으로도 구축 | `ingest.py:328-330` | `sync_name_map`의 `is_authoritative` 필터 제거 | **KILLED** | `test_sync_name_map_uses_only_authoritative_orders` |
| **M22** ★표면 | 「창 밖 발주」 열 제거 | `OtaoPurchaseOrders.tsx:117,129-140` | 잔량에서 뺀 몫을 화면에서 삭제 | **KILLED** | 자백① |
| **M23** ★표면 | notes 배너 제거 | `OtaoPurchaseOrders.tsx:88-99` | 백엔드 자백 문장을 화면에서 삭제 | **KILLED** | 자백① |
| **M24** ★표면 | 정본/대체됨 건수 제거 | `OtaoPurchaseOrders.tsx:77-85` | D-INV-3 근거 보존 표면 삭제 | **SURVIVED** | — → **P2-5** |
| **M25** ★표면 | 「붙음 N/M」 배지 제거 | `OtaoPurchaseOrders.tsx:151-155` | 매핑 신선도 표면 삭제 | **SURVIVED** | — → **P2-5** |

**집계: 25종 중 21 KILLED / 4 SURVIVED / 1 무효(등가).**
전역 §4 필수 항목인 **「최종 산출물까지 가는 경로를 끊는 변이」는 6종 주입**(M1·M2·M3·M4·M6·M13,
표면 계열 M5·M22·M23 포함 시 9종)이고 **9종 중 7종이 KILLED**다.
n=4 리뷰가 *"끊을 마지막 마디 자체가 존재하지 않는다"*고 적었던 자리가 이번엔 **실재하고,
대부분 잠겨 있다.**

---

## E. 실행 결과

| 항목 | 명령 | 결과 |
|---|---|---|
| 백엔드 | `cd backend && python3 -m pytest -q` | **6676 passed, 0 failed**, 856 warnings, 260.17s (0:04:20) — 포그라운드 완주 |
| 프론트 유닛 | `cd frontend && npx vitest run` | **Test Files 64 passed (64) · Tests 888 passed (888)**, 8.23s |
| 빌드 | `cd frontend && npm run build` | `✓ built in 362ms` · `dist/assets/index-BvBo_WW-.js 1,406.99 kB` · `stamp-build: commit=b77238816d86 dirty=0` (tsc -b 오류 0) |
| eslint | `npx eslint . --max-warnings 96` | **96 problems (0 errors, 96 warnings)** · exit 0 — 상한과 정확히 일치(신규 경고 0) |
| alembic | `python3 -m alembic heads` | `otao1po4n4a (head)` — **단일 head** |

미완주·타임아웃 **없음** ⇒ INCONCLUSIVE 사유 없음.

## F. 원복 확인

```
$ git status --porcelain
(출력 없음)

$ git diff --stat
(출력 없음)

$ grep -rn "MUTATION" --include="*.py" --include="*.ts" --include="*.tsx" \
    backend/app backend/tests backend/scripts frontend/src | wc -l
0

$ git rev-parse --short HEAD
b7723881
```

워킹트리 **완전 청결**, 변이 잔재 **0건**, HEAD는 리뷰 시작 시점과 동일한 `b7723881`.
(n=4 리뷰가 변이를 남긴 채 죽어 트리를 오염시킨 사고의 재발 없음.)

> ⚠️ 이 파일은 **커밋하지 않았다** — 커밋은 리뷰 의뢰자 몫이다.
