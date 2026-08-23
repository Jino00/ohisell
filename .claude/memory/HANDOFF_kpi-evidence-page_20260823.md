# 세션 인수인계: 근거자료 목표 — 대시보드 KPI 카드 근거 페이지
> 저장일시: 2026-08-23 19:55 KST · 세션 `8e967bcb` · 체인 `근거자료` n=1
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(작업): `/Users/jino/.claude-worktrees/ohiselling/kpi-evidence` (브랜치 `feat/kpi-evidence`, **병합 완료**)
- 공유 메인 폴더: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (다른 세션 미커밋 작업 다수 — 건드리지 않았다)
- 실행: `bash scripts/init.sh` (백엔드 :8000 / 프론트 :5173)
- prod: `https://sellc.ohitech.co.kr` · ssh `sellc.ohitech.co.kr` · **백엔드 활성 포트 :8011**(블루그린 전환됨)
- prod dist: `/home/ubuntu/ohisell/frontend/dist`
- 환경변수: `.env`(백엔드) — 이번 작업에서 추가/변경 **없음**

## 2. 이번 세션 완료 목록
- ✅ **`backend/app/services/kpi_evidence.py` 신설(208줄)** — 채널 행 + `_kpi_totals` 산출 → 근거 한 벌. **아무것도 새로 계산하지 않는다**(카드와 같은 경로 재사용).
- ✅ **`backend/app/routers/dashboard.py`** — `GET /api/dashboard/kpi/evidence` 신설. `dashboard_kpi`와 **같은 두 줄**(`_channel_rows` → `_kpi_totals`)을 쓴다. 추가로 `_merge_rg_summary`에서 3P 행의 `payable_vat`를 광고비 매입세액만큼 동기화(리뷰 P1-4).
- ✅ **`backend/app/schemas.py`** — `KpiEvidence`/`KpiEvidenceRow`/`KpiEvidenceTotals`/`KpiEvidenceChecks` 4종.
- ✅ **`backend/app/services/profit_calculator.py`** — `calculate_channel_summary` 행에 `payable_vat` 노출(net과 **같은 값**을 한 번만 계산). 순이익 계산식 자체는 불변.
- ✅ **`frontend/src/pages/KpiEvidence.tsx` 신설(약 510줄)** — 4탭(총매출·순이익·이익률·주문건수) 근거 화면. 검산식·✓/✗ 배지·하한 자백·분모 차액·주문건수 제외 사유·「—는 0원이 아니라 모른다」 각주.
- ✅ **`frontend/src/pages/Dashboard.tsx`** — KPI 4칸을 `<Link>`로 감싸 `/kpi-evidence`로 보낸다(조회 조건을 URL에 실어서). `conditionsFromSearch()` 신설로 **돌아왔을 때 조건을 이어받는다**.
- ✅ **`frontend/src/App.tsx`** — `/kpi-evidence` 라우트.
- ✅ **`frontend/src/lib/api.ts`** — `KpiEvidence` 타입 + `fetchKpiEvidence()`.
- ✅ 테스트 **30종 신설**: `backend/tests/test_kpi_evidence.py`(12) · `backend/tests/test_kpi_evidence_http.py`(10) · `frontend/src/pages/kpiEvidenceSurface.test.tsx`(16) · `frontend/src/lib/kpiEvidenceRequest.test.ts`(2).
- ✅ 기존 테스트 2파일(`rgSettlementAxisSurface.test.tsx`·`bulkPanelReachesTheUser.test.tsx`)을 `MemoryRouter`로 감쌈(Dashboard가 Router 컨텍스트를 쓰게 된 결과 — 재는 것은 불변).
- ✅ **prod 배포**: 백엔드 4파일 블루그린 재시작(19:2x, 다운타임 0초) · 프론트 `--frontend`(19:32, CAS 1회 거부 → `origin/main` 병합·**재빌드** 후 정상 통과).
- ✅ **PR #367 병합**(`9d83b23a`).

