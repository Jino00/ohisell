# 세션 인수인계: 근거자료 목표 — 릴레이만 하고 닫은 세션 (n=2)
> 저장일시: 2026-08-23 21:00 KST · 세션 `18b062a0` · 체인 `근거자료` n=2
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ⚠️ **이 세션은 코드를 한 줄도 바꾸지 않았다.** 릴레이 등록 + 인계 주장 실측만 했다. 홍보할 것이 없다.

## 1. 프로젝트 위치 및 환경
- 공유 메인 폴더: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
  — **로컬 `main`이 `origin/main`보다 143커밋 뒤처져 있고**, 살아 있는 세션들의 미커밋 수정 14파일이 얹혀 있다.
- 이 세션의 작업 워크트리: `~/.claude-worktrees/ohiselling/relay-n2` (브랜치 `docs/relay-n2-handoff`, base `origin/main` `9cf6a366`)
- n=1의 작업 워크트리: `~/.claude-worktrees/ohiselling/kpi-evidence` (브랜치 `feat/kpi-evidence`, 병합 완료)
- 실행: `bash scripts/init.sh` (백엔드 :8000 / 프론트 :5173)
- prod: `https://sellc.ohitech.co.kr` · ssh `sellc.ohitech.co.kr` · 백엔드 활성 포트 **:8011**
- 환경변수: 이번 세션 추가/변경 **없음**

## 2. 이번 세션 완료 목록
- ✅ **체인 `근거자료`에 n=2 등록** (`.claude/memory/chains/근거자료.jsonl`) — n=1은 `end_kst: 2026-08-23 20:02`로 정상 종결돼 있었다(생존 판정 CLEAR). 가로채기 아님.
- ✅ **n=1 HANDOFF·계약 정독** — `HANDOFF_kpi-evidence-page_20260823.md` · `docs/contracts/CONTRACT_kpi_evidence_page.md`(8/8 종결).
- ✅ **인계 §6 남은 슬라이스 ①의 주장을 실측으로 검증** (읽기 전용, `git show origin/main:` — 로컬이 143커밋 뒤처져 로컬 파일을 근거로 쓸 수 없었다):
  - `backend/app/schemas.py:362` `class KpiEvidenceChecks` = **4필드**(`revenue_matches`·`net_matches`·`order_count_matches`·`net_fully_explained`) — **`profit_rate_matches` 없음**.
  - `backend/app/services/kpi_evidence.py:229` `checks` 조립도 **같은 4개만** 만든다.
  - `frontend/src/pages/KpiEvidence.tsx` — `CheckBadge`가 붙는 자리는 `:92`(매출) `:143`(순이익) `:275`(주문건수) **3곳뿐**. `ProfitRateEvidence`(`:207~257`)에는 **없다**.
  - ⇒ **주장은 유효하다(유령 아님).** 이익률 탭에만 ✓/✗ 배지가 없다.
- ✅ **훅 `[체인] ⛔` 3줄 대조** — `pao-논의`·`sellc-원가-메뉴`·`접속-안정화` 셋 다 `origin/main` 등록부에서도 `end_kst: null` = **진짜 생존**(이번엔 오탐 아님). 셋 다 미접촉.
- ❌ **앵커를 쓰지 않았다** — 작성 직전 Jino가 중단시켰다(도구 거부). 그래서 코드 슬라이스는 **착수 자체를 안 했다**.

## 2-1. 완료 QA
해당 없음 — **앵커가 없다**(작성 시도가 거부됨). 전역 §2 「작업의 경계는 문서가 정한다 — 계약도 앵커도 없으면 완료 QA 대상이 아니다(잡일)」. 이 세션은 릴레이·조사 세션이다.

> ⚠️ 오해 방지: 계약 `CONTRACT_kpi_evidence_page.md`는 **n=1에서 이미 8/8로 종결**됐다(2026-08-23 19:50, 완료 QA 달성/달성). 이 세션이 그 계약을 이어받아 미달로 만든 것이 **아니다**.

