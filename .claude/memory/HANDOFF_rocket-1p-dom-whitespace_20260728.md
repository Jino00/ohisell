# HANDOFF: 로켓배송(1P) M2 — DOM 셀 공백 의존 제거

- 일시: 2026-07-28 KST
- 워크트리: `focused-torvalds-27d4ae`
- 브랜치: `claude/focused-torvalds-27d4ae`
- **origin에 푸시 완료**(tip `db3e7a2`). main 대비 9커밋 = M1 5개 + M2 4개. **PR 미생성**.

## 1. 작업 개요

쿠팡 로켓배송(1P) 트랙 유지보수 스프린트 M2 — 페처의 DOM 셀 추출이 쿠팡 마크업 들여쓰기에 의존하던 문제 제거.

## 2. 기전 (버그의 뿌리)

`DOMParser`로 만든 문서는 렌더링되지 않아 `td.innerText`가 `textContent`로 떨어지고, textContent는 요소 경계에 공백을 넣지 않는다. 지금까지 셀 안에 공백이 있었던 이유는 순전히 쿠팡 SSR 마크업의 들여쓰기(태그 사이 개행) 덕이었다 — 쿠팡이 HTML을 미니파이하면 그 공백은 사라진다.

터지는 자리 = `parse_po_item_rows`의 `barcode, _, name = cell.partition(" ")`. 공백이 사라지면 인덱스가 걸린 `barcode` 컬럼에 상품명이 통째로 들어가고 `product_name=None`이 된다 — 예외도 로그도 없는 **조용한 데이터 오염**.

## 3. 수정 4건 (커밋 `412042e`)

1. `tools/rocket_supplier_fetcher.py`: 정산·발주상세 두 추출기를 공용 `_CELL_HELPERS_JS`로 통일. 자손 텍스트노드를 명시적으로 `' '` 조인(깊이 무관 — `ul>li`·`div`·`a`·`button` 전부 커버). ★`<br>`은 분리자로 취급하지 않음 — 헤더가 `상품<br>번호`·`세액<br>부가세`로 조립돼 있어 공백을 넣으면 표 선택 토큰 매칭이 깨져 `rows=[]` 무성 전손이 된다. 표 선택 토큰 매칭은 `noWs()`로 공백 무시.
2. `backend/app/clients/coupang/rocket_supplier.py`: `_split_barcode_name` 신규. 판정 기준은 "공백 유무"가 아니라 **"선두 토큰이 바코드꼴인가"**(EAN 8~14자리 / 영문+숫자 내부코드) — 상품명 자체에 공백이 있어서 공백 유무로는 못 잡는다(첫 시도가 여기서 실패했고 테스트가 잡았다). 못 떼면 추측 없이 warning(종류당 1줄).
3. 같은 파일: 정산 헤더 매핑을 공백 무시로 비교, `_to_int`/`_to_dec`/`_to_date`는 공백을 지우면 숫자·날짜꼴이 되는 값만 복구(조용한 0 방지). `_to_transmitted`의 기존 공백 폴백은 안전망으로 유지(주석만 갱신).
4. `tools/verify_rocket_dom_extract.py` 신규 — 실제 Chromium에서 실제 추출 함수를 돌리는 실증 하니스. 백엔드 pytest 스위트는 브라우저를 안 띄우는 방침(`backend/tests/test_fetcher_button_only_chrome.py`가 playwright를 sys.modules 스텁으로 막는다)이라 pytest에는 소스 가드(innerText 회귀 금지)만 두고, 실동작 증명은 이 스크립트가 담당. 재실행: `python3 tools/verify_rocket_dom_extract.py`

## 4. 라이브 증거 (원칙22)

- **정산 마크업**: supplier CDP 포트는 **9225**(9223 아님 — 처음 9223으로 확인했다가 그건 다른 브라우저였고 "로그아웃"으로 오판했다). 기록 샘플 `docs/references/data/20_rocket_1p_settlement_dom_sample.json`과 **동일 URL**을 읽기전용 GET으로 재fetch → 수정 전(`ddb2c02`)·후 추출이 **11행 셀 단위 완전 일치** = 회귀 0. 기록 파일과의 차이는 전부 데이터 변동(신규 계산서 2건 06-17 유입으로 page1 뒤 2건이 page2로 밀림 + 확정일 `-`→`2026-06-18`).
- **발주상세 ref20b**: 원본 추출 == `backend/tests`의 `_PO_DETAIL_ROWS` 13행 일치, **태그 사이 공백을 전부 제거한(미니파이) HTML도 동일한 rows**(공백 독립성 실증). 미니파이에서 수정 전은 barcode가 `8809465525057오하이`로 오염 → 수정 후 정상 분리.
- 테스트: 파서 54 + 루트 전체 **3515 passed**.

