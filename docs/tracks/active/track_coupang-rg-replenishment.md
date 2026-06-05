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
- **D-6 (판매속도 평일/주말/휴일 구분 — S3부터 도입 + 매일 고도화, 확정 2026-06-05)**: S3 sales_velocity_estimator에서 평일/주말/휴일 3구간 구분을 **처음부터 도입**한다(S7로 미루지 않음). 매일 아침 RG order sync로 일자별 판매를 **누적**하고, ★RG 매출버그 수정일(2026-06-04) 이후의 깨끗한 일자 데이터가 쌓일수록 정확도를 점진 고도화한다. 각 구간 추정에 **표본수(관측일수)·신뢰도(confidence)·source를 함께 표기**해 투명성을 유지(S2 폴백 패턴 계승). 휴일 판정은 한국 공휴일(음력 설·추석 포함)이라 `holidays` 라이브러리 사용. 표본 부족 구간은 폴백(옵션 구간→옵션 전체→글로벌 구간→sold_30d/30). D-3("평일/주말 시작→점진 세분화")의 구체 실행 결정.

## 사용자 원문 인용 (왜곡 방지)
- "API로 연결하자. 창고보관료가 있기때문에 약 2~3일치의 재고만 보관하고 싶어. 그리고, 주말, 주중, 휴일등의 변수도 있기 때문에, 이런것들은 너가 계속 발전해나가면서 세분화시켜줘"
- "각 아이템별 우리 사무실에서 발송 후 도착 및 판매시작 기간을 예측해서 매일매일 발송해야 하는 갯수와 발송 일자를 알려주면 … 매일매일 언제 발송해야 하는지, 몇개를 발송해야 하는지 알 수 있지 않을까?"
- (S3 평일/주말/휴일, 2026-06-05) "그래, 이걸 너가 매일아침에 확인해서 계속 통계를 내고 정확도에 대해서 평일, 주말, 휴일에 대해서 구분해서 정확도를 고도화하자"

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
- [x] S1. coupang_rg_inbound 테이블 + rg_inbound_sync SA — **완료 + prod 라이브 검증 성공(2026-06-05)**. 입고 6건/옵션 47개 적재, 리드타임 1.15~4.5일. 아래 S1 결과 참조.
- [x] S2. lead_time_estimator SA — **완료 + prod 라이브 검증 성공(2026-06-05)**. 옵션별 리드타임 분포 + 글로벌 폴백. 아래 S2 결과 참조.
- [x] S3. sales_velocity_estimator SA (평일/주말/휴일) — **완료 + codex pass + prod 라이브 검증 성공(2026-06-05)**. 아래 S3 결과 참조.
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

## S1 라이브 검증 결과 (2026-06-05) — 성공
- prod 배포: cryptography 설치 + `COOKIE_ENC_KEY`(prod 신규키) .env 추가 + `alembic upgrade head`(테이블 2개 생성) + 코드 8파일 scp + pm2 restart. 커밋 `3ede9cd`(main).
- **inbound_id 실필드명 = `shipmentId`** 확정(예 '1063738045171253249'). content[0] 키엔 id/inboundId 없음 → 방어적 후보키의 `shipmentId`가 안정 포착(fallback 미사용). 코드 수정 불필요.
- 엔드포인트 라이브: `POST /inbound/cookie` 200 → `POST /inbound/sync` 200 = **입고 6건 / 옵션 47개 적재**(한 shipmentId당 평균 8옵션). 쿠키 status=green, last_success_at 기록(=만료 측정 시작).
- **리드타임 실측**(발송 statusId3 → 판매개시 statusId7): 1.15·2.18·4.5일 등. shipAt/stowAt/req·recv·stow 수량 전부 실데이터 정상.
- 스키마 일치: shipmentStatusHistory=dict("_N", statusId 숫자), skuDetails=list, plannedSku.{vendorItemId,skuId,requestedQty,cachedSkuName} + receivedQty/stowedQty. 전부 파싱 코드와 일치.

## S2 결과 (2026-06-05) — 완료 + codex pass + prod 라이브 검증 성공
- **신규 파일**: `app/services/coupang/lead_time_estimator.py`(읽기전용 SA, 새 테이블·마이그레이션 없음). `_percentile`(numpy 미의존 선형보간), `_summarize`((lead,stowing) 표본→count·mean·p50·p90·min·max·latest), `estimate_lead_times`(전체+글로벌), `estimate_lead_time(vii)`(단일, S4가 호출 — 원칙18-8 optional 입력).
- **수정 파일**: `routers/coupang_ops.py`(import estimator + `GET /api/coupang/ops/lead-times` 검증/UI 엔드포인트. read-only라 SA 직접 호출=원칙18-7 조회 예외).
- **폴백 정책(라이브 사실 기반)**: 옵션 표본 ≥ MIN_SAMPLES(=2) → 옵션 추정(source="option"), 미만 → 글로벌 분포 폴백(source="global"), 글로벌도 없으면 제외/None. 라이브 실측 옵션당 표본 1~2개라 글로벌이 주력.
- **안전재고(D-2)**: mean=기대 리드, p90=보수적 리드(변동성 흡수). S4 replenishment_calc가 둘 다 사용 예정.
- **codex**: pass(차단 이슈 없음 — NULL제외·폴백·percentile 전부 요구 일치). 운영리스크(표본2 옵션 p90 약함) 언급했으나 D-3 점진세분화·source표기로 합의(변경 불필요).
- **라이브 검증(prod GET /lead-times)**: global count=28·mean=2.16·p50=2.18·**p90=2.88**·min=0.99·max=4.5. 옵션 23개(0표본 1옵션 제외). source 분포 = global 18 / option 5 (DB 표본분포와 정확 일치: 표본1개 18옵션 폴백, 표본2개 5옵션 옵션추정). 표본2 옵션 예시 mean 1.67·p90 2.08.

