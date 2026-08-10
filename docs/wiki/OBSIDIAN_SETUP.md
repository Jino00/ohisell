# 옵시디언 셋업 — 이 repo를 볼트로 열기

> **옵시디언은 «뷰»이지 쓰기 주체가 아니다.** 사람이 그래프·백링크·검색으로 보는 창이고,
> 갱신은 세션이 git에 한다. 옵시디언에서 편집해도 되지만, 그건 git 파일을 고치는 것이다.

## 왜 마이그레이션이 0건인가

기존 memory 토픽 파일과 위키가 이미 `[[링크]]` 문법과 YAML frontmatter를 쓴다.
**폴더를 열기만 하면 그래프가 생긴다.** 별도 변환·동기화 스크립트가 없다 —
그게 이 설계의 요점이다(정본 이원화 금지).

## 여는 법

1. Obsidian 실행 → **Open folder as vault**
2. 폴더 선택:
   ```
   /Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling
   ```
3. "Trust author and enable plugins?" → **플러그인 없이 시작**해도 된다(v1은 코어 기능만).

## 반드시 할 설정 — 제외 경로

이 repo엔 코드가 수만 파일 있어서 그대로 열면 그래프가 무의미해진다.
**Settings → Files & Links → Excluded files** 에 아래를 추가:

```
backend
frontend
node_modules
.claude/worktrees
docs/archive
.venv
```

`.git`과 `.obsidian`은 옵시디언이 알아서 무시한다.

## 홈 노트로 쓸 4개

| 파일 | 무엇 |
|---|---|
| `docs/wiki/WISDOM.md` | **지혜 인덱스 + 부채 목록** — 여기서 시작 |
| `docs/TRACKS.md` | 활성 트랙 인덱스 |
| `.claude/memory/LESSONS_LEARNED.md` | 교훈 원장(L1 정본) |
| `docs/tracks/active/track_naver-ad-optimization.md` | PAO 결정 원장 |

즐겨찾기(Star)에 넣어두면 사이드바에서 바로 열린다.

## 그래프에서 볼 것

- **[[WISDOM]] 주변 클러스터** = 지혜층. 부채 3건이 어디에 매달렸는지 한눈에 보인다.
- **고립 노드**(링크 0개) = 아무도 참조하지 않는 지식. 감사에서 «패턴 아님»으로 처분할 후보.
- **허브 노드**(링크 다수) = 이 프로젝트의 반복 주제. `claimed-vs-wired`가 그럴 것이다.

## 알려진 한계

- ⚠️ **iPhone/iPad 옵시디언에서는 이 볼트를 못 연다.** 모바일 앱은 자체
  `iCloud~md~obsidian` 폴더 안의 볼트만 접근한다. 이 repo는 iCloud Drive의 다른 경로에 있다.
  모바일 열람이 필요하면 별도 결정 필요(v1 스코프 밖).
- `.obsidian/`은 커밋하지 않는다(`.gitignore`) — 개인 UI 설정이라 세션마다 갈릴 이유가 없다.
- **옵시디언에서 파일을 옮기면 링크가 자동 갱신되지만 git 이력이 끊길 수 있다.**
  파일 이동·이름변경은 git에서 하는 편이 안전하다.
