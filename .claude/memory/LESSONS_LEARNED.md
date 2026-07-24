# LESSONS_LEARNED.md — ohisell 프로젝트 학습 기록

## 1. Python 3.14 + SQLAlchemy 호환성 이슈

### 이슈
SQLAlchemy 2.0.40에서 `Mapped[str | None]` 사용 시 `TypeError: descriptor '__getitem__' requires a 'typing.Union' object` 에러 발생. Python 3.14의 typing 내부 변경으로 `Union.__getitem__` 동작이 달라짐.

### 해결
1. SQLAlchemy 2.0.48로 업그레이드
2. `from __future__ import annotations` 추가
3. `str | None` 대신 `Optional[str]` 사용

### 교훈
Python 3.14는 아직 최신이라 라이브러리 호환성 이슈가 있을 수 있음. SQLAlchemy는 반드시 2.0.48 이상 사용할 것. 새 Python 버전 사용 시 첫 마이그레이션에서 호환성을 바로 검증해야 함.

## N. 조망 원가 0 — "데이터 없음"이 아니라 "엉뚱한 원천" (2026-06-03, D-12)

### 🐛 이슈
종합조망 순이익에 원가가 거의 미반영(253옵션 중 201옵션 0원). "쿠팡 supplyPrice가 빈값이라 어쩔 수 없다"고 단정할 뻔함. 새 페이즈(P3/P5)를 더 쌓아도 이 구멍은 그대로였을 것.

### ✅ 해결
코딩 전 라이브 진단(읽기전용 SQL, 서버 DB)으로 원인을 사실로 확정 → 원가는 **이미 내부 product_master.cost_price에 792상품(89%) 있었고**, product_channel_mapping(coupang) 다리로 실거래 118옵션(66%)에 닿았다. 결합엔진이 coupang supply_price(0.6% 커버)만 보던 게 원인. 엔진 읽기측 조인을 내부 원가 다리로 바꿔 원가 0→468,313 반영(순이익 과대계상 교정). 신규 테이블·쿠팡호출 0.

### 📌 교훈
"데이터가 없다"고 단정하기 전에, **다른 원천에 이미 있는지 라이브로 진단**하라(원칙22). 특히 같은 시스템이 다른 경로(기존 회계엔진 profit_calculator)로 이미 그 데이터를 쓰고 있으면 거의 확실히 어딘가 있다. 진단 스크립트(diag_coverage.py·diag_bridge.py)로 "현재 원천 커버리지 vs 잠재 원천 커버리지"를 숫자로 비교하면 헛수고를 막는다. 부수: 확정 결정(D-10 saleAgentCommission 기준선)도 라이브에서 전제가 깨질 수 있으니(전부 0) 실데이터로 검증.

## N. 감사 "이상 0건"이 정상이 아니라 "비교 0건"일 수 있다 (2026-06-03, D-13)

### 🐛 이슈
P4 수수료 감사가 "anomaly 0"이라 잘 작동하는 줄 알았으나, 실제로는 기준선(saleAgentCommission)이 201옵션 전부 0이라 `registered<=0`에서 즉시 스킵 → **비교가 한 건도 안 일어남**. "0 이상"은 "전부 정상"이 아니라 "감사 부재"였다.

### ✅ 해결
기준선 데이터 실태를 라이브로 확인(전부 0). 기준선을 옵션 자기 정착 실측율(service_fee_ratio mode)로 교체(D-13). 검증 시 "정상 데이터→0" 뿐 아니라 **합성 이상 주입→플래그 1**을 반드시 확인해 감사가 실제로 비교하는지 증명. stats에 fee_options_checked(비교 시도 수)를 추가해 "비교 0건"과 "비교 후 0"을 구분 가능하게 함.

### 📌 교훈
감사/검증 로직의 "이상 0건" 결과는 **비교가 실제로 일어났는지** 먼저 확인하라(원칙22). 0이 나오면 "정상 0"인지 "스킵 0"인지 구분하는 카운터(시도 수)를 둬라. 검증은 정상→음성뿐 아니라 **합성 양성 주입→양성**으로 탐지력을 증명해야 한다(원칙14). 확정 결정(D-N)의 전제도 라이브에서 깨질 수 있으니 기준선 데이터 실태부터 본다.

