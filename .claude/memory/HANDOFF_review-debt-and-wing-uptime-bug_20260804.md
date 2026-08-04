# 세션 인수인계: 리뷰 부채 청산 → Wing 부팅 결함 → 화면 신뢰 경계
> 저장일시: 2026-08-04 18:00 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: 네이버 SA 광고 최적화(`docs/tracks/active/track_naver-ad-optimization.md`)
> 직전 인계: `.claude/memory/HANDOFF_session_naver-ad-observation-to-creative-grain_20260804.md`(색인)

## 0. 한 줄 요약
codex 리뷰 부채를 갚으러 시작했는데 **codex가 한도 소진이라 못 돌았고**, 대체 리뷰가 P1 1건을
잡았고(대조의 거짓 초록), 그 뒤 "main이 빨갛다"를 추적했더니 **7주 묵은 운영 결함**이 나왔다.
셋 다 고치고 배포했다.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (**main 브랜치, 루트 공유 폴더**)
- prod: `https://sellc.ohitech.co.kr` · 서버 `sellc.ohitech.co.kr:/home/ubuntu/ohisell`
- 테스트: `cd backend && python3 -m pytest -q` (현재 **4,604 passed**) / `cd frontend && npm run build`
- **백엔드 배포**: `scripts/safe_deploy.sh <파일…> [--migrate] [--restart]` — 직접 scp 금지(D-NAO-49)
- **★Mac 페처 배포**: `tools/install_local_runtime.sh` (경로가 `scripts/`가 아니라 **`tools/`**).
  `--files-only`=파일 CAS 복사만(launchd 재기동 안 함) · `--dry-run` · `--force=<파일>`
- prod 파이썬: `/home/ubuntu/ohisell/backend/.venv/bin/python3` / 스크립트 실행 전 `set -a && . ./.env && set +a`
- alembic head: `b3e75c1a8f04` (로컬 = prod). 이번 세션 DB 변경 **없음**
- origin/main과 동기 상태(마지막 push `0bc276a`, 이후 문서 커밋 1건 로컬)

## 2. 이번 세션 완료 목록
- ✅ **`164601f`** `backend/app/services/naver_ad/ad_creative_daily_sync.py` — 대조 대상 날짜를 「적재 결과」가 아니라 **요청 구간 전 날짜**로. `feed_reapply.py` — 소재→상품 매핑에 `synced_at DESC` 정렬(최신 관측 채택) + siblings도 `setdefault`. 테스트 4건 신규. **prod 배포 완료**
- ✅ **`2fba6a6`** `tools/wing_browser_fetcher.py` — 쿨다운 기준점 `0.0` → `None`(`last_fetch`·`last_rg`). 테스트 3건 신규. **Mac 런타임 배포(17:28) + 데몬 재기동(17:29, PID 14659·14662) 완료**
- ✅ **`488ca73`·`93e4260`·`0bc276a`** 트랙 처분 표·교훈 #122~#124·커밋 해시 정정
- ✅ 트랙에 **「수정 사항」 화면 신뢰 경계** 절 신설 + **다음 액션 0번**(캠페인·그룹 시각 부여) 등재
- ✅ `failures.jsonl` 2건(codex 한도 소진 오독 / monotonic 부팅 결함)
- ✅ push 완료(`7099b29..50eeabf`, `50eeabf..0bc276a`)

