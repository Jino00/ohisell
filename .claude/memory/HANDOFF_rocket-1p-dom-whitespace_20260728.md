# HANDOFF: 로켓배송(1P) M2 — DOM 셀 공백 의존 제거

- 일시: 2026-07-28 KST
- 워크트리: `focused-torvalds-27d4ae`
- 브랜치: `claude/focused-torvalds-27d4ae`
- ✅ **종결(12:20 실측)**: **PR #137 병합**(main `a99bdbf`) + 문서 정정 **PR #139 병합**(main `4031294`). 미푸시 0·작업트리 clean. M1은 별도로 PR #130으로 병합됨.

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

**백엔드 파서도 prod 배포 완료(12:13:52 KST)** — 병행 세션 `claude/final-deploy`가 `c9d6aae`(M2 포함)로 3파일 배포+재기동(03:13:53Z). prod 3파일 main과 byte-identical·파서에 `_split_barcode_name` 라이브·alembic head `f6a8c0b2d4e6` 적용·두 컬럼 실존·promo 테이블 5종 실존. ~~(옛 기록) 파서만 단독 배포하면 순서가 깨진다(컬럼 없는 DB에 코드만 올라가면 정산·발주상세 ingest 전체가 OperationalError로 침묵). 페처 신버전은 현행 마크업에서 구 파서와 동일 rows를 내므로 이 비대칭은 안전.

## 6. codex 게이트

M2는 **Jino 판단으로 면제**(2026-07-28, 원문 "이번건은 codex review를 건너뛰어줘"). 부채 아님·후속 칩 없음(칩 `task_8dc8b575` 철회). **M1의 codex 부채는 그대로 유효**(OpenAI 쿼터 2026-08-02 21:52 해제 후).

## 7. git 상태

브랜치 `claude/focused-torvalds-27d4ae` → **PR #137 병합**(main `a99bdbf`) + 문서 정정 **PR #139 병합**(main `4031294`). M1 5커밋은 별도로 **PR #130으로 이미 병합**됐다(내 브랜치의 M1 커밋은 전부 main 조상). M2 고유분 = `412042e`(수정) + 문서 4커밋. 미푸시 0·작업트리 clean.

기록: `.claude/memory/LESSONS_LEARNED.md` #50, `docs/tracks/active/track_coupang-rocket-1p.md` 체크리스트 M2, `failures.jsonl`에 pytest playwright 스텁 사고 1건.

## 8. 다음 세션이 즉시 할 일

**M1·M2 관련해서는 남은 것이 없다**(2026-07-28 12:20 실측 종결).

- ✅ PR #137·#139 병합(main `4031294`) / prod 코드·마이그레이션 배포 완료 / 로컬 런타임 배포 완료
- ✅ alembic **단일 head** `f6a8c0b2d4e6` — revision ID 충돌은 병행 세션 `claude/alembic-graph-reconcile`이 promo-pnl `a1c3e5f7b9d1` → **`c2998cfe1f7c`** 개명·재부모로 해소(내가 실행하려던 트랙의 "권고 복구 순서 5단계"는 착수 시점에 이미 **stale** — LESSONS #52)
- ✅ codex: M2 면제(Jino 판단, 부채 아님)

남은 것(이 트랙의 다음 스프린트, M2와 무관):
1. **S5 프론트** — 종합조망 1P 뷰(`rocket-overview` 소비: 매출·광고·원가·net_profit + 커버리지% 배지) + 원가 매핑 관리 UI + 갱신 버튼.
2. **M1 codex 부채**(08-02 쿼터 해제 후) — M1 소유 세션의 기록 참조.
3. (운영) 원가 매핑 채우기 → 커버리지% 상승.

⚠️ 병행 세션이 많다. 착수 전 `git fetch` + 라이브(prod alembic·매니페스트·락) 재확인 — 문서에 적힌 계획의 전제부터 검증할 것(LESSONS #52).

## 9. 새 세션 시작 프롬프트 (복사용)

```
HANDOFF `.claude/memory/HANDOFF_rocket-1p-dom-whitespace_20260728.md` 읽고 이어서 진행해줘.
핵심: 로켓 1P M2(DOM 셀 공백 의존 제거) **완료·종결** — 라이브 회귀 0 · codex 면제(Jino 판단) · PR #137·#139 병합(main 4031294) · prod 배포 완료(c9d6aae, 12:13:52 KST) · 로컬 런타임 배포 완료.
다음: M1·M2 잔여 없음. 트랙 다음 스프린트 = S5 프론트. M1 codex 부채는 08-02 이후. alembic 단일 head f6a8c0b2d4e6(revision ID 충돌은 병행 세션이 개명·재부모로 해소).
```
