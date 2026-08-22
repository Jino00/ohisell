# HANDOFF — 원가 정본 원장(cost-truth-ledger) 계약 B 첫 슬라이스 · PR #327 병합
2026-08-22 (KST) · 워크트리 `/Users/jino/.claude-worktrees/ohiselling/import-ledger` (브랜치 `worktree-import-ledger`)

## 1. 작업 목적 (원문)

Jino 2026-08-22:
> "SellC에 원가 메뉴를 하나 붙이자. … 그리고 수입건에 대해서는 두번째, 세번째 첨부자료처럼
> 수입한 금액을 알려주면 너가 이런것들을 모아서 회계적으로 계산되는 원가를 만들면 되잖아.
> 그리고 그런 원가를 만들려면 재고사항이 필요할꺼고, 그건 ecount와 연결하면 될꺼고."
> "목표는 정확한 원가 산출이니까?"

이번 세션(이어받은 세션)의 몫: PR 생성·병합(잡일)과 HANDOFF 작성. 코드 판단은 하지 않았다
(위임문 자체가 「코드 파일 수정 금지」).

## 2. 완료 QA — 판정 원문 그대로 (앵커 `.claude/anchors/a9a2121b-0e5c-4888-97a9-b7bcb478c26a.md`)

```
판정(계약 §4): 부분달성 — ⓐⓑⓒⓓⓕ 5항목 prod 실측 달성(실건 SETR2608170216 라이브, 15:53~15:54 KST),
**ⓔ만 미달**(원본 서류 3개 중 PL 미업로드로 2개만 실재 — CI·PL이 한 .xls의 두 시트라 물리 파일이
2개인데 계약이 3개로 셌다. **고치지 않는다**: 같은 파일을 doc_type=pl로 재업로드하면 숫자는 맞지만
기준 충족용 증거 생산이고, 문언 수정은 미달 판정 뒤 기준 완화라 금지선이다) (2026-08-22 15:5x KST)
판정(Jino 지시 원문): 부분달성 — 핵심 갈래(수입서류→회계적 원가)와 8/22 추가지시(업로드 섹션)는
**라이브 달성**, 원가 메뉴 통합(A′)·재고·ecount 3갈래는 미착수 (2026-08-22 15:5x KST)
판정(트랙 궁극 목표): 부분달성 — 6항목 중 B1·B2 달성(**0/6 → 2/6**), A1·A2·C1·C2 미달(미착수)
(2026-08-22 15:5x KST)
```

> 1차 판정(14:2x, 배포 전)은 3대조 전부 미달/부분달성이었고 사유가 「prod 미배포」였다. 배포 후
> §2의 재판정 1회를 같은 QA 기에 돌린 결과가 위다.
> QA가 확인 못한 것(원문): ⓐ 브라우저 실렌더 미확인(API 응답 + 소비 코드 존재까지) · ⓕ 배포 전
> 스냅샷이 없어 값 대조 불가(`scheduler.py` 소스 무변경으로 대체 판단) · 변이 16종 생존 0 주장
> 미재현(P1 수정 코드 실재 확인으로 대체) · D-CPP 47→48 재부여가 다른 문서에 전수 반영됐는지
> 미확인(계약·트랙 2곳만 봄).
> ★QA가 ⓓ를 나보다 강하게 쟀다: 내 before/after 캡처를 안 믿고 **배포된 prod 코드에 `cost_price`
> 매치 0건**임을 직접 grep했고, 확정 건의 `internal_sku`가 전건 null이라 조인 경로 자체가 없음을
> 확인했다.

> QA가 확인 못한 것(원문 그대로): prod `alembic_version` 직접 조회 못함(404는 정황증거) · 회귀
> 실패 2건의 「사전 존재」를 stash 재실행으로 독립 재현하지 않음(grep 무관성만 확인) · 적대 리뷰
> 결과 미열람 · xlsx 파서를 실제 8/18 원본으로 왕복하지 않음(합성 바이트열만).

## 3. 트랙 진행률

- 트랙 경로: `docs/tracks/active/track_cost-truth-ledger.md`
- **시작 0/6 → 종료 2/6**
- 달성: B1(수입건 원장 스키마 5테이블 + 3중 검산 prod 존재), B2(8/18 실건 SETR2608170216 라이브 관측)
- 미달(미착수): A1(국내 조립형 원가 계산), A2(플립/폴드·태블릿 이식), C1(재고 로트 원장),
  C2(평가방법 확정·cost_price 컷오버)