## 4. 네이버 클레임 쓰기 API 스펙 함정 — 철자·필드형이 직관과 다름

### 🐛 이슈
N7 wave2 반품 5종 구현 중, 네이버 API센터 실측 스펙이 직관과 어긋나는 지점이 있었다.
- 반품 보류 유형 enum에 `EXTRAFEEE`(E 3개)가 있다 — 오타처럼 보이지만 **원문 그대로**다. "고쳐서" EXTRAFEE로 보내면 거부될 위험.
- 반품 거부 사유 `rejectReturnReason`은 **자유 텍스트**인데, 취소 직접요청 `cancelReason`은 **enum**이다. 같은 "클레임 사유"라도 엔드포인트마다 enum/자유텍스트가 갈린다.
- 반품 수거 배송방법 `collectDeliveryMethod` enum은 발송용 `deliveryMethod`와 달리 RETURN_DELIVERY/RETURN_INDIVIDUAL/RETURN_MERCHANT/UNKNOWN을 포함 — 발송용 상수를 재사용하면 안 됨(별도 _VALID_COLLECT_DELIVERY_METHODS 신설).

### ✅ 해결
전부 API센터 스크린샷 실측만 사용(추측 금지). docs/references/14에 enum·철자 원문대로 기록(EXTRAFEEE 옆에 "★철자 원문대로" 주석). 수거 배송방법은 발송용과 별도 상수로 분리. 빌드/임포트/codex(P1·P2 0)/prod dry_run·검증400으로 확인.

### 📌 교훈
외부 API enum은 "오타로 보여도 고치지 말고" 실측 그대로 쓴다(원칙: 추측 금지). 비슷한 이름의 필드(cancelReason vs rejectReturnReason, deliveryMethod vs collectDeliveryMethod)라도 형(enum/자유텍스트)·허용값이 다를 수 있으니 **엔드포인트별로 스펙을 따로 확인**하고 상수를 재사용하지 마라. 스크린샷이 길어 잘리면(택배사 100+ 테이블 아래 송장/수량 필드) "이게 끝인가" 단정 말고 잘린 뒷부분을 반드시 확인한다.

## 5. 네이버 교환 쓰기 API — 반품과 닮았지만 필드명·제약이 미묘하게 다름

### 🐛 이슈
N7 wave3 교환 5종 구현 시 반품(wave2)과 구조가 거의 같아 그대로 복사하고 싶은 유혹이 있었으나, 실측 스펙이 미묘하게 달랐다.
- 경로가 `claim/exchange/*` (반품은 `claim/return/*`). 수거완료는 `claim/exchange/collect/approve`로 한 단계 더 깊다.
- 교환 보류 상세사유 필드명 = `holdbackExchangeDetailReason`, 추가비용 = `extraExchangeFeeAmount` (반품은 holdbackReturnDetailReason / extraReturnFeeAmount). holdbackClassType enum만 동일.
- 교환 재배송(dispatch)은 reDeliveryMethod/Company/TrackingNumber 3필드가 **전부 optional**(BODY는 required이나 개별 필드는 REQUIRED 배지 없음). N6 발송(dispatch)은 DELIVERY에 택배사+송장 필수였던 것과 대조.

### ✅ 해결
필드명은 실측대로 분리(Exchange 접미사). codex가 "재배송에 DELIVERY 시 택배사+송장 XOR 강제는 스펙에 없는 앱 제약"이라 지적 → 대화형 검증 후 합의 수용(원칙19): N6 dispatch와 다른 엔드포인트이고 실측상 전 필드 선택이므로 XOR 제거, 부분입력은 그대로 보내고 네이버가 검증. enum 상수(holdbackClassType, deliveryMethod)는 반품 것 재사용.

