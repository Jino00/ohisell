# 세션 인수인계: ohisell UX2 이어받기 — 밸브 관문 상태 실측 (코드변경 0)
> 저장일시: 2026-07-17 21:59 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ⚠️ **이 세션은 관찰·검증 전용. 코드 변경 0, 커밋 0, 배포 0.** 남길 결론은 "UX2의 밸브 관문은
> 온전하며 내일 아침 자동 판정된다" 한 줄 + 타임존 교훈 하나.

## 0. 이 세션의 정체 (왜 존재했나)

Jino: "아까 archive되었던 UX세션을 계속 이어서 하고 싶어" → 대상 특정 과정에서 밝혀진 것:

- **UX 시리즈는 UX1 · UX2 · "UX 세션 복구" 셋뿐. UX3는 없다**(세션 200개 제목 전수 + 아카이브
  포함 트랜스크립트 전문검색 모두 무소득). 흐름상 UX1 → UX2 → (UX 세션 복구 = 사실상 UX3).
- Jino 지정: **"UX2 세션 마지막에서 이어서 진행하자"**.
- ★**UX2가 아카이브된 이유 = 내가 PR #26을 병합해서**다. 데스크톱 앱 `ccAutoArchiveOnPrClose:true`가
  PR 병합 시 세션을 자동 아카이브했다(그 설정은 D-NAO-53 세션이 21:10에 OFF). Jino의
  "너가 archive해버렸던"이 이 뜻. **지금은 OFF라 재발하지 않는다.**

## 1. 프로젝트 위치 및 환경
- 로컬 경로(이 세션 워크트리): `Ohiselling/.claude/worktrees/naver-ad-x1b-sprint-a42eb5`
  - 브랜치 `claude/ux-session-continue-ba62f6` · HEAD `4634814` · 미커밋 0
  - ⚠️ **main보다 2커밋 뒤처짐**(`f523aca` 병합, `02d9c5f` 제안 카드 유형 필터). 여기서 코드 작업을
    시작하면 **ff 먼저.**
- UX2 원래 워크트리: `.claude/worktrees/mop-command-center-design-7e087a` (브랜치 동명)
- prod: `ssh sellc.ohitech.co.kr` (BatchMode 동작) · repo `/home/ubuntu/ohisell`
  - **DB = `/home/ubuntu/ohisell/backend/ohisell.db`** (198MB). ⚠️`/home/ubuntu/ohisell/ohisell.db`는
    4096B 빈 파일 — 속지 말 것.
  - **배포는 `scripts/safe_deploy.sh`만**(직접 scp 금지, D-NAO-49).
- 서버 시계 = **UTC**. DB 저장값은 대부분 **KST**(§5 참조).

## 2. 이번 세션 완료 목록

