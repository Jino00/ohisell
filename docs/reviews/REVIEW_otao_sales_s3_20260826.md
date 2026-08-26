# 적대 리뷰 — PR #473 (S3 채널 통합 판매 시계열)

- **대상**: 브랜치 `feat/po-forecast-n6` · 커밋 `6100e0a5` · `git diff origin/main...HEAD` (9파일 +1,415/−3)
- **계약**: `docs/contracts/CONTRACT_inventory_unified.md` §4 **S3**
- **리뷰 일시**: 2026-08-26 KST · 리뷰어 = 구현하지 않은 별도 기
- **베이스라인**: 백엔드 23 passed · 프론트 18 passed (변이 전후 동일, 잔여 diff 0)

---

## 판정

> ## **FAIL — P1 3건**

P1은 ①prod에서 **이미 틀린 숫자가 화면에 떠 있고**(중복 매핑 fan-out, 60일 창 +6.0%/+6.5%),
②S3 원문의 첫 요구인 **「시계열」이 화면에도 payload에도 없으며**,
③계약 §6이 **필수로 못 박은 표면 절단 변이가 실제로 생존**한다(prod가 타는 경로에서 판매 섹션을
통째로 지워도 18/18 초록)는 셋이다.

금지선 검사는 **전건 통과**했고(§3-2·§3-3·§3-8), 백엔드 서비스층 테스트는 변이 6개를 **전부**
죽였다. 결함은 「계산이 틀렸다」가 아니라 **「원장에 없는 수량이 조인에서 태어난다」**와
**「만든 것이 화면에 안 닿는다」** 두 축에 몰려 있다.

---

## P1

### P1-1 — `product_channel_mapping` 중복 행이 판매수량을 곱한다 (prod에서 이미 발생 중)

**무엇이**: 쿠팡 3P·RG 2P의 다리 조인이 `channel_product_id`의 **유일성을 가정**하는데, 그 컬럼엔
unique 제약이 없고 prod에 **중복 55키·121행**이 실재한다. 중복 키 1건은 **서로 다른 `product_id`
5개**에 붙어 있다. `outerjoin`이 1:N으로 펼쳐지면서 **판매행 1건이 N건이 되고, 같은 수량이 N번
더해진다.** 예외는 안 나고 숫자만 커진다.

**어디서**
- `backend/app/services/otao_po/sales.py:186-192` (`_wing_3p`의 `outerjoin(ProductChannelMapping, …)`)
- `backend/app/services/otao_po/sales.py:220-226` (`_rg_2p` — 같은 다리)
- 집계 지점: `sales.py:352-368` (`health.quantity += qty` / `row["total"] += qty`)

**재현 ① — prod 원장 대 화면 (읽기 전용, 60일 창 = 화면 기본값)**

```bash
scp -q /tmp/rev/q4.py sellc.ohitech.co.kr:/tmp/q4_review.py && \
ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && .venv/bin/python - < /tmp/q4_review.py"
```

```
--- WING 60d 원장 vs joined ---
    ('raw',    'COUPANG_WING1', 2109, 1980)
    ('joined', 'COUPANG_WING1', 2168, 2099)      ← +119 units (+6.0%)
--- RG 60d 원장 vs joined ---
    ('raw',    'COUPANG_WING1', 2097, 2117)
    ('joined', 'COUPANG_WING1', 2231, 2254)      ← +137 units (+6.5%)
--- 유령 수량이 붙는 SKU (RG, 60d) ---
    (929, 'OHI-0907', '오하이 맥세이프 이지 카드지갑 (투명)', 274, 268)
```

같은 창에서 모듈을 **그대로 실행**한 값이 joined 쪽과 정확히 일치한다 —
`wing3p_ofix qty=2099` · `rg2p_ofix qty=2254`. 즉 **화면에 지금 떠 있는 숫자가 원장보다 크다.**

**재현 ② — 단위 재현 (prod와 같은 모양, 커밋 안 함)**

```python
# 같은 vendor_item_id에 pcm 5행(서로 다른 product_id) — prod '13347448471'과 동형
session.add(CoupangVendorItemSalesDaily(..., vendor_item_id="V-DUP", units_sold=10))
ts = build_sales_timeseries(session, days=7, today=TODAY)
```
```
원장 units_sold=10 · 화면 quantity=50 · rows=5
SKU별 배분: {'OHI-0001': 10, 'OHI-0002': 10, 'OHI-0003': 10, 'OHI-0004': 10, 'OHI-0005': 10}
AssertionError: 원장은 10개인데 50개로 보고한다

원장 sales_quantity=100 · 화면 quantity=200   (RG, 중복 2행)
```

**★기존 테스트 중 이걸 잡는 것은 0건**이다 — 모든 픽스처가 키당 `ProductChannelMapping`을
**1행만** 넣는다(`test_otao_po_sales.py:139, 231, 246`).

