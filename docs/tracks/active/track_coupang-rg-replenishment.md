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
- [x] S4. replenishment_calc SA — **완료 + codex pass(1라운드 수정) + prod 라이브검증(DB사본) 성공(2026-06-05)**. 아래 S4 결과 참조.
- [x] S5. rg_replenishment Harness 조합 — **완료 + codex pass(0블로킹) + prod 라이브 엔드포인트 검증 성공(2026-06-05)**. 아래 S5 결과 참조.
- [x] S6. UI 컬럼(로켓그로스 탭) + 프론트 — **완료 + prod 라이브 배포(2026-06-05)**. 아래 S6 결과 참조.
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

## S4 결과 (2026-06-05) — 완료 + codex pass(1라운드 수정) + prod 라이브검증(DB사본) 성공
- **신규 파일**: `app/services/coupang/replenishment_calc.py`(읽기전용 SA, 새 테이블·마이그레이션 없음).
- **핵심 알고리즘**: ① 오늘부터 하루씩 `재고 −= segments[그날 평일/주말/휴일 예상판매]`로 깎는 **요일 인지 forward 투영**(S3 요일구분의 실사용 지점) → 안전재고선·0 도달일 산출. ② 안전재고 = `(p90리드 − mean리드) × base_rate`(변동성 흡수분, D-2). ③ 권장 발송일 = `(안전재고 도달일) − ceil(p90리드)`(보수적), 오늘 이하면 reorder_now(🔴). ④ 권장 수량 = `목표레벨(target_days×base_rate + 안전재고) − 발송분 도착시점 투영재고`(0 하한, 올림).
- **★Jino 승인 설계결정(D-7로 확정, 아래)**: ① 속도 None·리드 None·재고행 없음 → status=insufficient_data(추천 보류). ② base_source≠order_item(sold_30d/order_item_low)·요일계수 collecting·글로벌리드 폴백 → confidence=low(추천은 하되 표기). ③ 발송수량 목표일수 = 3일(2~3일치 상한).
- **공개 API**: `calc_replenishment(db, vii, *, target_days=3, current_stock=_UNSET, velocity=_UNSET, lead_time=_UNSET)`(단일, S5 Harness 호출 — 원칙18-8 optional 입력; `_UNSET` 센티넬로 "미주입" vs "계산했으나 None(데이터없음)" 구분). ★전체옵션 배치는 3 SA를 가로지르는 오케스트레이션이라 SA 아닌 **S5 Harness 책임**(원칙18-7) → 이번엔 SA만, 엔드포인트도 S5/S6에서.
- **codex**: 1차 차단 2건 — (B1) safety_stock=0일 때 `_days_until_below`가 `<0`까지 대기→소진 후 도착(off-by-one). **동의·수정**: threshold를 `_EPS`로 하한. (B2) horizon cap이 "120일째 교차"와 "끝까지 미교차"를 동일 반환→well_stocked 오분류. **동의·수정**: 헬퍼가 `(days, crossed)` 반환. nit 2건(_UNSET 센티넬, segment_factors 누락시 low)도 반영. nit 1건(reorder_now 과거 ship_by) **부분기각**: 과거 발송일은 "마감 지남"의 정직한 데이터, status가 긴급성 전달, S6 UI에서 렌더 — 유지. → 2차 **pass**.
- **라이브 검증(prod DB사본)**: 재고 784행 → insufficient 773(판매신호 전무, 결정① 정직 보류) / ok 5 / reorder_now 4 / well_stocked 2. trust_days=1이라 confidence 전부 low(결정②). 수동 검산 일치(예: 옵션 95521944483 재고0·sold30 19→base0.633·safety0.5·즉시발송·qty5 / 95521944481 재고12→qty2 rounding까지 대조). B1/B2 수정 후 분포 동일·safety>0 케이스 d2safe<d2zero 정상.
- **self-verify**: 합성입력 단위검증(안전재고·forward투영·발송일·수량·status전이·confidence) + codex 지적 엣지(safety=0, day-120 교차, factors 누락) 전부 통과.