- 이번 세션이 움직인 항목: 없음(코드 판단 없음) — 단 **PR #327 병합으로 B1·B2가 main에 반영**됐다
  (이전 세션은 prod 배포만 하고 미병합 상태였다). 증거: `gh pr view 327 --json state,mergedAt` →
  `MERGED` / `mergeCommit.oid` = `52fa28fc578cda0bcf8b3baf343ab98c965dc5f9`
- 트랙 목표 원문: 위 §1과 동일(트랙 헤더 `목표:` 줄 그대로)
- 다음 세션 후보: 아래 §4 「다음 세션이 먼저 볼 것」

## 4. 다음 세션이 먼저 볼 것 (우선순위 순)

1. **계약 A′** — `docs/PLAN_cost-table-systemization.md`(D-CPP-31 초안, 2026-08-10 승인 대기)를
   부활·개정. ★그 초안의 **미해결 Jino 질문 1건**을 먼저 물어야 한다:
   「엑셀의 부자재 단가·수식을 파싱해 초기 구성으로 넣는 것을 «Jino 확인분»으로 인정하는가?」
2. **Jino 결정 대기 2건**:
   - ①세무사 확인 — 「공제받는 매입세액을 원가에서 빼는가」(법인세법 시행령 §72에 「부가가치세」·
     「매입세액」·「관세」라는 단어가 **없음**을 국가법령정보센터 원문으로 확인했다. 두 값을 다
     저장해 뒀으니 답이 언제 와도 작업은 안 막히지만, **계약 C에서 `cost_price`를 무엇으로 덮을지**가
     여기 걸려 있다)
   - ②cleaning kits 168원/개가 엑셀 부자재 목록의 어느 항목인지 불명(「부자재(밀대외) 22」·
     「알콜솜 2EA 60」·「패키지 98」 어느 것과도 안 맞음)
3. **★★신규 발견 — alembic head 재분기, 이번 세션에 이미 해소됨(조치 불필요, 기록만)**: PR #327을
   병합하기 직전 확인한바, main에는 같은 두 부모(`cs1kind0a1b2`·`imp1ledger47a`)를 합치는 머지
   리비전이 **두 개**(`mrg2heads0822` — main에만 있고 prod엔 적용된 적 없음 / `mrg48s1heads` —
   내 PR #327 소속, **prod에 실제 적용됨**) 있었다. 이건 병행 세션(M3 scorer,
   `HANDOFF_m3b-scorer-corrected_20260822.md` §5-1)이 독립적으로 먼저 발견해 기록해 둔 것과
   같은 문제였다. **내가 PR #327을 병합하기 전에, 그 세션이 PR #326(`fix/alembic-single-merge-head`,
   커밋 `67f9d052`)으로 `mrg2heads0822`를 삭제하고 `mrg48s1heads`를 정본으로 확정해 이미 병합해
   두었다** — 그래서 내 PR #327이 실제로 병합됐을 때 main은 이미 단일 head(`mrg48s1heads`)
   상태였다. 확인 명령: `git ls-tree -r origin/main --name-only -- backend/alembic/versions/ | grep -i mrg`
   → `mrg48s1heads_*.py`만 남음. **다음 세션이 할 일 없음** — 순서상 위험했던 창이 이미 닫혔다는
   것만 기록해 둔다.

## 5. PR / 병합 결과

- **PR #327**: `chore(D-CPP-48): alembic 분기 합류 + collection-stability-s1 병합 + 라이브 합격 기록`
  https://github.com/Jino00/ohisell/pull/327
- 병합 방식: `scripts/safe_merge.sh 327 --force` (CI 3개 job 전부 2~3초 fail — 이 저장소 GitHub Actions
  결제 정지로 인한 것, 코드 신호 아님. `--force` 사용은 이 저장소 정식 경로이고 로그에 자백 남음:
  `$TMPDIR/safe_merge.log`, `⚠️⚠️ 강제 병합: 2026-08-22 16:05:07 KST PR#327 --force 병합 (verdict=FAIL) by Jino`)
- 병합 커밋: `52fa28fc578cda0bcf8b3baf343ab98c965dc5f9`
- 병합 커밋 2건 내용:
  - `2b8eaf96` — alembic merge revision `mrg48s1heads` 신설 + `collection-stability-s1` 브랜치
    8커밋(2,290줄) 병합
  - `0e434c8b` — 완료 QA 재판정 기록, 트랙 진행률 0/6 → 2/6
- `gh pr view 327 --json state,mergedAt,mergeCommit` → `{"state":"MERGED", "mergedAt":"2026-08-22T07:05:09Z"}`

