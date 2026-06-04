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
