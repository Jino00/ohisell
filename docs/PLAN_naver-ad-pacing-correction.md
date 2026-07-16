# PLAN — 완결도 보정계수 pacing 배선 (D-NAO-44 후보, 2026-07-16)

> 승인: Jino "보정계수 pacing 배선 진행하자, 구현은 sonnet으로" (2026-07-16 저녁).
> 설계=Fable(이 문서), 구현=Sonnet, 게이트=codex(원칙19). 브랜치 `claude/naver-ad-execution-loop-4124ce`(main 기준).

## 0. 왜 (문제와 근거 — 전부 라이브 실측, 원칙22)
- flight_loop(X2, prod dry-run 크론 2h)의 페이싱 입력 `남은예산 = daily_budget − 오늘보이는cost`인데, /stats 당일누적은 **저평가**다(완결도 곡선 v2: 12시 28%·18시 65%·23시 91%). → 남은예산 과대평가 → α 과속 편향.
- 지연이 **체계적**(IQR ±3~5%p)이라 보정 가능: `예상최종 = 보이는값 × factor(시각)`.
- 근거 문서: `naver-ad-execution-loop-6cc75b/docs/references/data/mop_ui/naver_stat_field_cadence_20260716.md` (v2 교정판).
- ⚠️ **집계 규약(치명)**: naver_ad_daily에 sentinel(`__backfill__`, 캠페인 grain)과 상세행 공존 — **확정치는 sentinel 행만**(= /stats 권위값 일치 실증). 전행 SUM=정확히 2배(failures.jsonl 2026-07-16).

## 1. 스코프
- **IN**: 완결도 곡선 SA 신설 + flight_loop 배선 + 테스트 + codex 게이트. dry_run=True **불변**(실쓰기 개방 없음 — 이 스프린트는 판단 품질만).
- **OUT**: dry-run 해제(별도 Jino 게이트), 스키마/테이블 신설(없음), hourly_snapshot 보존기간 변경(현 7일 유지), 캠페인별/요일별 곡선 세분화(후속).

## 2. C1 — completeness_curve SA (신규 `backend/app/services/naver_ad/completeness_curve.py`)
- 파일 첫 줄: 역할 주석(원칙: 이 파일은 /stats 당일누적의 시각별 완결도 곡선을 실측 이력으로 산출).
- `build_curve(db, *, lookback_days=14, min_daily_cost=50_000, ) -> dict[int, dict]`
  - finals: `naver_ad_daily WHERE adgroup_id == BACKFILL_SENTINEL_ADGROUP AND ad_date < kst_today() AND ad_date >= kst_today()-lookback_days` (campaign_backfill.BACKFILL_SENTINEL_ADGROUP import — 문자열 하드코딩 금지).
  - 표본: `naver_hourly_snapshot` join (campaign_id, ad_date). `final_cost >= min_daily_cost`인 캠페인-일만.
  - **캠페인-일별 비율(cost/final_cost)을 구해 시각별 median** (합산비 아님 — 캠페인 동등 가중).
  - 반환: `{hour: {"completeness": Decimal, "n": int}}`. hour는 KST snapshot_hour.
  - 실측 참고치(v2, 검증용): 12시≈0.28, 18시≈0.648, 23시≈0.913.
- `projection_factor(curve, hour, *, min_completeness=Decimal("0.10"), min_samples=5) -> Decimal | None`
  - `None` 조건(fail-safe): hour 미존재 / n < min_samples / completeness < min_completeness (오전 0~9시 자연 차단).
  - 반환: `1 / completeness` (Decimal).
- 순수 SA: 읽기 전용, 쓰기·API 호출 없음(원칙18-1). hourly_snapshot 보존 7일이라 실효 표본 ≈6일 — lookback_days는 상한일 뿐(있는 만큼만).

## 3. C2 — flight_loop 배선
- **선행 필수(semantic reconciliation)**: response_curve_builder의 곡선 point가 정확히 무엇(전일 예측 cost인지, 잔여시간 cost인지)을 뜻하는지 코드·docstring·기존 테스트로 확정하고, 예산 제약 비교가 **동종 물량 대 동종 물량**이 되도록 배선 위치를 정한다. 확정 내용을 코드 주석+이 문서 §7에 기록. (곡선이 전일 물량이면: 제약은 `projected_final_cost(α) ≤ daily_budget` 꼴이 정합.)
- run당 1회 `build_curve()` pre-compute(캠페인 루프 밖) → 각 캠페인: `factor = projection_factor(curve, kst_now().hour)`.
  - factor 있음: `projected_final_cost = today_cost × factor` → 페이싱의 예산 입력을 projected 기준으로 교체.
  - **factor None(오전/표본부족): α=1.0 강제 + binding_constraint="projection_unavailable"** (원 로직으로 계속 계산하지 않는다 — 저평가 입력으로 α 계산하는 것 자체가 버그이므로 중립이 안전).
- change_log detail(JSON)에 관측 필드 추가: `raw_today_cost, completeness, projection_factor, projected_final_cost`. dry-run 관찰·07-17 이후 대조의 원료.
- `dry_run=True` 불변. `_today_actuals`/`_budget_info`의 다른 소비자 회귀 없는지 확인.

## 4. C3 — 테스트 + 게이트
- TDD(superpowers): SA 단위 — ①sentinel만 집계(sentinel+상세 공존 fixture에서 2배 안 되는 **이중계산 회귀 테스트** 필수) ②min_daily_cost/min_samples/min_completeness 경계 ③factor None 조건들. flight 통합 — ④factor None→α=1.0+라벨 ⑤projected가 raw 대비 α를 실제로 낮추는 케이스 ⑥change_log detail 필드 존재.
- 전체 naver 스위트 회귀 0. 로컬 테스트=homebrew `python3 -m pytest`(venv 없음, 라우터 3파일 bcrypt collection 에러는 기존 이슈 — naver_ad 스위트 위주 실행).
- **codex review 게이트(원칙19)**: `codex exec` diff 리뷰, P1/P2 반영 또는 근거 기각, 최대 3라운드, 대화 기록.
- 커밋: C1/C2 단위로 분리, 메시지에 D-NAO-44.

## 5. 배포(구현 완료 후, Fable 검증 후 진행)
- 스키마 변경 없음·dry-run이라 마이그레이션/DB백업 불필요. file-copy 2파일(flight_loop.py, completeness_curve.py) → sha256 대조 → pm2 재시작 → 다음 2h 플라이트 크론에서 change_log detail에 신규 필드 실출현 확인(원칙22 라이브).

## 6. 완료 기준
1. 테스트 전부 green + 이중계산 회귀 테스트 존재.
2. codex PASS.
3. prod 배포 후 실제 크론 1회에서 `projection_factor`가 기록된 change_log 행 확인(저녁이면 factor ×1.1~1.5 범위).
4. 문서 신선도: 이 문서 §7 체크 + progress + 트랙파일.

## 7. 진행 기록 (구현자가 갱신)
- [ ] C1 SA + 단위테스트
- [ ] C2 배선 + 통합테스트 (+ 곡선 point 의미 확정 기록: ___)
- [ ] C3 codex PASS (라운드/지적/반영: ___)
- [ ] 배포 + 라이브 확인
