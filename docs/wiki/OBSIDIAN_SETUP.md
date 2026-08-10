# 옵시디언 셋업 — 이 repo를 볼트로 열기

> **옵시디언은 «뷰»이지 쓰기 주체가 아니다.** 사람이 그래프·백링크·검색으로 보는 창이고,
> 갱신은 세션이 git에 한다. 옵시디언에서 편집해도 되지만, 그건 git 파일을 고치는 것이다.

## ★먼저 알 것 — 옵시디언 볼트가 이미 하나 있다 (2026-08-10 실측)

`AI Program/Vault/AIOffice`에 **AI Office 볼트**가 이미 돌아가고 있다.
`entities/`가 **LLM Wiki 형식**이고 KG 미러 SA·Hermes·Graphify가 **자동 생성**한다
(company 803 · person 1,179 · product 211 · agents 997 · chats 674).

**단 그 볼트에 Ohiselling 내용은 0건이다** — `ohisell`·`PAO`·`naver`·`교훈` 전부 0,
`topics`·`projects` 폴더는 비어 있다. 즉 **비즈니스 CRM 지식그래프이지 엔지니어링 지혜층이 아니다.**

그래서 이건 **두 번째 볼트**다. 정당한 이유:
- **정본이 다르다** — AIOffice는 KG(SQLite+ChromaDB)가 정본이고 markdown은 자동 미러다.
  여기는 **git이 정본**이고 사람·세션이 직접 쓴다. 한 볼트에 섞으면 자동 재생성이 수동 파일을 덮거나 그 반대다.
- 옵시디언은 볼트 전환이 한 클릭이라 비용이 작다.
- ⚠️ 다만 **통합 여부는 Jino의 결정 사항이다**(AIOffice의 KG가 이 위키를 흡수하는 길도 있다).

## 왜 마이그레이션이 0건인가

기존 memory 토픽 파일과 위키가 이미 `[[링크]]` 문법과 YAML frontmatter를 쓴다.
**폴더를 열기만 하면 그래프가 생긴다.** 별도 변환·동기화 스크립트가 없다 —
그게 이 설계의 요점이다(정본 이원화 금지).

## 여는 법 — ★순서가 중요하다

### 1단계 (필수) — 열기 **전에** 설정을 심는다

```bash
scripts/setup_obsidian.sh
```

**이걸 먼저 하지 않으면 안 된다.** 이 repo는 파일 **163,106개**(`frontend/node_modules` 포함)이고
`.claude/worktrees` 아래에만 markdown이 **7,000개**인데 대부분 `LESSONS_LEARNED.md`·HANDOFF의
**워크트리 사본**이다. 그냥 열면 ①인덱싱이 오래 걸리고 ②그래프가 같은 문서의 사본 수천 개로
뒤덮여 지혜층이 안 보인다.

> ⚠️ **이 문서의 초판은 「열고 나서 Settings에서 제외」라고 적었는데 순서가 틀렸다** —
> 그땐 이미 인덱싱이 끝난 뒤다. 스크립트가 `.obsidian/app.json`의 `userIgnoreFilters`를
> **첫 실행 전에** 심는다. 실측: 인덱싱 대상 markdown **5,749 → 437개**.

### 2단계 — 앱에서 등록 (한 번만, 사람이 해야 한다)

Obsidian → **Open folder as vault** → 이 폴더 선택:
```
/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling
```

> ⚠️ `obsidian://open?path=...` URL 스킴은 **이미 등록된 볼트만** 연다 — 신규 등록엔 안 통한다
> (2026-08-10 실측). 그래서 이 한 단계만은 자동화가 안 된다.

"Trust author and enable plugins?" → **플러그인 없이 시작**해도 된다(v1은 코어 기능만).

### 3단계 — 확인

`docs/wiki/WISDOM.md`를 열고 그래프 뷰(좌측 리본)에서
지혜층 클러스터와 부채 노드가 연결돼 보이는지 본다.

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
