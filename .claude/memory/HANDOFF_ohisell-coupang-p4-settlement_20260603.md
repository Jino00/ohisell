# 세션 인수인계: ohisell-coupang-p4-settlement
> 저장일시: 2026-06-03 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙. 이 세션 = **P4 정산 도메인(수수료 감사) 완결**(명세 프로브→구현→codex 3R→prod 배포→라이브 실증→main 커밋·push) + **P3 로켓그로스 조사(실데이터 0 확인 → P7 우선 결정)**. 트랙 파일이 진짜 진실 원천.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **서버 포트=8001**
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp 파일복사**
- ⚠️ tar 전송: `COPYFILE_DISABLE=1 tar --exclude='*__pycache__*' --exclude='._*'` (macOS AppleDouble `._*`가 Linux alembic null-bytes 유발 — Failure Memory 기록됨)
- 최신 커밋(main, **push 완료**): **1c70c3d**(returns KST) ← **8abb607**(P4 정산) ← f2f35b2(P2) ← b786e11(B) ← 9a45eee(A) ← a4afac7(P1)
- DB head: 로컬·prod 모두 **c8f1a3b5d7e9** (P4 신규 테이블 3개)
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY
- ⚠️ 쿠팡 Open API는 IP 화이트리스트(D-8) — 로컬 전부 403, 실sync/검증은 **서버 SSH에서만**

## 2. 이번 세션 완료 목록
### ✅ P4 정산 도메인 — 완료(main 8abb607, prod 배포·라이브 실증, push 완료)
- ✅ 명세: 서버 라이브 프로브로 실응답 확정 → `docs/references/04_coupang_fees_map.md §6` 보강
  - revenue-history: 거래(orderId)단위 + `items[]` 옵션중첩. **`token` 파라미터 필수**(누락 400). recognitionDate(인식일) 기준. serviceFeeRatio·serviceFee·saleAmount·settlementAmount는 items[] 안. saleType=SALE/REFUND.
  - settlement-histories: **JSON 배열 직접 반환**(code 래핑 없음). `revenueRecognitionYearMonth`(YYYY-MM) 파라미터. sellerServiceFee(월55k)·finalAmount·deductionAmount. bank정보=PII.
- ✅ SA `backend/app/clients/coupang/settlement.py`: get_revenue_history·iter_revenue_history·get_settlement_histories + `__init__.py` export
- ✅ DB `backend/app/models.py`: `CoupangRevenueFee`(옵션 그레인 serviceFeeRatio)·`CoupangSettlementPayout`(정산단위·bank PII 제외)·`CoupangFeeChangeLog`(감사로그) + alembic `c8f1a3b5d7e9`
- ✅ Harness `backend/app/services/coupang/settlement_sync.py`: 매출내역 적재 + **fee_audit(D-11)** — 실측 serviceFeeRatio ≠ 등록 sale_agent_commission 감지 → 상품API saleAgentCommission 권위 재확인 → ①정당변동=자동갱신 ②등록율 그대로인데 실측만 다름=과오청구 의심→자동수용 거부+anomaly 플래그+Jino 보고
- ✅ 소비자 4경로: `POST /api/sync/coupang-settlement`(sync.py) + 스케줄러 잡 `sync_coupang_settlement`(05:50 KST, scheduler_service.py) + UI트리거(scheduler.py) + 조회 3개(`GET /api/settlements/coupang-fees·coupang-fee-anomalies·coupang-payouts`, settlements.py)
- ✅ codex PASS 3R(대화형): R1 [P1×3]=캐시키 vii단일→(vii,observed,registered)·settlement 에러dict 위장→성공code만 data·_dec silent0원→파싱실패 경고 → R2 PASS → R3 [P2]=조회범위 TZ→KST(_kst_today) 수용 → PASS
- ✅ ★라이브 실측 보정(원칙22, 격리로 못 잡음): ①recognitionDateTo=오늘이면 400→윈도우 끝 어제 ②등록율 0(product_sync 미설정)을 false anomaly→registered<=0 비교불가 ③서버UTC↔KST 날짜경계→_kst_today
- ✅ prod 배포: DB백업(ohisell.db.bak-20260603-p4settlement) → tar → alembic c8f1a3b5d7e9 → 앱로드57 → pm2재기동 → HTTP200. 핵심파일 prod=로컬 sha256 일치
- ✅ ★라이브 실증(prod): revenue_fee **191행**(WING1 7·WING2 184)·payout **39행**·실패0. 실측율 정상(옵션94365168294=10.5% §4일치), REFUND/SALE·RESERVE·음수정산 적재. anomaly 0. POST/GET 4경로 라이브 검증
- ✅ main 커밋 8abb607 + push 완료

### ✅ returns_sync KST 통일 — 완료(main 1c70c3d, 백그라운드 작업, push 완료)
- spawn_task로 띄운 백그라운드 세션이 returns_sync._date_windows의 datetime.now()(UTC)→_kst_today() KST 통일 + prod 배포·실증. 이 세션이 검토 후 커밋·push(1c70c3d).

