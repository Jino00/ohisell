# ref 119 — 원가 두 체계는 배선으로 «안 이어져 있다» (계약 준비도 실측)

> 실측 2026-08-31 19:0x~19:2x KST · prod `ohisell.db` **읽기 전용**(`mode=ro`) + 워크트리 `cost-menu-n21` 코드 전수
> 체인 `sellc-원가-메뉴` n=21 (`0ef4ee1d`) · 트랙 `docs/tracks/active/track_cost-truth-ledger.md` · 선행 ref 118
> 발단: Jino *"이 목표를 완성하기 위한 모든 내용이 들어간 계약이 다 들어갈 수 있게 준비됐어?"* → 답 **아니오** → 지시 **「나를 지금 재고 가자」**(19:02)

## 0. 한 문장 — ref 118의 447 격차는 «드리프트»가 아니라 «배선 부재»다

**원가 메뉴가 계산한 값이 손익 엔진이 쓰는 값으로 흘러가는 경로가 코드에 한 줄도 없다.** 이건 버그가 아니라 **선언된 설계**다 — `backend/app/routers/cost_menu.py:14-15`가 스스로 적어 뒀다:

> ***"★`product_master.cost_price`를 읽지도 쓰지도 않는다 — S1은 부자재 층까지고, 대조 표시는 표준원가 보드(S2·S3) 몫이다. 쓰기는 어느 슬라이스에서도 없다(계약 §3 금지선)."***

**검증**(내가 직접 재현): `backend/app/services/cost_menu/` 전체에서 `.cost_price =` 대입 **0건**. `ProductMaster` 참조 14곳은 전부 `SELECT`(비교·표시용, 예 `recipes.py:1855` `current = pm.cost_price if pm else None`).

⇒ ref 118이 잰 447건은 **두 값이 어긋난 것**이 아니라 **애초에 이어진 적이 없는 두 숫자**다. 그래서 「감시기 정본을 계산값으로 교체」만으로는 안 되고, **배선 자체가 계약의 산출물**이어야 한다.

## 1. 목표 3요소 대비 — 무엇이 비어 있나

Jino 원문(2026-08-31 17:26): *"원가 메뉴에 올라오는 원가가 **항상 정확하게** 올라오는거고, **수정이 있을때 실수없이** 잘 수정되어서 **최신 원가가 유지**되게 하는거"*

| | 필요한 것 | 실측 상태 |
|---|---|---|
| ①항상 정확 | 963 전부에 정본이 서야 함 | 판정 선 것 **278**(29%) — ref 118 §3 |
| ②수정 시 실수 없이 | 「수정」 경로가 셀 수 있고 각각 검사가 있어야 함 | **쓰기 경로 22개 · 드리프트 가드 2개**(§3) |
| ③최신 유지 | 계산 → 사용 배선 + 감시기가 새 정본을 봄 | **배선 0개**(§0) · 감시기 정본이 08-07 엑셀(ref 118) |

## 2. ①513(근거 없음)은 «정본이 하나가 아닌» 집단이다

```
513 근거 없음  (전부 cost_price는 보유)
├─ 474  draft `assembly` 레시피에 연결 (78개 레시피)
│    bar 380(54) · tablet 75(2) · flip 5(2) · fold 5(2)
│    buddy 5(1) · doorlock 3(2) · trifold 1(1)
└─  39  링크 자체 없음
     ├─ `[중복]` 표시    9   ← 저장소 전체 중복 9건이 «전부» 여기 있다
     ├─ 빈티지 의류      4   ← 리바이스 청바지. 다른 사업이다
     └─ 그 밖           26   ← 세트/멀티팩 변형(2세트·3세트·10p 2개입·30매)
                              + 신기종(Z플립8·폴드8·S26울트라)
```

### 2-1. ★「구성을 안 세워서」인가 「세울 수 없어서」인가 — 답은 **«칸은 있는데 안 채웠다»**

`cost_recipe.recipe_kind`가 매입품/조립품을 가르는 칸이고 **실제로 쓰이고 있다**:

| status | `recipe_kind` | 레시피 | SKU |
|---|---|---:|---:|
| approved | `assembly` | 19 | 392 |
| approved | **`imported_goods`** | **3** | **58** |
| draft | `assembly` | **78** | **474** |
| draft | `imported_goods` | **0** | **0** |

