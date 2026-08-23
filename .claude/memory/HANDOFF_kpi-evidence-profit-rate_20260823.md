# 세션 인수인계: 근거자료 목표 — 이익률 탭 검산 배지 (n=3)
> 저장일시: 2026-08-23 22:2x KST · 세션 `37552016` · 체인 `근거자료` n=3
> 새 대화 시작 시 이 파일을 먼저 읽을 것

한 줄 요약: **근거 페이지 네 탭 중 이익률만 「✓ 카드와 일치」 배지가 없던 비대칭을 닫았다.** 값은 이미 맞았고 «화면이 스스로 판정하는 층»만 한 칸 비어 있었다.

## 1. 프로젝트 위치 및 환경
- 공유 메인 폴더: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
  — ⚠️ **로컬 `main`이 `origin/main`보다 164커밋 뒤처져 있고**, 살아 있는 세션들의 미커밋 수정이 얹혀 있다. **코드를 그 폴더에서 읽지 마라** (n=2의 D-1이 그대로 유효).
- 이 세션의 작업 워크트리: `~/.claude-worktrees/ohiselling/kpi-evidence-n3` (브랜치 `feat/kpi-evidence-profit-rate`, base `origin/main` `e65ece2d`)
  - `frontend/node_modules`는 형제 워크트리(`kpi-evidence`)로의 **심볼릭 링크**다 — 새 워크트리엔 node_modules가 없어 그렇게 붙였다. `npm install` 없이 vitest·tsc·build 전부 돌았다.
- prod: `https://sellc.ohitech.co.kr` · ssh `sellc.ohitech.co.kr` · **백엔드 활성 포트 :8001**(이번 블루그린 전환으로 8011 → 8001)
- 환경변수: 추가·변경 **없음** · DB 마이그레이션 **없음**

## 2. 이번 세션 완료 목록
- ✅ **체인 `근거자료` n=3 등록** — n=2가 `end_kst: 2026-08-23 21:05`로 정상 종결돼 있어 생존 판정 **CLEAR**. 가로채기 아님.
  - ★**등록부 생존 판정은 «로컬 ∪ origin/main»으로 봐야 한다** — 체인마다 최신 행이 «그 세션이 쓰고 있는 쪽»에만 있어 두 곳이 서로 다르게 뒤처진다. 이번 실측: `pao-논의`는 로컬 `null`인데 origin/main엔 `end_kst 21:36`(닫힘 — 훅 줄이 **오탐**), `sellc-원가-메뉴`는 로컬 n=2인데 origin/main엔 **n=4**, `접속-안정화`는 로컬이 **n=5**로 더 앞섰다. 한쪽만 보면 오탐·미탐이 둘 다 난다.
- ✅ **인계 §6 슬라이스 ① 구현** — `profit_rate_matches` 신설(백엔드) + 이익률 탭 배지 렌더(프론트) + 탭별 `data-testid`.
- ✅ **적대 리뷰 PASS(P1=0)** · 변이 8종 · P2 2건 **둘 다 채택**해 생존 변이 2종까지 죽임.
- ✅ **prod 배포·라이브 관측** — 백엔드 블루그린 0초, 프론트 CAS 통과, 화면에서 「✓ 카드와 일치」 실물 확인.
- ✅ 전체 백엔드 스위트 **6341 passed**(기지 실패 2파일 제외) — 회귀 없음.