## 2-3. 착지
- **완료 단계**: 커밋 → push → PR → (리뷰 생략) → **머지까지 전부 완료**
- **멈춘 단계**: 없음
- **재개 명령**: 해당 없음
- **좌표**: 커밋 `57fa87e5` → PR **#376** → 머지 **`6bf536fb`** / 착지 절·등록부 마감 커밋은 PR **#380**
- **리뷰 판정**: ⚠️ **리뷰 생략: 기록물만** — `.claude/memory/HANDOFF_kpi-evidence-relay_20260823.md` · `.claude/memory/chains/근거자료.jsonl` (코드 0파일, 전역 §6 기록물 예외)
- **CI**: 잡 3종 전부 `conclusion=failure`인데 **`steps=0` · 로그 없음**(`gh run view 32638128676 --log-failed` → `log not found`) = **결제정지로 실행되지 않음**. 빨간불이 아니라 「안 돎」 — n=1과 동일. `safe_merge.sh 376 --force`로 병합, 자백이 `$TMPDIR/safe_merge.log`에 기록됨(`2026-08-23 21:00:34 KST PR#376 --force 병합 (verdict=FAIL)`).
- **착지 전제 검사 결과**: L1 = 내 대상 체인 `근거자료`는 CLEAR(n=1 `end_kst` 채워짐), 살아 있는 3체인은 미접촉 → «예» · L2 = 공유 메인 폴더에 남의 미커밋 14파일 → **작업을 그 폴더에서 하지 않고 `origin/main`에서 딴 워크트리 `~/.claude-worktrees/ohiselling/relay-n2`에서 수행**, 커밋은 경로 지정 · L3 = 내 브랜치는 이 워크트리에만 · L4 = 워크트리 base가 `origin/main`이라 0 · L5 = 로컬 `main`이 공유 메인 폴더에 잡혀 있음 → 「저장소를 main에 세워둔다」 **생략**(그 폴더는 143커밋 뒤처진 채 두고 건드리지 않았다. 다음 세션은 `git switch -c <새> origin/main`으로 원격을 명시해 갈라질 것).
- **정리**: 브랜치 `docs/relay-n2-handoff`·`docs/relay-n2-landing`은 이 워크트리에만 있다. 워크트리 제거는 다음 세션 재량.

