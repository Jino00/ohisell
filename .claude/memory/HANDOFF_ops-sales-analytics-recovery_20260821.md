# 세션 인수인계: 운영/소방 — 판매분석 수집 복구 · WING2 요약축 · prod 디스크
> 저장일시: 2026-08-21 23:10 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★**이 세션은 PAO 트랙 작업이 아니다.** 「PAO 논의」 체인(현재 30회차)과 **섞지 말 것** —
> PAO 진행률에 기여 0이고, 여기서 다룬 것은 전부 운영 자산의 고장 수리다.
> 앵커(`.claude/anchors/582f8ade-*.md`) **없음**(0건) ⇒ §2-1 완료 QA·§2-2 트랙 진행률 절은
> 규칙대로 뺐다(전역 §2 「작업의 경계는 문서가 정한다」 — 둘 다 없으면 완료 QA 대상이 아니다).

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (공유 메인 폴더, main)
- prod: `sellc.ohitech.co.kr` (ssh 별칭 — `sellc` 단독은 해석 실패) · 앱 `/home/ubuntu/ohisell`
- Mac 페처(launchd, 전부 `poll` 상주):
  - `com.ohisell.rocket` → `~/.ohisell/tools/rocket_supplier_fetcher.py` · 로그 `~/.ohisell_rocket_fetcher.launchd.log`
  - `com.ohisell.wing2` → `~/.ohisell/tools/wing_browser_fetcher.py` · 로그 `~/.ohisell_wing2_fetcher.launchd.log`
  - `com.ohisell.wing`(WING1) · 로그 `~/.ohisell_wing_fetcher.launchd.log`
- 페처 config: `~/.ohisell_rocket_fetcher.json`(`prod_base_url`·`basic_auth_user/pass`·`ingest_token`·`sales_days`)
- 재시작: `launchctl kickstart -k gui/501/com.ohisell.rocket`

## 2. 이번 세션 완료 목록
- ✅ **판매분석 수집 복구** — `tools/rocket_supplier_fetcher.py` `_SALES_PERMITTED_LEVELS`에 `"DISCOVERY"` 추가(+근거 주석 6줄). 커밋 `80e1ddcd` → **PR #321 MERGED** (`b350af13`).
- ✅ **Mac 사본 동시 수정** — `~/.ohisell/tools/rocket_supplier_fetcher.py` 같은 한 줄 surgical 적용 + 데몬 재시작(19:46:34). ★**통째 복사 금지**: Mac 사본에만 `_basic_auth`(11곳)가 있다(repo에 없음).
- ✅ **적대 리뷰(PR 경계 1회, Sonnet)** — `PASS · P1 0건`. 변이 3종: (나)(다) 잡힘 / **(가) 살아남음**.
- ✅ **WING2 요약축 9일 정체 해소** — 크론과 같은 엔드포인트 직접 호출로 90일 push + 옵션축 246행.
- ✅ **트랙 기록** — `docs/tracks/active/track_revenue-wing-truth.md`에 「운영 기록」 절 신설. 커밋 `37a19ff4`.
- ✅ **prod 디스크 95% → 81%** (여유 5.7GB → 20GB) + `pm2-logrotate` 설치로 재발 방지.
- ✅ **`failures.jsonl` 3건 기록** (판매분석 등급 / WING2 세션 만료 / 디스크).
- ✅ **낡음 검사로 유령 5건 제거** — 아래 §5 참조.

## 3. 확정된 결정사항
- **판매분석 허용 등급에 `DISCOVERY`를 넣는다** — 라이브 실측(`permittedLevel=DISCOVERY subscribed=DISCOVERY freeTrialEnd=None`) 근거. 열어도 안전한 이유는 `_collect_sales_rows`의 창 전체 판정이 **짝으로** 붙어 있어서다(vendorItems 합계 0 → `_SalesAccessDenied` 재차단). 이번 실행에서 그 안전망은 **발동하지 않았다** = DISCOVERY는 데이터를 주는 등급이 맞다.
- **P2(변이 (가) 생존)는 트랙 이월** — 「허용 집합에 새 값이 몰래 추가돼도 테스트가 안 잡는다」는 이 PR 범위 밖. 라운드 늘리지 않음(전역 §4 종료 규칙).
- **유효 구간 가설은 코드에 등재하지 않는다** — 관측 2회짜리라 추정 등재 금지. PR #321 코멘트에만 보존.
- **`.cache/huggingface`(5.8G)·`ms-playwright`(2.5G)는 안 지운다** — ai-office·stock 소관, 여기서 판단 근거 없음.
- **DB 증가율 대응은 별건**(Jino 선택: "지금은 청소만").

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `tools/rocket_supplier_fetcher.py:1600~1670` | 판매분석 구독 게이트 `_sales_access_ok` + 허용 등급 집합 |
| `tools/rocket_supplier_fetcher.py:1935~1960` | 창 전체 판정(2차 안전망) — `_SalesAccessDenied` / `_SalesMappingError` |
| `tools/rocket_supplier_fetcher.py:368` | ★`sales_days=30` 근거 주석 — **「롤링 57일 창」 숫자가 관측과 안 맞는다**(§5) |
| `backend/app/services/scheduler_service.py:1454~1498` | `request_wing_vendor_summary_daily_job`(05:20 KST) |
| `backend/app/routers/coupang_ops.py:2435` | `POST /wing/vendor-summary/request-refresh` — 수동 트리거 경로 |
| `backend/app/services/scheduler_health.py:111~122` | 판매분석 축 4종 신선도 규칙(max_age_days=3.0) |
| `docs/tracks/active/track_revenue-wing-truth.md` | WING2 요약축 소관 트랙 — 이번 복구 기록 추가 |
| `backend/backups/daily_backup.sh` | 일일 백업 `KEEP=7` 로테이션(로그는 **UTC**) |

