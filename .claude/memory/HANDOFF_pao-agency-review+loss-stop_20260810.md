# 세션 인수인계: PAO 대행사 운영 평가 + 2배 오집계 정정 + 손실 광고 2건 정지
> 저장일시: 2026-08-10 10:35 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오) = Profit Ad Optimizer** — `docs/tracks/active/track_naver-ad-optimization.md`
> 직전 HANDOFF: `HANDOFF_pao-naming+actor-attribution_20260809.md` (그 파일의 §5·§6도 이번에 갱신됨)

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (공유 메인 폴더 = **main 고정**)
- prod: `sellc.ohitech.co.kr` · 백엔드 `/home/ubuntu/ohisell/backend`
- **★prod DB 정본 = `/home/ubuntu/ohisell/backend/ohisell.db`** (1.7GB). 루트의 `/home/ubuntu/ohisell/ohisell.db`는 4KB 껍데기 — 열지 말 것. `.env`의 `DATABASE_URL=sqlite:///./ohisell.db` + cwd=backend.
- **★pm2 포트는 블루-그린으로 바뀐다** — 이번 세션 중 8011 → **8001**로 전환됐다. 항상 `pm2 list`로 확인.
- prod python: `/home/ubuntu/ohisell/backend/.venv/bin/python3` (cwd=backend, `sys.path.insert(0, ".../backend")`)
- **★prod 임시 스크립트는 `from app.database import SessionLocal`을 반드시 넣을 것** — 안 넣으면 `.env` 미로드로 네이버 API 서명이 **빈 문자열**이 되어 403 `Invalid Signature`가 뜬다. 나는 이걸 「계정 전체 차단」으로 오진단했다(§5).
- 배포 `scripts/safe_deploy.sh` · 병합 `scripts/safe_merge.sh` · 번호 `scripts/next_ids.sh`(**정규식 결함 — §5 참조**)
- 테스트: `cd backend && python3 -m pytest -q`

## 2. 이번 세션 완료 목록
- ✅ **대행사 운영 평가**(07-31~08-08 정지 기간) — prod 실측. 결과는 §3·§5.
- ✅ **★어제 HANDOFF의 계정 광고비·ROAS 2배 오집계 정정** — 커밋 `983b515`.
  - `.claude/memory/HANDOFF_pao-naming+actor-attribution_20260809.md` §5 전면 교체 + §6 재구성
  - 메모리 토픽 **신설** `naver-ad-daily-aggregation-rule.md`(전역 memory 폴더) + `naver-ad-data-cadence.md`에서 링크 + `MEMORY.md` 인덱스·미결 갱신
  - 교훈 **#195**(최초 #189 → 번호 충돌로 재부여) · `failures.jsonl` 1줄
- ✅ **손실 광고 2건 라이브 정지**(Jino 지시) — 커밋 `95a0d6a`.
  - `10. 컨텐츠매체` 캠페인 `cmp-a001-02-000000008336372` → PAUSED, change_log **5854**
  - `폴드8울트라_사생활` 광고그룹 `grp-a001-01-000000071340962` → PAUSED, change_log **5855**
  - 네이버 API 독립 재조회로 `userLock=True` 확인 · **형제 그룹 3개 미변경 확인**
