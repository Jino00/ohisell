# 세션 인수인계: 하네스 ↔ Anthropic 공개 문서 대조 개정 + iCloud dataless 가드
> 저장일시: 2026-08-16 20:55 KST (세션 기간 2026-08-14 20:19 ~ 08-16 20:55)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ⚠️ 이 세션은 **Ohiselling에서 돌았지만 바꾼 것은 거의 다 이 repo 밖**이다(전역 하네스 + 다른 프로젝트).
> **다음 세션이 이어받을 것은 §6 맨 위 한 건뿐이다** — `WorktreeCreate` 훅 git 리포 실험. 나머지는 종결됐다.

### 이 세션이 남긴 커밋
| repo | 커밋 | 내용 |
|---|---|---|
| `~/.claude` | `8f134b2` | 전역 CLAUDE.md를 iCloud 밖으로 + Anthropic 대조 개정 (127→135줄) |
| `~/.claude` | `30bd9af` | `icloud-dataless-guard.sh` 신설 + SessionStart 배선 (변이 3종 KILLED) |
| `~/.claude` | `68742d2` | stock 자동 push 관측 기억 + 프로젝트 원장 git 상태 |
| `~/.claude` | `c2e528b` `9c6de8b` `19f7edf` | 워크트리 인벤토리 → 방치 기간 분석 → Jino 결정·경로 조사 |
| `stock` | `6c3e48e` | 프로젝트 CLAUDE.md 정리 ⚠️ **자동 push + OCI pull이 딸려 실행됨** |

## 1. 프로젝트 위치 및 환경
- 세션 cwd: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (브랜치 main)
- **실제 작업 대상**: `~/.claude/CLAUDE.md`(전역) · `AI Program/` 아래 프로젝트 CLAUDE.md 17개 · `~/.claude/hooks/` · `~/.claude/settings.json`
- 관련 명령: `readlink -f ~/.claude/CLAUDE.md` · `stat -f "%N size=%z blocks=%b" <파일>` · `~/.claude/hooks/icloud-dataless-guard.sh`(직접 실행 시 정상이면 무출력)
- 환경변수: 해당 없음

## 2. 이번 세션 완료 목록
- ✅ **Anthropic 공개 문서 5종 1차 수집** — `building-effective-agents`(2024-12) · `multi-agent-research-system`(2025-06) · `effective-context-engineering-for-ai-agents`(2025-09) · `code.claude.com/docs/en/sub-agents` · `code.claude.com/docs/en/best-practices`. 뒤 둘은 `docs.claude.com`에서 **301/308 리다이렉트**됨(옛 URL 참조 주의).
- ✅ **전역 `~/.claude/CLAUDE.md` 개정 127 → 135줄**
  - **삭제 0건** — A~E가 명시적으로 반대하는 규칙이 하나도 없었다.
  - 신설 3(§7): 「가장 단순한 구조부터 올라간다」 · 「컨텍스트는 유한 자원이다」 · 「위임 프롬프트는 4요소를 담는다」
  - 수정: §5 Fable 상주 파트너에 D와의 긴장 명기 + 감사 재심 트리거 / §5 하향 위임 강제층에 「빠르고 좁은 변경·지연 민감은 인라인이 정당」 예외 명문화 / `(25절)` 오참조 정정 / **벤치 주장 → 역할 근거로 교체**(확인 불가·시한성이라 §3 위반) / 근거 태그 12개 제거(3자리만 존치)
  - 배선 2: §6 「트랙에 쓰기가 발생할 세션은 필독을 Sonnet 1기에 위임」(요약 아닌 **항목별 실측+좌표** 반환) / §4 「7일 경과 시 제안」을 **재는 순간** 신설(`ls -t docs/references/*audit*`)
  - 헤더에 프로젝트 원장 포인터 1줄 — 어느 프로젝트에서든 「내 CLAUDE.md가 왜 줄었나」의 답에 닿는다
