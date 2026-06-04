# 세션 인수인계: ohisell-coupang-p2-and-fees
> 저장일시: 2026-06-03 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙. 이 세션 = **P2 반품/취소/교환 완료(배포·라이브 실증)** + **수수료(P4 정산) 조사·설계 진행 중(미착수 코딩)**. 트랙 파일이 진짜 진실 원천.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **서버 포트=8001**
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp 파일복사**
- ⚠️ tar 전송 시 **`COPYFILE_DISABLE=1 tar --exclude='*__pycache__*' --exclude='._*'`** (macOS AppleDouble `._*`가 Linux alembic null-bytes 유발 — 이번 세션 교훈, Failure Memory 기록됨)
- 최신 커밋(main): **f2f35b2** (P2 라이브보정) ← 73e376f(P2머지) ← b786e11(B) ← 9a45eee(A) ← a4afac7(P1). origin/main push 완료
- DB head: 로컬·prod 모두 **a3d7c9e1f2b4** (P2 신규 테이블)
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY
- ⚠️ 쿠팡 Open API는 IP 화이트리스트(D-8) — 로컬 전부 403, 실sync/검증은 **서버 SSH에서만**

## 2. 이번 세션 완료 목록
### ✅ P2 반품/취소/교환 도메인 — 완료(main f2f35b2, prod 배포·라이브 실증)
- ✅ 명세 `/browse` 공식수집 → `docs/references/03_coupang_returns_api_specs.md` (반품7+교환4, §2.5 라이브 실측보정)
- ✅ `backend/app/clients/coupang/returns.py`(7 SA: 읽기4 구현·쓰기3 stub)·`exchanges.py`(4 SA: 읽기1·쓰기3 stub)
- ✅ `backend/app/clients/coupang/_base.py`: POST body 지원 + `CoupangReadError`(읽기 하드실패 표면화)
- ✅ `backend/app/models.py`: `CoupangReturnItem`(옵션 그레인 순매출 차감)·`CoupangExchange` + alembic `a3d7c9e1f2b4`
- ✅ `backend/app/services/coupang/returns_sync.py`: Harness (RETURN status별 순회·CANCEL·31일/7일 윈도우 분리·철회 withdrawn 마킹·교환 적재·seen-cache·api_failures 표면화)
- ✅ 소비자 3경로: 스케줄러 `sync_coupang_returns`(05:45 KST)·UI 트리거(scheduler.py)·`POST /api/sync/coupang-returns`(sync.py)
- ✅ codex PASS 3R: R1[P1]읽기실패 0건위장→CoupangReadError·[P2]철회 vendor_id 스코프 → R2 합의 → R3(라이브보정 후)PASS
- ✅ ★라이브 실측 보정(원칙22, 격리로 못잡음): RETURN은 status 필수(400 "OrderId can't be null")→RU/UC/CC/PR 순회 / 교환·철회 최대7일(400 "less then 7day")→7일 윈도우 / UNIQUE 중복(500)→per-run seen-cache
- ✅ prod 배포: DB백업(ohisell.db.bak-20260603-p2returns) → tar(COPYFILE_DISABLE) → alembic a3d7c9e1f2b4 → 앱로드53 → pm2재기동 → HTTP200. 배포파일10개 prod=main sha256 일치
- ✅ ★라이브 실증(prod): 반품 13행(RETURN3·CANCEL10)·실패0. **반품⨝주문 10옵션 매칭**(순매출차감 — 갤S24플러스 취소1/단가16900 실금액). 교환0(윈도우내 없음)

### ✅ 수수료 조사 — 완료(코딩 미착수, 설계 진행 중)
- ✅ 외부 공식 조사(`/browse`) → `docs/references/04_coupang_fees_map.md` 작성:
  - 윙(3P): 판매수수료 카테고리 4~10.8%(오하이 대부분 7.8%)·유료배송 결제수수료3.3%·월 서비스이용료55,000원(매출100만↑)
  - 로켓그로스(2P, 현재판매0): 판매수수료(윙동일)+입출고비/배송비(사이즈6단계 XS600/1350~특대형1375/5600)+보관비+반품회수/재입고/반출비(300원)+바코드부가서비스. 사이즈=가로+세로+높이+무게(D-5 RG상품API 치수로 모델 가능)
  - API 실측 출처: 매출내역(revenue-history) `serviceFeeRatio`·`serviceFee`·`settlementAmount`·`deliveryFee`(옵션ID별, 사용중·필드 미저장) / 지급내역(settlement-histories `GET /v2/providers/marketplace_openapi/apis/api/v1/settlement-histories`) `finalAmount`·`sellerServiceFee`·정산차감(미사용)
- ✅ ★라이브 실측 비교(서버): 오하이 실제 판매수수료율이 공식표와 **일치** — 7.8%(대부분, 공식 가전디지털7.8%)·10.5%(고가품 패션잡화)·6.4%. revenue-history는 **최대 ~7일 범위**(30일은 400)