- ✅ **내가 만든 주체 오귀속 버그 발견·수리** — 교훈 **#196**. change_log의 action·값 형식 교정(코드 변경 0). 라이브 피드 `by_actor {ours:2, agency:0}` 확인.
- ✅ **origin/main 병합 + 교훈 번호 충돌 해소** — 커밋 `26d6042`. 내 #189 → **#195** 재번호(main이 트렁크, 본문 불변, 참조 3곳 갱신).
- ✅ 워크트리 `actor-attribution-fix` 정리(PR #261 병합 완료분).

## 3. 확정된 결정사항
- **★계정 광고비·ROAS의 정본 집계 규칙** — `naver_ad_daily`는 두 소스가 한 테이블에 산다. **실단위(`adgroup_id<>'__backfill__'`, keyword_id 조건 없음)** 또는 **sentinel만**, **택일**. 검산 = 두 합이 ±수원 내 일치(08-05 실측 675,089 vs 675,090 = **1원 차**). ⚠️**`keyword_id=''` 필터는 해결책이 아니라 두 번째 함정**(파워링크 실단위가 통째로 빠지고 쇼핑이 2배). → `[[naver-ad-daily-aggregation-rule]]`
- **대행사 운영 평가 결론**: **전술(입찰·컷)은 유능, 전략(어디에 돈을 쓸지)은 부재.** 확대 판단이 전부 ROAS·볼륨 기준이고 총이익 기준이 아니다.
- **★단, 같은 잣대로 우리를 재면 우리가 더 나빴다** — 우리 자동운영 기간(07-21~29) **PAO 6개 캠페인 = 계정 광고비 57.7% 쓰며 −726,654원 적자** vs **대행사 나머지 42.3%로 +118,053원 흑자**. 어제 낸 「정지 전 계정 −608,601원 적자」의 정체는 대부분 우리 것이었다.
- **폴드8/플립8 라인은 흑자**(ROAS 1.828 · 9일 +283,255원). 어제의 「BEP 아래일 가능성」은 2배 오류였다. **단 이 라인을 빼면 나머지 계정은 −147,110 적자** — 집중 위험.
- **정지는 Jino 지시였다**(07-30 10:44 원문: *"우리가 진행중인 광고 모두 정지 시켜줘. 너한테 맏기면 망하겠다."*).
- **★P0의 답이 바뀌었다** — 「정착창을 하루 당기기」가 아니라 **스마트스토어 실시간 판매를 CPC 판단에 배선**하는 것이 근본이다(§6 A).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-ad-optimization.md` | PAO 트랙 정본. **D-NAO-132가 재개 선행조건 P0~P4의 원문**(159~167행) |
| `backend/app/services/naver_ad/auto_operator.py` | 정착창 정본 `_settlement_window`(:265) · `_settlement_agg`(:371, `NaverAdDaily`만 씀 = 스마트스토어 미연결) |
| `backend/app/services/naver_ad/expansion_allocator.py` `expansion_pressure.py` | 정착창 **로컬 복제 2곳**(:66, :54) — 한 곳만 고치면 조용한 divergence |
| `backend/app/services/naver_ad/budget_pacing.py` | **`today_proxy_revenue`(:213)** — 캠페인별 당일 매출 프록시. **이미 있고 화면·예산 페이싱에 연결됨** |
| `backend/app/services/naver_ad/change_actor.py` | 주체 판정 5규칙. **`OURS_ACTIONS_BY_AXIS`(:118)** · **`axis_value`(:147)** ← 교훈 #196의 두 관문 |
| `backend/app/services/naver_ad/naver_sa_writer.py` | `set_campaign_lock`(:435) · `set_adgroup_lock`(:413). **change_log 기록은 호출부 책임** |
| `backend/app/services/naver_ad/ad_creative_daily_sync.py` | 소재별 성과(P1-b) — **완료·라이브 가동 중** |
| `backend/app/models.py:2116` | `__backfill__` sentinel의 회계 의미(구매+장바구니 합산) |

## 5. 알려진 이슈 / 주의사항
- **★`scripts/next_ids.sh` 정규식 결함이 실제로 터졌다** — 이번 세션에 #186을 줬으나 실제 최댓값은 #188이었고, 그 사이 병행 세션이 origin에 #189~#194를 올려 **내 #189가 중복**됐다. 규칙대로 내 것을 **#195**로 재부여. ⚠️다른 세션이 08-10 커밋에서 이 정규식을 수리했다고 적었으니(`1f4f434`) **다음 세션은 수리 여부를 먼저 확인**할 것.
- **★교훈 #196 — 내가 어제 고친 오귀속을 오늘 새로 만들었다.** change_log에 `manual_loss_stop`이라는 **새 action 이름을 지어냈고** 값도 `"userLock=True (paused)"`로 썼는데, `OURS_ACTIONS_BY_AXIS`에도 없고 `axis_value`도 못 읽는다 → 다음 감사에서 「대행사 조작」으로 잡혔을 것. **발견은 우연**이었고 내 원래 합격기준(쓰기 성공·라이브 PAUSED) 안에서는 **전부 초록이었다**. 수리 = action `set_user_lock` + 값 `{"userLock": true}`.
- **★403 `Invalid Signature`를 「계정 차단」으로 오진단했다** — 판별자는 **403 본문의 `type`**(`urn:naver:api:problem:invalid-signature`). 원인은 §1의 `.env` 미로드.
- **★SQLite `HAVING <별칭>`은 원시 컬럼으로 해석될 수 있다** — `HAVING cost>0`이 그룹의 임의 행 기준이 되어 광고그룹 3개를 **조용히 삼켰다**(전환 0인 86,319원짜리 포함). 집계 결과를 거를 땐 `HAVING SUM(cost)>0`.
- **전환 성숙도는 D+1 이후 불변**(성숙도 스냅샷 시계열 실측), 재수집은 **D+3까지** 돈다(`synced_at` 실측). 단 「네이버가 D+1에 확정한다」가 아니라 「우리 파이프라인이 그 뒤 갱신하지 않는다」일 수도 있다 — 양 창에 동일 적용이라 전후 비교는 공정.
- **계정 단일 BEP는 존재하지 않는다** — 상품별 1.223~5.81, 평균 **1.749**(n=540, 공헌이익률 58.7%). 계정 ROAS 1.669가 1.637과 1.749 **사이**라 계수에 따라 흑/적이 갈린다. **「흑자 전환」이라 말하지 말 것.**
- **⚠️ 로컬에 push 안 된 커밋 3개**(`983b515` `95a0d6a` `26d6042` + 스케줄 루틴 `be0cc23`). Jino에게 push 여부를 물었으나 **답을 못 받았다**. 병행 세션이 활발해 안 올리면 또 갈라진다.
- **스케줄 감사 루틴이 매일 08:5x에 progress 하단에 append 한다**(읽기 전용). 그 루틴도 독립적으로 sentinel 부풀림·BEP 밴드 겹침을 짚었다 — 내 정정의 교차검증.
- 대행사 행위 373건(피드 재적용 76건 제외) 중 **파워링크 두 캠페인에 276건(74%)**, TPU엔 24건(6%). **주말(08-02·08-08·08-09) 0건.**

## 6. 다음에 할 작업 (미완료) — **PAO 업데이트 목록**

### 🔴 A. 재개 차단 조건 (D-NAO-132 P0·P1 — 해소 전 새 기능 개방 금지)
- [ ] **P0-a ★최우선 — 스마트스토어 실시간 판매를 CPC 판단에 배선**
  - 올리기 = 상한 프록시가 BEP 미달이면 **상향 차단**(허가 근거로는 **쓰지 않는다** — 광고 귀속이 아니라 최대치라 최대 2.1배 과대. 실측 07-22: 스마트스토어 178,000 vs 광고귀속 83,500)
  - 내리기 = 원료를 실시간으로(지금은 하루 늦게 안다)
  - 근거: 07-30 폴드8 **주문상한 0.834 < BEP 1.9639**인데 자동이 2,380→2,730으로 되올렸다. **그 숫자가 그날 이미 시스템에 있었다**
  - 상태: `today_proxy_revenue` **이미 존재**, 화면·예산 페이싱에 연결됨. `auto_operator`만 미연결 → **새 기능이 아니라 배선**
  - ⚠️전제: **상품↔캠페인 매핑 커버리지 실측 필수**. 매핑 없는 캠페인은 「안전」이 아니라 **「모름」**
- [ ] **P0-b** 정착창 `[-8,-2]` → `[-8,-1]` + **3곳 복제를 단일 출처로**(auto_operator 정본 + expansion_allocator + expansion_pressure, 테스트가 정합 고정 중)
- [ ] **P0-c** **하향 쿨다운** — 자동 하향한 대상은 N일간 자동 상향 금지. P0-a·b만으로는 되올림 구조가 남는다
- [ ] **P1-a** 통합검색 순위 `pcNxAvgRnk`·`mblNxAvgRnk` 수집(**grep 히트 0건 = 미착수**). stat 필드 + 컬럼 + **`--migrate`** + 순위 판단이 어느 축을 쓸지 결정
- [x] ~~P1-b 소재별 성과~~ — **완료**(D-NAO-140, 08-04). 라이브 08-01~09 7,766행 + 대조 로직

### 🔵 B. 이번 세션에서 새로 나온 것
- [ ] **B-1 `change_log` 쓰기 가드** — `OURS_ACTIONS_BY_AXIS` 밖 action으로 `dry_run=False` 행을 쓰거나 `axis_value`가 못 읽는 값이면 경고/거부(교훈 #196. 지금은 **규칙을 아는 사람만** 지킬 수 있다)
- [ ] **B-2 집계 정본 헬퍼** — sentinel/실단위 택일 + 검산식을 공용 함수로(교훈 #195. 서비스 코드는 각자 제외하지만 **조회·분석 경로엔 가드가 없다**)
- [ ] **B-3 BEP 기준선 표면화** — 판정에 어느 계수를 쓰는지 화면·로그에

### 🟡 C. D-NAO-132의 나머지
- [ ] **P2** 폴드8 **6매입 단품**(BEP 1.9639) 채산성 — 여전히 열림. 오늘 확인한 폴드8/플립8 **라인 전체**(1.828 흑자)와 **같은 대상이 아니다**
- [ ] **P3** 손실 고삐 트리거를 「지출 누적」 대신 **즉시 판정**으로(현재 `추정ROAS<BEP AND 당일소진≥하루평균` → 평소만큼 쓴 뒤에야 발동)
- [ ] **P4** 자동운영 범위(27개 중 5개=55.4%만, **22개 440만원이 관측 밖**) · 신규 캠페인 보호(`launch_target_rank` 없으면 순위 하한 대상 아님) · 사다리 신선도(22시간 묵음)

### 🟣 D. Jino 결정 (기술 아님)
- [ ] **TPU 캠페인** — 계정 광고비 **44.3%**를 쓰며 9일 **−337,659원 적자**, 대행사 조작 6%로 방치. ★우리가 돌릴 땐 **−733,754원**으로 2.2배 나빴다
- [ ] **03 아이폰_강화유리 일예산** 200,000 → 50,000 원복(스케줄 루틴도 "여전히 미조치"로 매일 보고 중)
- [ ] **★대행사 통보 여부** — 오늘 정지한 2건은 대행사 캠페인이고, **07-30에 우리가 끈 걸 대행사가 같은 날 되살린 선례**가 있다. 조율 규칙이 없다
- [ ] **대행사 신규 그룹 6개에 BEP·목표순위가 없다** — 재개해도 PAO는 「기준 없음」으로 다룬다. 그 위에서 자동화를 돌리는 의미를 먼저 정할 것
- [ ] **push 여부**(§5) · **이 목록을 트랙 파일에 D-NAO 결정으로 기록할지**

### 착수 순서 제안
**P0-a → P0-c → P0-b → B-1 → P1-a**. P0-a가 07-30 사고의 직접 원인이면서 **함수가 이미 있어 작업이 가장 작다**. B-1은 내가 오늘 실제로 밟은 지뢰라 다음 사람도 밟는다.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_pao-agency-review+loss-stop_20260810.md 읽고 이어서 작업해줘