## 2-1. 완료 QA (별도 Sonnet · 읽기 전용 · 2026-08-23 22:23 KST) — 판정 원문 그대로
```
판정(계약 CONTRACT_kpi_evidence_page.md §2-3): 달성 — 이익률 탭에 profit_rate_matches 체크가
신설되어 다른 세 탭과 같은 모양(행 재합산 ↔ totals 대조)으로 판정식이 짜였고, prod API·prod 화면
양쪽에서 「✓ 카드와 일치」가 실제로 관측된다. §2-3 "검산식은 항상 카드 값과 대조해 ✓/✗를 화면에
찍는다"의 네 칸 비대칭이 해소됨 (2026-08-23 22:23 KST)

판정(HANDOFF_kpi-evidence-relay_20260823.md §6 슬라이스 ①): 달성 — 인계가 지목한 손댈 좌표
(schemas.py:362·kpi_evidence.py:229·KpiEvidence.tsx:207·테스트 2층·api.ts)가 실제로 전부
수정됐고, 인계가 미리 설계해 둔 "공허 단언 회피 판정식"(행 합 == totals) 그대로 구현됐다.
적대 리뷰 변이에 이익률 탭 배지 렌더 절단(표면 변이)이 포함됐다는 기록도 확인됨 (22:23 KST)

확인 못 한 것: 없음 — 다만 적대 리뷰 「원 세션」 자체의 실시간 로그는 재현 불가한 과거 사건이라
PR 본문·커밋 로그·로컬 재현 테스트로 간접 확인했다는 점은 명시해 둔다.
이월: 없음.
```