**왜 계약 위반인가**
- **§2-9** *"조용히 넣으면 발주 오염"* — 유령 수량은 원장에 없는 판매이고, **팔린 적 없는 4개 SKU에
  수요가 생긴다.** 조용히 빼는 것과 대칭인 병이고 조항이 명시적으로 겨눈 자리다.
- **§2-1** *"항상 원장이 예측보다 먼저다"* — 이 시계열은 §4 **S5 발주 추천**의 판매속도 입력이다.
  +6%가 리드타임 29일 발주량에 그대로 실린다.
- **§4 S3** — *"채널 통합 SKU별 판매수량"*이 사실과 다르다.
- 부수 효과: `mapping_rate`의 분자·분모가 함께 부풀어 **매핑 건강도를 실제보다 좋게** 보고한다.

**참고 — 같은 의심 중 반증된 것**: `_rocket_1p`의 `RocketProductCostMap.product_number`는
`unique=True`이고 prod 중복 **0건**, joined 행수 = raw 행수(3,994 = 3,994). **1P는 무해하다.**

---

### P1-2 — S3이 요구한 「시계열」이 화면에 없다 (payload에도 SKU×날짜 축이 없다)

**무엇이**: S3 원문의 첫 요구는 *"**채널 통합 SKU별 판매수량 시계열**이 보이고"*다. 백엔드는
`daily`를 계산하고, `otao_po.py:139`가 body에 싣고, `api.ts`가 타입을 정의하고,
`test_sales_body_carries_every_confession_field`가 존재를 단언한다. **그런데 화면은 그것을 한 번도
그리지 않는다.**

**어디서**
- 생성: `sales.py:392-395` (`out.daily = [...]`)
- 직렬화: `backend/app/routers/otao_po.py:139`
- 타입: `frontend/src/lib/api.ts` (`daily: { date; total; by_channel }[]`)
- **렌더: 없음** — `frontend/src/pages/otaoSalesPanel.tsx`에 `daily` 참조 0건

**재현 — 렌더 결과 전문 덤프** (패널을 3일치 `daily`와 함께 렌더)

```
grep -rn "daily" frontend/src/pages/otaoSalesPanel.tsx frontend/src/pages/OtaoPurchaseOrders.tsx
  → (0건)

=== RENDERED TEXT ===
N1채널별 판매 — 매핑률과 결손일2026-06-28 ~ 2026-08-26 (60일)채널법인판매수량매핑됨매핑률취소·반품
데이터 있는 날빈 날의 정체네이버오하이1009090%−458판매 0 1일 · 데이터 없음 1일 ⚠
매핑 필요 — SKU 시계열에서 빠져 있는 판매10개채널수량네이버10
SKU별 채널 통합 판매수량1개 SKUSKU상품명네이버합계OHI-0001필름9090
발주 축 ↔ 판매 축 다리: 겹치는 값 0개 …

daily_dates_on_screen: []      ← 2026-08-24 / 08-25 둘 다 화면에 없음
charts (svg|canvas): 0
```

화면에 뜨는 유일한 날짜는 카드 헤더의 **창 라벨**(`2026-06-28 ~ 2026-08-26`)이고, 이는 시계열이
아니라 기간 표기다. 표는 **60일 합계 1열**뿐이다.

**★그리고 payload에도 요구된 축이 없다**: `daily`는 **날짜 × 채널**이고(`sales.py:365` —
`per_day[d][key]`, key는 채널), `rows`는 **SKU × 창 전체 합계**다. **SKU × 날짜**는 어디에도
생성되지 않는다. 즉 `daily`를 지금 그린다 해도 *"SKU**별** … 시계열"*은 여전히 못 만든다.

**왜 계약 위반인가**
- **§4 S3** 세 요구 중 첫째가 미충족이다(둘째 매핑률·셋째 결손일 구분은 충족).
- 전역 **§2 표면 규칙** — *"「X가 계산된다」가 아니라 「화면 어디에 X가 뜨는가」"*. 이 건은 그
  조항의 교과서적 형태다: 계산·직렬화·타입·HTTP 단언까지 다 있고 **렌더만 없다**.
- 코드가 스스로 그 부재를 증언한다 — 패널의 카드 제목이 *"매핑 필요 — SKU **시계열**에서 빠져
  있는 판매"*로 **있지도 않은 시계열을 지목**한다(`otaoSalesPanel.tsx:150`).
- 하류 영향: §4 **S5**의 판매속도(개/일)는 SKU×날짜 축을 요구하는데 이 슬라이스가 그것을 안 만든다.

---

### P1-3 — 표면 절단 변이 SUR-M1이 생존한다 (prod가 타는 경로가 테스트 사각지대)

**무엇이**: 계약 **§6 종료 조건**이 *"**표면 절단 변이 1개 필수**(최종 산출물까지 가는 경로를 끊는
변이)"*를 못 박았다. 실제로 넣어 보니 **판매 섹션을 prod 경로에서 통째로 삭제해도 18/18이 초록**이다.

