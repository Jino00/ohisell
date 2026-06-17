# TRACKS — 활성/완료 메가 프로젝트 인덱스

## 🟢 Active
- [쿠팡 로켓배송(1P) 종합조망 편입](tracks/active/track_coupang-rocket-1p.md) — 오하이테크 1P(supplier.coupang.com) 발주·납품·정산 수집→종합조망. 매출=발주, 순이익=발주−원가−광고(D-1~8). **★2026-06-17 S1 정찰 완료(1/N)**: 라이브 실측(ref20+D-9) — ①발주+②납품=`/po-web/app/purchase-order/list` JSON 1개(sumOfOrder/ReceivingAmount), ③정산=`/scm/settlement/general/purchase/account` SSR HTML(계산서 grain, 공급가+VAT=지급예정). Akamai방어→헤드풀 CDP 페처 필수. 다음=S2(데이터모델+수집SA, 착수 전 라이브확인 6건). 2026-06-15 생성.
- [쿠팡 RG 수수료 회계 자동화](tracks/active/track_coupang-rg-fee-accounting.md) — RG 판매 실청구 수수료(입출고·배송·보관·판매수수료 등)를 윙 내부 API로 옵션 단위 자동수집→종합조망 순이익 반영. **8/8 코드 완료**(S1~S4 대조뷰, S5 회계규칙 잠금, S6-core/auto 옵션단위수집·자동다운로드+scheduler 06:15, S7 net_profit 전액차감 플립(D-16), **S8 과오청구 감사**(사이즈 분류+이상치 스크리닝, GET /rg/fee-audit, prod 22옵션 15플래그)). 전 Sprint prod self-verify 완료. **운영 단계**. ★2026-06-15 라이브 감사: size_mismatch_high **1건**(아이패드미니필름 91313543029, 등록 극소형 60.5cm vs 배송청구 대형1 3배, 실측값 미확보) — Jino 결정 **자동해제 대기**(다음 입고 PRODUCT_SIZE_COMPARISON 수집 시 자동 판가름, 코드변경 없음). 후속(선택): 감사 프론트 UI(미정).
- [쿠팡 RG 재고·발송 관제 (Replenishment)](tracks/active/track_coupang-rg-replenishment.md) — RG 현재고+판매속도+리드타임으로 "언제·몇 개 발송" 역산. **Phase 1(S1~S6) 완료·prod 라이브**(GET /replenishment-plan, 로켓그로스 탭 UI). **★2026-06-16 Phase 2 착수**: 3축 조사(ref 19) 진단=예측 단순평균이라 855옵션 98.6% insufficient_data + in-transit 부재. **D-10 예측 SBA/TSB(statsforecast Apache-2.0✓)·D-11 in-transit Wing API·D-12 newsvendor 분위수(서비스수준 99% 시작)·D-13 유효재고=현재고+발송중(판매개시갭)**. **★2026-06-17 구조 승인 + 계획서 작성(`docs/PLAN_rg-replenishment-phase2.md`, S8~S13)**. 신규 SA 3개(demand_classifier·sba_forecaster·in_transit_estimator). 다음=(선택)plan-eng-review→Sonnet S8(P0 분류).

## ⏸ Paused
- (없음)

## ✅ Completed
- [Wing 세션 자동화](tracks/completed/track_wing-session-automation.md) — **완료 2026-06-15 (6/6)**. wing.coupang.com 내부 API를 세션 만료 없이 자동 호출하는 공용 헤드풀 페처(CDP 모드, Akamai 우회) → (A)매출 자동대조(vendor-summary RevenueDriftCard, ref18 3P+1.83%·RG+7.40% 재현·net_profit 불변) + (B)RG정산 자동수집(rg CLI+데몬 com.ohisell.wing 예약+갱신 버튼). S0~S5 전부 prod 라이브 self-verify. 9종 sellerReportType 코드명 확보(파서=WAREHOUSING_SHIPPING 1종). 후속(선택): 나머지 8종 XLSX 파서.
- [쿠팡 매출·광고 정합성](tracks/completed/track_coupang-revenue-ad-reconciliation.md) — **완료 2026-06-14**. 종합조망 매출·광고·순이익을 쿠팡과 일치 + 계정별 분리. S1~S7(계정분리·2중계상·RG매출편입[52%갭]·net_profit D-3·신선도 reconcile/이중차감 D-12·검산 대시보드·비-PA ALL전환 D-15/커버리지 D-13) + 옵션 30일 확대 + **vendor-summary 프로브로 매출 정합 라이브 1:1 입증**(6/8~6/13 3P+1.8%·RG+7.4%, 전부 문서화 잔차). 자동대조(드리프트 감시)는 → Wing 세션 자동화 트랙.
- [쿠팡 API 전 기능 연결 + 종합 조망](tracks/active/track_coupang-full-integration.md) — 읽기 P1~P7 + 쓰기 W1~W5 전부 prod 배포·라이브 실증 완료(2026-06-04). 잔여: W4·W5 codex 교차검증(별도). ※파일은 active/ 위치 유지(이력).
- [네이버 스마트스토어 커머스 API 전 기능 연결](tracks/completed/track_naver-full-integration.md) — N1~N8 완료(N2 skip). 읽기+쓰기(발주/발송·클레임·판매상태변경) 전부 prod 배포·dry_run 라이브·codex pass (2026-06-05).
