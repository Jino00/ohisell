# PLAN.md — Sprint 4A 계획서 (리뷰 반영)

## Sprint 정보
- Sprint ID: sprint-4a
- 목표: 실제 API 동기화 + 쿠팡 Wing/로켓그로스 검증 + cafe24 토큰 자동갱신 + 설정 페이지
- 시작일: 2026-04-02
- 예상 완료: 2026-04-03 (1.5일)

## 배경 (Why)
Sprint 0~3에서 전체 시스템을 구축하고 API 키를 모두 입력했다.
Sprint 4A는 데이터 레이어를 검증하여 "실제 주문 데이터로 정확한 순이익을 보여줄 수 있는 상태"로 만든다.
배포(Oracle Cloud)와 네이버 연동은 Sprint 4B로 분리하여 데이터 정확성을 먼저 확보한다.

## 이번 Sprint에서 만들 것 (What)

### F-401: 쿠팡 주문 동기화 — Wing/로켓그로스 검증
- 실제 쿠팡 API 호출하여 주문 데이터 수집
- 현재 구조 검증: 4개 채널(WING1, WING2, RG1, RG2)이 각각 별도 vendor_id로 분리됨
- API 응답 10건 샘플링 → 배송방식 필드 확인 → 구분 방식 확정
- fetch_orders의 FINAL_DELIVERY 필터 검토 (진행중 주문도 필요한지 판단)
- profit_calculator.py: 광고비 채널간 bleeding 버그 수정 (proportional allocation → option_id 직접 매칭)

### F-403: cafe24 토큰 자동갱신 + 주문 동기화
- sync_service.py에서 on_token_refreshed 콜백 연결 (현재 None)
- 스케줄러에 cafe24 토큰 proactive refresh 작업 추가 (만료 30분 전)
- 동시 refresh 방지: threading.Lock으로 보호
- refresh 실패 시 에러 로깅 (silent fail 방지)
- cafe24 주문 API 실제 호출 검증

### F-405: 프론트엔드 설정 페이지 (API 연동 상태)
- 각 채널의 API 연결 상태 표시 (connected / expired / error)
- cafe24 OAuth 인증 버튼 (auth-url → 리다이렉트)
- 마지막 동기화 시각, 다음 동기화 시각 표시
- 수동 동기화 트리거 버튼

## 리뷰 반영 사항
- [CEO] "실제 30일 주문으로 정확한 순이익 확인" 비즈니스 수준 수락 기준 추가
- [CEO] 쿠팡 배송방식 필드 실제 API 응답에서 검증 후 코딩
- [CEO] 정산 대사: 첫 동기화 후 플랫폼 대시보드와 수동 비교
- [Eng] profit_calculator 광고비 bleeding 버그 수정
- [Eng] cafe24 on_token_refreshed 콜백 sync_service에 연결
- [Eng] 동시 refresh race condition 방지 (Lock)
- [Eng] OAuth state 파라미터를 CSRF 토큰으로 변경

## 하지 않을 것 (Out of Scope → Sprint 4B)
- F-402: 네이버 주문 동기화 (Oracle IP 등록 선행 필요)
- F-404: Oracle Cloud 배포 (sellc.ohitech.co.kr)
- 보안: API 인증/IP 화이트리스트 (배포 시점에)
- ad_data.db 서버 전송 (배포 시점에)

## Sprint Contract (Generator ↔ Evaluator 합의)
- [ ] 쿠팡 API 실제 호출 시 주문 데이터가 DB에 저장됨
- [ ] 4개 쿠팡 채널 주문이 올바른 채널(WING1/WING2/RG1/RG2)로 저장됨
- [ ] 광고비가 채널간 bleeding 없이 정확히 배분됨
- [ ] cafe24 토큰 갱신 시 DB에 자동 업데이트됨 (on_token_refreshed 연결)
- [ ] cafe24 주문 API 실제 호출 시 주문 데이터가 DB에 저장됨
- [ ] 프론트엔드 설정 페이지에서 각 채널 연동 상태 확인 가능
- [ ] 첫 동기화 결과를 플랫폼 대시보드와 수동 비교하여 정합성 확인

---

## Sprint 4B 후보 (Sprint 4A 완료 후)
- F-402: 네이버 주문 동기화 (Oracle IP 사전등록 선행)
- F-404: Oracle Cloud 배포 (nginx + certbot + systemd)
- 보안: nginx IP 화이트리스트 또는 Basic Auth
- ad_data.db 서버 동기화 방법
- Python 3.14 Oracle Linux 빌드