**어디서**: `frontend/src/pages/OtaoPurchaseOrders.tsx:209` — 정상(원장 비어 있지 않음) 반환부의
`{salesSection}`

**재현**

```bash
# 변이: 정상 경로의 {salesSection} 삭제 (ledger_empty 조기반환부의 사본은 그대로 둔다)
cd frontend && npx vitest run src/pages/otaoSalesReachesTheUser.test.tsx \
                              src/pages/otaoPoReachesTheUser.test.tsx
```
```
 Test Files  2 passed (2)
      Tests  18 passed (18)          ← SURVIVED
```

**기제 — 두 테스트 파일의 교집합이 prod 상태다**

| 테스트 파일 | 로스터 | 판매 fetch | 렌더되는 경로 |
|---|---|---|---|
| `otaoSalesReachesTheUser.test.tsx` | `ledger_empty: **true**` | 정상 | **조기반환부** 사본만 |
| `otaoPoReachesTheUser.test.tsx` | 비어 있지 않음 | **일부러 throw** | 정상 경로, 단 판매는 EmptyState |
| **prod** | **비어 있지 않음** | **정상** | **정상 경로 — 아무도 안 봄** |

**prod가 정말 그 경로인지 실측**:
```
otao_purchase_order rows: 95  lines: 1205
=> ledger_empty would be: False
```

**왜 계약 위반인가**: §6이 요구한 변이가 형식적으로만 충족됐다. 판매 섹션이 사라져도 CI가 초록이면
**S3 표면 전체가 다음 리팩터 한 번에 조용히 증발**할 수 있고, 그게 이 트랙이 n=4에서 이미 한 번
겪은 *"코드는 있으나 아무도 못 본다"*의 재발 경로다.

---

## P2 — **선택 사항**(트리아지: 채택/기각/이월 중 하나로 처분, 라운드를 늘리지 않는다)

| # | 무엇 | 좌표 | 메모 |
|---|---|---|---|
| P2-1 | **`unmapped`이 「어느 상품인지」를 안 준다** — 채널별 수량 합뿐이라 사람이 고칠 수 없다. S1 로스터는 「품목명 30종 8,390개」로 **품목명까지** 준다(`otaoPoReachesTheUser` SUR-4가 그걸 단언). §2-9의 *"「매핑 필요」로 표면에 드러낸다"*를 글자로는 만족하나 **행동 가능성**이 없다. prod 현재 총 30개로 영향은 작다 | `sales.py:361-363` · `otaoSalesPanel.tsx:151-160` | 이월 후보 |
| P2-2 | **수량 0인 채널이 「매핑 필요」에 행으로 뜬다** — prod `unmapped`에 `wing3p_ohitech: 0` 실재. `qty=0`인 미매핑 행이 키를 만든다 | `sales.py:361-363` | 잡음 |
| P2-3 | **순수량 ≤ 0이면 화면이 「잴 수 없음」이라 거짓말한다** — 재현: 행 2건(+5, −5) → `quantity=0 · mapping_rate=None` → 툴팁 *"이 창에 판매 수량이 없어"*(행은 2건, 데이터 있는 날 2일). 동시에 `active` 필터(`quantity > 0`)가 **SKU 표의 그 채널 열을 통째로 드롭**해 열 합이 `total`과 안 맞는다. **음수는 실재**(모델 docstring이 *"units_sold 음수 허용"*을 명시, prod 60일 Wing 39행·rocket 94행). 현재 미발화지만 `wing3p_ohitech`는 1,861행에 **순 86개**로 여유가 얇다 | `sales.py:99-104` · `otaoSalesPanel.tsx:22-32, 68` | 잠복 |
| P2-4 | **`order_axis.note`를 지워도 아무도 안 죽는다**(변이 SUR-M4 생존) | `otaoSalesPanel.tsx:196` | 헤드라인 자백은 SUR-S6가 지킴 |
| P2-5 | **백엔드가 쓴 `notes` 블록을 통째로 지워도 9/9 초록**(변이 SUR-M7 생존). 같은 취지의 자백이 채널 셀·다리 배너로 **중복 렌더**되므로 거짓말은 아니고 요약층 손실 | `otaoSalesPanel.tsx:73-85` | 이월 후보 |
| P2-6 | **`NON_DEMAND_STATUSES`가 prod 상태 어휘와 대조된 적 없다** — 하드코딩 2값인데 prod 60일 ch6엔 `delivered/cancelled/shipped/returned/exchanged/confirmed` 6종, ch7엔 5종. `exchanged`(ch6 54건·ch7 6건)·`pending`(15건)이 수요로 계산된다. 교환이 수요인지는 **판단이 필요한 문제**이고 지금은 판단 없이 기본값이 이겼다 | `sales.py:73` | 결정 필요 |
| P2-7 | **`order_codes_reached_by_name_map`이라는 이름이 실제보다 많이 약속한다** — 세는 것은 `OtaoItemNameMap`의 distinct `product_code`(=품목명→코드)이고, 그게 **판매 축에 닿는다는 뜻이 전혀 아니다**(`overlap`은 0). prod 33 | `sales.py:275-284` | 이름 오도 |
| P2-8 | **`func.date(...) >= start.isoformat()`은 dialect 의존**. prod는 **SQLite**라 현재 안전(문자열 비교). PostgreSQL에선 `date` 대 `text` 비교가 되어 다르게 굴 여지 | `sales.py:161-162` · `216-217` | 잠복(이 PR 밖) |
| P2-9 | JSX 본문에 마크다운 `**다른 축**`이 그대로 들어가 **별표가 그대로 렌더**된다 | `OtaoPurchaseOrders.tsx:68-69` | 표기 |

