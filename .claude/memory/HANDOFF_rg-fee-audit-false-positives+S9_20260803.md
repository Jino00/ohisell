# 세션 인수인계: RG 청구 감사 오탐 규명 + S9 정산서 실측 근거
> 저장일시: 2026-08-03 20:10 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(워크트리): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/serene-agnesi-feee36`
- 브랜치: `claude/serene-agnesi-feee36` (main 기준 8커밋, 전부 push됨)
- 백엔드 테스트: `cd backend && python3 -m pytest -q` (4,448 passed)
- 프론트: `cd frontend && npm run build` · `npm test` (182 passed)
- prod: `https://sellc.ohitech.co.kr` (PM2 `ohisell-backend`, DB `/home/ubuntu/ohisell/backend/ohisell.db`)
- prod DB 읽기: `ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && sqlite3 'file:ohisell.db?mode=ro' -header -column \"<SQL>\""`
- ★배포는 `scripts/safe_deploy.sh`만 (직접 scp 금지, D-NAO-49). DB 변경 시 `--migrate` 필수
- 감사 화면: 재고관리 > 로켓그로스 > 서브탭 `청구 감사`
- 감사 API: `GET /api/coupang/ops/rg/fee-audit?account_key=COUPANG_WING1|COUPANG_WING2`
- 환경변수: `AD_INGEST_TOKEN`(페처 push), `DATABASE_URL` — 값은 prod `.env`/`~/.ohisell_wing*_fetcher.json`

## 2. 이번 세션 완료 목록
- ✅ **오탐 4건 규명·수정** (`backend/app/services/coupang/rg_fee_anomaly.py`, `rg_fee_audit.py`)
  - `_load_fees`가 주기를 합치지 않고 그대로 반환(주기별 청구액 보존), `_load_qty_by_period` 신설(주문을 정산주기 안으로만 버킷팅, 조회범위로 주문 재절단 안 함)
  - `detect_fee_anomalies_by_period` 신설: 미대응 주기는 분모에서 제외, 대응 0개면 `unit_unknown` 강등. 판정은 **판정주기 집계 1회**, 주기별은 `period_detail`·`periods_flagged`로 표시만
  - `charged_*`(전 주기 청구총액)와 `judged_*`(단가의 분자) 분리 노출 + disclaimer에 "charged를 order_count로 나누지 말 것"
- ✅ **S9: 청구 근거를 정산서에서 직접** (`backend/app/models.py`, `rg_settlement_sync.py`, 마이그레이션 `rg9billed7c4e`)
  - 컬럼 3개 추가(nullable): `billed_size_type`(개별포장사이즈=청구에 쓴 등급)·`billed_order_count`(distinct 주문ID)·`billed_quantity`(Σ판매수량)
  - 파서가 정산 엑셀 상세에서 세 값 수집. 주문ID는 **distinct 집합**(라인 수로 세면 분모 부풀림). 등급 갈리면 None. 컬럼 없으면 0이 아니라 NULL
  - 감사가 정산서 1순위 → 물류센터 실측 → 등록치수 순 폴백. 플래그 이름이 근거 세기를 말함(`billed_size_vs_amount_mismatch` > `measured_vs_billed_mismatch` > `size_mismatch_high`)
  - `CATEGORY_TR` 시트명 매핑(`주문내역, 판매수수료`→`sale_fee`)
- ✅ **감사 뷰 프론트** (`frontend/src/components/RgFeeAuditView.tsx` 신규, `pages/InventoryPage.tsx`, `lib/api.ts`)
- ✅ **회귀 테스트 22건 신설** (`tests/test_rg_fee_anomaly.py` 6, `test_rg_fee_audit.py` 5+5, `test_rg_settlement_sync.py` 6)
- ✅ **prod 배포 3회** (감사 2회 + S9 마이그레이션·코드 + 프론트 dist)
- ✅ **origin/main 병합** — 다른 세션 마이그레이션 2개 합류 + 내 마이그 재부모(f3c1d7e9a482→b6e1c93f4275)
- ✅ **기록**: 트랙(`docs/tracks/completed/track_coupang-rg-fee-accounting.md`), LESSONS #106·#107·#108, `failures.jsonl` 1줄, PR #190 본문