## 2-1. 완료 QA
- **작업 목적(계약 §1 원문)**: "대시보드 KPI 4칸(총매출·순이익·이익률·주문건수)을 누르면 **그 숫자가 어디서 나왔는지**를 채널별 구성과 검산식으로 보여주는 전용 페이지로 이동하고, 원래 화면(조회 기간·로켓 축 그대로)으로 돌아올 수 있다."
- **합격기준(원문)**: 계약 §4 A1~A8 — 정본 `docs/contracts/CONTRACT_kpi_evidence_page.md`(체크박스 **8/8**, 증거 좌표 병기).
- **대조 대상 2개를 각각 판정했다**(§2 — 하나가 나머지를 덮으면 「달성」만 남는다):

### 판정(계약 §4): **달성**
> A1~A8 전항목 라이브 증거로 확인, §1 「안 하는 것」 침범 없음·§3 금지선 5개 준수 (2026-08-23 19:50 KST)

| 항목 | 관측(KST) | 판정 |
|---|---|---|
| A1 총매출 | 19:38 브라우저 클릭 → `/kpi-evidence?metric=revenue`, 채널 합계 1,670,990원 = 카드, 「✓ 카드와 일치」 | 달성 |
| A2 순이익 | 19:38 검산식 렌더·−285,507원 일치·✓, 「⚠️ 이 순이익은 하한입니다 — … 570,405원만 비용으로 반영」 | 달성 |
| A3 이익률 | 19:39 분자 −285,507원·분모 1,528,900원 둘 다 숫자, 「분모는 총 매출이 아닙니다 — 142,090원은 … 분모에서 빠졌습니다」 | 달성 |
| A4 주문건수 | 19:39 84건 = 카드·✓, 「로켓배송 1P는 이 카드에서 빠집니다 — 매입 구조라 주문 개념이 없고 …」 | 달성 |
| A5 되돌아가기 | 19:40 복귀 URL `?date_from=2026-08-22&date_to=2026-08-22&rocket_basis=settlement`, 화면 조건 유지 | 달성 |
| A6 「—」 | 19:38~39 RG·로켓1P 원가/부가세/분담금이 「0원」 아닌 「—」 + 각주 상시 노출 | 달성 |
| A7 카드 불변 | 19:36 prod curl `1670990.00 / -285507.2568… / -18.67 / 84` = 배포 전 스크린샷(17:49) 원 단위 일치 | 달성 |
| A8 리뷰·변이 | 적대 리뷰 **1R FAIL(P1 4) → 2R FAIL(P1-1) → 3R RESOLVED(P1=0)**, 표면 절단 변이 5종 전건 사망 | 달성 |
| §3 금지선 | ①카드 4값 불변 ②근거용 계산 경로 신설 0 ③`safe_deploy.sh`·CAS 재빌드(매니페스트 `forced:false`) ④살아 있는 세션 미접촉 ⑤`git add -A` 흔적 없음 | 전건 준수 |

### 판정(Jino 2026-08-23 17:49 지시 원문): **달성**
> 지시 6조각 전부 라이브 관측 (2026-08-23 19:50 KST)

①4칸을 «누르면» ②근거자료(계산페이지)가 «나온다» ③별도 페이지(`/kpi-evidence`) ④되돌아오기 ⑤채널별 구성 ⑥검산식 — **6/6 브라우저에서 직접 확인.**

- **미달·미판정 항목**: 없음.
- **QA가 확인 못 한 것(원문 그대로)**: ①적대 리뷰 3라운드 어디서도 prod 라이브·브라우저 실화면을 안 봤다(QA가 메움) ②다일 조회·`rocket_basis=sales` 화면은 200 응답만 확인했고 브라우저 클릭 재현은 안 했다 ③`git add -A` 미사용은 커밋 파일 목록의 정합성으로 **간접** 확인 ④리뷰어가 「회귀 아님」으로 처분한 기존 부채 3건은 QA도 별도 검증하지 않았다.
- **목적 전환**: 없음(`🔁` 선언 0회).