### ✅ P3 로켓그로스 조사 — 실데이터 0 확인(코딩 안 함)
- 서버 프로브 2회로 RG 실데이터 파악: RG 상품은 등록됨(오하이 액정보호필름 등 승인완료, 15중 6 RG플래그)이나 **사이즈(skuInfo.height/length/width/weight) 전부 null**·**재고(maximumBuyCount) 0**·**RG 판매 0**.
- → P3 지금 구현해도 사이즈/재고/주문 전부 빈 껍데기, 라이브 실증 불가(원칙22). **Jino 결정: P7 종합조망 먼저, P3는 RG 활성화(사이즈 입력·판매 시작) 시점에.**
- RG 상품 사이즈는 `item.rocketGrowthItemData.skuInfo`에 중첩(현재 null). RG 상품조회 API = P1 상품조회와 동일 엔드포인트(products.get_product).

## 3. 확정된 결정사항 (번복 금지)
- **트랙 D-10**(승인): 수수료 비교 기준선 = 등록 수수료율(coupang_product_item.sale_agent_commission) ↔ 실측 serviceFeeRatio. 공식 카테고리표는 정적 보관.
- **트랙 D-11**(승인): 자동 업데이트 안전장치 = 권위확인된 변동만 자동(정당변동). 등록율 그대로인데 실측만 다르면 과오청구 의심→자동수용 금지·anomaly 플래그·Jino 보고. (등록율 0/None은 미설정→비교불가, 쿠팡 최소 4%)
- **P3 보류, P7 우선**(Jino 결정 2026-06-03): RG 실데이터 0이라 P3는 실증 불가 → P7 종합조망(prod 라이브 엔진 → UI) 먼저.
- D-3 유지: 시스템은 사실/지표 정리만, 전략판단은 Jino.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실원천. D-1~D-11, §4 페이즈(P4 [x]), §5 아키텍처, §7 진행, §8 다음액션. **먼저 읽기** |
| `docs/references/04_coupang_fees_map.md` | 수수료 전체지도 + §6 라이브 실응답 구조(revenue-history·settlement-histories) |
| `docs/references/01_coupang_api_full_catalog.md` | 100개 엔드포인트 카탈로그. §11 로켓그로스(P3) |
| `backend/app/clients/coupang/settlement.py` | P4 SA |
| `backend/app/services/coupang/settlement_sync.py` | P4 Harness + fee_audit(D-11) |
| `backend/app/services/coupang/intelligence.py` | ★P7 결합 엔진(트랙 §5에 정의, **아직 미생성** — P7에서 만들 것) |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **수수료 비교 데이터 토대 약함(D-3 사실)**: 실측율 보유 84옵션 중 등록율 매칭 4개뿐 — product_sync가 대부분 옵션의 sale_agent_commission 미커버(P1 제약, 옵션 다수 vendorItemId null). 감사 메커니즘은 작동하나 비교 확대는 product_sync 커버리지 개선 필요(별도).
- ⚠️ 배포: macOS tar AppleDouble(`._*`) → Linux alembic null-bytes. `COPYFILE_DISABLE=1 --exclude='._*'` 필수.
- ⚠️ revenue-history는 token 파라미터 필수·recognitionDate 인식일 기준(과거)·윈도우 끝 어제까지. settlement-histories는 배열 직접 반환.
- prod 롤백자산: DB백업 ohisell.db.bak-20260603-p4settlement, 코드백업 /tmp/rollback_P4(서버, 임시).
- 스케줄러 prod 등록: sync_coupang_products(05:30)·sync_coupang_returns(05:45)·sync_coupang_settlement(05:50) 전부 enabled.
- 미커밋 워킹트리: `M CLAUDE.md`(세션 전부터, P4 무관) + `?? docs/TRACKS.md·references/01·02·03`(이전 페이즈 미추적 문서 — P7 때 같이 커밋 가능).

## 6. 다음에 할 작업 (미완료) — P7 종합 조망(Command Center)
**Jino가 P7 우선 결정. D-2 최종목적. 대형 신규(결합엔진+라우터+프론트). Opus 권장.**
- [ ] **구조 설계 먼저**(CLAUDE.md 새 도메인 원칙): 결합 엔진 intelligence.py가 3축(①회계 진짜순이익 ②광고 사실정리 ③상품 판매현황)으로 무엇을 파생할지 + overview 라우터 응답 형태 + Command Center UI 도표 → Jino 승인 → 코딩.
- [ ] 백엔드 우선(D-6): `services/coupang/intelligence.py`(옵션ID 결합 엔진: coupang_ad_option_daily ⨝ coupang_product_item ⨝ orders ⨝ coupang_return_item ⨝ coupang_revenue_fee → 3축 파생) + `routers/overview.py`(신규).
- [ ] 결합축은 전부 prod 라이브 적재됨: 광고옵션(coupang_ad_option_daily)·상품(coupang_product_item)·주문(orders)·반품(coupang_return_item)·정산수수료(coupang_revenue_fee). vendorItemId 단일 결합키(D-8).
- [ ] 프론트: 사이드바 "🎯 종합 조망" 메뉴 + Command Center 페이지(3축 뷰, drill-down). D-3: 사실/지표 정리만(추천 엔진 없음).
- [ ] codex PASS → prod 배포 → 라이브 실증(원칙22).
- (보류) P3 로켓그로스: RG 사이즈 입력·판매 시작 시점에. 현재 실데이터 0.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-p4-settlement_20260603.md 읽고 이어서 작업해줘. P7 종합조망 시작하자.
```
