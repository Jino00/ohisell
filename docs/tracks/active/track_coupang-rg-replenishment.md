# 트랙: 쿠팡 RG 재고·발송 관제 (Replenishment)

> 시작일: 2026-06-05 · 상태: 🟢 Active
> 단일 진실 원천. 이 트랙을 무시·변형해서 진행하지 말 것. 변경은 Jino 승인 후 D-N으로 기록.

## 목표 (한 줄)
쿠팡 로켓그로스(RG) 상품별로 현재고·일판매속도·입고 리드타임을 모아, FC에 **약 2~3일치 재고만** 유지하도록 **"언제·몇 개를 발송하라"를 매일 역산**해 보여준다.

## 확정 결정사항 (D-N)
- **D-1 (입고 API 연결, D-14 수정)**: 입고(inbound) 리드타임 데이터를 **Wing 내부 API**(`GET wing.coupang.com/tenants/rfm-inbound/data/inbound/search`, 세션쿠키 인증)로 연결한다. 기존 쿠팡 트랙 D-14("입고는 공식 Open API만")를 **이 기능에 한해 변경**. 사유: 발송→판매개시 리드타임은 공식 Open API에 없고(RG 9개·물류센터 8개 전수 확인 완료) Wing 내부 API에만 있음(`shipmentStatusHistory` CREATED→SHIPMENT_CREATED→판매개시 ms 타임스탬프, 운송장, requestedQty/receivedQty, CBM — 네트워크 캡처 실검증, references/05 §입고).
- **D-2 (목표 재고 2~3일치)**: 쿠팡 FC 목표 보관량 = 약 2~3일치 판매량. 보관료·자본 효율 우선. 안전재고는 리드타임 변동성 흡수분만 최소로.
- **D-3 (요일/휴일 점진 세분화)**: 판매속도 모델은 1단계 평일/주말 구분에서 시작, 데이터 누적에 따라 휴일·시즌까지 점진 세분화. "처음부터 완벽" 아님.
- **D-4 (출력 성격)**: 시스템은 데이터 기반 "권장 발송수량·발송일"을 **지표로 제시**. 실제 발송 결정·실행은 Jino. (트랙 단위 운영 보조이며, 종합조망 D-2 '전략추천 금지'는 광고전략 맥락 — 본건은 운영 재고보충 역산값.)

## 사용자 원문 인용 (왜곡 방지)
- "API로 연결하자. 창고보관료가 있기때문에 약 2~3일치의 재고만 보관하고 싶어. 그리고, 주말, 주중, 휴일등의 변수도 있기 때문에, 이런것들은 너가 계속 발전해나가면서 세분화시켜줘"
- "각 아이템별 우리 사무실에서 발송 후 도착 및 판매시작 기간을 예측해서 매일매일 발송해야 하는 갯수와 발송 일자를 알려주면 … 매일매일 언제 발송해야 하는지, 몇개를 발송해야 하는지 알 수 있지 않을까?"

## 구조 (승인됨 2026-06-05)
```
[Agent] 쿠팡 RG 재고·발송 관제
  └─[Harness] rg_replenishment (정보 유통 허브)
       ├─[SA] rg_inbound_sync         ★신규 — Wing 내부 입고 API → coupang_rg_inbound 테이블
       ├─[SA] rg_inventory_sync       기존 — 현재 주문가능재고 + 30일판매량
       ├─[SA] rg_order_sync           기존(수정완료) — 일별 판매수
       ├─[SA] lead_time_estimator     ★신규 — 발송→판매개시 리드타임 분포
       ├─[SA] sales_velocity_estimator ★신규 — 평일/주말→휴일/시즌 세분화
       └─[SA] replenishment_calc      ★신규 — 현재고+속도+리드타임+2~3일치 → 권장 발송수량·발송일
```
UI: 상품별 현황(로켓그로스 탭) 컬럼 추가 — `현재고 | 최근 일판매 | 리드타임(추정) | 며칠치 남음 | 권장 발송일·수량`