## 2-3. 착지
- **완료 단계**: 커밋 → push → PR #386 → 적대 리뷰(PASS) → **머지** → **prod 배포(백엔드+프론트)** → 라이브 확인
- **멈춘 단계**: 없음
- **좌표**: 커밋 `0f74e320`(구현) · `986c868d`(P2 채택) → PR **#386** → 머지 **`f3b7cc01`**
- **리뷰 판정**: PASS · P1=0 · 변이 8종(표면 변이 #5 포함) · P2 2건 채택
- **CI**: 잡 3종 `conclusion=failure`인데 **`steps=0` · `log not found`** = **결제정지로 실행되지 않음**. 빨간불이 아니라 「안 돎」 — n=1·n=2와 동일. `safe_merge.sh 386 --force`로 병합, 자백 `$TMPDIR/safe_merge.log`에 `2026-08-23 22:18:22 KST PR#386 --force 병합 (verdict=NONE)`.
- **배포 좌표**: 백엔드 `safe_deploy.sh backend/app/schemas.py backend/app/services/kpi_evidence.py --restart` → CAS 통과 · 블루그린 `:8011 → :8001` 다운타임 0초 · 프론트 `safe_deploy.sh --frontend` → CAS 통과 · prod 스탬프 `commit=f3b7cc017b24…` == 내 HEAD.
- **착지 전제 검사**: L1 = 내 체인 `근거자료` CLEAR, 살아 있는 3체인 미접촉 · L2 = 커밋 전부 **경로 지정**(`git commit -- <경로>`) · L3 = 내 브랜치는 이 워크트리에만 · L4 = origin/main이 **6커밋 앞서 있어 머지 전에 병합**(충돌 0, 가져온 건 남의 HANDOFF·체인 파일뿐이라 코드 diff 0 — 재확인함) · L5 = 로컬 `main`이 공유 메인 폴더에 잡혀 있어 **「main에 세워둔다」 생략**.
- **정정 경로**: `git revert -m 1 f3b7cc01` 후 재배포. **force-push 금지**.

## 3. 확정된 결정사항
- **D-1. 이익률 검산의 판정식은 «행 합 ↔ totals» 모양이다.** `rate`는 `totals`의 두 값을 나눈 것이라 그걸 다시 `totals.profit_rate`와 비교하면 **같은 값을 두 번 읽는 공허 단언**이고, 그런 배지는 어떤 변이도 못 잡으면서 화면엔 언제나 ✓로 뜬다 — **초판 `checks`가 정확히 그 병이었다**(PR #367 리뷰 1R). ⇒ 분모·분자를 행에서 다시 세어 totals와 맞추고, 그 행 합으로 비율을 새로 만든다.
- **D-2. 행 합은 «끊기 전» 값으로 나눈다.** 먼저 원 단위 2자리로 끊고 나누면 그 반올림이 비율에 실려 소수 둘째 자리가 갈린다 — 실측 반례 `net=-1,161,247.5454… / basis=1,417,279` → 정순서 **−81.93%** / 선반올림 **−81.94%**. 그 날 화면은 「값은 맞는데 ✗」를 찍는다(D-7과 같은 병). 이 경계를 지키는 테스트를 넣었다.
- **D-3. 검산 배지엔 탭별 `data-testid`를 붙인다.** 텍스트로만 찾으면 「어느 탭 배지든 하나만 있으면 초록」이 되어, **한 탭의 렌더를 끊는 변이가 다른 탭 배지에 가려 살아남는다.**

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/kpi_evidence.py` | `build_kpi_evidence`의 `profit_rate_matches` 판정식 + `RATE_SCALE` |
| `backend/app/schemas.py` (`KpiEvidenceChecks`) | 네 칸이 전부 자기 배지를 갖는다 — 칸을 늘리면 프론트 렌더까지 가야 한다 |
| `frontend/src/pages/KpiEvidence.tsx` | `ProfitRateEvidence`의 총계 행 + `CheckBadge testId` |
| `backend/tests/test_kpi_evidence.py` | 공허 단언·분모 단독 분기·반올림 순서 경계 3종 |
| `backend/tests/test_kpi_evidence_http.py` | **`/kpi` ↔ `/kpi/evidence`를 둘 다 호출**하는 유일한 층 — 새 체크도 여기까지 와야 한다 |
| `frontend/src/pages/kpiEvidenceSurface.test.tsx` | 표면 층 — 배지가 «사람 눈에 닿는가» |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **공유 메인 폴더 164커밋 뒤처짐** — `KpiEvidence.tsx`는 그 폴더에 **아예 없다**. `git show origin/main:<경로>` 또는 워크트리에서 읽을 것.
- ⚠️ **CI는 결제정지로 실행되지 않는다**(잡 3종 `steps=0`). 회색·빨간 칩을 판정 근거로 쓰지 말 것.
- ⚠️ **기존 부채 2건**(`test_health_partial_sync.py`·`test_vendor_item_axis.py`)은 이 작업 이전부터 실패한다 — 이번 diff와 무관해 스위트에서 제외하고 돌렸다.
- ⚠️ **프론트 CAS 스탬프의 정본 경로는 `<repo>/.frontend-deploy-stamp`다** — `frontend/dist/.deploy-stamp`는 **레거시**다. 레거시 경로만 보면 「스탬프가 없다」로 오독한다(이번 세션이 한 번 그렇게 읽었다가 정정).
- ⚠️ 새 워크트리엔 `frontend/node_modules`가 없다 — 형제 워크트리로 심볼릭 링크하면 `npm install` 없이 돈다.

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문 — 계약 §1)**: *"대시보드 KPI 4칸(총매출·순이익·이익률·주문건수)을 누르면 **그 숫자가 어디서 나왔는지**를 채널별 구성과 검산식으로 보여주는 전용 페이지로 이동하고, 원래 화면(조회 기간·로켓 축 그대로)으로 돌아올 수 있다."*
  — **n=1에서 8/8 달성**, n=3에서 §2-3 비대칭까지 닫혔다. **이 계약에 남은 미달 항목은 없다.**
- **남은 슬라이스**:
- [ ] (선택·n=2에서 이월) 다일 조회·`rocket_basis=sales` 화면을 브라우저로 한 번 재현 — n=1 QA가 「확인 못 한 것」으로 남긴 항목. 200 응답까지는 확인됨. 이번 세션은 **단일일·settlement 축만** 라이브로 봤다.
- [ ] (기록만·타 트랙 소관) `_kpi_totals(db, …)`의 `db` 인자 미사용 → `track_coupang-2p-parity.md` / `rg_channel_pnl.py`가 「cost를 0으로 실으면 안 된다」 주석 바로 아래서 `cost = ZERO`를 싣는 모순 → `track_coupang-rg-replenishment.md` / `test_kpi_evidence_http.py` 로켓 픽스처의 비필수 키 불일치 3건
- ★**체인을 이어갈 일이 남았는가**: 계약이 종결됐으므로 이 체인은 **닫아도 되는 상태**다. 위 선택 항목만 남았고 둘 다 「없어도 목적은 달성」이다.

## 7. 새 세션 시작 프롬프트
```
/session-relay 근거자료
```