---

## 반증된 의심 (재현 실패 — P1 아님, 기록만)

| 의심 | 실측 | 판정 |
|---|---|---|
| `_collected_days`가 O(runs×days)라 prod에서 비쌀 것 | ch6 성공 run **1,503**·ch7 **1,001** 확인. 그러나 실측 `days=60: ch6 0.01s / ch7 0.00s`, `days=365: 0.02s / 0.02s` | **반증** — 병목 아님 |
| `days=365`에서 응답이 터질 것 | 전체 `build_sales_timeseries` **2.37s** · JSON **≈173KB**(`daily` 365행 + `rows` 693행). `days=60`은 **1.95s**·118KB | **반증** — 허용 범위 |
| `_rocket_1p`의 `product_number` 조인이 1:N일 것 | `unique=True` + prod 중복 **0건**, joined 3,994 = raw 3,994 | **반증** |
| `date.fromisoformat(str(d))`가 깨질 입력 | NULL은 WHERE가 이미 배제. SQLite는 `str`, PG는 `date` → 둘 다 `str()`이 `YYYY-MM-DD` | **반증** |
| 결손일 구분이 실제로 안 돌 것 | `days=365`에서 naver `no_data=185`·cafe24 `no_data=127`, 쿠팡 5축은 전부 빈 채로 `missing_day_evidence=False`. cafe24 60일 `collected_zero=7`은 계약 S0-c의 「결손 0일·카운트 6~8 흔들림」과 정합 | **반증 — 정상 동작** |

---

## 금지선 검사 (§3) — 전건 통과

| 항목 | 결과 |
|---|---|
| prod 쓰기 | **0** — `backend/app/` 추가분에 `commit(`·`session.add`·`delete`·`INSERT/UPDATE/DELETE` 0건. `sales.py`의 `.add(` 2건은 파이썬 `set.add()`(`:145`, `:356`) |
| 마이그레이션 | **0파일** (`backend/alembic` diff 없음) |
| ECOUNT 호출 (§3-3) | diff 전체 `ecount` **0건** |
| 수입 원장 수정 (§3-8) | `import_invoice*`·`import_shipment`·`customs` **미변경** |
| 자동 실행 (§3-2) | 추가된 엔드포인트는 `@router.get("/sales")` **하나**, `select()` 9회, 쓰기 primitive 0 → **읽기 전용 확인** |
| 배포 (`safe_deploy`/`scp`/`rsync`) | diff에 **0건** |
| 합산 단일 숫자 (§3-9) | 판매 축엔 비적용(§3-9는 예약잔량·운송중·현재고 3분 표기). 발주 3칸은 이 PR에서 미변경 |

---

## 변이표

★ = 「사용자에게 닿는 마지막 표면」을 끊는 변이 (계약 §6 필수 항목)

