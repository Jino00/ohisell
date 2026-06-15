# 세션 인수인계: ohisell 후속 정리 (트랙정리 + S8감사 + S7점검)
> 저장일시: 2026-06-15
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-git-push-complete_20260615.md`

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload` (8000)
- 테스트: `python -m pytest -q` (191 그린)
- prod: `sellc.ohitech.co.kr` (ssh Host=sellc.ohitech.co.kr, User=ubuntu). PM2 `ohisell-backend`(online). DB=SQLite. alembic head=n8o9p0q1r2s3.
- CDP Chrome: `tools/wing_browser_fetcher.py chrome` → 전용 프로필 `~/.ohisell_wing_chrome`(port 9222)
- 데몬: launchd `com.ohisell.wing`(running). 로그=`~/.ohisell_wing_fetcher.log`
- git: origin push 완료(`6d89957`), 미push 0

## 2. 이번 세션 완료 목록
직전 HANDOFF §6 후속 4항목을 "순서대로" 처리(Jino 지시).
- ✅ **① 트랙 정리** (커밋 `6b06121`): `docs/tracks/active/track_wing-session-automation.md` → `completed/` 이동(git mv). `docs/TRACKS.md`에서 Wing 트랙 `4/6` stale 표기 → Completed 섹션으로 이동(6/6 완료 요약).
- ✅ **② RG수수료 S8 라이브 재감사** (커밋 `db...`, 두번째): prod `GET /api/coupang/ops/rg/fee-audit` 라이브 조회. **size_mismatch_high 4→1건**(HANDOFF 4건은 stale; 이전 3건은 PRODUCT_SIZE_COMPARISON 실측 자동수집되며 해소). 남은 1건 기록 → `track_coupang-rg-fee-accounting.md` "다음 액션" + TRACKS.md 갱신.
- ✅ **③ RG발송관제 S7 라이브 점검** (커밋 세번째): prod `GET /api/coupang/ops/sales-velocity`. trust_days=11, 게이트 정상 작동 확인(고장 아님). `track_coupang-rg-replenishment.md` "다음 액션(S7)" 라이브 수치 기록.
- ✅ **git push** origin main: `2bdd148..6d89957`(커밋 3개).

## 3. 확정된 결정사항
- **S8 size_mismatch_high 1건(아이패드미니필름 91313543029): Jino 결정 = 자동해제 대기**. 등록 극소형(세변합 60.5cm, 355×245×5mm/169g, 공식표대로 정확분류) vs 쿠팡 배송청구 주문당 4,050원(=대형1 정합, 극소형 floor 1,350의 3배). size_source=registered_dims(실측 미확보, 최근 입고 없음). 다음 입고 시 PRODUCT_SIZE_COMPARISON 실측 수집되면 자동 판가름(ⓐ극소형확정→과오청구 / ⓑ대형1확정→정당). 코드변경 없음.
- **④ 8종 XLSX 파서: 보류 권장(Jino 미결정)**. 사유 3중: ⓐ 실제 XLSX 샘플 부재(추정 금지 원칙상 작성 불가, 종류별 시트·헤더·컬럼 상이) ⓑ 돈 영향 없음(D-14: net_profit은 계정단위 status/api가 권위, 옵션파서는 대조뷰 표시용) ⓒ 8 미니스프린트 규모. 필요한 종류만 핀포인트로 하는 게 효율적.
- **③ S7은 코딩 항목 아님** — sales_velocity_estimator가 데이터 누적 시 자동 승격(별도 코딩 없음). HANDOFF의 "S7 UI"는 오기.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/TRACKS.md` | 트랙 인덱스. Wing 트랙 Completed로 이동 완료 |
| `docs/tracks/completed/track_wing-session-automation.md` | 이동된 Wing 트랙(6/6 종료) |
| `docs/tracks/active/track_coupang-rg-fee-accounting.md` | S8 후속 1건 자동해제대기 기록(다음 액션) |
| `docs/tracks/active/track_coupang-rg-replenishment.md` | S7 라이브 점검 기록(다음 액션) |
| `backend/app/services/coupang/rg_fee_anomaly.py` | size_mismatch_high 로직(실측값 있으면 스킵) |
| `backend/app/services/coupang/rg_size_classifier.py` | 세변합∪무게→사이즈 분류(공식표 §7) |
| `backend/app/services/coupang/sales_velocity_estimator.py` | 요일계수 승격 게이트(SEGMENT_MIN_DAYS) |

## 5. 알려진 이슈 / 주의사항
- **S8 1건은 입고 대기**: 아이패드미니필름(91313543029) 입고 전엔 실측 미확보 → 자동해제 안 됨. 입고 후 다음 정산주기에 자동 처리.
- **S7 승격 임박**: weekday 7/8(1일 부족)·weekend 3/4·holiday 1/2. 다음 깨끗한 평일 1회 누적 시 평일계수 자동 활성. RG order sync(데몬) 계속 돌아야 함.
- **prod alembic head**: n8o9p0q1r2s3 (변경 없음, 이번 세션 코드변경 0 — 전부 docs).
- **활성 트랙 3개**(RG수수료=운영, RG발송관제=6/7 데이터대기, coupang-full=이력보관). 전부 운영/대기 상태, 진행 중 코딩 작업 없음.

## 6. 다음에 할 작업 (미완료/선택)
- [ ] **④ 8종 XLSX 파서** — Jino 결정 대기(보류 권장). 진행 시 라이브 샘플 다운로드부터(Wing 세션 필요).
- [ ] RG수수료 S8 size_mismatch_high 1건 — 입고 후 자동해제 관찰(능동 작업 아님).
- [ ] RG발송관제 S7 — 평일계수 승격 관찰(능동 작업 아님).
- [ ] (선택) RG수수료 감사 프론트 UI(로켓그로스 탭 감사 뷰) — 미정.
- [ ] (선택) CATEGORY_TR 파서(판매수수료 옵션단위) — 기능 영향 없음(status/api 계정단위 이미 수집).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-followup-cleanup_20260615.md 읽고 이어서 작업해줘