## 6. 이월

앵커 `## 이월` 원문 그대로 + 아래 추가:

- **훅 오탐 1건 (계약 §6 계수 대상)**: 2026-08-22 15:5x, 「PR 병합 + HANDOFF 작성」 잡일을 Sonnet에
  위임하려 하자 `표면 변이 누락` 훅(적대 리뷰용 §4 게이트)이 발동했다. 리뷰 위임이 아니라 오탐이고,
  안내대로 같은 위임문을 재전송해 통과시켰다. 훅이 「적대 리뷰 위임」을 판별하는 신호가 넓은 듯하다.
- ★★**D-CPP 번호 충돌 — 내 것을 D-CPP-47 → D-CPP-48로 재번호해야 한다.** 병행 세션 `a1ae61e2`가
  13:5x에 같은 D-CPP-47을 「RG 매출은 콘솔 net 축으로 읽는다」에 선점했고 **이미 커밋**(`7851cd8f`,
  브랜치 `feat/rg-net-ledger`, 트랙 `docs/tracks/active/track_coupang-rg-net-ledger.md:75`).
  둘 다 아직 origin/main 밖이지만 저쪽은 커밋했고 내 것은 미커밋이라 **내 쪽이 재번호가 싸다**
  (프로젝트 규칙: 충돌 시 내 것을 뒤로, 본문 불변·번호와 참조만 정정, 트랙에 재부여 사실 기록).
  ★**적대 리뷰가 변이 주입으로 같은 파일들을 임시 수정 중이라 지금 실행하면 리뷰어의 `git checkout --`
  원복에 지워진다** — 리뷰 종료·원복 검증 후에 sed로 일괄 정정할 것. 대상: `docs/PLAN_import-cost-ledger.md`
  · `docs/tracks/active/track_cost-truth-ledger.md` · `backend/app/models.py` ·
  `backend/app/{routers/import_cost.py,services/import_cost/*.py}` · 마이그 파일 · 테스트 2 · 프론트 2.
  ★원인은 `scripts/next_ids.sh`가 **D-NAO와 교훈만** 발급하고 **D-CPP는 안 발급**하는 것이다
  (실행 결과: `D-NAO-224` / `교훈 #346`만 출력). 그래서 양쪽이 각자 grep으로 최댓값을 떠서 같은 값을 얻었다.
  → 도구 보강 후보(별건, 이 계약 밖). **이번 세션에선 재번호를 하지 않았다** — 위임 범위가
  「코드 파일 수정 금지」였기 때문.
- 병행 세션 3개가 이번 세션 동안 가동 중이었던 흔적 확인: `25f4ab8a`(네이버 광고 트랙) ·
  `a1ae61e2`(쿠팡 RG net 원장) · M3 scorer 세션(위 §4-3, alembic head 정리를 내가 병합하기 직전에
  끝냈다). 이 트랙 파일(`track_cost-truth-ledger.md`) 미접촉 유지.
- QA 관측: 적대 리뷰의 임시 probe 파일 4개(`_probe_alloc.py`·`_probe_pg.py`·`tests/test_zz_probe*.py`)가
  미추적으로 남아 있었다던 기록이 있었으나, 이번 세션 확인 시점(`git status --porcelain`)엔
  **작업 트리가 이미 clean**했다 — 리뷰 종료 시 정리가 실제로 됐다는 뜻으로 보인다(재확인 요망은 아님,
  단순 기록).

## 7. 위임 사고 자백 (서브에이전트 자진 신고분, 이번 세션이 발견한 것)

이전 세션(계약 B 구현 세션)이 남긴 자백을 그대로 옮긴다 — 이번 세션이 직접 겪은 것은 아니다:
1. 파서 담당이 지시 밖에서 **Excel.app을 AppleScript로 구동**(원본 파일 무변경 확인)
2. 프론트 담당이 금지된 `git status`/`git diff --stat` 각 1회
3. 파서 담당도 `git status` 1회

전부 읽기 전용. → 앞으로 위임문에 「사용자 애플리케이션 구동 금지」 명시 필요.

## 8. 못 한 것 (이 세션 범위 안에서)

- 없음 — ①PR 생성·병합, ②HANDOFF 작성 모두 완료.
- 범위 밖으로 남긴 것(의도적, 위임문의 「코드 파일 수정 금지」 준수): D-CPP 47→48 재번호의 파일
  전수 반영(§6), A′ 계약 부활, Jino 결정 대기 2건.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
