# docs/wiki — 지혜층 (L2)

> **이 폴더의 존재 이유 한 줄**: 교훈은 «적혔는데 코드가 안 읽어서» 재발한다.
> 여기 있는 각 패턴은 **집행 지점(`enforcement`)을 반드시 하나 갖는다.**
> 발단 = 교훈 #202 (D-NAO-164, 2026-08-10) — 07-22에 기록된 지식이 배선되지 않아
> PAO 집행 59건이 옥션에 닿지 않았고, 발견한 것은 시스템이 아니라 Jino의 질문이었다.

## 층 구조 — 여기서 뭘 하고 뭘 하지 않는가

| 층 | 위치 | 역할 |
|---|---|---|
| **L1 기록** | `.claude/skills/failure-memory/failures.jsonl` · `.claude/memory/LESSONS_LEARNED.md` · `HANDOFF_*.md` · `docs/tracks/**` · 전역 memory 토픽 파일 | **정본.** 사건이 일어난 대로. 소급 수정 금지 |
| **L2 지혜** | **여기** `docs/wiki/` | L1의 교훈을 **재사용 가능한 패턴**으로 승격 + **집행 지점 지정** |
| **L3 집행** | `scripts/*.sh` · 코드 판별자 · 테스트/변이 · git 훅 | 지혜의 실체. 사람이 기억 안 해도 발동 |

**정본은 L1 하나다.** 위키는 L1을 대체하지 않고 요약·링크·처분만 한다 —
두 목록이 갈라져 모순 지시가 됐던 사고(2026-08-07 통합)를 반복하지 않기 위해서다.
위키와 L1이 어긋나면 **L1이 이긴다**.

## 규칙 (짧다 — 길면 안 지켜진다)

1. **한 파일 = 한 패턴.** 파일명은 kebab-case 동사구(`write-to-the-binding-layer.md`).
2. **frontmatter 필수 4필드**: `pattern` · `sources` · `enforcement` · `recurrence_tags`.
3. **`enforcement`는 아래 6값 중 하나**. `none`이면 그 항목은 **지식 부채**이고
   [[WISDOM]]의 부채 목록에 자동으로 뜬다 — 부채인 것 자체는 정상이다. 숨기는 게 문제다.
4. **`sources`는 L1을 가리킨다**(교훈 번호·D-N·HANDOFF 파일명). 위키에 새 사실을 창작하지 않는다.
5. 링크는 `[[파일명]]` — 옵시디언 문법이자 기존 memory 토픽 파일의 문법이다.

### `enforcement` 6값

| 값 | 뜻 | 이 repo의 예 |
|---|---|---|
| `tool` | 절차를 스크립트/훅이 강제 | `safe_deploy.sh` · `next_ids.sh` · `safe_merge.sh` · pre-commit 훅 |
| `discriminator` | **코드가 데이터를 읽어** 분기 | `use_group_bid_amt`로 입찰 레이어 판별(B-4, 미구현) |
| `test` | 회귀를 테스트·변이로 고정 | 501개 청크 경계 테스트(D-NAO-163 2R) |
| `hook` | 세션 하네스가 개입 | `scope-anchor.sh` · `chore-delegation-gate.sh` |
| `principle` | CLAUDE.md 판단기준 | **최후수단** — 텍스트 규칙은 세 번 다 못 막았다는 게 이 시스템의 출발 명제 |
| `none` | **미처분 = 지식 부채** | 감사가 매주 들춘다 |

## 주간 지혜 감사

`docs/wiki/AUDIT_PROTOCOL.md` 참조. 산출물 없이 종료 금지.

## 옵시디언

이 repo 루트를 볼트로 연다. `docs/wiki/OBSIDIAN_SETUP.md` 참조. **옵시디언은 뷰이지
쓰기 주체가 아니다** — 사람이 그래프·백링크로 보는 창이고, 갱신은 세션이 git에 한다.