- ✅ **전역 파일을 iCloud 밖으로 이동** — `~/.claude/CLAUDE.md`가 iCloud 심볼릭 링크였다. 실파일로 전환. iCloud엔 `CloudDocs/Claude/CLAUDE.md.backup-not-live`(정본 아님)만 남김. Jino 승인: *"다른 mac에서는 안써"*
- ✅ **프로젝트 CLAUDE.md 17개 정리 1,394 → 1,337줄** — 실변경은 `AI_office`(358→310) · `stock`(127→119) · `Claude Code 업무관리`(18→17) **3개뿐**. 나머지 14개는 애초에 중복이 없었다.
- ✅ **적대 리뷰 1R FAIL(P1=1) → 복원 6건 → 2R PASS(P1=0)**
- ✅ **`icloud-dataless-guard.sh` 신설 + SessionStart 배선** — 변이 주입 3종 중 1종이 살아남아 수리 후 전건 KILLED
- ✅ 기억 2건 신설: `harness-file-lives-in-icloud` · `stock-commit-auto-pushes-to-prod`
- ✅ 원장 2건: `~/.claude/archive/alignment_ledger_20260814.md`(전역) · `alignment_ledger_projects_20260814.md`(프로젝트, 308줄 — 맨 뒤에 프로젝트별 git 상태 절 포함)

## 2-1. 완료 QA (별도 Sonnet, 읽기 전용 — 판정 원문 그대로)
- **작업 목적(앵커 원문)**: 전역 + 모든 프로젝트 CLAUDE.md를 Anthropic 공개 문서(A~E) 1차 출처와 대조해 ①명시적 충돌 제거 ②빠진 원칙 보강 ③전역과의 중복 제거로 매 세션 주입 비용 절감.
- **합격기준(원문)**: ①정본 파일별 대조표 산출(일치/보강/충돌/무관 + 충돌엔 출처 대목) ②충돌 판정 건이 파일에서 실제로 사라짐(diff 확인) ③새 세션 주입 본문에 새 조항 확인 ④프로젝트 파일 총 줄 수 감소(before/after 병기) ⑤적대 리뷰 P1=0 + 별도 Sonnet 완료 QA
- **판정**: **부분달성** — ①②④⑤ 달성, ③ 판정불능, **미달 0건** (2026-08-14 22:11 KST)
- **항목별**:
  - ① 달성 — `alignment_ledger_projects_20260814.md` 확인, 17개 파일 `wc -l` 재검증 1,337 일치
  - ② 달성 — 17개 전 파일 `diff -u /tmp/bak17/... vs 현재` 실행, 삭제·정정 실제 반영 확인
  - ③ **판정불능** — `stat -L` 결과 전역 실제 mtime 21:40, 이 세션 시작 20:19. **모든 편집보다 세션이 먼저 시작**됐으므로 주입 스냅샷이 구버전인 것은 파이프라인 결함이 아니라 시간 순서. 이전 「미달」 근거는 무효화. 그러나 편집 이후 시작된 새 최상위 세션을 열 수 없어 확증도 불가.
  - ④ 달성 — before 1,394 → after 1,337 (−57)
  - ⑤ 달성 — 2R PASS(P1=0) 판정문 + 이월 2건 복원까지 파일에서 직접 확인
- **미달·미판정 항목**: ③ 하나. **다음 세션이 시작 시 주입 본문에 `v2.1`과 새 조항이 실제로 보이는지 1회 확인하면 닫힌다.**
- **목적 전환 여부**: 없음(`🔁 목적 전환` 선언 0건)