| ID | 변이 | 결과 | 무엇이 죽였나 | failed / error |
|---|---|---|---|---|
| ★**SUR-M1** | `OtaoPurchaseOrders.tsx` **정상 경로**(prod 경로)의 `{salesSection}` 삭제 | 🔴 **SURVIVED** | — | 18 passed |
| ★SUR-M2 | `MissingDayCell`이 `missing_day_evidence`를 무시(배지 위조) | ✅ KILLED | `SUR-S4` | **1 failed** |
| ★SUR-M3 | `mapping_rate: null`을 `0%`로 렌더(배지 위조) | ✅ KILLED | `SUR-S3` | **1 failed** |
| ★SUR-M4 | 다리 자백에서 `order_axis.note` 제거 | 🔴 **SURVIVED** | — | 18 passed |
| ★SUR-M5 | 「매핑 필요」 카드 통째로 제거 | ✅ KILLED | `SUR-S1` + 「매핑 필요」 | **2 failed** |
| ★SUR-M6 | 「데이터 없음 N일」을 `0일`로 위조 | ✅ KILLED | `SUR-S5` | **1 failed** |
| ★SUR-M7 | 백엔드가 쓴 `notes` 블록 제거 | 🔴 **SURVIVED** | — | 9 passed |
| BE-M1 | Wing 다리를 `vendor_item_id` → `product_id`로 교체 | ✅ KILLED | `test_wing_bridge_is_vendor_item_not_product_id` 외 2 | **3 failed** |
| BE-M2 | `NON_DEMAND_STATUSES = set()` (취소·반품 미제외) | ✅ KILLED | `test_cancelled_and_returned_are_excluded_but_not_silently` | **1 failed** |
| BE-M3 | `mapping_rate`가 `None` 대신 `0.0` 반환 | ✅ KILLED | `test_mapping_rate_is_none_not_zero_…` | **1 failed** |
| BE-M4 | 쿠팡 채널에 결손 구분 근거를 **날조** | ✅ KILLED | `test_channels_without_collection_log_…` + HTTP | **2 failed** |
| BE-M5 | HTTP body에서 `daily` 제거 | ✅ KILLED | `test_sales_body_carries_every_confession_field` | **1 failed** |
| BE-M6 | `order_axis` 위조(`overlap=1` → 다리 있는 척) | ✅ KILLED | `test_sales_body_confesses_the_missing_bridge…` | **1 failed** |
| REPRO-1 | (변이 아님) prod와 동형인 pcm 중복 행 주입 | 🔴 **결함 재현** | 기존 테스트 **0건**이 잡음 | P1-1 |
| REPRO-2 | (변이 아님) 순수량 0이 되는 음수 행 주입 | 🔴 **결함 재현** | 기존 테스트 **0건**이 잡음 | P2-3 |

**집계**: 표면 변이 7개 중 **4 KILLED / 3 SURVIVED** · 백엔드 변이 6개 중 **6 KILLED / 0 SURVIVED**.
죽은 변이는 **전부 `failed`**(테스트가 잡음)이고 `error`(문법 파손)는 **0건**이다.

**읽는 법**: 백엔드 서비스층은 강하다 — 다리 오조인·자백 필드 삭제·근거 날조를 전부 잡는다.
구멍은 **①테스트가 상상하지 못한 데이터 모양**(중복 매핑·음수)과 **②프론트 경로 커버리지**에 있다.

---

## 재작업 제안 (P1만)

1. **P1-1** — 쿠팡 두 다리에서 `channel_product_id` 중복을 제거한다. 조인 전 `product_id`를
   1개로 접거나(서브쿼리 `GROUP BY channel_product_id` + 다중 매핑은 **「매핑 모호」로 표면화**),
   최소한 `channel_id` 필터를 건다 — prod 중복 키 중 `'5,2'`·`'2,4'`·`'3,1'`처럼 **채널을 가로지르는**
   것들이 있어 필터만으로도 상당수가 걷힌다. 다만 `'13347448471'`(5행, 전부 `channel_id=6`)처럼
   **채널 안 중복**도 있으므로 필터만으로는 부족하다. 회귀 테스트는 REPRO-1을 그대로 쓴다.
   ★§2-9에 맞추려면 **모호한 매핑은 조용히 하나 고르지 말고 「매핑 필요」로 드러내는 편**이 옳다.
2. **P1-2** — `per_day`를 SKU까지 쪼개(`per_day[d][sku]` 또는 `rows[*].daily`) **SKU별 시계열**을
   만들고 화면에 그린다. 렌더가 붙으면 표면 테스트도 같이 붙인다.
3. **P1-3** — `otaoSalesReachesTheUser.test.tsx`의 픽스처를 **`ledger_empty: false`**(prod 상태)로
   바꾸거나 두 경로를 모두 도는 케이스를 추가한다. 그러면 SUR-M1이 죽는다.

P1-1은 **숫자가 이미 틀려 있으므로** 먼저다. P1-2는 범위가 커 보이면 §2 목적 전환 선언과 함께
슬라이스를 쪼개는 편이 낫다 — 다만 **S3 체크박스를 `[x]`로 찍는 것은 그전엔 안 된다.**

---

## 리뷰 완주 확인

- 도구 실패·타임아웃 **없음**. 백엔드 pytest 포그라운드 완주, 프론트 vitest 완주, prod 조회 5회 완주.
- prod 접근은 **읽기 전용**만(`SELECT` + 모듈 read-only 실행). 쓰기·배포·적재 **0건**.
  prod `/tmp`에 올린 조회 스크립트는 리뷰 종료 시 **삭제 완료**.
- 프론트 `node_modules`는 메인 저장소에서 **심볼릭 링크로 빌려 쓰고 삭제**했다. 메인 저장소 쓰기 0.
- 모든 변이는 `git checkout -- <파일>`로 원복했고 **잔여 diff 0**(`git diff --stat` 공백,
  최종 재확인 백엔드 23 passed).