## 3. 확정된 결정사항
- **D-1. 공유 메인 폴더에서 커밋·merge 하지 않는다 (이 시점 실측 근거).** `git diff --name-only HEAD origin/main` ∩ `git status`의 수정 파일 = **12개 겹침**(`LESSONS_LEARNED.md`·`pao-논의.jsonl`·`쿠팡-손익정합.jsonl`·`reflection_loop.py`·`wisdom_scorecard.py`·`test_naver_diary_reflection.py`·`TRACKS.md`·`82_pao_north_star`·`track_naver-ad-optimization.md`·`api.ts`·`NaverAdOptimizationConsole.tsx`·`naverAdWisdomScorecardPanel.test.tsx`). 여기서 `git merge origin/main`을 하면 **살아 있는 세션 3기의 미커밋 작업을 덮는다.** ⇒ 착지는 `origin/main`에서 딴 새 워크트리에서만 한다.
- **D-2. 로컬 `main`을 pull 하지 않았다** — 같은 이유. 전역 §6 「저장소를 main에 세워둔다」의 실질은 «다음 세션이 `origin/main`에서 갈라질 수 있는 상태»이고, 브랜치를 딸 때 `origin/main`을 명시(`git switch -c <새> origin/main`)하면 충족된다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/contracts/CONTRACT_kpi_evidence_page.md` | **정본 계약**(8/8 종결) — §2-3 「검산식은 항상 카드 값과 대조해 ✓/✗를 화면에 찍는다」가 남은 슬라이스의 근거 |
| `.claude/memory/HANDOFF_kpi-evidence-page_20260823.md` | **n=1 인계** — §3 D-1~D-7(설계 결정 7건)·§5 주의사항이 그대로 유효 |
| `backend/app/schemas.py:362` | `KpiEvidenceChecks` — 여기에 `profit_rate_matches`를 더해야 한다 |
| `backend/app/services/kpi_evidence.py:229` | `checks` 조립부 + `_q()`(원 단위 2자리·음의 0 펴기) |
| `frontend/src/pages/KpiEvidence.tsx:207` | `ProfitRateEvidence` — 배지가 빠진 자리 |
| `backend/tests/test_kpi_evidence_http.py` | **`/kpi` ↔ `/kpi/evidence` 원 단위 대조** — 「✓ 카드와 일치」가 카드를 실제로 보게 하는 유일한 층. 새 체크도 여기 층까지 와야 한다 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **공유 메인 폴더 143커밋 뒤처짐** — 그 폴더의 파일을 근거로 코드를 읽으면 **없는 파일·낡은 내용**을 본다(`KpiEvidence.tsx`는 로컬에 아예 없다). `git show origin/main:<경로>`로 읽을 것.
- ⚠️ **n=1이 남긴 병 2종은 아직 유효한 함정이다**: ①「픽스처가 producer 실산출과 갈라짐」을 **한 세션에서 두 번** 밟았다 — 근거·요약 행 테스트를 쓸 땐 producer를 실제로 한 번 돌려 모양을 확인할 것 ②「`checks`가 자기 자신을 비교하면 공허 단언」 — 새 `profit_rate_matches`도 **자기 payload끼리 비교하면 초판과 같은 P1이 난다**.
- ⚠️ **기존 부채 2건**(`test_health_partial_sync.py`·`test_vendor_item_axis.py`의 health 라우트)은 이 작업 이전부터 실패한다 — 이 diff와 무관.
- ⚠️ **CI는 결제정지로 실행되지 않는다**(잡 3종 `steps=0`). 회색 칩을 초록으로 오독하지 말 것 — `safe_merge.sh`가 그걸 막는다.

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문 — 계약 §1)**: *"대시보드 KPI 4칸(총매출·순이익·이익률·주문건수)을 누르면 **그 숫자가 어디서 나왔는지**를 채널별 구성과 검산식으로 보여주는 전용 페이지로 이동하고, 원래 화면(조회 기간·로켓 축 그대로)으로 돌아올 수 있다."*
  — 이 목표는 n=1에서 **달성됐다**(A1~A8 8/8). 아래는 그 위의 «폴리시»다.
- **남은 슬라이스**:
- [ ] **이익률 탭에만 「✓ 카드와 일치」 배지가 없다** — 실측으로 **유효 확인**(위 §2). 계약 §2-3의 문자와 어긋나는 비대칭. → **묻지 말고 진행해도 되는 건이다**(목표·범위 불변 · 되돌릴 수 있음 · 사후 가시성·정정 경로·근거 보존 3요소 충족 — 전역 §2 3문 검사 ③).
  - 손대야 하는 곳(실측 좌표): `schemas.py:362` `KpiEvidenceChecks` + `kpi_evidence.py:229` `checks` + `KpiEvidence.tsx:207` `ProfitRateEvidence` + 테스트 2층(`test_kpi_evidence.py`·`test_kpi_evidence_http.py`) + `frontend/src/lib/api.ts` 타입. **5~6파일 = 중형 → 앵커 필수**, 계약은 §2-3 안이라 새 계약 불필요.
  - ★**공허 단언을 피하는 판정식(이 세션이 설계만 해 둠, 미구현)**: `profit_rate_matches`를 `totals.profit_rate`와 자기 자신으로 비교하면 안 된다. 다른 세 체크와 **같은 모양**(행 합 ↔ totals)으로 쓴다 — ①`net_profit`이 있는 행들의 `net_basis_revenue` 합 == `totals.basis_revenue` ②같은 행들의 `net_profit` 합 == `totals.net_profit` ③`net/basis*100`을 2자리로 끊은 값 == `totals.profit_rate`. 셋 다 `_q()`로 원 단위 2자리·음의 0 펴기를 거친다(D-7: `payable_vat`의 `×10/110`이 28자리 먼지를 남긴다).
  - 적대 리뷰 변이 중 **최소 1개는 이익률 탭 배지 렌더를 끊는 표면 변이**여야 한다(전역 §4).
- [ ] (선택) 다일 조회·`rocket_basis=sales` 화면을 브라우저로 한 번 재현 — n=1 QA가 「확인 못 한 것」으로 남긴 항목. 200 응답까지는 확인됨.
- [ ] (기록만·타 트랙 소관) `_kpi_totals(db, …)`의 `db` 인자 미사용 → `track_coupang-2p-parity.md` / `rg_channel_pnl.py`가 「cost를 0으로 실으면 안 된다」 주석 바로 아래서 `cost = ZERO`를 싣는 모순 → `track_coupang-rg-replenishment.md` / `test_kpi_evidence_http.py` 로켓 픽스처의 비필수 키 불일치 3건(`unmapped_revenue`·`promo_burden`·`cost_coverage`)

## 7. 새 세션 시작 프롬프트
```
/session-relay 근거자료
```