## 3. 확정된 결정사항 (번복 금지)
- **P2 완료**: 순매출 = 주문매출 − (반품/취소 cancelCount × 단가), 철회분(withdrawn) 제외. 결합키 vendorItemId(광고·상품·주문·반품 동일축)
- P2 페이즈 제약(라이브 확정): 반품목록 31일·RETURN status필수(RU/UC/CC/PR)·CANCEL status없음 / 철회·교환 7일 / 호출간 0.3s
- **수수료 비교 방향(설계 확정, 코딩 전 승인 대기)**: 기준선 = 등록 수수료율(coupang_product_item.sale_agent_commission, product_sync가 매일 갱신) ↔ 실측율(revenue-history serviceFeeRatio). 공식 카테고리표는 references/04 정적보관
- **★수수료 자동 업데이트 안전장치(Jino 요구, 설계 확정·승인 대기)**: 불일치 감지 → **권위 재확인(상품API saleAgentCommission 재조회)** → ① 등록율 바뀜=정당변동→자동 업데이트+fee_change_log 기록 ② 등록율 그대로인데 실제만 다름=과오청구 가능→**자동 수용 금지·이상 플래그·Jino 보고**. (원칙18-9 피드백루프·원칙22 권위검증·D-3 사실만)
- D-3 유지: 시스템은 사실/지표 정리만, 전략판단은 Jino

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실원천. D-1~D-9, §4 페이즈(P2 [x]), §7 진행, §8 다음액션. **먼저 읽기** |
| `docs/references/03_coupang_returns_api_specs.md` | P2 반품/교환 명세(라이브 실측보정 §2.5) |
| `docs/references/04_coupang_fees_map.md` | ★수수료 전체지도 + API 실측비교 (다음 작업 토대) |
| `backend/app/clients/coupang/returns.py`·`exchanges.py` | P2 SA |
| `backend/app/services/coupang/returns_sync.py` | P2 Harness |
| `backend/app/clients/coupang/_base.py` | HMAC+POST body+CoupangReadError |
| `backend/app/services/coupang/product_sync.py` | 상품 동기화(sale_agent_commission 갱신 — 수수료 기준선 출처) |
| `backend/app/clients/coupang/channel.py` | revenue-history 사용중(test_connection) — 정산 SA 신설 시 참고 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ 배포: macOS tar AppleDouble(`._*`) → Linux alembic null-bytes. `COPYFILE_DISABLE=1 tar --exclude='._*' --exclude='*__pycache__*'` 필수. 사고 시 서버 `find app alembic -name '._*' -delete`
- ⚠️ revenue-history 최대 조회기간 ~7일(30일 400). 정확한 한계·인식일(배송완료+7/구매확정) 의미 재확인 필요(references/04 §6)
- ⚠️ 지급내역(settlement-histories) 라이브 미호출 — 실응답 구조 미검증
- RG(로켓그로스) 판매 0 → RG 수수료는 모델만, 실측 비교 불가(현재)
- prod 롤백자산: DB백업 ohisell.db.bak-20260603-p2returns, 코드백업 /tmp/rollback_P2
- 스케줄러 prod 등록: sync_coupang_products(05:30)·sync_coupang_returns(05:45) enabled

## 6. 다음에 할 작업 (미완료) — 수수료(P4 정산) 구현
**Jino가 2개 요구 확정, 구조 설계까지 완료, 코딩 미착수. Opus 전환 권장(DB스키마+새Harness+외부API+피드백루프).**
- [ ] **Jino 최종 승인 대기 중 2건**: ① 안전장치 방향(권위확인된 변동만 자동업데이트·설명안되는 차이는 플래그) ② 기준선=등록수수료율 1차. (Claude 강권: 무조건 자동수용 반대 — 과오청구 눈감음)
- [ ] 승인 후 트랙에 **D-10(수수료 캡처·비교) + D-11(자동 업데이트 안전장치)** 기록
- [ ] SA `clients/coupang/settlement.py`: get_revenue_history(수수료 필드)·get_settlement_histories(지급내역) 신규
- [ ] DB: `coupang_revenue_fee`(옵션 그레인: vendorItemId·인식일·매출·serviceFeeRatio·serviceFee·settlementAmount·배송비수수료·할인)·`coupang_settlement_payout`(정산단위)·`coupang_fee_change_log`(옵션·이전율·새율·감지일시·구분[정당변동/이상]·해소여부) + alembic
- [ ] Harness `services/coupang/settlement_sync.py`: 2계정 순회 적재 + **fee_audit**(불일치 감지→권위 재확인→자동갱신 or 이상 플래그)
- [ ] 소비자: POST /api/sync/coupang-settlement + 스케줄러 잡(매일 정산동기화 후 수수료 감사) + 조회 엔드포인트
- [ ] codex PASS → prod 배포(마이그레이션) → 라이브 실증(원칙22)
- (이후) 트랙 페이즈: P3 로켓그로스 / P7 종합조망 화면

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-p2-and-fees_20260603.md 읽고 이어서 작업해줘
```
