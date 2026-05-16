# CONTEXT.md — 기술 결정 맥락 노트 (Sprint 4B-cafe24)
# 왜 이런 기술/구조를 선택했는지 기록합니다.

## D-1. 추정 금지 — 모든 수수료/enum은 공식 문서로 확인
- 네이버페이 요율: help.admin.pay.naver.com 공식 FAQ "주문형 가맹점 Npay 수수료"
  (카페24가 공식 호스팅사로 명시됨 → 결제수단별 요율 적용)
- KCP/카카오/토스 요율: cafe24 결제대행사 안내 이미지 (Jino 제공)
- cafe24 payment_method/order_status enum: developers.cafe24.com 공식 API 문서
- 이유: CLAUDE.md 추정 금지 원칙. 잘못된 요율은 순이익 전체를 왜곡

## D-2. 식별은 payment_method 단독이 아니라 조합
- 실측 결과 `card`가 KCP/카카오/토스/네이버페이 모두에 존재
- 판별 키: market_id(NCHECKOUT=네이버페이) > payment_gateway_name(kakao/toss) > 기본 KCP
- market_id/payment_method는 JSON 앞부분 → 10000자 잘림 무관 (백필 가능)

## D-3. 수수료를 동기화 시점에 Order.commission_amount로 저장
- 이유: profit_calculator가 잘린 raw_data를 재파싱하지 않도록. 계산기는 합산만.
- SA(Classifier/Resolver)는 순수 함수, sync_service(Harness)가 호출·저장

## D-4. 비-cafe24 채널 로직 불변
- 쿠팡/네이버는 기존 channel.commission_rate 유지 → 회귀 방지
- cafe24만 commission_amount 경로 사용 (channel.code 분기)

## D-5. 배송비는 cafe24 데이터가 아닌 고정값
- cafe24 shipping_fee는 고객청구분(무료배송이라 대부분 0)
- 실제 우리 비용 = 한진택배 1,900원/주문 → ShippingResolver가 주문당 부과
- 라인 여러 개여도 주문 1건 = 1,900원 (Jino 확인: 건당 고정)

## D-6. raw_data 10000자 잘림은 이번 범위 밖
- 복합결제 금액 분해 불가 → 전체 라인매출 기준 근사 (~15건, 영향 미미)
- sync 잘림 제거 + 결제필드 컬럼 추출은 후속 작업으로 분리 (스코프 절제)

## 기술 스택 (변경 없음)
- SQLAlchemy 2.0.48 + Alembic, Python 3.14(로컬)/3.10(서버)
- models.py `from __future__ import annotations` 필수
- 전체 KST 전제