### 📌 교훈
"비슷한 API"일수록 **복붙 후 필드명·경로·필수여부를 1:1로 재대조**하라. 같은 개념(보류 상세사유)도 클레임 종류마다 필드명이 다르다(Return/Exchange). 한 엔드포인트(N6 발송)에서 필수였던 조건이 유사 엔드포인트(교환 재배송)에선 선택일 수 있으니 이전 구현의 제약을 그대로 이식하지 마라 — 스펙에 없는 제약은 유효 요청을 막는다. codex가 "스펙에 없는 앱 제약"을 지적하면 추측금지 원칙상 대체로 수용이 맞다.

## 6. 네이버 N8 상품 쓰기 — prod 실측으로 범위를 줄이는 게 안전 (옵션 미사용·가격 묶임)

### 🐛 이슈
N8 "재고·가격·수정·등록 전부" 요청을 그대로 다 구현하려다, API센터 스펙과 prod 데이터를 보고 두 가지를 발견.
- "상품 옵션 재고 변경"(option-stock) API는 이름과 달리 `salePrice`가 REQUIRED라 재고만 바꿔도 가격을 함께 보내야 함 → read-modify-write 안 하면 가격 손실 위험. 게다가 옵션별(optionCombinations/optionStandards) 페이로드 필요.
- prod 상품 1,202개 실측: 오하이는 원상품(origin_product_no)당 재고 1개, 변종은 group_product_no로 묶인 **별도 원상품**(= 옵션 미사용). 옵션별 재고 API가 애초에 불필요.
- 반면 "판매 상태 변경"(change-status)은 가격을 전혀 안 받고 원상품 단위 재고/상태(SALE/OUTOFSTOCK/SUSPENSION)를 처리 → 품절/재입고/판매중지를 가격 위험 0으로 커버.

### ✅ 해결
Jino와 대화로 범위를 2단계 축소(D-11): 수정·등록 제외(라이브 상품 손상 위험) → 옵션재고도 제외(미사용+가격위험) → **change-status만** 구현. codex 1차 P1×2/P2×3 전부 합의 수정(OUTOFSTOCK→재고0 강제, SALE→재고≥1, 클라 enum allowlist, 전이규칙 위반까지 막는 NAVER_STATUS_TRANSITIONS, 타입 union) → 2차 pass. prod dry_run 7케이스 라이브 통과.

### 📌 교훈
"전부 구현해줘" 요청이라도 **API 스펙 실측 + 실데이터 프로브로 진짜 필요/위험을 먼저 확인**하면 범위가 줄어든다(원칙22). 특히 외부 쓰기 API는 이름(옵션 재고 변경)과 실제 동작(가격까지 필수)이 다를 수 있으니 필수 필드를 꼭 본다. 같은 목적(재고 관리)을 더 안전한 엔드포인트(가격 안 받는 change-status)로 달성할 수 있으면 그쪽을 택한다. 전이 규칙이 있는 상태머신 API는 현재상태별 유효 전이만 UI에 노출해 무효 요청(네이버 400)을 사전 차단한다.

## 7. pause 직후 관측 함정 — 로컬 entity status는 다음 아침 sync까지 stale (2026-07-20)

### 🐛 이슈
D-NAO-65 B4 라이브 사전 검증에서 `shopping_lever_resume_candidates`가 0건 — 맥세이프 MO가 당일 08:50에 pause됐는데 후보에 안 잡혔다. "코드 결함인가"로 보일 수 있는 상황.

### ✅ 해결
원인 추적: 후보 판별의 첫 게이트가 `NaverEntity.status == 'off'`인데, entity sync 크론은 07:35라 **당일 08:50 pause는 다음날 아침까지 로컬 status에 반영 안 됨**(stale 'on'). 나머지 게이트(실효입찰 source='ad'·manual_bid·카나리·change_log 이력)는 전부 통과 확인 → 코드 정상, 시차 문제로 결론. 다음날 sync가 자동 치유.

### 📌 교훈
우리가 API로 실행한 상태 변경(pause/resume/입찰)은 **naver_change_log에는 즉시, NaverEntity에는 다음 sync(07:35)에** 반영된다. 당일 실행분을 entity 테이블 기준으로 관측하면 "안 됐다"로 오판한다 — 당일 검증은 change_log(+ API 응답)로, entity 기반 보드·후보는 "다음날 아침부터 잡히는 게 정상"으로 읽을 것. 라이브 사전 검증(read-only 시뮬)은 이런 시차를 미리 드러내줘서 다음날 관측의 오경보를 막는다.