- ✅ **UX 세션 3건 식별·UX3 부재 확증** — `list_sessions`(200건) 제목 전수 + `search_session_transcripts`("UX3", 아카이브 포함) → 0건.
- ✅ **UX2 마지막 지점 규명** — UX2의 실제 줄기 = D-NAO-47 커맨드센터 + D-NAO-48 스위치 +
  D-NAO-50 키워드 밸브(PR #26·#27 병합·배포 완료). 곁가지였던 쿠팡 광고비 페처 버그
  ("고칠까요?"로 끊긴 질문)는 **후속 "UX 세션 복구" 세션이 PR #30으로 이미 처리** → 재작업 불필요.
- ✅ **밸브 첫 실측 관문 상태 실측**(§3 표) — prod DB 직접 조회. **0건 = 정상 예정 상태**로 판정.
- ✅ **예약 실재 확인** — `dnao47-valve-cron-verify` enabled · fireAt `2026-07-17T22:50Z`(= 07-18 07:50 KST).
- ✅ **예약 프롬프트 타임존 버그 가설 → 실측 기각**(§5). 파일 수정 안 함.
- ✅ **메모리 갱신 1건** — `~/.claude/projects/…-Ohiselling/memory/sqlite-server-default-now-is-utc.md`에
  **"★역방향 함정"** 절 추가(이 세션 유일한 파일 변경).

## 3. 확정된 결정사항 / 실측 사실

**Jino 결정**: 밸브 첫 실측은 **오늘 밤 수동 발화로 당기지 않고 예정대로 내일 07:35 자연 발화**로 둔다.
(당기기 선택지를 제시했으나 "예정대로" 선택 — 관문 조건을 원설계대로 유지.)

**prod 실측 (2026-07-17 21:2x~21:5x KST)**:

| 항목 | 실측값 |
|---|---|
| `naver_change_log` 전체 | 35행 |
| `external_bid_change` | **0건** (누적 0 — 한 번도 없음) |
| 최근 3일 action 분포 | flight_pacing 12 · update_bid 4 · optimizer_change 2 |
| `sync_naver_entity` 마지막 실행 | **07-17 07:37:43 KST** · `last_status=ok` · `is_enabled=1` |
| 밸브 코드 prod 배포 시각 | **07-17 12:25:27 KST** (`entity_sync.py` mtime) |
| 크론 정의 | `sync_naver_entity` = `35 7 * * *` (**07:35 KST 하루 1회**) |

★**해석(중요)**: **0건은 경보가 아니다.** 밸브 배포(12:25)가 오늘 동기화(07:37)보다 늦어 **밸브에게
아직 기회가 없었을 뿐**. 잡은 enabled·ok이므로 오늘 쿠팡 13일 정지의 진범이었던 "크론 미등록"
유형이 **아니다**. → **첫 실측 = 07-18 07:35 동기화**, 판정 = 07:50 예약.

**밸브 코드 검증(읽기만)**: `entity_sync.py`의 `NaverChangeLog` 삽입 **5곳 전부** `changed_at=now`
(= `kst_now()`, line 447) 전달 → 밸브 행도 KST. 예약 프롬프트의 `date(changed_at)=date('now','+9 hours')`
비교는 **양변 KST로 정합** = 버그 아님.

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `~/.claude/scheduled-tasks/dnao47-valve-cron-verify/SKILL.md` | **내일 07:50 판정 프롬프트.** 기준·롤백절차·플랩감시 전부 내장. 수정 불필요(검증 완료) |
| `backend/app/services/naver_ad/entity_sync.py` | 밸브 2종 본체. `_log_external_bid_change`(L214~), `_emit_inventory_side`(L388~), `now=kst_now()`(L447) |
| `backend/app/services/scheduler_service.py` | L989 `("sync_naver_entity","35 7 * * *")` · L1197 핸들러 매핑 · L283 잡 함수 |
| `backend/app/models.py` L1603 | `NaverChangeLog` 모델(`changed_at`은 server_default=UTC지만 **모든 write가 KST 명시 전달**) |
| prod `scheduler_state` 테이블 | 크론 발동 판정의 단일 진실 (`job_name/is_enabled/cron_expression/last_run_at/last_status`) |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 파일. 내일 판정 결과를 D-NAO-47·50에 한 줄씩 기록할 곳 |
| `.claude/memory/HANDOFF_ohisell-mop-command-center-implemented_20260717.md` | UX2 본체 인계문서 |

## 5. 알려진 이슈 / 주의사항

★**타임존 — 이 세션에서 내가 양방향으로 두 번 헛짚음. 반드시 읽을 것.**
메모리 `sqlite-server-default-now-is-utc.md`의 "★역방향 함정" 절에 기록함. 요지:
- **`naver_change_log.changed_at` = KST** (id=21 `08:55:38` = 실제 08:55 KST 17E 실집행과 일치로 확증)
- **`scheduler_state.last_run_at` = KST** (서버 시계가 UTC인데도)
- **셸 `date` / SQLite `datetime('now')` = UTC** ← 이 둘만 `+9` 필요
- 내 실수 ①`last_run_at`에 `+9` → **미래 시각**(내일 06:05) 산출하고도 잠깐 못 알아챔
  ②`changed_at`이 UTC일 거라 가정 → "예약 프롬프트에 타임존 버그" 가설 → **원시값 조회로 기각**
  (멀쩡한 판정 프롬프트를 고칠 뻔)
- **적용**: 타임존은 외우지 말고 **알려진 사건과 대조해 실측**("08:55 실집행이 몇 시로 찍혔나").
  `+9` 결과가 **미래면 이중 보정**. 판정 쿼리 고치기 전에 **원시값부터**.

**원칙 22 — 아직 말하면 안 되는 것**: "밸브가 작동한다"는 **내일 실측 전까지 금지**. 현재까지의
증거는 "밸브가 아직 안 돌았다"뿐이다.

**병행 세션(건드리지 말 것)**:
- `naver-ad-critical-bugs-27ff24` / `claude/ecstatic-bhabha-a8f472` — **PR #33 OPEN, 실행 중**.
  형제 페처 3종(Wing 판매분석·RG 정산·오하이테크)에 실패 보고 경로 추가. UX 세션 복구가 칩으로
  남긴 건. **내 영역 아님**(원칙 20 · D-NAO-49 상호 clobber).
- `mop-command-center-design-7e087a` / `claude/peaceful-lewin-cd898c` — RG 정산 업로드 60s 타임아웃.

## 6. 다음에 할 작업 (미완료)

- [ ] **(자동, 07-18 07:50 KST) 밸브 2종 첫 실측 판정** — `dnao47-valve-cron-verify` 예약이 스스로
      발화·판정·보고한다. 사람/세션이 미리 할 일 없음.
      판정 기준: `external_bid_change` 오늘 행수가 **수십~수백=✅정상** / **수천~91,005 근접=❌붕괴
      → 즉시 롤백**(`_log_external_bid_change` 호출 주석 + `pm2 restart ohisell-backend`, 백업
      `backups/naver-d-nao-47_20260716_2241_predeploy.db`) / **0건=⚠️크론 발동 여부부터 확인**
      (`SELECT MAX(datetime(synced_at)) FROM naver_entity WHERE entity_type='keyword'`가 07:35 근처인가
      — 아니면 크론 문제이지 밸브 문제 아님).
      키워드 밸브: 방향당 200 초과인데 `__bulk__`가 아닌 개별행으로 쌓이면 ❌가드 붕괴 → 롤백.
- [ ] **(자동, 매일 08:55) `naver-04-auto-operation-daily`** — 04 자동운영 감사·보고(D-NAO-51/52).
- [ ] 판정 후 **트랙 파일 기록** — `track_naver-ad-optimization.md`의 D-NAO-47·D-NAO-50에 "밸브 라이브
      실측" 한 줄씩(예약 프롬프트가 지시하고 있음).
- [ ] (조건부) 플랩 관측 시 codex R2 AGREE-DEFER 합의대로 **댐핑 재론** → Jino 보고.
- [ ] (선택) 이 워크트리에서 코드 작업 재개 시 **main ff 먼저**(2커밋 뒤).

## 7. 새 세션 시작 프롬프트

아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/worktrees/naver-ad-x1b-sprint-a42eb5/.claude/memory/HANDOFF_ohisell-ux2-valve-gate-verified_20260717.md 읽고 이어서 작업해줘
```