## 2-2. 트랙 진행률
해당 없음 — 앵커에 `트랙:` 줄이 없다. 이 작업은 대시보드 표면 작업으로 어느 활성 트랙에도 귀속되지 않는다(계약 §6 「종료 조건」에 명시).

## 2-3. 착지
- **완료 단계**: 커밋 → push → PR → 적대 리뷰 → **머지까지 전부 완료**
- **멈춘 단계**: 없음
- **재개 명령**: 해당 없음
- **좌표**: 커밋 `3c5e9d5a`(구현) · `52f84fe6`(1R 수정) · `42cc6462`(2R 수정) · `17d8ece6`(origin/main 병합) → PR **#367** → 머지 **`9d83b23a`**
- **리뷰 판정**: **PASS(P1=0)** — 3라운드, 변이 누적 30종
- **CI**: 잡 3종 전부 `steps=0`·로그 없음 = **결제정지로 실행되지 않음**(빨간불이 아니라 「안 돎」). `safe_merge.sh 367 --force`로 병합, 자백이 `$TMPDIR/safe_merge.log`에 기록됨.
- **정리**: 로컬 `main`이 공유 메인 폴더에 체크아웃돼 있어(착지 검사 **L5**) 「저장소를 main에 세워둔다」는 **생략**. 브랜치 `feat/kpi-evidence`는 내 워크트리에만 있어 삭제 가능하나 남겨 둠.