## 8. 설계 시 기존 테이블 전수 확인 — "영속 안 됨" 단정이 신규 마이그레이션을 낳음 (2026-07-20)

### 🐛 이슈
IU-R(순위 서보) 설계에서 Opus 설계자가 "유닛별 시간당 실측 순위가 어디에도 영속되지 않는다"고 단정하고 신규 append-only 테이블+마이그레이션을 설계함. 실제로는 `NaverKeywordHourly`(D-NAO-46②)가 키워드+쇼핑그룹 grain hh24 avg_rank를 D-1 스윕으로 365일 영구 축적 중이었음. codex consult 교차검증(원칙19)이 잡아냄.

### ✅ 해결
§1 사실 오류 철회 → R3를 change_log×NaverKeywordHourly 조인 기반으로 재설계, 마이그레이션 0으로 출하 범위 축소.

### 📌 교훈
"X가 없다"를 근거로 신규 테이블/마이그레이션을 설계하기 전, models.py에서 관련 도메인 테이블을 전수 grep(예: `grep -n "class Naver" models.py`)하고 각 docstring을 읽어라(feedback_verify_existing_before_declaring_absent의 설계판). DB 스키마 변경은 "없음을 실측"한 뒤에만.

## 라우팅 우회 — "내가 하는 게 빠르다"로 Fable이 Sonnet 몫을 직접 수행 (2026-07-21)

### 🐛 이슈
Jino가 세션 시작에 라우팅을 명시(구조=Fable·설계/구현=Opus·단순=Sonnet, 코딩은 sonnet 기본)했는데,
백필 조사·러너 코딩(~100줄)·파서 수정을 Fable(메인)이 직접 수행. "위임 왕복보다 직접이 정확/빠름"을
명분으로 최고가 모델을 단순 작업에 소모 → Jino 질책("token 물어내").

### ✅ 해결
라우팅 즉시 복원: 검증·실행성 작업도 Sonnet 위임, 중요 쓰기 경로만 Opus, Fable은 구조 판단·검토만.

### 📌 교훈
효율 판단으로 사용자의 명시 라우팅을 우회하지 말 것. "몇 줄이라 직접이 낫다" 싶어도 지시가 있으면
위임이 기본값. 위임 프롬프트에 확정 사실(프로브 결과 등)을 그대로 옮기면 정확도 손실도 없다.

## HANDOFF의 "예약됨" 주장이 실제 스케줄에 없었음 — 예약·자동화 주장도 실측 대상 (2026-07-22)

### 🐛 이슈
전 세션 HANDOFF에 "내일 아침 관문 4개 자동 검증 예약됨(07:45)"이라 기록돼 있었으나,
scheduled-tasks 목록 실측 결과 해당 예약이 존재하지 않았음. 그대로 믿었으면 관문 검증이
통째로 누락될 뻔(관문 실패 시 발견 지연).

### ✅ 해결
list_scheduled_tasks로 전수 확인 → 부재 확정 → 일회성 예약 2건 직접 생성
(bm-layer-4gates-verify-0723 07:45 / codex-retro-review-0723 09:30).

### 📌 교훈
원칙22("됐다"는 라이브 증거로만)는 코드·배포만이 아니라 **예약·크론·자동화 장치 주장에도 적용**된다.
HANDOFF에 "예약됨/걸어둠"이 있으면 세션 시작 시 스케줄 목록을 실측해 존재를 확인하고, 없으면
즉시 재생성할 것. 예약은 세션 컨텍스트 밖 장치라 기록자 자신도 실패를 모른 채 인계할 수 있다.

## 회귀 테스트가 프로덕션에 없는 문자열을 검증 — "허구 픽스처" 함정 (2026-07-22)

