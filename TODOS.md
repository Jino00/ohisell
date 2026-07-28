# TODOS

> 트랙 외/후속 작업 백로그. 트랙 결정사항은 docs/tracks/active/*.md 우선.

## RG 수수료 회계 트랙 (track_coupang-rg-fee-accounting)

- [x] **크로스채널 대시보드 쿠팡 순이익에 RG 비용 반영** (S7 eng-review D4, 완료 7269a38)
  - **What:** `app/routers/dashboard.py` / `app/services/profit_calculator.py`의 쿠팡 순이익에도 RG 정산 비용(non-ad, D-15)을 반영.
  - **Why:** S7 후 command-center 쿠팡 순이익(RG 반영)과 다채널 대시보드 쿠팡 순이익(RG 미반영)이 화면 간 불일치. 예전 배송비 회계 4화면 불일치(docs/PLAN.md 이력)와 같은 종류.
  - **Pros:** 화면 간 순이익 일관성.
  - **Cons:** profit_calculator 구조 재조사 필요(쿠팡 정산 경로가 command-center와 방법론 다름 — 옵션단위 실측수수료 vs 정산). 머니코드라 별도 신중 검토.
  - **Context:** 두 화면은 이미 방법론 차이로 기존에도 쿠팡 순이익이 다를 수 있음. command-center가 쿠팡 deep-dive 권위. S7은 command-center만 플립(의도적 스코프).
  - **Depends on:** S7 완료(D-15 non-ad 차감 공식 확정 후 동일 규칙 적용).

## 배포 하니스 (트랙 외)

- [ ] **`/codex review` — safe_deploy.sh 마이그레이션 순서 가드 (2026-08-02 21:52 이후)**
  - **What:** 2026-07-28 추가한 `scripts/safe_deploy.sh` alembic 순서 가드에 대한 원칙 19 교차검증.
  - **Why:** 구현 시점에 codex 쿼터가 2026-08-02 21:52까지 소진 → 원칙 19 게이트를 통과하지 못한 채 머지 대기.
  - **대체 리뷰(구현 시점에 수행):** 스텁 ssh/scp/alembic 회귀 하니스로 12개 시나리오·19개 단언
    (정상/대기차단/--migrate 순서/파일누락/upgrade실패/CAS/락/restart차단/멱등/fail-closed) + 실 prod
    읽기전용 프로브(`alembic current`=`heads`=a1c3e5f7b9d1). **다른 모델의 독립 리뷰는 아직 없음.**
  - **중점 검토 요청:** ①`current` vs `heads` 판정이 다중 head/mergepoint에서 안전한가
    ②`git ls-files` 기반 누락 탐지의 오탐 여지 ③`--migrate` 자동 upgrade의 권한 범위가 적절한가.