**draft 78개가 전건 `assembly` 기본값**이다. 승인 쪽엔 `imported_goods`가 3건 서 있으니 값 자체는 살아 있는데 draft 쪽만 아무도 안 채웠다.

⇒ n=19가 *"가르는 DB 신호가 없다"*고 한 것의 정확한 위치다 — **신호가 없는 게 아니라 비어 있다.**

### 2-2. 그래서 정본이 집단마다 다르다

| 집단 | 원가 정본이 되어야 할 것 | 소관 |
|---|---|---|
| 매입품 (474 중 일부) | **매입가** `cost_purchased_price` — 계산값이 **원리적으로 없다** | D-CPP-63 (진행 중 · S1 완료) |
| 조립품 초안 (474 중 나머지) | **계산값** `cost_standard` — 구성을 세워야 생긴다 | 트랙 A1 / A2 |
| 세트·신기종 26 | **계산값** — 레시피 자체가 없다 | 트랙 A1 / A2 |
| 빈티지 의류 4 | **매입가** | **소관 없음** — 다른 사업 |
| `[중복]` 9 | **없음 — 정리 대상** | **소관 없음** |

★ **「감시기 정본을 계산값으로 교체」는 이 표 앞에서 틀린 설계다.** 매입품엔 계산값이 원리적으로 없으므로, 감시기는 **「이 SKU의 정본이 무엇인지」를 먼저 알아야** 한다. 그 답이 `recipe_kind`인데 474건이 비어 있다.

⚠️ **474 중 매입품이 몇 개인지는 못 쟀다** — `recipe_kind` 미분류라서다. 📄 D-CPP-63은 318로 잡았고, 📄 n=19 라이브 파서는 「묶음 66 · 분류필요 437」을 냈는데 **그 둘이 같은 수를 세는지는 확인 안 했다.**
⚠️ `product_master.category`는 분류에 못 쓴다 — 963 중 **940이 NULL**.

## 3. ②「수정」 경로 — 22개가 있고, 드리프트 가드는 2개에만 걸려 있다

> 아래 22개 목록은 서브에이전트 전수 조사 결과다(좌표 병기). **★표시한 항목은 내가 직접 재현해 확인했다.**

### 3-1. `product_master.cost_price` — 4경로, 검사 수준이 서로 다르다

| # | 경로 | 좌표 | 드리프트 가드 | 이력 |
|---|---|---|---|---|
| 1 | `POST /api/products` | `routers/products.py:86` ★ | **없음** | 안 남음 |
| 2 | `PUT /api/products/{id}` | `routers/products.py:97` ★ (`setattr(p,k,v)` 111행) | **없음** | 안 남음 |
| 3 | `POST /api/products/upload` (시트1) | `routers/products.py:468·476` ★ | **있음** `:459` ★ | 안 남음 |
| 4 | `POST /api/products/upload-by-name` | `services/product_mapping_ingest.py:181·187` ★ | **있음** `:284` ★ | 안 남음 |

★★ **같은 칸에 「잠긴 문」과 「안 잠긴 문」이 공존한다.** #2(`PUT` API)로 버퍼값을 넣으면 아무도 안 막지만, #3(엑셀 업로드)은 같은 값을 막는다. #2는 `model_dump(exclude_unset=True)`를 **필드별 검사 없이 통째로 `setattr`** 한다.
★ **네 경로 모두 이력이 안 남는다** — `product_master`에 `*_history` 테이블이 없다.

### 3-2. 나머지 18경로 (원가 메뉴 체계)

| 대상 | 경로 수 | 성격 |
|---|---:|---|
| `cost_material` / `cost_material_price` | 7 | 사람 클릭 6 + **크론 1**(`cost_auto_refresh`, 매일) |
| `cost_recipe` / `cost_recipe_line` | 6 | 전부 사람 클릭 (크론 없음) |
| `cost_standard` | 1 | **사람이 직접 못 씀** — `recompute()` 파생만 |
| `cost_purchased_price` | 1 | 사람 클릭(묶음 확인) · **append-only 원장** |

📄 서브에이전트가 짚은 설계상 강점(내가 재확인 안 함): 크론 `cost_auto_refresh`는 **「이미 사람이 한 번 연결한 짝」의 반복만** 한다(신규 매칭 자동 생성 금지) · `cost_purchased_price` confirm은 **서버가 화면 목록을 재검사**한다 · 승인된 레시피는 라인 교체 경로 3개가 전부 거부한다.