### 🐛 이슈
VT 충돌 방지 게이트가 rationale LIKE '%[터미널정지]%' 매칭으로 A축 정지 이력을 걸렀는데,
그 토큰은 프로덕션 어디서도 안 쓰는 죽은 문자열(유일 사용처=게이트 자신+테스트 픽스처).
테스트는 그 허구 토큰을 시드해서 통과 — 게이트가 실전 사유문([스톱로스정지 — 캠페인 정책] 등)을
못 잡는 걸 전혀 고정하지 못함. GATE 적대 리뷰가 발견(P1).

### ✅ 해결
문자열 매칭 자체를 폐기 → 구조화 필드 판정(set_user_lock change_log의 after_value.userLock 권위)
+ 실코드에서 grep한 실전 사유문·sync-lag 창을 재현하는 픽스처로 테스트 교체.

### 📌 교훈
①문자열 계약(rationale·마커)으로 안전 게이트를 만들면 포맷 드리프트에 조용히 뚫린다 — 구조화
필드(after_value JSON 등)로 판정하라. ②픽스처 문자열은 반드시 프로덕션 코드에서 grep으로 실증
후 시드하라("이 문자열을 실제로 쓰는 곳" 앵커 주석). 설계에 테스트를 맞추면 통과가 거짓 안심이 된다.

## 12. 레버 오독 — 그룹 bid_amt만 보고 "방치" 단정 (2026-07-22)

### 🐛 이슈
03 아이폰17 그룹들이 "노출 34%인데 그룹입찰 50원 방치"라고 진단·D-NAO-82①에 기록. 실제로는 그룹입찰 50원은 비활성 레버(소재 96%가 useGroupBidAmt=false)였고, 실제 작동 레버인 소재입찰은 이미 2,290원까지 관리·경제성 상한 도달 상태였음.

### ✅ 해결
라이브 실측(effective_bid·change_log 소재 스텝 이력)으로 정정, 트랙 파일 D-NAO-82①에 정정 기록.

### 📌 교훈
쇼핑 그룹의 입찰 상태 판단 전 반드시 effective_bid(소재 레버)와 change_log의 ad-grain 스텝 이력을 함께 본다. naver_entity.bid_amt 단독으로 "방치" 단정 금지.

## 13. 데이터 부재 ≠ 0 — 확정 테이블에 오늘 행이 없는 것을 "노출 0"으로 오독 (2026-07-22)

### 🐛 이슈
naver_ad_daily(as_of=D-1)에 07-22 행이 없는 것을 보고 "오늘 이 그룹 노출 0"이라고 Jino에게 보고. 실제 /stats 장중 조회 결과 노출 217회 진행 중이었음.

### ✅ 해결
/stats(datePreset=today) 라이브 조회로 정정. 판정 전 데이터 소스의 as_of 경계를 명시하는 습관.

### 📌 교훈
D-1 확정 테이블로 "오늘"을 말하지 않는다. 오늘 상태는 /stats 장중 조회(또는 hourly_snapshot)가 정본. 부재를 0으로 읽는 것은 원칙22 위반.

## 14. 순위 중심 보고 — 입찰가 나열은 보고가 아니다 (2026-07-22, Jino 질책)

### 🐛 이슈
17프로 소재 입찰 스텝(1,760→2,290)을 입찰가 축으로만 보고. Jino: "순위로 판단하라고 예전에 말하지 않았어? 계속 7위에 있으면서 cpc만 조금씩 올리고 할일 다했다고 하는 경우가 발생하잖아."

### ✅ 해결
"입찰 X원이 순위 몇 위를 샀는가"를 기본 보고 축으로 전환. 실측: 2,290원=5.1위(유령 지면)=클릭 0. → ref 38 가시성 분석·D-NAO-83로 승격.

### 📌 교훈
광고 조작의 성과 축은 입찰가가 아니라 순위(그리고 그 순위의 가시성). 순위 이동 증거 없는 연속 스텝은 실패로 보고한다. 가시 임계=4위(자체 90일 실측), 5위 밖=유령 지면.

## 15. fail-open 하니스는 SA 간 "빈 상태" 가드가 대칭이어야 한다 (2026-07-23, codex 소급 리뷰 BM P1-2)

