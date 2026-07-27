# PLAN — 쿠팡 refresh claim 재시도 계약 (lease 방식)

> 작성: 2026-07-27 13:47 KST (Fable 설계·Jino 승인 완료)
> 배경: HANDOFF_ohisell-ondemand-button-only-complete_20260727.md §3 — "claim이 성공 전 소비(재시도 없음)" 백로그.
> 선행 보류 이력: docs/superpowers/specs/2026-07-19-coupang-on-demand-collection-design.md에서 스코프 밖 보류, codex와 보류 합의(07-27).

## §0 방향 고정 (Jino 확정 2026-07-27 13:45)

- **Jino 원문**: "그 해석대로 진행해" — 아래 해석에 대한 승인:
  - **버튼 1회의 의도는 재시도 3회까지 포함한다** (버튼-only 원칙 "버튼 클릭 시에만 크롬 기동"과 충돌하지 않는 것으로 해석 확정).
  - **로그인 필요 실패는 재시도 제외** — 재시도해도 실패하고 창만 반복해서 뜸. 기존대로 창 유지 + 배너 안내.
- 이 두 줄은 이 스프린트의 금지선이다. 변경은 Jino 승인 필요.

## 1. 문제

버튼 → `refresh_requested_at` set → Mac 데몬이 `refresh-claim`에서 플래그를 **즉시 clear** → 크롬 기동·수집. 수집이 실패하면 요청은 이미 소비되어 재시도 없음(2026-07-17 실사고, coupang_ops.py 주석 4곳에 기록). 동일 패턴이 5개 스트림에 복제:

| 스트림 | 백엔드 서비스 | Mac 페처 |
|---|---|---|
| wing 판매분석(vendor-summary) | vendor_summary_sync.py | tools/wing_browser_fetcher.py |
| wing RG 정산 | rg_settlement_sync.py | tools/wing_browser_fetcher.py |
| ofix 광고비 | ad_cost_sync.py | tools/ad_cost_browser_fetcher.py |
| ohitech 로켓광고 | ohitech_ad_sync.py | tools/ohitech_ad_fetcher.py |
| 로켓 발주/정산 | rocket_supplier_sync.py | tools/rocket_supplier_fetcher.py |

## 2. 설계 — lease 계약

**공용 SA 1개** `backend/app/services/coupang/refresh_contract.py` (단일 책임: 이 계약만). 5개 스트림이 각자 state 행에 대해 이 SA를 호출 (state 모델이 스트림마다 다르면 어댑터로 흡수 — 구현 전 실측).

상태 전이 (state 행에 `claimed_at`·`attempt_count` 컬럼 추가, alembic 마이그레이션):

1. **request**: `refresh_requested_at=now` (기존과 동일. 이미 pending이면 덮어쓰지 않고 no-op).
2. **claim**: `requested ∧ (claimed_at IS NULL ∨ now-claimed_at > TTL)` → `claimed_at=now, attempt_count+=1` 원자적 UPDATE. **`refresh_requested_at`은 지우지 않는다.** 조건 불충족 시 `claimed=false`.
3. **fetch-success**: `refresh_requested_at=NULL, claimed_at=NULL, attempt_count=0`.
4. **report-run-failure**: lease 반납(`claimed_at=NULL`) → 다음 폴에서 자동 재claim(=재시도).
   - 단 `kind=login_required` **또는** `attempt_count>=3`이면 요청 소멸(`refresh_requested_at=NULL`) + `last_error`에 사유("로그인 필요" / "재시도 3회 소진") 명시.
5. **TTL**(데몬이 보고 없이 죽는 경우의 안전망): 최장 실측 수집 시간(백필 포함)보다 길어야 이중 기동이 없다 — 구현 시 로그/타임아웃 실측으로 정하되 기본 20분.

**페처 변경 최소화**: report-run-failure에 optional `kind` 필드 추가(하위호환). 페처는 로그인 필요를 이미 스스로 판별(창 유지 로직)하므로 그 지점에서 `kind="login_required"`만 전달. claim/status 엔드포인트 시그니처 불변 → 데몬 폴링 루프 무변경.

**UI**: refresh-status의 `requested`는 성공 전까지 true 유지(재시도 중=진행 중으로 보임). 소멸 시 last_error로 실패 표기 — 기존 프론트 폴링 로직(성공/실패를 last_success_at/last_error_at 변화로 판별)이 그대로 동작하는지 확인.

## 3. 순서

1. 5개 스트림 state 모델 실측 → refresh_contract.py 설계 확정
2. alembic 마이그레이션(컬럼 2개) — head 단일 유지
3. TDD: 계약 단위 테스트(claim/성공/실패/TTL/3회 상한/login_required) 먼저
4. 스트림별 배선 + 라우터 테스트
5. 페처 4개에 `kind` 전달(로그인 분기 지점만)
6. `/codex review` → pass까지 (최대 5라운드, 대화 표시)
7. PR 생성까지만 — **병합·배포·데몬 재시작은 메인 세션 결정**

## 4. 완료 기준

- 전체 pytest 통과(기존 92 + 신규 계약 테스트)
- codex review pass
- 라이브 합격 시나리오(병합·배포 후 별도 수행): ①정상 버튼 1회 무회귀 ②인위 실패 주입(테스트 계층) 시 재시도·상한·login_required 소멸이 계약대로

## 5. 주의

- prod 배포는 safe_deploy.sh만. 페처(tools/)는 Mac 로컬 실행 — 코드 변경 후 `launchctl kickstart -k` 필요(stale 구코드 사고 이력), 단 이 스프린트에서는 재시작 금지(병합 후).
- SQLite server_default now()=UTC 함정 — 시각 비교는 kst_now() 일관 사용(기존 코드 관례 따름).