## 3. 확정된 결정사항
- **「Anthropic 철학에 반하는 것 제거」의 실제 답은 「제거할 것이 없었다」** — 전역·프로젝트 통틀어 A~E가 명시적으로 반대해 지운 규칙 **0건**. 지운 것은 전부 「전역에 이미 있는 중복」.
- **삭제 기준 = 「명시적 반대」에만. 부재는 반대가 아니다**(§3 추정 금지의 적용). 이 기준이 없었으면 복원한 6건이 근거 없이 사라졌다.
- **적대 리뷰 「1기 + 루브릭」 유지** — 렌즈를 여러 기로 쪼개자는 제안은 **철회**. Anthropic이 다중 전문 심판을 실제로 시도했고 「루브릭 + 단일 판정기」가 더 일관했다.
- **전역 파일은 로컬에 둔다**(Jino 승인 *"다른 mac에서는 안써"*).
- **감사 팀화는 보류, 트리거가 먼저** — 감사가 드문 원인이 비용인지 트리거 부재인지 구분되지 않았다. 재는 순간을 세운 뒤 판단한다.
- **③ 규범형 지식 독립 2경로는 보류** — 「두 경로를 다 돌았는데도 틀렸다」는 관측이 없다. 근거 없이 구조를 더하는 것은 방금 넣은 §7 첫 조항에 걸린다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `~/.claude/CLAUDE.md` | 전역 하네스 v2.1 (135줄, **이제 로컬 실파일**) |
| `~/.claude/archive/alignment_ledger_20260814.md` | 전역 대조 원장(존치 사유 12건 포함) |
| `~/.claude/archive/alignment_ledger_projects_20260814.md` | 프로젝트 대조 원장 308줄 + **프로젝트별 git 상태** |
| `~/.claude/hooks/icloud-dataless-guard.sh` | SessionStart — CLAUDE.md dataless/0바이트/스텁 탐지 |
| `~/.claude/archive/CLAUDE.md.bak-20260814-2304-premove` | 이동 직전 전역 백업 |
| `~/.claude/archive/project-claudemd-bak-20260814.tgz` | 프로젝트 17개 변경 전 백업 (`/tmp/bak17/`에 전개돼 있음) |
| `~/.claude/archive/settings.json.bak-20260814-2324` | settings.json 백업 |
| `memory/harness-file-lives-in-icloud.md` | 전역은 해결, **프로젝트 17개는 여전히 iCloud** |
| `memory/stock-commit-auto-pushes-to-prod.md` | ⚠️ stock 커밋 = 외부 발송 |
| `.claude/anchors/d950d652-...md` | 이 세션 앵커(판정 기록 포함) |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **stock 저장소는 커밋만 해도 GitHub push + OCI 서버 pull이 자동 실행된다**(23:27 실관측, 의도치 않았음). 실린 것은 `CLAUDE.md` 1파일(3+/10−)로 실질 피해 없음. **stock에서는 문서 한 줄이라도 커밋 전 Jino 확인.**
- ⚠️ **`AI_office`의 `CLAUDE.md`·`frontend/CLAUDE.md`가 미커밋 상태로 남아 있다.** 그 repo가 브랜치 `feat/finance-slice7-business-card`에서 **다른 세션 작업 중**이라 일부러 커밋하지 않았다(남의 PR에 섞이는 2026-08-07 사고 형태). AI_office 세션이 처리할 것.
- ⚠️ **프로젝트 CLAUDE.md 17개는 여전히 iCloud에 있다.** 실측: `AI Program` 트리 20,000개 중 **2,360개(11.8%)가 dataless**, 최소 18바이트 — **크기는 방어가 안 된다.** `optimize-storage=1`. 현재 CLAUDE.md dataless는 0건이나 방어 기제가 「자주 열림」뿐이라 **휴면 프로젝트가 무방비**. 클래스 제거(폴더 pin)는 `AI Program`이 37G라 불가.
- `AI Program/CLAUDE.md`(공통)와 `Claude Code 업무관리/CLAUDE.md`는 **git 저장소가 아니다** — 버전 이력이 없고 원장이 유일한 기록.
- `Edit` 툴은 **심볼릭 링크 쓰기를 거부**한다. `stat`도 `-L` 없이는 링크 자체의 mtime을 보여줘 오도한다(완료 QA가 이걸로 오판할 뻔했다).
- 측정 중 **내 python 스크립트가 조용히 「0건」을 반환**한 적이 있다(cwd 리셋으로 상대경로 `stat`이 전부 실패했는데 `except: continue`가 삼킴). 교훈 #123 그대로 — 발견 0건과 실행 안 됨은 같은 숫자로 보인다.

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문)**: 전역 + 모든 프로젝트 CLAUDE.md를 Anthropic 공개 문서(A~E) 1차 출처와 대조해 ①명시적 충돌 제거 ②빠진 원칙 보강 ③전역과의 중복 제거로 매 세션 주입 비용 절감.
- **남은 슬라이스**: **본체는 끝났다.** 남은 것은 아래 ★ 하나(실험)뿐이고, 그 외엔 QA가 ③을 「달성」으로 닫는 절차뿐이다.