### 🐛 이슈
BM SA-2(detect_agency_ops)의 bootstrap 가드는 **D-1 스냅샷 부재(prev)만** 검사. SA-1이 실패하면 fail-open 하니스가 SA-2를 독립 실행 → 오늘 스냅샷(curr)이 비어 prev 전건을 removed로 오검 → 기존 op 삭제 + 대량 오탐 브리핑·Slack.

### ✅ 해결
`if not curr:` 대칭 가드 추가(빈 curr = SA-1 실패 추정 → 스킵). fail-open 하니스의 각 SA는 상류 SA 실패로 자신의 입력이 빈 경우를 스스로 방어해야 한다. 부수: fail-open except에 `db.rollback()` 누락 시 다음 SA query가 PendingRollbackError로 연쇄 실패 → fail-open 격리 자체가 무력화(P2-1).

### 📌 교훈
독립 try로 감싼 fail-open SA들은 (1) 상류 실패로 인한 **빈 입력**을 각자 가드하고, (2) except에서 `db.rollback()`으로 세션을 정리해야 격리가 실제로 성립한다. "관찰 전용이라 무해"는 오탐 브리핑·연쇄 실패를 못 막는다(원칙22). codex 소급 리뷰는 이미 가드된 코드(IU-R rank-step TOCTOU=harness:713-737에 base_bid==live 검사 존재)를 재지적할 수 있으니, 수용 전 현재 파일에서 가드 실재를 반드시 확인한다.

## 16. prod DB 시간 규약이 테이블마다 다르다 — 감사 쿼리 오독

### 🐛 이슈
2026-07-24 04 자동운영 감사에서 "오늘 집행 내역" 쿼리에 `date(changed_at,'+9 hours')=date('now','+9 hours')`를 쓰자 **어제 23:20 KST 건이 오늘로 섞여 들어왔다.** 반대로 diary를 `date(created_at)='2026-07-24'`로 조회하니 **오늘 항목이 0건**으로 나와 "레인이 조용히 죽었다"고 오판할 뻔했다.

### ✅ 해결
prod ohisell.db 실측 규약(2026-07-24):
- `naver_change_log.changed_at` = **KST**(코드가 명시 대입) → 오늘 = `date(changed_at)=date('now','+9 hours')`
- `ops_diary_entries.created_at`, `naver_proposals.created_at` = **UTC**(`server_default=CURRENT_TIMESTAMP`) → 오늘 = `created_at >= '<어제> 15:00'`, 표시는 `+9 hours`
같은 사건(08:50 일 레인)이 change_log엔 `2026-07-24 08:50`, diary엔 `2026-07-23 23:50`으로 남는다.

### 📌 교훈
감사 쿼리를 짜기 전에 **테이블별 시간 규약을 먼저 확인**한다(같은 사건의 두 행을 대조하면 즉시 판별). [[sqlite-server-default-now-is-utc]]의 확장판 — "이 DB는 UTC"가 아니라 **컬럼마다 다르다**. "오늘 데이터 0건 = 크론 사망"이라고 단정하기 전에 규약부터 의심할 것(원칙22: stale/오독 단정 금지).

## 17. 크론 "ok" ≠ 레인 작동 — 게이트가 조용히 전건 차단할 수 있다

### 🐛 이슈
같은 감사에서 `scheduler_state`는 전 레인 `ok`였는데, 실제로는 ①시간당 explore 레인이 00:20~08:20 9회 전부 실행 0(소급채점 stale fail-closed 31건), ②EX 확장은 압력 판정·제안 생성까지 정상인데 일 레인 게이트에서 전건 보류로 **무인 발사 0**이었다.

### ✅ 해결
레인 건강은 `last_status` 대신 **실집행 수 + blocked 사유 분포**(`ops_diary_entries`의 event_type/rationale 집계)로 판정. 사유별 카운트를 보면 "정상 절제"와 "구조적 전건 차단"이 구분된다.

### 📌 교훈
크론 상태는 "예외 없이 끝났다"만 증명한다. **게이트가 100% 차단해도 ok로 찍힌다.** 자동화 감사는 반드시 blocked 사유 분포까지 봐야 하고, 동일 사유가 전건을 덮으면 정책 문제로 격상해 사람에게 표면화한다(원칙23 인라인 의무).
