# 세션 인수인계: prod 무중단 배포 + 광고 push 500 수정 + 대시보드 전체 갱신 (2026-08-05 밤)
> 저장 2026-08-05 23:0x KST · 트랙: **쿠팡 손익 정합**(인프라 갈래) — 트랙 파일 `docs/tracks/active/track_coupang-rocket-1p.md` D-19
> 이 워크트리 `claude/zero-downtime-deploy` HEAD `2a9fd69`(PR #203) · 관련 PR **#204**(`fd21cc7`)·**#205**(`e8cf08b`) 별도 워크트리
> 세 PR 전부 codex 소급 리뷰 대기 상태로 **OPEN**(병합 미실행)

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (main 고정, 작업은 워크트리)
- 이 세션 워크트리: `.claude/worktrees/zero-downtime`(브랜치 `claude/zero-downtime-deploy`, PR #203)
- 관련 워크트리(같은 세션이 만든 자매 PR): `.claude/worktrees/fix-ad-change-log-idempotent`(브랜치 `claude/fix-ad-change-log-idempotent`, PR #204) / `.claude/worktrees/agent-a0877aa18cb944a75`(브랜치 `claude/unified-refresh-button`, PR #205)
- prod: `ssh sellc.ohitech.co.kr`, 백엔드 포트 **8001**(nginx 앞단) → 무중단 배포 후 활성 포트는 **8011**(블루-그린 페어 중 하나), 배포는 `scripts/safe_deploy.sh --restart`만(무중단 경로 기본화됨, 아래 §3)
- 테스트: 백엔드 `cd backend && PYTHONPATH="$PWD/../tools:$PWD" .venv/bin/python -m pytest -q` (**4,813 passed**, `test_rocket_1p_channel_pnl` 1건 실패는 origin/main에서도 동일 — 무관) / 프론트 `cd frontend && npx vitest run`(**212 passed**)

## 2. 이번 세션 완료 목록
- ✅ **출발점 규명** — Jino가 대시보드 빨간 배너("쿠팡 광고비 수집 중단")를 보고 "이건 왜 뜨는거야?" 질문 → 배너는 정직했다(광고비 2큐 26시간 미갱신). 상류를 파고들어 병소 3개 확정.
- ✅ **★핵심 발견 — prod 502는 서버 장애가 아니라 배포 재시작 그 자체였다.** pm2 종료 기록 전수가 exit 0 + SIGINT/SIGKILL(명령에 의한 정상 종료), 3일간 크래시·OOM **0건**. 재시작 횟수 08-03 34회·08-04 19회·08-05 8회가 배포 빈도와 정확히 비례, 종료→기동 각 ~47초 창이 nginx `connect() failed` 시각과 초 단위 일치. 그 47초 창에서 갱신 버튼 POST·Mac 페처 push가 유실 → "버튼을 눌러도 아무 일이 없다"의 정체(교훈 #142).
- ✅ **①블루-그린 무중단 배포** (`fe4c035`, `scripts/zero_downtime_restart.sh` 신설 + nginx upstream 구조 + `backend/app/services/scheduler_leader.py` 파일락 리더 선출). `safe_deploy.sh --restart`가 이 경로를 기본으로 쓰도록 배선.
- ✅ **②쿠팡 광고 설정 push HTTP 500 멱등 수정**(PR #204, `fd21cc7`) — `SessionLocal(autoflush=False)`에서 `ad_change_history.ingest_events()`와 `ad_settings_diff.ingest()`가 한 트랜잭션을 공유해 교차 서비스 중복 삽입이 UNIQUE 위반을 냄. SAVEPOINT(`db.begin_nested()`) + 즉시 flush로 흡수(교훈 #143).
- ✅ **③대시보드 전체 갱신 버튼**(PR #205, `7a716d5`+`e8cf08b`) — 6큐(쿠팡·네이버·광고비 등) 한 번에 트리거 + POST 재시도 + 폴링 실패 방어 + sessionStorage 영속화.
- ✅ **④cao.service 크래시 루프 정지** — 3일간 약 4.8만 회 재시작 루프를 발견해 정지 처리(별도 원인, 이 인프라 정리와 병행 처치).
- ✅ **적대 리뷰 3PR 전건 실행** — codex CLI 쿼터 소진으로 Opus 1기 적대 리뷰로 대체(스킬 폴백 규칙). PR #203 자체 리뷰 → P1 4건 전건 채택·수정(`2a9fd69`). PR #204 → P1 1건(SAVEPOINT 이전 흡수 가정) 근거 실측으로 **부분 기각** + 하드닝 채택, P2 4건 채택. PR #205 → P1 2건 전건 채택. 처분 표는 PR #204·#205 코멘트로 게시 완료(§7).
- ✅ 트랙 파일 D-19 / LESSONS_LEARNED #142·#143 / claude-progress.txt 기록(`fbc1c9b`, 병렬 세션 시점 — 이 HANDOFF가 review-round 결과까지 마저 덮음)

## 3. 확정된 결정사항 (번복 금지)
- **D-19(트랙 §확정 결정사항) — prod 백엔드 재시작 = 무중단(블루-그린) 기본, 구 `pm2 restart`는 legacy 플래그로만.** 재시작이 곧 다운타임이던 구조를 닫는다. `safe_deploy.sh --restart`가 기본 경로.
- **`/api/health`가 `scheduler_leader`·`holds_scheduler_lock`을 노출한다**(`backend/app/main.py:99` 이하) — 배포 스크립트가 "구 프로세스 종료 후 신 프로세스가 스케줄러를 실제로 물려받았는지"를 판정하는 유일한 신호. 로컬 `127.0.0.1:포트`가 아니라 **공개 URL로 프로브해 pid까지 대조**해야 한다(서버 로컬에서 Host 헤더로 찌르지 않음 — 07-17 무인증 공개 사고의 처방을 훼손하므로).
- **리더 락 보유(`holds_lock()`)와 스케줄러 실제 가동(`is_leader()`)은 별개 신호로 분리 유지** — 합치면 "락은 쥔 채·크론은 죽은 채·헬스는 리더"인 거짓 초록이 난다(P1-1, 아래 §5).
- **`.restart-lock`으로 무중단 스크립트 동시 실행을 막는다** — 단독 실행이 정식 사용법이라도, 세션 A가 드레인 중일 때 B가 겹치면 방금 라이브된 프로세스를 B가 죽인다(P1-4).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `scripts/zero_downtime_restart.sh` | 블루-그린 전환 본체. `.restart-lock`·pm2 이중 save·공개 URL pid 대조 포함 |
| `backend/app/services/scheduler_leader.py` | 파일락 리더 선출. `is_leader()`=락+기동 성공, `holds_lock()`=락만 |
| `backend/app/main.py` | lifespan에서 리더 승격/해제, `/api/health`가 두 신호 노출 |
| `scripts/safe_deploy.sh` | `--restart`가 무중단 경로 기본 호출 |
| `backend/app/routers/coupang_ops.py`(또는 광고 설정 ingest 라우터) · `backend/app/services/ad_change_history.py` · `ad_settings_diff.py` | PR #204 — SAVEPOINT 멱등 흡수 |
| `frontend/src/lib/streamRefresh.ts` · `frontend/src/lib/bulkRefreshPersistence.ts` · `frontend/src/pages/Dashboard.tsx` | PR #205 — 전체 갱신 버튼·폴링 방어·세션 영속화 |
| `docs/tracks/active/track_coupang-rocket-1p.md` (D-19) | 이 세션의 확정 결정 |
| `.claude/memory/LESSONS_LEARNED.md` #142·#143 | 원인 규명 서사 |

## 5. 알려진 이슈 / 주의사항 — 적대 리뷰에서 나온 것 (전부 처리됨, 재발 방지용 기록)
- **P1-1(가장 위험했던 것): "안전장치가 거짓 초록을 낼 수 있다."** 초판은 락을 잡는 즉시 `_is_leader=True`로 만들고 그 뒤에 스케줄러를 켰다. 기동 실패(예: 구 프로세스가 SQLite 쓰기 락을 오래 쥐어 `OperationalError`)를 lifespan이 흡수하면 **락은 쥔 채·크론은 죽은 채·`/health`는 "리더"**가 되고, 배포 스크립트는 그 헬스만 보고 "✅ 무중단 재시작 완료"를 출력 — 주문 동기화·광고 적재·자동입찰이 전부 정지한 채로. 이 저장소가 반복해 당해온 green-while-stale과 같은 계열. 수정: `is_leader()`=락+기동 성공 분리, 기동 실패 시 락 반납(고착 방지), 백오프 재시도.
- **P1-2**: pm2 save가 마지막에만 실행돼 중간 실패 시 dump에 구 프로세스가 남아 재부팅 시 영구 502 시한폭탄 — 전환 확정 직후·구 프로세스 삭제 직후 두 번 저장으로 수정.
- **P1-3**: 전 과정 검증이 `127.0.0.1:포트`뿐이라 nginx(사용자 경로)를 한 번도 안 통과 — sites-enabled 중복이면 스크립트는 "다운타임 0초"를 출력해도 실제는 전 요청 502일 수 있었다. `/api/health`를 공개 URL로 프로브해 pid 대조하도록 수정.
- **P1-4**: 무중단 스크립트 동시 실행 가드 부재 — `.restart-lock` 신설.
- **PR #204 P1-5는 근거 실측으로 부분 기각**: 리뷰어는 `db.begin_nested()` 안의 `flush()`가 이전 pending 작업까지 롤백시킨다고 주장했으나, SQLAlchemy 2.0.48 소스 확인 + 재현 스크립트로 **`begin_nested()`가 SAVEPOINT를 걸기 전에 이미 flush한다**(전제 불성립)를 검증. 다만 사전 flush를 명시적으로 넣어 불변식을 우리 코드에 못 박았다(비용 실측상 잡음 범위).
- **PR #205 P1-6**: 폴링 중 `getStatus` 무방비 — 215초 창의 502 한 번이면 성공한 수집이 "❌ 실패"로 확정됐다. `POLL_FAILURE_LIMIT`(5) 연속 실패해야 확정, 성공 시 리셋.
- **PR #205 P1-7**: 갱신 패널이 컴포넌트 로컬 state라 페이지 이동/새로고침 시 실패 흔적이 사라졌다. `bulkRefreshPersistence.ts`(sessionStorage + `savedAt`, 30분 초과분 미복원, 진행 중 상태는 `stale`로 변환해 복원 — "더는 추적 안 함"을 숨기지 않음).

## 6. 라이브 증거 (2026-08-05, 이 세션에서 직접 관측)
- 블루-그린 전환 **5회 각 7~10초**, 0.2초 간격 프로브 61/61·62/62 = 전부 200.
- standby → 승격 로그: `21:31:36` → `21:31:40`.
- 광고 설정 push 성공: `21:34:06`, `duplicate 116` 흡수 집계, **그동안 유실되던 변경 11건 최초 적재**.
- 공개 URL `/api/health`가 `scheduler_leader`·`holds_scheduler_lock`을 실제로 노출(curl 확인).
- 백엔드 전체 테스트 **4,813 passed**(`test_rocket_1p_channel_pnl` 1건은 origin/main과 동일 실패, 무관). 프론트 **212 passed**(vitest), `npm run build` 성공.

## 7. 이번 세션에서 처리한 PR·코멘트
| PR | 내용 | 상태 |
|---|---|---|
| #203 `claude/zero-downtime-deploy` | 무중단 배포 본체 | OPEN, P1 4건 자체 리뷰 후 `2a9fd69`로 수정 완료(별도 PR 코멘트는 이 HANDOFF 작성 시점 기준 미게시 — 다음 세션이 필요시 게시) |
| #204 `claude/fix-ad-change-log-idempotent` | 광고 설정 push 500 SAVEPOINT 멱등 | OPEN, push 완료(`fd21cc7`) + 처분 표 코멘트 게시: https://github.com/Jino00/ohisell/pull/204#issuecomment-5192697524 |
| #205 `claude/unified-refresh-button` | 전체 갱신 버튼 | OPEN, 처분 표 코멘트 게시: https://github.com/Jino00/ohisell/pull/205#issuecomment-5192711170 |

## 8. 미결/이월
- ⓐ **codex 소급 리뷰** — CLI 쿼터 리셋(08-09) 후 3PR 전부 소급 실행 필요.
- ⓑ **`scripts/next_ids.sh` 정규식 사각지대** — `## #140 —` 형식(공백+대시)을 못 읽어 이미 쓴 번호를 "다음 번호"로 내놓는다. 충돌 방지 도구 자체의 결함 — 다음에 이 도구를 손볼 세션이 고칠 것.
- ⓒ `test_rocket_1p_channel_pnl::test_net_profit_suppressed_when_promo_source_is_absent`가 origin/main에서 이미 실패 중(이 세션 무관, 별도 조사 필요).
- ⓓ **PR 3건(#203·#204·#205) 병합 대기** — Jino 승인 후 병합 순서 판단 필요(#203이 인프라 기반이라 먼저가 자연스러움).
- ⓔ **★통합 버튼(전체 갱신)의 라이브 1클릭 검증 미실시** — 6큐 전원의 `last_success_at`이 실제로 전진하는지 브라우저에서 확인하는 절차가 남았다. Jino Mac에서 Chrome 창이 수 분간 뜨는 작업이라 **Jino 확인 후 실행 예정**(다음 세션이 임의로 선행하지 말 것).
- ⓕ **오픽스 RG 매출이 손익 엔진 밖(−17,342,298원)** — 쿠팡 손익 정합 트랙의 원래 미결(계약 합격기준 ① 유일 미충족), 이번 세션 스코프 아님. 상세: `.claude/memory/HANDOFF_rocket-1p-parity-live+asn-receivable_20260805.md` §6.

## 9. 다음 세션이 이어받을 것
1. Jino 승인 하에 PR #203·#204·#205 병합 순서 결정(§8-ⓓ).
2. §8-ⓔ 라이브 1클릭 검증 — Jino가 신호를 주면 진행.
3. codex 소급 리뷰(08-09 이후, §8-ⓐ).
4. 오픽스 RG 매출 엔진 편입 작업 재개(별도 트랙 앵커, §8-ⓕ).

## 10. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_zero-downtime-deploy+unified-refresh_20260805.md 읽고 이어서 작업해줘
```


---

## 11. ★후속 종결(23:0x~23:28, 같은 세션) — §8-ⓓ·ⓔ 해소됨

**ⓓ PR 3건 전부 병합 완료**(main `da9aaae`): #204 → #205 → #203 순. #203은 병행 세션과 **D-18 번호 충돌**(같은 날 ASN 수집 결정이 main에 먼저) → 규칙대로 main을 트렁크로 두고 **이 세션의 결정을 D-19로 재번호**(본문 불변), HANDOFF·claude-progress·TRACKS.md 참조 동기 수정(`13c6e4b`+`7cc9907`). next_ids.sh가 못 막은 3번째 실사고 — §8-ⓑ 수리 근거 추가. **prod = main 정합**(오늘 순차 배포로 이미 동일 코드 가동 중, 병합에 따른 추가 배포 없음).

**ⓔ 통합 버튼 라이브 1클릭 검증 완료**(23:00, Jino 승인 하 실행): 대시보드 버튼 1회 클릭 → **6큐 전원 요청 전달**, 패널이 큐별 단계(요청 전달됨→Mac 수집 중→✅완료)를 실시간 표시.
- 4큐(ofix 판매분석·ofix 광고비·ohitech 로켓광고·로켓 발주/정산): 자동 완주, `last_success_at` 전진 실측 ✅
- RG 2큐: 쿠팡 로그인 만료(08-03부터)로 1차 실패 — **설계대로 표면화**(🔑 로그인 필요). Jino 로그인 후 두 계정 모두 run 성공(WING2 23:04:41 데몬 경유 · WING1 23:20:45 1회성 `rg`). **정산 신규 파일 0건**(`열거 5 / prod 결손 0`)이라 시각 미전진 — RG는 주 단위 정산이라 **정상**이며 stale=false. 빠져 있던 상품사이즈 파일 push(153행+497행).
- **판정: 합격기준 ①의 "6큐 last_success_at 전진"은 RG 캐던스에 과했던 기준. "1클릭 → 6큐 요청 전달·수집 실행"은 전 큐 검증 완료.**

**이 과정에서 새로 발견·기록(이월 2건 추가, failures.jsonl 2줄)**:
- ⓖ **RG "성공·새 파일 0건" 완주가 UI에 안 보인다** — refresh-complete(`mark_run_complete`)는 요청만 소멸시키고 `last_success_at`을 안 올림(`_settle_values` 실측). 시각은 정산 엑셀 실업로드(`rg_mark_heartbeat`) 시에만 전진. 새 파일 없는 성공 run을 패널이 215초 후 "응답 없음"으로 오보(23:04:41 WING2가 실사례). 처방 후보: 완주 시각 표면 추가 or UI가 「요청 소멸+무에러」를 "완료(변경 없음)"로 읽기.
- ⓗ **RG 쿨다운(3600s)이 로그인 실패 시도에도 소모** — 로그인 직후 재시도가 1시간 봉쇄. ★재기동 우회는 금물: 데몬 자식인 Chrome까지 죽어 **방금 한 로그인이 소멸**한다(23:14 실사고, JSESSIONID=세션 쿠키). 올바른 우회 = 1회성 실행(`wing_browser_fetcher.py rg`) — 폴 루프 상태를 안 거쳐 쿨다운 무관, 기존 Chrome adopt라 로그인 유지. 처방 후보: login_required 실패는 쿨다운 미소모.

**다음 세션이 이어받을 것(§9 갱신)**: ①codex 소급 리뷰(08-09 이후) ②§8-ⓑ next_ids.sh + ⓖ·ⓗ RG 표면/쿨다운 수리(한 묶음 후보) ③§8-ⓒ 기존 테스트 실패 조사 ④§8-ⓕ 오픽스 RG 매출 엔진 편입(트랙 본류).