## 핵심 리스크
- **세션쿠키 인증**: Wing 내부 API는 셀러 로그인 세션쿠키 필요. 백엔드(IP화이트리스트+HMAC만 쓰던 서버)가 세션을 어떻게 유지·갱신하느냐가 관문. 쿠키 만료 시 입고 동기화 중단. → **선검증 1순위.**

## 체크리스트
- [x] S0. 세션쿠키 인증 방식 실증 — **성공(2026-06-05)**. 아래 S0 결과 참조.
- [x] S1. coupang_rg_inbound 테이블 + rg_inbound_sync SA — **코드+codex pass 완료(2026-06-05)**. ⚠️라이브 검증 대기(Jino 쿠키). 아래 S1 결과 참조.
- [ ] S2. lead_time_estimator SA
- [ ] S3. sales_velocity_estimator SA (평일/주말)
- [ ] S4. replenishment_calc SA
- [ ] S5. rg_replenishment Harness 조합
- [ ] S6. UI 컬럼(로켓그로스 탭) + 엔드포인트
- [ ] S7. 요일/휴일 세분화 점진 개선 (지속)

## S0 실증 결과 (2026-06-05) — 성공
- **엔드포인트**: `GET https://wing.coupang.com/tenants/rfm-inbound/data/inbound/search?pagingSize=10&pageIndex=0` (GET, body 없음).
- **인증**: 세션쿠키 + 헤더 `x-xsrf-token`(=XSRF-TOKEN 쿠키값). 필수 쿠키는 최소 3개(sid·JSESSIONID·XSRF-TOKEN)로는 부족 → 302 로그인 리다이렉트. **전체 쿠키셋(특히 sid·sxSessionId·web-session-id·seller-uid 포함)**이면 200. Akamai 봇쿠키(_abck·bm_*)는 **불필요**(빼고도 200).
- **서버 호출 가능**: 프로덕션(Oracle 강원, KR IP)에서 전체쿠키로 **HTTP 200, 68KB**. 지역/IP/봇 차단 없음. ★프로덕션 경로 실증 완료.
- **응답 스키마(실측)**: `content[]` 각 입고 = `vendorId`, `createdAt/updatedAt`(ms), `skuDetails[].plannedSku.{vendorItemId, skuId, vendorInventoryId, requestedQty, cachedSkuName}` + `receivedQty/stowedQty`, `shipmentStatusHistory.{_N}.{statusId, internalLifecycleStatus, updatedAt(ms)}`, `pagination`.
- **생애주기(리드타임 핵심)**: statusId 1~7 = CREATED→PO_CREATED→SHIPMENT_CREATED→INIT_COMPLETED(=발송시점, 동시각) → UNLOADING(FC도착) → RECEIVING(검수) → **STOWING(=판매개시)**. ★리드타임 = SHIPMENT_CREATED → STOWING.
- **실측 리드타임**: 1.0~4.5일(샘플 4건: 4.5/1.1/2.2/1.0/1.3일). 변동성 큼 → 안전재고 필요.
- **이력 규모**: 전체 입고 6건(반년치). 적음 → 리드타임 추정은 적은 표본에서 시작, 누적 개선.

## D-5 (쿠키 갱신 — 확정 2026-06-05)
- **수동 붙여넣기로 시작 + 실제 만료 주기 측정 → 잦으면 자동화 추가.** 설정 화면에 쿠키(또는 cURL 통째→쿠키 자동추출) 붙여넣기 칸 + 저장. 서버 시크릿 저장. 일일 입고 sync가 곧 측정기(302 발생 시점 = 만료). 만료 시 🔴 상태+알림.
- 자동화 보류 사유(Jino와 합의): ① 입고 데이터 거의 불변(반년 6건) → 쿠키 끊겨도 손해 작고 자동화 이득도 작음(비대칭) ② Mac 쿠키 자동수확 데몬이 기능 중 최취약·최난해 부품 ③ 미측정 문제에 보험 거는 격 ④ 핵심가치(발송추천) 지연. 측정 후 만료 잦으면 방법1(Mac 자동수확)/방법2(로그인1회+세션유지) 추가. 방법3(서버 자가 ID/PW 로그인)은 로그인페이지 Akamai+2FA로 비추천.
- ★IP 비귀속 실증: Jino 브라우저 쿠키를 서버(다른 IP)에서 재생→200. 쿠팡이 세션을 IP에 안 묶음 → "브라우저 쿠키를 서버가 사용"은 작동 보장됨. 자동화는 '수확'만 남은 문제.