## S5 결과 (2026-06-05) — 완료 + codex pass(0블로킹) + prod 라이브 엔드포인트 검증 성공
- **신규 파일**: `app/services/coupang/rg_replenishment.py`(Harness — 정보유통 허브, 원칙18-6, 읽기전용·새 테이블 없음).
- **수정 파일**: `routers/coupang_ops.py`(import rg_replenishment + `GET /api/coupang/ops/replenishment-plan?account_key=&target_days=` 엔드포인트 — Harness 경유, 원칙18-7. 3 SA 가로지르는 오케스트레이션이라 조회예외 아님).
- **배치 역산(원칙18-8)**: `estimate_sales_velocities`·`estimate_lead_times`·`_load_inventory`를 **각 1회** 산출 → 옵션별 `calc_replenishment(current_stock=, velocity=, lead_time= 주입)`. 셋 다 주입 시 calc는 DB 미접근(_UNSET 분기 스킵) → N×전체스캔 제거. 모집단=rg_inventory 보유 옵션(orderable_qty NULL은 None 주입).
- **★등가성 계약(이 Harness의 핵심)**: 배치 주입 결과 = calc_replenishment 미주입(_UNSET, 단일 SA 직접 호출) 결과와 **정확히 동일**. 단일↔배치 SA 출력 형태 차이 2곳을 어댑터가 메움:
  - `_velocity_for`: 배치 `options[vii]`엔 `segment_factors`·`trust_days`가 없음(단일 함수는 반환 직전 붙임). 안 붙이면 calc `_confidence`가 강제 low로 오판 → 글로벌 factors 병합. `base_source=='none'`이면 None(단일과 동일 규칙).
  - `_lead_for`: 입고 표본 0개 옵션은 배치 `options`에 부재 → 단일 함수의 글로벌 폴백(`source='global'`)을 Harness가 재현(글로벌도 없으면 None).
- **codex review**: **No blocking issues**(2차 없이 1차 pass). 5개 등가성 지점 독립 검증 확인(velocity/lead 어댑터, orderable_qty=None 등가, vendor_item_id unique로 dict collapse 없음, account_key 필터 일관성). nit 3건 — ①등가성 회귀 테스트 부재(부분수용: 프로젝트가 committed 테스트 없는 라이브 self-verify 컨벤션·784/784 라이브 대조로 계약 확인, pytest 인프라는 S5 범위밖→후속 후보) ②`_sort_key` int-only(수용·하드닝: (int,float) 허용·bool 배제) ③`_summarize` 미지status 누락(유지: status 닫힌집합 4종).
- **self-verify(prod DB사본 784행)**: build_replenishment_plan items 784건을 옵션별 `calc_replenishment(_UNSET)`와 전수 대조 → **불일치 0건(배치==단일)**. status 정렬 단조 확인.
- **라이브 검증(prod GET /replenishment-plan)**: HTTP 200, 784건. summary={reorder_now 4·ok 5·well_stocked 2·insufficient_data 773·low_confidence 11}(S4 DB사본 분포와 일치). lead_global p90=2.88, sort monotonic=True. S4 샘플 옵션 95521944483(stock0·base0.633·qty5) 라이브 재현. ★실 프로덕션 HTTP 경로 증거(원칙22, DB사본 아님).