## 3. 확정된 결정사항
- **D-1. 근거는 카드와 «같은 경로»에서만 나온다** — `_channel_rows()` → `_kpi_totals()` 재사용, 근거용 재계산 금지. (D-22 실사고: 경로가 둘이면 카드 569,635원 vs 표 −28,253원처럼 갈라진다.)
- **D-2. 행이 «안 가진» 항목은 0이 아니라 `null` → 화면 「—」.** 0으로 채우면 「원가 0원」 거짓말.
- **D-3. 「원가를 모른다」의 판정 신호는 `net_scope == "ad_only"` 또는 `net_profit is None`이다.** `net_profit is None`만 보면 **로켓1P를 못 잡는다**(producer가 하한을 자기가 만들어 넣는다). `net_scope not in (None, AD_ONLY)`로 쓰면 **주문 축 행 전부**를 삼킨다(그 행들은 `net_scope` 키가 아예 없다).
- **D-4. producer(`rocket_1p_channel_pnl`·`rg_channel_pnl`)의 `cost`는 안 고쳤다** — 다른 화면이 이미 읽고 있고 계약이 「숫자 자체 수정」을 금지했다. 근거 화면에서만 «모른다»로 내린다.
- **D-5. 검산 배지는 `net_matches`만 본다** — `net_fully_explained`까지 묶으면 기본 축에서 **매일 ✗**가 뜬다.
- **D-6. 잔차는 «더하기»다** — `net = 매출 − 항목합 + residual`.
- **D-7. 잔차·검산 비교는 원 단위 2자리로 끊고 음의 0을 편다** — `payable_vat`의 `×10/110`이 28자리 먼지를 남긴다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/contracts/CONTRACT_kpi_evidence_page.md` | **정본 계약** — 목표·판단기준·금지선·A1~A8(8/8 체크·증거 병기) |
| `backend/app/services/kpi_evidence.py` | 근거 산출(재계산 없음)·`_cost_is_unknown`·잔차 |
| `backend/app/routers/dashboard.py` | `/api/dashboard/kpi/evidence` · `_merge_rg_summary`의 `payable_vat` 동기화 |
| `backend/app/schemas.py` | `KpiEvidence*` 4종 — **칸을 지우면 화면 자백이 조용히 사라진다** |
| `frontend/src/pages/KpiEvidence.tsx` | 근거 화면 4탭 |
| `frontend/src/pages/Dashboard.tsx` | KPI 카드 → 링크 · `conditionsFromSearch()` |
| `backend/tests/test_kpi_evidence_http.py` | **`/kpi` ↔ `/kpi/evidence` 4값 원 단위 대조** — 「✓ 카드와 일치」가 카드를 실제로 보게 하는 유일한 층 |
| `frontend/src/lib/kpiEvidenceRequest.test.ts` | 클라이언트 URL 조립(`rocket_basis` 누락 방지) |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **픽스처가 producer 실산출과 갈라지는 병을 이 작업에서 두 번 밟았다.** 1R 지적이 정확히 그것이었는데 1R 수정에서 **새로 만든** 픽스처가 또 `net_profit: None`(producer가 안 만드는 모양)이었다. → **근거·요약 행을 다루는 테스트를 쓸 땐 producer를 실제로 한 번 돌려 모양을 확인할 것.**
- ⚠️ **`checks`가 자기 자신을 비교하면 공허 단언이 된다.** 초판의 「✓ 카드와 일치」는 **카드를 본 적이 없었고**, 「두 번째 계산 경로 신설」 변이가 전건 초록으로 살아남았다. 지금은 `test_kpi_evidence_http.py`가 실제로 두 엔드포인트를 부른다.
- ⚠️ **프론트 CAS는 이 세션에서도 1회 거부됐다.** prod dist 커밋 `1d382d52`가 **이미 `origin/main`에 병합된 것**임을 확인하고 정규 절차(병합 → **재빌드** → 재배포)로 갔다. 강제하지 않았다(매니페스트 `forced:false`).
- ⚠️ **기존 부채 2건**(`test_health_partial_sync.py`·`test_vendor_item_axis.py`의 health 라우트)은 이 작업 이전부터 실패한다 — 다른 워크트리에서도 동일 재현. 이 diff와 무관.
- ⚠️ 훅이 띄우는 `[체인] ⛔ 살아 있는 세션` 줄은 **공유 메인 폴더의 낡은 사본**을 읽는다. 이 세션 착수 시 4건 중 3건이 오탐이었다 — `git show origin/main:<체인파일>`로 대조할 것.

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문)**: 계약 §1 목표는 **달성됐다**(A1~A8 8/8). 아래는 그 위의 «폴리시」이지 미완 슬라이스가 아니다.
- **남은 슬라이스**:
- [ ] **이익률 탭에만 「✓ 카드와 일치」 배지가 없다** — 다른 세 탭 대비 비대칭이고 계약 §2-3(「검산식은 항상 ✓/✗를 찍는다」)의 문자와도 어긋난다. 붙이려면 `checks`에 `profit_rate_matches`를 더해야 하니 **스키마 + 적대 리뷰가 다시 필요하다**. → **묻지 말고 진행해도 되는 건이다**(목표·범위 불변 · 되돌릴 수 있음 · 사후 가시성·정정 경로·근거 보존 3요소 충족 — 전역 §2 3문 검사 ③).
- [ ] (선택) 다일 조회·`rocket_basis=sales` 화면을 브라우저로 한 번 재현 — QA가 「확인 못 한 것」으로 남긴 항목. 200 응답까지는 확인됨.
- [ ] (기록만·타 트랙 소관) `_kpi_totals(db, …)`의 `db` 인자 미사용 → 쿠팡 손익정합 트랙 / `rg_channel_pnl.py`가 「cost를 0으로 실으면 안 된다」 주석 바로 아래서 `cost = ZERO`를 싣는 모순 → `track_coupang-rg-replenishment.md` / `test_kpi_evidence_http.py` 로켓 픽스처의 비필수 키 불일치 3건(`unmapped_revenue`·`promo_burden`·`cost_coverage`)

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_kpi-evidence-page_20260823.md 읽고 이어서 작업해줘
```