## 3. 확정된 결정사항
- **판정: 과오청구 아님.** 옵션 91313543029 주문당 실청구 배송비 = **2,025원**(4,050 아님). 2,025 < 대형1 최소 2,200이라 청구 등급이 대형1일 수 없음. 이의제기 금액 **0원**
- **ⓒ(주기 단위 판정) 폐기 → 집계 판정.** 라이브가 반증: 주기별로 쪼개니 WING1 mismatch 3→**20**(below_floor 6→31). 커버리지 결손은 주기 **안에도** 있고, 표본을 줄이면 노이즈가 증폭된다
- **표본 하한 미도입** (Jino 판단 불요로 정리) — 저볼륨 옵션의 진짜 신호를 죽이고, S9 컬럼이 차면 자동 해소
- **S9 컬럼 백필 = 방치(ⓑ)** — Jino 결정. prod 쿠키 수명 ~2h라 강제 백필이 durable하지 않고, 감사는 PR #190으로 이미 신뢰 가능. 신규 주기부터 Mac 페처가 자동 충전
- **CATEGORY_TR 페처 활성화 보류** — 회계 영향 0(판매수수료는 status/api로 계정 단위 수집 중), 실파일 컬럼 미확인 상태로 켜면 422→재시도 소진
- **prod 크론/쿠키 문제는 손대지 않음** — Jino 지시("니 트랙 밖이면 건들지마")
- **`net_profit` 이동은 되돌리지 않음** — 다른 세션 소관
- **PR #190은 codex 리뷰 전에 병합**(Jino 지시) — 사유는 6절 참조. codex 부채는 남아 있다

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/rg_fee_anomaly.py` | SA3 판정. 단가 전제·오탐 실사고가 상단 주석에 있음 |
| `backend/app/services/coupang/rg_fee_audit.py` | Harness. 주기별 로드·분모 출처 결정·summary |
| `backend/app/services/coupang/rg_settlement_sync.py` | 엑셀 파서·ingest. S9 컬럼 수집, 시트명 맵 |
| `backend/app/services/coupang/rg_fee_reference.py` | 사이즈별 최소금액(floor) — ref 17 §7 |
| `backend/alembic/versions/rg9billed7c4e_*.py` | S9 컬럼 3개 추가(prod 적용 완료) |
| `frontend/src/components/RgFeeAuditView.tsx` | 감사 뷰 |
| `docs/tracks/completed/track_coupang-rg-fee-accounting.md` | 트랙 단일 진실 원천(이미 Completed) |
| `docs/references/17_coupang_rg_fulfillment_fee_policy.md` | §7 사이즈표, §8-1 엑셀 컬럼 실증 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **`net_profit` −5,154,860 → −22,069,706 이동은 이 세션과 무관.** 다른 세션이 17:11·17:29에 **재시작 없이** 배포한 회계 코드를 내 `--restart`가 활성화. 불변식으로 분리 확인: `net_profit_pre_rg − net_profit` 격차 전후 **27,853,855 동일**, `rg_settlement_total` **27,042,555 불변**. → LESSONS #108
- ⚠️ **prod RG 정산 크론 2개 7일 미발동** (`auto_download_rg_settlement`·`sync_coupang_rg_settlement`, `last_run_at=2026-07-27`) + prod Wing 쿠키 **양 계정 `red`**. 데이터 유실은 없음(Mac 페처가 두 층 대체 중). **Jino가 손대지 말라고 지시** — 별도 세션 몫
- ⚠️ **S9 컬럼은 현재 전부 NULL.** 페처 결손 게이트(`layer2-gaps`)가 "행은 있고 컬럼만 NULL"을 결손으로 안 봄 → 신규 주기부터만 채워진다. 감사는 NULL이면 폴백(정상)
- **잔존 오탐 1건**: `95570603539` 2,850원(2.11배). 드릴다운에서 `07-01~07-05 = 3,800원/1주문`(=2×1,900)으로 **자기설명**됨. S9 컬럼 차면 해소
- **`below_floor` 6건은 과소청구**: `95501699184`(맥세이프 카드지갑)은 쿠팡이 06-08~07-19 **6주 연속 0원 청구**(미청구 ≈28만원). 원인 **확인 안 됨** — Wing 정산 상세 수동 대조 필요
- **정정된 오판 2건(재발 금지)**: ①"6월 이후 주문 수집 결손" → 틀렸음. 단가를 1,800으로 오인한 결과이고 2,100으로 보면 `75,600÷2,100=36 = DB 36건` 정확 일치. 주기별 노이즈 원인은 **매출인식일≠결제일** ②"Wing 로그인 필요" → 틀렸음. 브라우저 세션은 살아 있었고 `auth_error`는 **prod가 따로 보관하는 쿠키**였다(페처는 prod로 쿠키를 push하지 않음)
- ⚠️ **prod ↔ main 의도된 드리프트**: - `rg_fee_audit.py`·`rg_fee_anomaly.py`: prod가 main보다 **주석만** 낡음(LESSONS 번호 #99→#106 재번호). 동작 동일 → 배포 불요.
- `models.py`: prod가 main보다 낡은 것은 **다른 세션의 `ad_apply_tm` 컬럼 + BigInteger 임포트**다. 그 마이그레이션 `c4a7e2b91d63`이 **prod 미적용**이라 지금 `models.py`를 배포하면 ORM이 없는 컬럼을 SELECT해 그 테이블 ingest 경로가 침묵한다. **그쪽 세션이 `--migrate`로 함께 배포할 몫** — 이 세션은 손대지 않았다.
- alembic은 병합 리비전 `mrg9b1c4a7e2`로 단일 head 복구. prod는 `rg9billed7c4e`에 있고, 다음 배포자의 `--migrate`가 `c4a7e2b91d63` → 합류점 순으로 적용한다(safe_deploy가 순서를 구조로 강제).
- alembic revision id는 **기존과 충돌 확인 필수** — `a1b2c3d4e5f6`가 이미 쓰여 있어 CycleDetected가 났다
- 내가 9222·9223 CDP Chrome에 Wing 탭을 각 1개 열어뒀다(로그인 확인용). 페처가 그 Chrome을 adopt하므로 임의로 닫지 말 것

## 6. 다음에 할 작업 (미완료)
- [x] **PR #190 병합 완료(2026-08-03 20:2x)** — Jino 지시 "병합해줘". codex 교차 리뷰는 **미실행 부채로 남음**(한도 리셋 2026-08-09 16:16) → 원하면 사후 리뷰. 병합을 미루지 않은 이유: prod가 이미 이 코드를 돌리고 있어 main과 6일간 갈리면 CAS 가드가 다른 세션의 같은 파일 배포를 거부하게 되고(오늘 실제로 겪은 마찰), 마이그레이션은 nullable 3컬럼 추가라 병합 위험 자체는 낮다.
- [ ] (선택) **codex 사후 리뷰** — 08-09 이후, 대상 = PR #190 diff
- [ ] **S9 컬럼 자동 충전 확인** — 다음 정산주기(2026-08-03~08-09) 수집 후 `billed_size_type`·`billed_order_count`가 채워지는지, `divisor_source`가 `settlement`로 바뀌는지 라이브 확인
- [ ] **`95501699184` 6주 0원 청구** — Wing 정산 상세 수동 대조(소급 청구 가능성)
- [ ] (선택) **CATEGORY_TR 활성화** — 실파일 1건 컬럼 확인 후 페처 config `rg_report_types`에 추가
- [ ] (트랙 밖·별도 세션) prod RG 정산 크론 2개 미발동 + Wing 쿠키 red

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_rg-fee-audit-false-positives+S9_20260803.md 읽고 이어서 작업해줘