## 확정 결정사항 추가 (D-7)
- **D-7 (S4 발송 역산 정책 — 확정 2026-06-05)**: ① 일판매속도·리드타임·현재고 중 하나라도 없으면 권장 보류(insufficient_data, Jino 수동). ② sold_30d/order_item_low 기반이거나 요일계수 collecting·글로벌리드 폴백이면 추천하되 confidence=low로 투명 표기. ③ 발송수량 목표는 D-2 "2~3일치"의 **상한 3일**(과소발송보다 품절 회피, 보관료는 3일이면 짧음). 안전재고는 (p90−mean)×일판매로 리드 변동성만 흡수(D-2 "최소"). Jino 원문: "그래"(①②③ 일괄 승인). **※ D-9로 목표일수 변경됨(3→7).**
- **D-9 (재고 목표 1주일치 — 확정 2026-06-08, D-2·D-7③ 변경)**: FC 목표 보관량을 **7일치**로 상향(기존 D-2 "2~3일치"·D-7③ "상한 3일"에서 변경). 발송수량 산정 기준 target_days=3→7. 효과: 권장 발송수량 약 2배↑, 발송 빈도↓·품절 리스크↓, 대신 FC 보관 재고·보관료↑(Jino가 트레이드오프 인지하고 결정). 안전재고 공식((p90−mean)×일판매)은 불변. 구현: replenishment_calc.DEFAULT_TARGET_DAYS=7, 엔드포인트 Query 기본 7, 프론트 fetchReplenishmentPlan(7). Jino 원문: **"우리의 재고 목표를 1주일치로 잡자"**.

## S6 결과 (2026-06-05) — 완료 + prod 라이브 배포
- **수정 파일**: `rg_replenishment.py`(CoupangProductItem LEFT 조인으로 item_name 1회 조회 → items에 주입), `frontend/src/lib/api.ts`(ReplenishmentPlan/Item 타입 + fetchReplenishmentPlan()), `frontend/src/pages/CoupangOps.tsx`(RgReplenishmentSection 컴포넌트 + channelFilter=로켓그로스 시 표시).
- **UI 구성**: 로켓그로스 탭 선택 시 발송관제 섹션이 테이블 위에 나타남. 상단 summary 배지(즉시발송🔴·정상🟢·여유🔵·부족⬜·저신뢰). 컬럼: 상품명(item_name·vendor_item_id) | 상태 | 현재고 | 일판매 | 리드타임(p90) | 며칠치 | 권장발송일 | 권장수량. 기본=발송필요(reorder_now+ok)만 / 전체보기 토글.
- **라이브 검증**: item_name 필드 정상 반환(784건, reorder_now 4건 상품명 확인 — 예: "2개입 아이폰15"). prod 배포 = rg_replenishment.py scp + pm2 restart + frontend dist 배포. 커밋 ddcd666.

## 현재 진행 단계
- 2026-06-05: **S6 완료 + prod 라이브 배포**. 로켓그로스 탭에 발송관제 섹션 UI 완성. 진행 6/7. 다음 = S7 요일/휴일 세분화 지속 개선(데이터 더 쌓인 후).

## 다음 액션 (S7)
- **S7 요일/휴일 세분화 지속 개선(D-6)**: 매일 RG order sync로 깨끗한 일자 누적 → 임계(평일8/주말4/휴일2) 넘으면 요일계수 자동 활성(약 2~3주 후 평일계수부터). 별도 코딩 없이 sales_velocity_estimator가 자동 승격.
- **★2026-06-15 라이브 점검(prod GET /sales-velocity)**: trust_days=11. 게이트 정상 작동 확인(고장 아님) — weekday sample_days 7/min 8(1일 부족)·weekend 3/4·holiday 1/2, 전부 collecting·factor=1.0. **평일계수는 다음 깨끗한 평일 1회 누적 시 자동 승격 임박**. 코딩 불필요, 데이터 누적만 대기.
- 참고: 쿠키 만료 주기 측정 중. 일일 sync 302 발생 시점 = 만료. D-5대로 잦으면 자동화 검토.
- ★코드 커밋: S2=b8b6fa5, S3=0dd51f7, S4=0a3b496, S5=cd16ddc(feat)+bf4e41f(docs), S6=ddcd666(feat).
- 참고(후속 후보): S5 등가성 계약 committed 회귀 테스트(codex nit, 현재는 라이브 self-verify로 대체). pytest 인프라 도입 시 함께.