## S1 결과 (2026-06-05) — 코드 완료 + codex pass, 라이브 검증 대기
- **신규 파일**: `app/clients/coupang/inbound.py`(CoupangInboundClient — HMAC 미상속, 세션쿠키, 302=만료, 스키마 방어), `app/services/coupang/rg_inbound_sync.py`(Harness — 쿠키 CRUD·동기화·리드타임 파싱·fail-soft), `app/utils/crypto.py`(Fernet 암복호화), `alembic/versions/e1f3a5c7b9d2_*.py`(마이그레이션).
- **수정 파일**: models.py(CoupangRgInbound + CoupangWingCookie), routers/coupang_ops.py(엔드포인트 4개), scheduler_service.py + routers/scheduler.py(sync_coupang_rg_inbound job, cron `20 5 * * *`), clients/coupang/__init__.py, requirements.txt(cryptography==48.0.0).
- **엔드포인트**: `POST /api/coupang/ops/inbound/cookie`(body {account_key, curl} — cURL 통째→쿠키·xsrf 추출·Fernet암호화), `GET /api/coupang/ops/inbound/cookie/status`(계정별 green/red/unknown/none + 저장·성공시각), `POST /api/coupang/ops/inbound/sync`(수동 트리거, ?account_key 선택), `GET /api/coupang/ops/inbound`(적재 조회).
- **보안**: 쿠키/xsrf = Fernet 암호화 저장(키=.env `COOKIE_ENC_KEY`). 응답·로그에 평문 노출 없음(cookie_len만).
- **fail-soft(D-5)**: 302/401(만료)·read오류 → status=red + rollback(마지막 이력 유지) + items=0. 성공 시 last_success_at=만료 측정기. 쿠키 미설정=cookie_missing 스킵.
- **grain**: (account_key, inbound_id, vendor_item_id). ⚠️inbound_id 필드명은 라이브 실응답으로 확정 전 → 방어적 추출(후보키→없으면 vendorId_createdAt 합성) + raw_json 보관. 리드타임 = statusId 3→7(_int 변환 매칭).
- **self-verify(로컬)**: 마이그레이션 적용, import, 암호화 왕복, cURL 파싱, 리드타임 계산, 스키마 방어, fail-soft rollback, 라우터 4개 전부 통과.
- **codex**: 1차 needs-changes(stale 위장·statusId 문자열·rollback 통계·skuDetails 방어·중복인덱스) → 6건 반영 → **2차 pass**.

## 현재 진행 단계
- 2026-06-05: **S1 코드 완료 + codex pass**. ⚠️아직 prod 미배포·라이브 미검증(원칙22). 다음 = prod 배포 + Jino 쿠키로 라이브 검증.

## 다음 액션 (S1 라이브 검증 → S2)
- **S1 라이브 검증(즉시)**: ① prod에 cryptography 설치 + `COOKIE_ENC_KEY` 신규키를 prod .env에 추가 ② prod `alembic upgrade head` ③ 코드 scp + pm2 restart ④ Jino가 Wing 입고페이지에서 'Copy as cURL' → `POST /inbound/cookie` 저장 ⑤ `POST /inbound/sync` → 200 + 입고 6건 적재 + **실응답으로 inbound_id 실제 필드명 확정**(필요시 _inbound_id 후보키 보강) + 리드타임 값 검증.
- **S2 (라이브 검증 후)**: lead_time_estimator SA — coupang_rg_inbound에서 옵션별 발송→판매개시 리드타임 분포(평균·p50·p90, 표본수). 표본 적으니(반년 6건) 전체 평균 폴백 + 표본 누적 개선.