## 5. 배포

`tools/install_local_runtime.sh` 실행 완료 — `~/.ohisell/tools/rocket_supplier_fetcher.py`가 워크트리와 byte-identical, `com.ohisell.rocket` pid 20786→44099 교체(green-while-stale 아님).

★실행 전에 나머지 4종 페처가 워크트리와 byte-identical임을 확인했다 — 이 스크립트는 5종을 전부 복사하므로 미푸시 형제 브랜치(`claude/elated-hawking-cd94ea` 5커밋·`claude/lucid-darwin-ed234e` 1커밋이 wing 페처를 건드림)의 작업을 덮을 위험이 있었다. 다운그레이드 0.

**백엔드 파서는 prod 미배포**(의도적) — 이 트랙 백엔드(S2~S4.5c) 자체가 prod 미배포·alembic `f6a8c0b2d4e6` 미적용 상태라 파서만 단독 배포하면 순서가 깨진다(컬럼 없는 DB에 코드만 올라가면 정산·발주상세 ingest 전체가 OperationalError로 침묵). 페처 신버전은 현행 마크업에서 구 파서와 동일 rows를 내므로 이 비대칭은 안전.

## 6. codex 게이트

M2는 **Jino 판단으로 면제**(2026-07-28, 원문 "이번건은 codex review를 건너뛰어줘"). 부채 아님·후속 칩 없음(칩 `task_8dc8b575` 철회). **M1의 codex 부채는 그대로 유효**(OpenAI 쿼터 2026-08-02 21:52 해제 후).

## 7. git 상태

브랜치 `claude/focused-torvalds-27d4ae`, origin에 **푸시 완료**(tip `db3e7a2`). main 대비 커밋 9개 = M1 5개(`85967cf`·`e3da1f6`·`39c1c39`·`a386614`·`ddb2c02`, 다른 세션 작업 위에 스택) + M2 4개(`412042e` 수정, `d7a1c84` 트랙·LESSONS, `806ea8a` codex 면제, `db3e7a2` progress). **PR 미생성**.

기록: `.claude/memory/LESSONS_LEARNED.md` #50, `docs/tracks/active/track_coupang-rocket-1p.md` 체크리스트 M2, `failures.jsonl`에 pytest playwright 스텁 사고 1건.

## 8. 다음 세션이 즉시 할 일

1. **PR/병합 결정** — ★promo-pnl 워크트리(`worktree-agent-a77a1755db4c87ada`)의 alembic `a1c3e5f7b9d1`이 우리 `f6a8c0b2d4e6`과 **부모가 같다**(`e5f7a9c1b3d5`). 각 브랜치는 단일 head지만 둘 다 main에 들어가면 head 2개가 된다 → **나중에 병합하는 쪽이 자기 `down_revision`을 먼저 병합된 revision으로 재연결**(merge revision보다 재연결 선호).
2. **prod 배포는 `alembic upgrade head` → 코드 순서 강제.** `scripts/safe_deploy.sh`에 alembic 단계가 없으므로 PR 본문에 순서를 명시할 것.
3. **M1 codex 부채 소화**(08-02 이후, OpenAI 쿼터 해제 후).
4. **트랙 다음 스프린트는 S5 프론트.**

## 9. 새 세션 시작 프롬프트 (복사용)

```
HANDOFF `.claude/memory/HANDOFF_rocket-1p-dom-whitespace_20260728.md` 읽고 이어서 진행해줘.
핵심: 로켓 1P M2(DOM 셀 공백 의존 제거) 완료·라이브 회귀 0·codex 면제(Jino 판단)·origin 푸시 완료(db3e7a2)·PR 미생성.
다음: promo-pnl과 alembic head 충돌 정리 후 PR/병합 결정 → prod는 alembic 먼저 → 코드. M1 codex 부채는 08-02 이후.
```
