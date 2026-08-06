# 세션 인수인계: frontend-refetch + deploy-cas
> 저장일시: 2026-08-06 17:15 (KST) — 오후 작업(PR #225) 반영해 갱신
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
  (루트 폴더는 **main 고정** — 브랜치 작업은 `.claude/worktrees/`)
- prod: `https://sellc.ohitech.co.kr` · 서버 `sellc.ohitech.co.kr:/home/ubuntu/ohisell`
- 프론트 빌드: `cd frontend && npm run build` (워크트리에선 `node_modules` 심볼릭 링크 필요)
- 배포: **`scripts/safe_deploy.sh` 만** — 백엔드 `... --restart` / 프론트 `--frontend`
- 백엔드 테스트: `cd backend && python3 -m pytest tests/...`
- 환경변수: `backend/.env`(`DATABASE_URL` 등) — 값은 저장소 밖

## 2. 이번 세션 완료 목록
PR 5건 병합(#213·#214·#217·#219·#221), 전부 prod 반영.

- ✅ **GFA 신선도 배너 거짓 빨강 제거** (#213) — `backend/app/routers/ad_costs.py`
  `/gfa/status`가 `source='gfa:쇼핑'`(수동 CSV)만 읽어 자동 적재분(`gfa:advoost`·`gfa:da`,
  08-03부터 매일 07:10)을 못 보고 "63일 전 ⚠️"를 띄우고 있었다. `gfa:%` 계열 전체 +
  `auto`/`manual`/`by_source` 분해. `days`는 `COUNT(*)`→`COUNT(DISTINCT ad_date)`.
  프론트 문구도 자동 수집 기준으로(`NaverOps.tsx`·`Settings.tsx`). 가드 테스트 4건.
  ⚠️**이 수정은 이미 3판으로 교정됐다 — 아래 §5 참조.**
- ✅ **기간 버튼 재조회 복구 + 진행 표시** (#214) — `NaverOps.tsx`·`CoupangOps.tsx`·`Busy.tsx`(신설)
  원인 둘: ①요약 로드 `useEffect` 의존성이 `[]`라 마운트 1회만 실행 ②지연 콜백 stale closure.
  `loadRef`/`selRef`·`reqSeq`·`MIN_BUSY_MS=350` 도입. 진행 표시 공용 조각 `components/Busy.tsx`.
- ✅ **프론트 배포 CAS** (#217→#219) — `scripts/safe_deploy.sh`·`frontend/scripts/stamp-build.mjs`·
  `frontend/package.json`·`scripts/tests/safe_deploy_frontend_test.sh`·`backend/tests/test_safe_deploy_frontend.py`
  빌드가 `dist/.build-stamp`를 심고, 배포는 prod 스탬프가 내 HEAD의 조상인지 검사해 아니면 거부.
  회귀 36검사(F1~F11).
- ✅ **`runPreview` stale closure 제거** (#221) — `NaverOps.tsx` 16곳 치환 + 렌더 중 ref 대입을
  `useEffect`로 이동(`CoupangOps.tsx` 포함).
- ✅ **「오늘」 광고비를 당일 누적으로** (#225) — `backend/app/routers/naver_ops.py`·`scheduler.py`·
  `frontend/src/lib/api.ts`·`NaverOps.tsx`·`backend/tests/test_naver_ops_today_ad_spend.py`
  Jino 지적("오늘 화면 광고비가 어제 걸 물고 오는 것 같다")에서 출발. `ad_costs`에 오늘 행이
  없어 **어제 전일치**를 넣고 라벨만 달아뒀고, 그 탓에 이익이 「오늘 매출 − 어제 광고비」였다.
  실측: 화면 −202,434원(−23.2%) 적자 → 실제 **+157,683원 흑자. 부호가 뒤집혀 있었다.**
  당일 누적은 이미 `naver_hourly_snapshot`(매시 :05, /stats datePreset=today)에 있었고
  손익 쿼리가 그걸 안 봤을 뿐. 축 교차검증(08-05): 스냅샷 675,090 vs ad_costs SA 675,089.
  응답에 `ad_basis{kind,as_of,scope}` 추가, 스냅샷 없으면 0+사유(어제치 폴백 금지).
  부수: 트리거 라우터가 자기 job_map을 복제해 등록된 잡의 수동 실행이 거부됐다 → 정본
  `job_func_for` 통일. 가드 5건.
- ✅ **적대 리뷰 3기**(codex 대체, Jino 승인) — #213 P1=0 · #214 P1=0 · #217 **P1=5**→전부 수정.
- ✅ 교훈 #148·#149·#150 기록 · `failures.jsonl` 2건 · 기억 `naver-ad-safe-deploy-cas.md` 갱신.

## 3. 확정된 결정사항
- **프론트 배포도 CAS를 건다**(Jino 승인 2026-08-06). 스탬프는 **배포가 아니라 빌드가** 심는다 —
  배포가 심으면 "병합만 하고 옛 dist 올리기"가 통과하며 스탬프만 전진해 손실을 은폐한다.
- **거부(ABORT) 시 절차는 merge → 반드시 재빌드 → 재배포.** 병합만 하고 옛 dist를 올리면
  상대 작업이 사라지는데 스탬프만 최신이 되어 더 나쁘다. (CLAUDE.md 등재)
- **codex 교차 리뷰는 자체 적대 리뷰로 대체**(Jino 승인, 쿼터 소진 ~08-09). 게이트는 P1으로만,
  P2는 채택/기각/이월로 처분하고 라운드를 늘리지 않는다.
- **「오늘」 광고비 = 검색광고 당일 누적**(최신 `naver_hourly_snapshot`). 스냅샷이 아직 없으면
  **0 + 사유 표기**이고 어제치로 되돌리지 않는다 — 모르는 것을 아는 척한 게 이 결함의 원인이었다.
  디스플레이(GFA·ADVoost)는 실차감이라 당일치가 없어 **제외하고 화면에 명시**한다(Jino 승인).
- **「지금 기준」 수동 갱신 버튼은 두지 않는다**(승인 후 실측으로 철회). NAVER `/stats` 당일
  데이터가 시간 단위로 갱신돼 같은 시간대 3회 관측이 원 단위까지 동일했고(16:05/16:43/16:46
  모두 360,731원), 이득 0인데 `snapshot_hour` 슬롯을 밀어 `hourly_pacing` 증분만 왜곡했다.
- 기간 전환 진행 표시는 **최소 노출 350ms**(응답 ~0.19초라 없으면 안 보인다). 옛 값은 지우지
  않고 흐린 채로 남기고 오버레이가 상태를 말한다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `scripts/safe_deploy.sh` | 배포 게이트. 백엔드 CAS·마이그 순서·**프론트 스탬프 CAS** |
| `frontend/scripts/stamp-build.mjs` | 빌드 시점 `dist/.build-stamp`(commit·dirty) 생성 |
| `scripts/tests/safe_deploy_frontend_test.sh` | 프론트 CAS 회귀 36검사(F1~F11) |
| `backend/app/routers/ad_costs.py` | `/gfa/status` — ⚠️#222가 이후 크게 고쳤다 |
| `frontend/src/components/Busy.tsx` | `Spinner`·`BusyOverlay`·`MIN_BUSY_MS` 공용 |
| `frontend/src/pages/NaverOps.tsx` | 스마트스토어 패널. `loadRef`·`selRef`·`reqSeq` 패턴의 원본 |
| `frontend/src/pages/CoupangOps.tsx` | 쿠팡 운영 패널. 같은 패턴 이식 |
| `backend/app/routers/naver_ops.py` | `sales_summary` — days=0 광고비 축(`ad_basis`) |
| `backend/app/services/naver_ad/hourly_snapshot.py` | 매시 :05 당일 누적 수집(슬롯 교체 멱등) |
| `backend/app/services/naver_ad/hourly_pacing.py` | 슬롯 **차분**으로 증분 산출 — 수동 트리거가 여길 흔든다 |
| `backend/app/routers/scheduler.py` | 트리거 — 이제 정본 `job_func_for`만 본다 |
| `.claude/memory/LESSONS_LEARNED.md` | 교훈 #148~#150 |

## 5. 알려진 이슈 / 주의사항
- ★★**내 GFA 수정(2판)은 이미 다른 세션이 3판으로 교정했다**(PR #222, 교훈 #147).
  합집합 `MAX(ad_date)` 판정은 형제 소스가 죽어도 초록인 **거짓 초록**을 만들었다. 3판은 판정
  대상을 데이터가 아니라 **수집기 실행 기록**(`scheduler_state.last_run_at`)으로 바꿨다.
  → `ad_costs.py`의 GFA 부분을 만질 때는 **#222 이후 코드**를 기준으로 볼 것.
- ★**내 적대 리뷰가 그 거짓 초록을 P1=0으로 통과시켰다**(교훈 #150). 리뷰어가 "지금 라이브
  데이터에서는 안 틀린다"를 확인했고, 나는 그걸 안전 확인으로 읽었다. **P1=0은 "그 렌즈로는
  못 찾음"이다.**
- **프론트 배포 clobber 3회**(09:09·09:23 피해 / 10:28 가해) — 전부 복구됨. 가드는 도입 당일
  11:17에 4번째를 실제로 막았다(`7c3c50e`, 미병합 커밋). 다른 세션도 새 스크립트 사용 중
  (매니페스트에 `"forced":false` 기록).
- **부트스트랩 경로는 남은 구멍** — prod에 스탬프가 없으면 통과한다. 10:28 사고의 직접 원인.
  스탬프가 `dist` 밖으로 나가 지워지지 않으니 재발 조건은 사라졌지만, 구멍 자체는 남아 있다.
- **`scripts/next_ids.sh`의 교훈 번호 정규식이 깨져 있다** — 실제 최댓값 147인데 140을 뱉는다
  (08-05 인계에도 이월돼 있던 항목, 아직 안 고침). 교훈 번호는 직접 셀 것:
  `grep -oE '^#+ *#[0-9]+' .claude/memory/LESSONS_LEARNED.md | grep -oE '[0-9]+' | sort -n | tail -1`
- **`auto_sync_orders`가 253초 걸린다**(curl 실측 245.8초, 브라우저 253.1초). 쿠팡 운영 페이지
  진입마다 POST되는데 **완료·소요 로그가 한 줄도 없다** — 서버에서 관측 불가.
- **쓰기 경로(취소·반품 승인) 라이브 재현은 의도적으로 안 했다** — 실제 주문이 바뀐다.
  #221의 근거는 tsc + 호출부 전수 확인 + 비파괴 경로까지다.
- **NAVER `/stats` 당일 데이터는 실시간이 아니다** — 실측상 시간 단위로 갱신된다(같은 시간대
  3회 관측이 원 단위까지 동일). 공식 문서로 확인한 것은 아니므로 "실측상"이다. 당일 광고비를
  더 자주 보이게 하려는 시도는 이 사실을 먼저 확인할 것.
- **스냅샷 잡을 시간 중간에 수동 실행하면 페이싱이 왜곡된다.** 슬롯이 `snapshot_hour` 단위로
  교체되는데 `hourly_pacing`은 슬롯 간 차분으로 증분을 내므로, 그 시간 증분은 과대·다음 시간은
  과소가 된다. 트리거 라우터 정본화로 **이제 수동 실행 자체는 가능해졌다** — 가드는 없다.
- 화면 상단 빨간 배너 **"ofix 판매분석 갱신 실패 · 로그인 필요"** 는 하루 종일 떠 있었고 미착수.

## 6. 다음에 할 작업 (미완료)
- [ ] 상단 배너 "ofix 판매분석 갱신 실패 · 로그인 필요" 원인 조사(오픽스 로그인 만료 계열로 추정 — 확인 필요)
- [ ] `auto_sync_orders` 253초: 소요·완료 로그 추가, 페이지 진입마다 동기 호출하는 구조가 맞는지 재검토
- [ ] `scripts/next_ids.sh` 교훈 번호 정규식 수정(3번째 이월)
- [ ] 남은 적대 리뷰 P2(이월): `daysSince` 브라우저 로컬 타임존 · CoupangOps 첫 로드 실패 시
      "검색/필터 결과 없음" 오문구 · `AbortController` 부재 · `BusyOverlay`가 스크롤 밖일 수 있음
- [ ] 프론트 CAS 부트스트랩 구멍(스탬프 없으면 통과)을 닫을지 판단 — 전 세션 전환 완료 후
- [ ] codex 쿼터 복구(~08-09) 후, 자체 리뷰로 대체한 5건을 소급 교차 검토할지 Jino 판단
- [ ] NAVER `/stats` 당일 갱신 주기 확정(공식 문서/실측) → 화면 문구 "(매시 05분 갱신)" 정밀화
- [ ] 스냅샷 잡 수동 실행 가드를 둘지 판단(예: 같은 시간대 재실행 시 슬롯 교체 대신 거부)
- [ ] 워크트리 정리: `.claude/worktrees/guard-r2` · `runpreview` · `today-ad` (전부 병합 완료)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_frontend-refetch+deploy-cas_20260806.md 읽고 이어서 작업해줘