### ★ 다음 세션의 첫 작업 — `WorktreeCreate` 훅이 git 리포에서 먹는지 1회 실험
> Jino 지시(2026-08-16 20:54): *"handoff하고 다음세션에서 하자"* — 이번 세션에선 착수하지 않았다.

- **왜**: 먹으면 미래 워크트리가 처음부터 iCloud 밖에 생긴다 → 이동도 삭제도 불필요해지고, `AI Program` 37G 중 25G를 차지하는 워크트리가 iCloud 압박에서 통째로 빠진다. 지금은 **삭제도 안 하기로 결정**됐으므로(위 참조) 이게 유일하게 남은 근본책이다.
- **왜 확정이 아닌가**: 공식 문서(`code.claude.com/docs/en/worktrees.md`)가 *"placing worktrees somewhere other than `.claude/worktrees/`"*를 이 훅의 용도로 **적시**하지만, **예시가 전부 비-git VCS 맥락**이다. git 리포 지원 여부는 문서에서 확정 확인 안 됨 → **추정하지 말고 실험으로 판정할 것.**
- **실험 절차(제안)**: ①`/tmp`에 빈 git 리포 하나 만들고 커밋 1개 ②`WorktreeCreate` 훅을 그 리포 범위에 등록해 iCloud 밖 경로를 stdout으로 반환하게 함 ③`claude --worktree test1`로 워크트리 생성 시도 ④**실제 생성 위치를 `git worktree list`로 확인** — 훅이 준 경로면 성공, `.claude/worktrees/`면 훅이 git 리포에서 무시된 것 ⑤결과를 `~/.claude/archive/worktree_inventory_20260814.md` 맨 뒤에 추가.
- **경계**: 실험은 **`/tmp`의 새 리포에서만** 한다. AI_office·Ohiselling 등 실제 리포에 훅을 걸지 말 것 — 74개 워크트리가 걸린 환경에서 실험하면 되돌리기 어렵다.
- **먹지 않으면**: 확인된 우회로는 `git worktree add <iCloud 밖 경로>` 후 `cd` + `claude` 수동 실행뿐. 그 경우 「자동화 불가」로 결론 내고 닫는다.
- [ ] **③ 종결(사실상 해소)** — 23:33 KST에 `claude -p`로 연 **새 최상위 세션**이 주입 본문에서 `v2.1` 제목 + §7 「컨텍스트는 유한 자원이다」 + §6 Sonnet 위임 조항을 **전부 보고**했다. 증거는 확보됐으나 **판정은 QA 원문(부분달성)을 유지**했다 — §2 재판정 1회를 이미 썼고 내가 만든 증거로 내 판정을 고치면 자기채점이기 때문. **다음 세션 QA가 「달성」으로 닫으면 된다.** 재현 명령: `claude -p "도구 쓰지 말고 주입된 전역 CLAUDE.md 첫 줄만 출력"`
- [x] ~~**node_modules를 iCloud 밖으로**~~ — **하지 않기로 결론(실측이 제안을 기각).** 전부 합쳐 **1.0GB**로 `AI Program` 37G의 2.7%뿐이라 evict 압박이 안 내려가고 빌드 위험만 진다. 내가 앞서 「옮기면 압력이 내려간다」고 한 것은 측정하지 않은 주장이었다.
- [x] ~~**워크트리 정리**~~ — **2026-08-15 Jino 결정: 삭제하지 않는다**(원문 *"그래, 지우지 말고 두자"*). A등급 7개도 대상 아님. **다음 세션은 용량을 이유로 삭제를 재제안하지 말 것** — 회수량이 12%뿐이고 90일 이상이 0개라 생성 속도가 원인이므로 지워도 다시 찬다. 아래는 그 판단에 쓴 근거이므로 남긴다.
- [ ] **워크트리 위치 이전(경로 설정 조사 완료, 실험만 남음)** — 공식 문서 확인 결과 `settings.json` 키·환경변수·CLI 플래그로는 **워크트리 루트를 바꿀 수 없다**(전부 없음). 문서가 명시한 유일한 수단은 **`WorktreeCreate` 훅** — *"placing worktrees somewhere other than `.claude/worktrees/`"*가 이 훅의 용도로 적시돼 있다. **단 예시가 비-git VCS 맥락이라 git 리포 지원 여부는 문서에서 확정 확인 안 됨.** → **다음 행동: 작은 리포에서 1회 실험.** 먹으면 미래 워크트리가 처음부터 iCloud 밖에 생겨 이동도 삭제도 불필요해진다. 확인된 우회로는 `git worktree add <외부 경로>` 후 `cd` + `claude` 수동 실행. `git worktree move` 후 거동은 문서에 없다.
- [ ] ~~워크트리 정리 상세(참고용)~~ — 진짜 용량 원인은 **워크트리 74개 = 24.92GB**(`AI_office/.claude/worktrees`가 22GB, 68개). 그런데 **61개(21.73GB)가 미커밋 변경을 보유(C등급)해 삭제 후보가 아니다.** 안전 회수는 A등급 7개 **1.50GB**뿐. 인벤토리 전문: `~/.claude/archive/worktree_inventory_20260814.md`(등급 A→B→C 정렬, 브랜치·미커밋·main병합·origin존재 병기). 이상치 `reverent-williams-ed89c5`(미커밋 **5,803건**) 직접 확인 권장. **삭제는 되돌릴 수 없는 액션이라 승인 없이 하지 않았다.**
  - 함의: **iCloud 압박은 당분간 안 내려간다** → 오늘 넣은 `icloud-dataless-guard.sh`가 임시방편이 아니라 상시 필요한 장치가 된다.
  - **2026-08-15 추가 분석 — 삭제로는 안 풀린다**: C등급 61개를 방치 기간순으로 재정렬한 결과 **90일 이상 0개 / 60~89일 9개(1.64GB) / 30~59일 36개(12.25GB) / 30일 미만 16개(7.33GB)**. A등급 1.50GB를 더해도 **3.14GB(전체의 12%)**뿐이고, 덩어리는 「한 달 전」 구간이라 방치로 단정할 수 없다. 90일 이상이 0개라는 건 오래 썩은 게 쌓인 게 아니라 **최근 두 달 사이 생성·방치 속도가 빠르다**는 뜻 — 삭제는 증상 대응이다.
  - **방향 전환 제안: 삭제가 아니라 위치.** 워크트리는 재생성 가능한 작업 공간이지 동기화할 자산이 아니다. iCloud 밖으로 옮기면 **아무것도 지우지 않고** 22GB 압박이 사라진다(전역 CLAUDE.md와 같은 수법 — 감시가 아니라 클래스 제거). 수단은 **`git worktree move`**(`mv` 금지 — `.git/worktrees/<name>/gitdir`의 절대경로가 깨진다). 단 이 워크트리들은 **`git status` 한 번에 2분 초과**(08-15 실측 타임아웃, iCloud dataless가 원인 추정)라 일괄 이동은 위험 → 60일 이상 9개부터 단계적으로.
  - **확인 안 됨**: Claude Code가 워크트리 루트 경로를 설정으로 받는지 — 받는다면 미래 워크트리를 처음부터 iCloud 밖에 만드는 게 근본책이다. 문서 확인 필요(추정하지 않음).
  - 별개 이슈: 미완성 워크트리 61개 방치 자체가 관리 문제다(용량과 무관하게).
- [ ] **AI_office 미커밋 2파일** — AI_office 세션이 자기 판단으로 처리
- [ ] **§5 「Opus가 코딩 벤치에서…」 문구** — 역할 근거로 교체 완료. 실제 모델 성능·가격이 궁금하면 별도 조회(하네스 규칙의 근거일 필요 없음)
- [ ] **③ 규범형 2경로** — 보류 유지. 「두 경로를 다 돌았는데도 틀렸다」는 관측이 나오면 그때
- [ ] **감사 팀화** — 트리거를 세웠으니, 감사가 실제로 돌기 시작한 뒤 무거우면 그때 4축 팬아웃

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/memory/HANDOFF_harness-anthropic-alignment_20260814.md 읽고 이어서 작업해줘`