- 자기검증 반증 1건: 초판 조회의 `CURRENT_DATE - 60`은 SQLite에서 **날짜 산술이 아니라
  `2026 - 60 = 1966` 정수 비교**라 창 필터가 통째로 무력했다. `date(CURRENT_DATE,'-59 day')`로
  다시 재고 P1-1 수치를 확정했다.

---
---

# 2라운드 재판정 — 수정 커밋 diff만

- **대상**: `git diff 6100e0a5..HEAD` = `a5e63331`(P1 3건 수정) + `0d254169`(0잡음 정리)
  · 6파일 +388/−60. **전체 브랜치 재리뷰 안 함**(§4 종료 규칙 ③).
- **질문**: 「P1 3건이 해소됐는가」 **하나뿐**. 새 지적은 만들지 않고 발견은 **이월**로만 적는다.
- **베이스라인**: 백엔드 `test_otao_po_sales.py`+`test_otao_po_http.py` **29 passed**(1R 23 → +6)
  · 프론트 **22 passed**(1R 18 → +4) · `tsc --noEmit` **exit 0** · 잔여 diff **0**

---

## 판정

> ## **PASS — P1 0건 (3건 전부 해소)**

세 건 모두 **내가 직접 변이를 재주입해** 확인했고, P1-1·P1-2는 **prod 라이브 실측**으로 교차
확인했다. 1R에서 생존했던 표면 절단 변이 **SUR-M1은 이제 4건 failed로 사망**한다.

---

## P1-1 — 판매 부풀림 · **해소**

**수정 방식**: outerjoin 폐기 → `_channel_sku_index`가 `channel_product_id → set[internal_sku]`를
만들고 `_resolve`가 파이썬에서 가른다. 집합 1이면 붙이고, **≥2면 안 붙이고** `quantity_ambiguous`로
센다(다수결 금지).

**증거 ① — prod 라이브 실측이 원장과 «정확히» 일치한다** (읽기 전용, 60일 창)

| 채널 | 원장 | 1R 화면 | **2R 화면** | 판정 |
|---|---|---|---|---|
| Wing 3P 오픽스 | **1,980** | 2,099 (+6.0%) | **1,980** | ✅ 일치 |
| Wing 3P 오하이테크 | **86** | 86 | **86** | ✅ |
| RG 2P 오픽스 | **2,117** | 2,254 (+6.5%) | **2,117** | ✅ 일치 |
| RG 2P 오하이테크 | **6** | 6 | **6** | ✅ |

원장 조회는 조인 없는 `SUM(units_sold)` / `SUM(sales_quantity)`이고, 화면 값은 수정된 모듈을
prod DB에 그대로 실행한 값이다. **부풀림 0.**

**증거 ② — 변이 재주입**

- `R2-M1`(모호성 가드 `if len(skus) > 1` 제거 → 아무거나 하나 고름) → **2 failed**
- `R2-M2`(인덱스가 `set` 대신 「마지막 행이 이긴다」) → **2 failed**

★M2가 중요하다 — **「집합을 안 쌓는」 형태의 회귀**까지 잡힌다는 뜻이다.

### 코디네이터가 지목한 세 의문 — 전부 실측으로 답함

**(a) `channel_id`를 안 보고 키만 보는 게 위험한가 → 현재 위험 없음. 실측 0건.**

```sql
-- 쿠팡 채널(1~4)만 보면 유일한데, 전 채널로 보면 모호해지는 키
SELECT COUNT(*) FROM (
  SELECT p.channel_product_id, COUNT(DISTINCT pm.internal_sku) all_sku,
         COUNT(DISTINCT CASE WHEN p.channel_id IN (1,2,3,4) THEN pm.internal_sku END) cp_sku
  FROM product_channel_mapping p JOIN product_master pm ON pm.id=p.product_id
  GROUP BY p.channel_product_id) t
WHERE all_sku > 1 AND cp_sku = 1;
--  (0,)
```

1R에서 내가 관측한 채널 간 중복 키(`'5,2'`·`'2,4'`·`'3,1'`)는 **같은 상품을 여러 채널에 판다**는
뜻이라 `internal_sku` 집합이 **1로 접힌다** — 매핑 테이블의 정상 용법이다. 그래서 채널을 안
가르는 것이 억울한 억제를 만들지 않는다. **채널 필터는 지금 넣어도 바뀌는 게 없다.**

**(b) 모호성 규칙이 실제로 무언가를 억누르는가 → 아니다. `amb=0`.**

인덱스 집합 크기 분포: `{1: 2838, 2: 38, 3: 8, 5: 1}` — **모호 키 47개** 실재. 그런데 60일·365일
**두 창 모두 전 채널 `quantity_ambiguous = 0`**이다. 그 47개 키엔 창 안 쿠팡 판매가 없다
(대부분 `channel_id=6` 네이버 쪽이고, 네이버는 이 인덱스를 아예 안 쓴다 — `orders.product_id` 직결).
⇒ **이 수정은 순이득이다**: 부풀림을 없애면서 정당한 매핑을 하나도 잃지 않았다.
⇒ 1R의 +119/+137은 **「같은 상품을 가리키는 중복 행」**이 만든 것이었고(집합 1로 접힘),
   그래서 «모호»가 아니라 «중복»이 병이었다는 것이 사후에 확정됐다.