## S3 결과 (2026-06-05) — 완료 + codex pass(1라운드 수정) + prod 라이브 검증 성공
- **신규 파일**: `app/services/coupang/sales_velocity_estimator.py`(읽기전용 SA, 새 테이블·마이그레이션 없음). 데이터원 = rg_order_item(paid_at·sales_quantity 일자별 누적) + rg_inventory.sold_30d(폴백).
- **요일 분류(D-6)**: `_classify_day` = 한국 공휴일(`holidays.SouthKorea()`, 음력 설·추석 포함, 선거일까지) 우선 → 토/일 weekend → 평일 weekday. requirements.txt에 `holidays==0.98`(MIT) 추가, prod .venv 설치 완료.
- **신뢰도 게이트(D-6 핵심)**: 글로벌 구간 관측일 ≥ 임계(평일8/주말4/휴일2) → 그 구간 요일계수(factor=구간rate/전체rate) 활성(confidence="ok"), 미달 → factor=1.0(confidence="collecting"). 옵션 base_rate = 관측일≥14+판매>0 → order_item / sold_30d>0 → sold_30d/30 / 신뢰기간 실판매만 있음 → order_item_low(신상품 안전망) / 전무 → None.
- **★신뢰 기준일 TRUST_START=2026-06-04**(RG 매출버그 수정일). 이전 일자 order_item은 paidDateTo 배타버그로 과소적재 → 신뢰표본 제외. `until=어제`(오늘 부분일 제외). 매일 sync로 깨끗한 일자 1일씩 누적 → 자동 고도화.
- **codex**: 1차 차단이슈 1건 — estimate_sales_velocity의 global 폴백이 overall_rate(포트폴리오 전체속도)를 단일옵션 base_rate로 써 차원오류·S4 과발송 위험. **동의·수정**: global 폴백 제거(둘 다 없으면 None) + order_item_low 안전망 추가. → 2차 **pass**(비차단 docstring 권장도 반영).
- **공개 API**: `estimate_sales_velocities(db, account_key=None)`(전체+글로벌, UI/검증) / `estimate_sales_velocity(db, vii, account_key=None)`(단일, S4 호출 — 원칙18-8 optional 입력). 엔드포인트 `GET /api/coupang/ops/sales-velocity`.
- **라이브 검증(prod GET /sales-velocity)**: trust_days=1(06-04만, 06-05 오늘 제외) → segment_factors 전부 collecting/1.0(표본 임계 미달, 정상). 옵션 11개 base_source 전부 sold_30d(예: 옵션 8→0.267, 19→0.633). 없는옵션→None(글로벌 차원오류 제거 확인). global_daily_rate 23.0은 UI지표로만.
- **self-verify(로컬+prod사본)**: 요일분류(음력 포함), 요일계수 임계 게이트, base_rate 폴백 체인(수동대조 일치), 없는옵션 None, order_item_low 안전망 전부 통과.

## 현재 진행 단계
- 2026-06-05: **S3 완료 + prod 라이브 검증 성공**(평일/주말/휴일 신뢰도 게이트, 현재 trust_days=1이라 전부 collecting·sold_30d 폴백, 매일 누적 고도화). 다음 = S4 replenishment_calc. 진행 3/7.

## 다음 액션 (S4)
- **S4 replenishment_calc SA**: 현재고(rg_inventory.orderable_qty) + 일판매속도(S3 estimate_sales_velocity, 요일별 segments) + 리드타임(S2 estimate_lead_time, mean·p90) + 목표 2~3일치(D-2) → 권장 발송수량·발송일 역산. 안전재고 = (p90 리드 − mean 리드)×일판매. ★S3 None(데이터 전무 옵션)·collecting 신뢰도를 어떻게 다룰지(보수적 처리 or 추천 보류) S4에서 결정. → 새 SA라 Opus 권장.
- 그 다음 S5 Harness 조합(원칙18-6 정보유통 허브) / S6 UI 컬럼(로켓그로스 탭) + 엔드포인트 / S7 요일·휴일 세분화 지속.
- 참고: 쿠키 만료 주기 측정 중(last_success 06-05 10:59~). 일일 sync 302 발생 시점 = 만료. D-5대로 잦으면 자동화 검토.
- ★코드 커밋 완료: S2=b8b6fa5, S3=0dd51f7(feat). 문서는 docs 커밋. prod엔 scp+restart로 라이브 반영.