📄 서브에이전트가 짚은 구멍(내가 재확인 안 함): `PATCH /api/cost/materials/{id}`가 **단가 0건인 종도 `status=approved`로** 바꿀 수 있다(*"승인은 단가가 있어야 한다"*는 검사 없음) · draft 상태에선 엑셀 재수입·픽·종 교체 세 경로가 **순서 없이 서로를 덮는다**(마지막 클릭이 이긴다).

### 3-3. ★드리프트 가드 호출부 전수 (내가 직접 확인)

```
쓰기를 «막는» 곳 — 2곳
  routers/products.py:459               ← /api/products/upload
  services/product_mapping_ingest.py:284 ← /api/products/upload-by-name

읽기(관측)만 — 1곳
  services/scheduler_health.py:631      ← summarize_drift, 배너용. 아무 쓰기도 안 막음

cost_menu 디렉터리 전체              → 0건
```

⇒ **금액을 쓰는 22개 경로 중 드리프트 가드가 걸린 것은 2개다.** 그리고 `cost_material_price`·`cost_recipe`·`cost_standard`·`cost_purchased_price`는 **이 가드의 존재 자체를 모른다.**

(설계상 당연한 면이 있다 — 가드가 대조하는 정본은 «`product_master.cost_price`용 엑셀 버퍼» 판정이지 원가 메뉴 체계의 개념이 아니다. 그러나 결과는 위와 같다.)

⚠️ 가드는 **fail-open**이다 — `try_load_truth()`가 스냅샷 파일을 못 찾으면 `None`을 돌려주고, 두 경로 모두 **검사를 조용히 건너뛴 채 계속 진행**한다(`products.py:430`). 📄 스냅샷 파일의 실재·최신 여부는 이번에 확인 못 했다.

## 4. 그래서 「목표 완성 계약」이 답해야 할 것

이 문서를 계약 §0(발단·면적)으로 그대로 쓸 수 있다. 계약이 답해야 할 질문은 다섯이다:

1. **배선을 놓는가** — 계산값·매입가 → `product_master.cost_price`. 지금은 코드에 없고 «금지선»으로 선언돼 있다(`cost_menu.py:15`). 금지선을 푸는 것이 계약의 첫 결정이다.
2. **정본을 SKU마다 어떻게 아는가** — `recipe_kind` draft 474건을 채우는 일. 사람 클릭인가(D-CPP-63 설계) 다른 신호인가.
3. **감시기가 무엇을 보는가** — 집단마다 정본이 다르므로 「엑셀 ↔ cost_price」 한 쌍이 아니라 「그 SKU의 정본 ↔ cost_price」여야 한다.
4. **22개 문 중 어디를 잠그는가** — 특히 `PUT /api/products/{id}`(#2)가 무검사로 열려 있다. 전부 잠글지, 정식 경로 하나만 남길지.
5. **이력을 남기는가** — `product_master.cost_price` 4경로 전부 이력이 없다. 「수정이 실수 없이」를 사후에 확인할 방법이 지금은 없다.

## 5. 이번에 확인 못한 것

- 474 중 매입품/조립품의 실제 비율 (`recipe_kind` 미분류)
- 18개 원가 메뉴 경로의 개별 검사 내용 — 서브에이전트 조사분을 내가 재현하지 않았다(좌표는 병기됨)
- 드리프트 가드가 대조하는 정본 스냅샷 파일의 실재·최신 여부
- 프론트가 UI 레벨에서 거는 가드(백엔드만 봤다)
- alembic 마이그레이션 중 우회 표현(`op.bulk_insert` 등)으로 값을 심는 것이 있는지

## 6. 재현

```bash
# 배선 부재 (핵심 주장)
grep -rnE "\.cost_price\s*=[^=]" backend/app/services/cost_menu/     # → 0건
sed -n '14,15p' backend/app/routers/cost_menu.py                     # → 「읽지도 쓰지도 않는다」

# 드리프트 가드 호출부 전수
grep -rn "screen_cost\|try_load_truth\|summarize_drift" backend/app --include='*.py'

# recipe_kind 미분류 (prod, 읽기 전용)
SELECT status, recipe_kind, COUNT(*) FROM cost_recipe GROUP BY 1,2;
```
