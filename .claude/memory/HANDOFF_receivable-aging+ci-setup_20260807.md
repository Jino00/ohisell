# 세션 인수인계: 미수금 연령(aging) 재판정 → CI 신설·병합 가드
> 저장 2026-08-07 13:3x KST · 트랙: **쿠팡 손익 정합** (착수 근거: `HANDOFF_settlement-axis-to-receiving-date_20260806.md` §6 최우선)
> 병합 완료 PR: **#231 · #232 · #233 · #234 · #236 · #238** · 신규 issue **#235**(별도 세션 진행 중)
> ⚠️ 이 세션은 계약(미수금 연령) 종료 후 Jino 지시로 **CI·lint·병합 가드까지 파생**했다. 아래 §2에서 두 덩어리를 나눠 읽을 것.

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (루트는 main 고정, 작업은 워크트리)
- 이 세션 워크트리: `.claude/worktrees/receivable-aging` · `ci-setup` · `ci-lint` · `dcpp20-verify` (전부 병합 완료, 정리 가능)
- prod: `ssh sellc.ohitech.co.kr` · 백엔드 8001 · DB `/home/ubuntu/ohisell/backend/ohisell.db` · **prod venv = Python 3.10.12**(개발 Mac은 3.14)
- 배포는 `scripts/safe_deploy.sh`만 · **PR 병합은 `scripts/safe_merge.sh`**(이번에 신설)
- 실행: 백엔드 테스트 `cd backend && python -m pytest -q`(★`pytest`가 아니라 `-m` — 교훈 #165) · 프론트 `npm ci && npx tsc -b && npm run test && npm run build`

## 2. 이번 세션 완료 목록

### 2-A. 계약 본체 — 미수금 연령 (PR #231, ref 50)
- ✅ **159라인 재판정 — 변동 0.** 백필된 정산라인 전량(계산서 480건 9,926라인, 종전 대조는 112건 3,563라인)과 재대조 → 정산수량 변동 0건, 총액 **8,033,970 → 8,033,970원(차 0)**
- ✅ **연령 기준일 선정** — 후보 6종의 커버리지를 먼저 재고 정했다. 채택 = ①하차일(제3자 한진) → ②센터도착일 → ③같은 PO 정산입고일 → ④판정불가
- ✅ **판정불가 = 2라인 88,590원**(추적기록 자체가 없는 것, 애초에 「청구 제외 권장」이던 라인) → **청구 157라인 7,945,380원은 전부 연령이 나온다**
- ✅ 연령 분포: 최소 23 · **중앙값 221** · 최대 374일. **180일 초과 86라인 5,050,445원(62.9%)**
- ✅ 「계산서 없는 입고완결 PO」 재판정 = **14건 1,428,550원**(발행 대기)
- ✅ 산출물 `docs/references/50_rocket_1p_receivable_aging_20260806.md` · `docs/references/data/50_coupang_unsettled_aging_20260806.csv` · 재현 스크립트 `tools/rocket_receivable_aging.py`
- ✅ **적대 리뷰 2라운드** — codex가 사용량 한도(리셋 08-09)로 3렌즈 전부 미실행 → Opus 1기 대체. 1차 5종 P1=0 → **2차 3렌즈에서 P1 2건 적출·수정**
- ✅ **codex 소급 리뷰 면제**(Jino 결정 08-07 06:50) — 부채로 남기지 않는다. ref 50 §9-1에 원문 인용

### 2-B. 이월 해소 — D-CPP-20 실효 범위 (PR #238, ref 50 §10)
- ✅ 두 축(작성일 vs 라인 실입고일)을 prod에서 직접 계산 비교 → **일별 305일 전건 동일 · 다른 날 0 · 총액 934,800,723원 동일 · 월별 diff 14개월 전부 0원**
- ✅ **D-CPP-20의 축 전환은 완전한 no-op**임을 확정(D-CPP-22)
- ✅ 인계에 적힌 월별 이동액(04 +10,494 / 05 +83,127 / 07 +830,500)의 정체 = **라인 없는 역발행 차감 계산서 3건 금액과 정확히 일치** → 이동이 아니라 **폴백분이 빠졌던 측정 아티팩트**
- ✅ `rocket_1p_channel_pnl.py`의 **틀린 근거 주석 교체**(로직 무변경)

### 2-C. 파생 작업 — CI 인프라 (PR #232·#233·#234·#236)
- ✅ **`.github/workflows/ci.yml` 신설** — 그전까지 이 repo엔 **CI가 하나도 없었다**(모든 PR "no checks reported"). backend 매트릭스 **py3.10·3.14** + frontend(`tsc -b` → vitest → `npm run build`)
- ✅ **`backend/requirements-dev.txt` 신설** — **테스트 의존성이 어디에도 선언돼 있지 않았다**(로컬은 pytest 전역 설치 덕). prod는 계속 `requirements.txt`만
- ✅ **lint 추가** — 실측 60건 중 6건 수정(죽은 eslint-disable 4 · `argsIgnorePattern` 설정 교정 · 삼항을 문으로 쓴 오용 1), 나머지 52건은 **래칫**(warn 강등 + `--max-warnings 54`)
- ✅ **`scripts/safe_merge.sh` 신설** — 브랜치 보호를 못 걸어서(§5) 도구로 대체. `CLAUDE.md` 금지선 절에 등재
- ✅ 번호 충돌 재번호 — 병행 세션이 ref 49·교훈 #155~#162를 선점 → 내 것을 **ref 50 · 교훈 #163·#164**로 뒤로 밀었다(트랙에 재부여 사실 기록)

## 3. 확정된 결정사항 (번복 금지)
- **D-CPP-21 — 연령 기준일 = 하차일(제3자) → 센터도착일 → 같은 PO 정산입고일 → 판정불가.** `receiving_finished_at`을 정본으로 쓰면 안 된다(원천이 90일 창만 줘서 6.4M이 판정불가가 된다)
- **증거등급(집하/도착/하차)과 기준일은 다른 축이다.** 등급은 "갔는가", 기준일은 "언제 갔는가" — 섞으면 ref 45 §17-6 오염이 재발한다. 교차표로 따로 낸다
- **D-CPP-22 — D-CPP-20 축 전환은 숫자를 바꾸지 않지만 되돌리지 않는다.** ①작성일=입고일은 관측이지 쿠팡의 보장이 아니다 ②라인은 SKU 그레인이라 재판정·연령·원가 결합이 얹힌다. ★"날짜를 고쳤다"가 아니라 **"연 것이 있다"**(정산 라인 9,926행)
- **한 계산서 = 하루치 입고, 작성일 = 그 입고일**(480건 전건, 예외 0. 독립 수집본으로 재현)
- **codex 소급 리뷰 면제**(Jino 08-07 06:50 원문: "자채 적대적 리뷰했으니까 codex 소급 리뷰는 취소해줘")
- **GitHub Free 유지**(Jino 결정) → 브랜치 보호 불가, `safe_merge.sh`로 대체
- **미수금 금액 8,033,970원 불변** — 이 세션은 축만 붙였다

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/references/50_rocket_1p_receivable_aging_20260806.md` | ★정본. §1 재판정 · §2 기준일 선정 · §3 결과 · §4 계산서 구조 · §5 발행대기 · §8·§9 적대 리뷰 · §10 D-CPP-20 검증 |
| `docs/references/data/50_coupang_unsettled_aging_20260806.csv` | 159라인 + 연령기준일·출처·경과일수·버킷 |
| `tools/rocket_receivable_aging.py` | 재현 스크립트(repo CSV + prod 1쿼리로 전 표 재생산) |
| `backend/app/services/coupang/rocket_1p_channel_pnl.py` | 계산서 축 귀속(주석 정정됨, 로직 무변경) |
| `scripts/safe_merge.sh` | ★PR 병합 가드(CI·충돌·체크0건). `CLAUDE.md`에 등재 |
| `.github/workflows/ci.yml` | CI. backend py3.10·3.14 매트릭스 + frontend |
| `backend/requirements-dev.txt` | 테스트 의존성(런타임과 분리) |
| `docs/tracks/active/track_coupang-promo-pnl.md` | D-CPP-21·22 + 번호 재부여 이력 |

## 5. 알려진 이슈 / 주의사항
- **★브랜치 보호는 이 플랜에서 불가.** classic protection·rulesets **둘 다 403**(`Upgrade to GitHub Pro or make this repository public`). private+Free이고 Jino가 무료 유지 결정 → **CI는 보이기만 하고 병합을 못 막는다.** 같은 403을 재시도하지 말 것(메모리 `github-branch-protection-needs-pro.md`)
- **★`python -m pytest`를 쓸 것.** 콘솔 스크립트 `pytest`는 CWD를 `sys.path`에 안 넣어 `import app`이 전부 죽는다(첫 CI 실행 227 collection errors). conftest.py·pytest.ini가 없어서 `-m`이 유일한 경로
- **lint 상한 54는 래칫이다.** 부채를 갚아 숫자가 줄면 `ci.yml`의 값도 같이 내릴 것(안 내리면 헐거워진다)
- **번호 충돌은 `next_ids.sh`가 정상 동작해도 난다** — 도구가 번호를 준 시점과 병행 세션이 가져간 시점이 겹치면 막을 수 없다. 이번엔 ref·교훈이 동시에 겹쳤다(ref 49→50, #155·#156→#163·#164). PR 본문·커밋 메시지의 옛 번호는 못 고치므로 **트랙에 재부여 사실을 남기는 것**이 정본 처리
- **적대 리뷰 1차가 P1=0을 냈는데 2차에서 P1 2건이 나왔다**(교훈 #164) — 자기 리뷰는 성실함이 아니라 **각도**를 바꿔야 두 번이 된다
- 시효 연수는 **적지 않았다** — 공식 1차 출처(law.go.kr) 접근 실패. 확인된 건 "최고령 374일"뿐

## 6. 다음에 할 작업 (미완료)
- [ ] **발행 대기 14건 1,428,550원 → 08-08경 재확인.** 계산서가 붙으면 "발행 대기" 가설이 라이브로 검증된다(붙지 않으면 그게 발견이다)
- [ ] **소멸시효 연수** — 규범형 수치라 공식 1차 출처 확인이 선행. law.go.kr은 SPA/Open API 키가 필요해 이번에 실패. Jino·세무 확인 사항
- [x] ~~**issue #235**(stale-closure 기간 되돌림)~~ — ✅ **완료**(08-07 13:29, PR #239 병합·issue CLOSED).
      ★창 실측이 닫혔다: `/api/sync/realtime` = **약 30초**(브라우저 라이브 5회 27.4~31.5s) —
      내가 이슈에 "초 단위 이상, 실측 안 함"으로 남긴 자리를 그 세션이 실제로 쟀다.
      진단도 일치: CommandCenter는 `syncAndLoad` **한 곳만** `selRef.current`에서 빠져 있었고,
      AdReport는 가드 전무라 `doFetch(f, t)` 코어로 분리해 둘 다 도입.
      라이브 증거 = 프론트 번들 로컬 서빙 + `/api/*`만 prod 프록시로 **실제 30초 창**에서 전/후 대조.
      회귀 테스트 `staleSyncSelection.test.tsx` 3건 신설(212→**215 passed**).
      ★**lint 래칫이 첫 사용에서 작동했다** — 892줄 추가에도 `--max-warnings 54`에서 0 errors/54 warnings.
- [ ] **오픽스 RG 배선** — 라이브 −13,869,712원. **계약 합격기준 유일 미충족**, 갈림길 2개로 Jino 답변 대기
- [ ] **원가 브리지 전환** — Jino 원가표 입력 대기(측정은 ref 47). 커버리지 79.26%, `rocket_product_cost_map` confirmed 184건 중 183건이 이름 유사도 자동 확정(교훈 #117 위반 상태)
- [ ] 계산서 라인이 SKU 그레인이라 **원가 결합 경로가 열렸다** — 라인 단위 원가 매칭으로 정합도를 더 올릴 수 있는지 검토
- [ ] lint 부채 52건(any 31 · react-refresh 11 · static-components 6 · set-state-in-effect 4) — 갚을 때 `ci.yml` 상한 동기화
- [ ] 워크트리 4개 정리 가능(`receivable-aging` · `ci-setup` · `ci-lint` · `dcpp20-verify`)

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_receivable-aging+ci-setup_20260807.md 읽고 이어서 작업해줘
```