**(c) 매핑 테이블 전체를 메모리에 올리는 게 괜찮은가 → 괜찮다.**

`_channel_sku_index: 2885 keys · 0.161s`. prod 2,951행 기준 사실상 무시할 만하다. 오히려
**전체 응답이 1R보다 빨라졌다**(아래 P1-2 표) — outerjoin 제거로 조인 비용이 사라졌기 때문이다.

---

## P1-2 — 시계열 부재 · **해소**

**수정 방식**: `SalesTimeseries.dates`(날짜 축) + `rows[*].series`(자리 대응 배열) 신설, 라우터가
`dates`를 싣고, 화면이 인라인 SVG `Sparkline`을 SKU 행마다 그린다.

**증거 ① — `series`가 정말 SKU×날짜인가 (prod 실측)**

| 검산 | days=60 | days=365 |
|---|---|---|
| `len(series) == len(dates)` 불일치 SKU | **0건** | **0건** |
| `Σseries == total` 불일치 SKU | **0건** | **0건** |

두 항등식이 전 SKU에서 성립한다 ⇒ `series`는 **창 합계의 진짜 일별 분해**다. 1R에서 지적한
「`daily`는 날짜×채널, `rows`는 SKU×합계라 SKU×날짜가 어디에도 없다」가 **해소**됐다.

**증거 ② — 화면에 닿는가**: SUR-S7이 `svg[role="img"] polyline`의 **점 개수**를 단언한다.
내가 넣은 표면 변이 둘이 모두 죽었다 — `R2-M6`(Sparkline 렌더 제거) **1 failed**,
`R2-M8`(스파크라인이 `series`를 무시하고 평평한 선) **1 failed**. ★M8이 중요하다: **「그리긴
그리는데 데이터를 안 읽는」 위조**까지 잡힌다.

**증거 ③ — 성능·크기 (prod 실측, 1R 대비)**

| | 1R 시간 | **2R 시간** | 1R payload | **2R payload** | rows × dates |
|---|---|---|---|---|---|
| `days=60` (화면 기본값) | 1.95s | **0.14s** | 118KB | **220KB** | 545 × 60 |
| `days=365` | 2.37s | **0.19s** | 173KB | **926KB** | 693 × 365 |

**허용 범위로 판정한다** — 화면 기본값 60일은 220KB·0.14s이고, `days=365`는 URL로만 닿는
경로인데도 0.19s다. **시간은 오히려 10배 빨라졌다**(outerjoin 제거 효과).
다만 926KB는 **대부분 0인 희소 행렬**이라 낭비적이다 → **이월 R2-C**(P1 아님).

---

## P1-3 — 표면 절단 변이 생존 · **해소**

**수정 방식**: `describe("prod가 타는 경로 — 원장이 차 있고 판매도 정상일 때")` 4건 신설.
`LOADED_ROSTER`(`ledger_empty: **false**`)를 `mockResolvedValue`로 덮고 판매 fetch는 **정상**으로
둔다 — 1R에서 지적한 두 파일의 사각지대 **교집합**이 정확히 그 조합이었다.

**증거 — 내가 SUR-M1을 직접 재주입했다** (prod 경로의 `{salesSection}` 삭제)

```
× ★발주 3칸과 판매 섹션이 **둘 다** 뜬다
× 두 축이 **다른 라벨 공간**임을 화면이 말한다
× SUR-S7 — SKU별 **일별 시계열**이 화면에 그려진다
× SUR-S8 — 「매핑 모호」 수량이 화면에 뜬다
⎯⎯⎯ Failed Tests 4 ⎯⎯⎯
TestingLibraryElementError: Unable to find an element with the text: 판매 (채널 통합)
```

1R **18 passed(생존)** → 2R **4 failed(사망)**.

**「다른 이유로 초록인가」 검사**: 실패 사유가 *"판매 (채널 통합)을 못 찾겠다"*·*"OHI-0001을 못
찾겠다"*로 **정확히 삭제한 그 표면**을 지목한다. 우연한 초록이 아니다. 그리고 이 4건은 **비어
있지 않은 원장**을 렌더하므로(`GAPIP15PR`·`합계 — 발주 30,090 · 픽업 18,970 · 잔량 11,120` 단언)
prod 상태와 같은 경로다 — 1R에서 실측한 prod `otao_purchase_order` 95행과 부합한다.

---

## 2R 변이표

★ = 사용자에게 닿는 마지막 표면을 끊는 변이