## 5. 알려진 이슈 / 주의사항
- ★★**낡음 검사 결과 — 인계 유령 5건**: MEMORY.md가 「PR #309·#310·#312·#313·#314 OPEN·미병합」이라 적어 뒀는데 **전부 MERGED**다(실측 23:09). 현재 열린 PR은 **#294 하나뿐**. 다음 세션은 그 항목들을 쫓지 말 것.
- ★**`sales_days=30`의 근거 숫자가 틀렸을 수 있다**: 주석은 「원천은 롤링 57일 창」이라는데, 실측은 08-20 **80일**(`06-01~08-19`) / 08-21 **51일**(`07-01~08-20`). **두 관측 다 시작일이 월초** ⇒ 「롤링 N일」이 아니라 **「전 N개월 1일부터」**로 보인다(BASIC=전2개월 / DISCOVERY=전1개월). 맞다면 **매월 1일에 소급 한도가 ~30일까지 줄어** `sales_days=30`과 맞닿는다. **데이터 소실은 아니다**(30일 롤링이 메움) — 좁아지는 건 «수동 백필 유예»다. **확정 관측일 = 2026-09-01**(그날 `판매분석 유효 구간 수신:` 줄이 `2026-08-01 ~ 08-31`이면 가설 확정).
- ★**CI 빨강은 코드 신호가 아니다** — 세 잡 전부 `steps: 0` · 로그 없음 · 2초 종료 = **GitHub Actions 결제 정지**. PR #321은 `safe_merge.sh --force`로 병합했고 자백이 `$TMPDIR/safe_merge.log`에 남았다(`verdict=FAIL`).
- ★**WING2 복구 경로는 「사람이 창에서 로그인」 하나뿐이다** — 끊기면 **또 조용히 N일 정체**한다. 배너는 사후 표면화일 뿐 자동 회복이 없다. `coupang_wing_cookie.status`는 green인 채 `last_error`에 「로그인 필요」가 쌓이는 **green-while-dead** 구조.
- ★**Mac 페처 사본 ≠ repo 파일** — `~/.ohisell/tools/rocket_supplier_fetcher.py`에만 `_basic_auth`(11곳)가 있다. **repo 파일을 통째 덮으면 prod Basic Auth 배선이 사라진다.** 수정은 항상 surgical.
- ★**prod DB 증가율 ≈ 158MB/일** — 백업 로그: 08-15 87테이블 1,720MB → 08-20 96테이블 2,509MB. 일일 백업 7개도 231MB→355MB로 동반 증가. **청소로 3~4주 벌었을 뿐**이다.
- 공유 메인 폴더에 **다른 세션의 미커밋 3건**이 그대로 있다(`CLAUDE.md`, `.claude/memory/chains/pao-논의.jsonl`, `.claude/settings.local.json.bak-20260821`). **내 것이 아니다 — 건드리지 말 것.**
- 적대 리뷰 부수 관측: `backend/.venv.broken-py314`로 pytest를 돌리면 `ModuleNotFoundError: coupang_auth`(tools/ 경로 누락)로 3분 34초 뒤 죽는다. 이름 그대로 broken venv — 이 PR과 무관한 로컬 환경 결함.

## 6. 다음에 할 작업 (미완료)
> 3문 검사 결과: 아래 전부 **③(목표 변경 아님·되돌릴 수 있음)** ⇒ **묻지 말고 진행**한다.
> Jino 추인 대기 항목 **없음**.

- **이어지는 작업의 목적(원문)**: 없음 — 이 세션은 **소방 세션이고 종결됐다.** 아래는 «이 세션이 만든 부채»이지 이어지는 작업의 슬라이스가 아니다. **PAO 트랙은 「PAO 논의」 체인이 갖는다 — 여기서 이어받지 말 것.**
- **남은 슬라이스**:
- [ ] **prod DB 증가율 진단** (별건, Jino가 "지금은 청소만" 선택) — 87→96 테이블 중 어느 축이 158MB/일을 쓰는지 테이블별 실측 + 보존 정책 제안. 소관은 축을 만든 PAO 트랙일 가능성이 높으나 **디스크는 전 프로젝트 공유**라 소관 확정부터 필요.
- [ ] **P2 이월 — 허용 등급 집합 커버리지 공백**: `test_unknown_permitted_level_is_blocked`가 리터럴 `"NONE"`만 확인해, `_SALES_PERMITTED_LEVELS`에 `"FREE"`를 넣는 변이가 **살아남는다**(적대 리뷰 변이 (가)). 테스트를 「집합에 FREE류가 들어가면 실패」 성질로 바꾸는 일. 파일 `backend/tests/test_rocket_promo_fetcher.py:679·691`.
- [ ] **유효 구간 가설 확정** — **2026-09-01**에 `~/.ohisell_rocket_fetcher.launchd.log`의 `판매분석 유효 구간 수신:` 줄 1회 관측. `2026-08-01 ~ 08-31`이면 확정 → `tools/rocket_supplier_fetcher.py:368` 주석의 「57일」을 실측값으로 교체.
- [ ] (관찰) **WING2 로그인 수명** — 다음 만료 때 또 N일 정체한다. 자동 회복 or 조기 경보가 필요하면 소관은 `track_revenue-wing-truth.md`.
- [ ] (관찰) **`.pm2/logs` 로테이션 실작동** — `pm2-logrotate` 설치는 했으나 첫 회전(자정)은 미관측. 다음에 `du -sh ~/.pm2/logs`가 20M×잡수 근처에서 안정되는지 확인.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ops-sales-analytics-recovery_20260821.md 읽고 이어서 작업해줘