## 3. 확정된 결정사항 (번복 금지)
- **codex 리뷰는 Opus 1기 적대 리뷰로 대체**했다(스킬 폴백 경로, 다기 패널 금지). **codex 소급 리뷰는 부채로 남는다 — 08-09 16:16 한도 리셋 이후 1회**, 스코프는 `4693369...`의 22파일
- **대조는 요청 구간 전 날짜를 검사한다** — 양쪽 다 빈 날은 0==0으로 자동 통과하므로 경보가 늘지 않는다
- **쿨다운 "아직 안 함"은 `0.0`이 아니라 `None`으로 표현한다**(단조시계 기준점 규약)
- **나머지 두 페처(`ad_cost_browser_fetcher`·`ohitech_ad_fetcher`)의 같은 패턴은 안 고쳤다** — 쿨다운 45·60초라 피해가 부팅 후 1분 이내. 그 페처 손댈 때 함께
- **「수정 사항」 화면은 숫자 대사용이 아니다** — 읽는 용도로만(트랙 신뢰 경계 절)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|---|---|
| `backend/app/services/naver_ad/ad_creative_daily_sync.py` | 소재 grain 적재 + **대조**(이번에 P1 수정) |
| `backend/app/services/naver_ad/feed_reapply.py` | 피드 재적용 판별(가드①②·창 900초, 이번에 매핑 정렬 수정) |
| `tools/wing_browser_fetcher.py` | Wing 폴 데몬(`cmd_poll` 쿨다운 기준점 수정) |
| `backend/tests/test_wing_poll_fetch_error_report.py` | 부팅 직후 R4 테스트 3건 추가 |
| `docs/tracks/active/track_naver-ad-optimization.md` | **D-N 정본 + 신뢰 경계 절 + 다음 액션 0번** |
| `.claude/memory/LESSONS_LEARNED.md` | #122·#123·#124 |

## 5. 알려진 이슈 / 주의사항
- ★**루트 공유 폴더에서 병행 세션이 동시 작업 중**(쿠팡 광고설정 D-CAC 트랙). `git add -A`·`commit -a` 금지, 파일 명시. **★`git log -1 HEAD`로 "방금 내 커밋" 해시를 읽지 말 것** — 이번에 그 사이 남의 커밋이 끼어들어 트랙에 잘못 적었다(`0bc276a`로 정정)
- ★**"파일은 새것·프로세스는 옛것"**(green-while-stale). Mac 데몬은 파일 교체만으론 안 바뀐다 — `launchctl kickstart -k gui/$(id -u)/com.ohisell.wing`(및 `.wing2`). prod는 이번엔 문제 없었다(프로세스 11:44 > 파일 10:26)
- ★**내일 07:30 판정은 `last_status=ok`만 보면 속는다** — 소재 grain은 fail-open이라 그 부분만 조용히 실패해도 ok가 찍힌다. 트랙 「다음 액션 2」의 4항목 기준대로 볼 것
- ★**RG 수정의 라이브 행동 증거는 없다** — 차이는 부팅 1시간 안에서만 관측 가능한데 오늘 창(11:26~12:26)은 지났다. **다음 재부팅 직후 RG 버튼을 한 번 눌러 확인할 것**
- **prod `.env` 포맷 결함**: `AD_DATA_DB_PATH`가 공백 포함 Mac 경로인데 따옴표가 없어 `. ./.env` 소싱 시 에러가 뜬다. 앱은 정상이나 운영 스크립트가 샐 수 있고, **prod에 Mac 로컬 경로가 있는 것 자체가 이상**하다
- **자동운영은 07-30부터 정지**(D-NAO-132). 라이브 확인함(7캠페인 `auto_operate=False`, 실집행 07-30 10:50 이후 0건)
- 소재 성과 편향 3가지(간접전환 미정착·표본 짧음·ADVoost/GFA 미포함)는 여전 — 판단 금지

## 6. 다음에 할 작업 (미완료)
- [ ] **08-05 07:30 크론 자체 발화 확인** — 트랙 「다음 액션 2」의 **4항목 기준**대로(①last_run_at ②실패 로그 문자열 부재 ③`naver_ad_creative_daily`에 08-04 행 ④대조 3일 일치)
- [ ] **캠페인·광고그룹에 발생 시각 부여**(Jino 지시로 등재, **계약 승인 필요**) — 전제 실측 완료: `/ncc/campaigns`·`/ncc/adgroups` 응답에 **`editTm`이 이미 온다**, 추가 API 콜 0
- [ ] **codex 소급 리뷰** — 08-09 16:16 이후
- [ ] **S3 백필** — 결손 = 08-01 이전 전체(소재 테이블 3일뿐). 선결 질문 = **네이버가 며칠 전까지 보고서를 재생성해 주는가**(미확인, 네이버 API 호출 필요)
- [ ] 다음 재부팅 직후 RG 버튼 라이브 확인
- [ ] 나머지 두 페처 monotonic 패턴 정리(우선순위 낮음)
- [ ] prod `.env` `AD_DATA_DB_PATH` 정리

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_review-debt-and-wing-uptime-bug_20260804.md 읽고 이어서 작업해줘
```