| ID | 변이 | 결과 | 무엇이 죽였나 | failed / error |
|---|---|---|---|---|
| ★**SUR-M1** (1R 생존분 **재주입**) | prod 경로의 `{salesSection}` 삭제 | ✅ **KILLED** | 새 `prod가 타는 경로` describe 4건 | **4 failed** |
| R2-M1 | 모호성 가드 `if len(skus) > 1` 제거(임의 선택) | ✅ KILLED | `…is_left_ambiguous` · `test_rg_uses_the_same_ambiguity_rule` | **2 failed** |
| R2-M2 | 인덱스가 집합을 안 쌓음(마지막 행이 이김) | ✅ KILLED | 같은 2건 | **2 failed** |
| R2-M3 | `series`를 안 채움(전부 0) | ✅ KILLED | `…series_aligned_to_the_date_axis` · `test_series_sums_channels_per_day` | **2 failed** |
| R2-M4 | HTTP body에서 `dates` 제거 | 🔴 **SURVIVED** | — | 29 passed |
| R2-M5 | HTTP body에서 `quantity_ambiguous` 제거 | 🔴 **SURVIVED** | — | 29 passed |
| ★R2-M6 | `Sparkline` 렌더 제거 | ✅ KILLED | `SUR-S7` | **1 failed** |
| ★R2-M7 | 「매핑 모호」를 항상 0으로 위조 | ✅ KILLED | `SUR-S8` | **1 failed** |
| ★R2-M8 | 스파크라인이 `series`를 무시(평평한 선) | ✅ KILLED | `SUR-S7`(점 개수 단언) | **1 failed** |

**집계**: 9개 중 **7 KILLED / 2 SURVIVED**. 죽은 변이는 **전부 `failed`**, `error` **0건**.
표면 변이 4개(SUR-M1·M6·M7·M8)는 **전부 사망**.

---

## 이월 — 새 P2 (**선택 사항 · 라운드를 늘리지 않는다**)

| # | 무엇 | 좌표 | 왜 P1이 아닌가 |
|---|---|---|---|
| **R2-A** | **HTTP body 테스트가 새 필드 2개를 안 잠근다** — `test_sales_body_carries_every_confession_field`의 키 목록에 `dates`·`quantity_ambiguous`가 빠져 있어 body에서 지워도 **29 passed**. 지워지면 화면은 「일별 추이 (undefined ~ undefined)」가 되고 「매핑 모호」는 **영구히 0**으로 그려진다 — 그 파일 docstring이 경고하는 **교훈 #321 바로 그 모양** | `backend/tests/test_otao_po_http.py:296-299` | P1 3건의 **해소 여부**와 무관(화면은 실제로 그려지고 숫자도 맞다). 회귀 «보호»의 구멍이지 현재 결함이 아니다 |
| **R2-B** | 프론트 테스트가 `fetchOtaoSales`를 mock하므로 **라우터 body ↔ 화면 계약을 잇는 테스트가 없다**. R2-A가 안 잡히는 구조적 이유 | 두 층 사이 | 위와 같음 |
| **R2-C** | `days=365` payload **926KB**(60일 220KB). 693 SKU × 365칸이 **대부분 0인 희소 행렬**이라 낭비적. 시간은 0.19s로 문제없음 | `sales.py` `series` | 화면 기본값은 60일이고 허용 범위 안 |

**1R의 P2 9건(P2-1~P2-9)은 그대로 유효하다** — 단 **P2-2(수량 0인 미매핑 줄)는 `0d254169`가
해소**했다(prod 실측: `unmapped`에서 `wing3p_ohitech: 0`이 사라짐, 60일 `{naver:2, cafe24:4,
wing3p_ofix:2, rg2p_ofix:2, rocket1p:20}`). 나머지 8건은 이번 diff가 건드리지 않았다.

---

## 2R 완주 확인

- 도구 실패·타임아웃 **없음**. 백엔드 pytest 포그라운드 완주(29 passed), 프론트 vitest 완주
  (22 passed), `tsc --noEmit` exit 0, prod 조회 2회 완주.
- prod 접근은 **읽기 전용**만(`SELECT` + 수정 모듈을 `/tmp`에서 read-only 실행). 쓰기·배포·적재
  **0건**. prod `/tmp` 스크립트 **삭제 완료**.
- 프론트 `node_modules`는 메인 저장소에서 심볼릭 링크로 빌려 쓰고 **삭제**했다. 메인 저장소 쓰기 0.
- 변이 9개 전부 `git checkout -- <파일>`로 원복. **잔여 diff 0**(`git diff --stat` 공백,
  최종 재확인 백엔드 29 passed).
- 코디네이터가 「이미 확인했다」고 준 값은 **믿지 않고 전부 다시 쟀다**. Wing 1,980 · RG 2,117 ·
  SUR-M1 사망 · 스파크라인 렌더 — 넷 다 **독립 재현으로 일치**했다.
