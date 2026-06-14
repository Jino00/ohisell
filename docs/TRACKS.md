# TRACKS — 활성/완료 메가 프로젝트 인덱스

## 🟢 Active
- [Wing 세션 자동화](tracks/active/track_wing-session-automation.md) — **4/6**. wing.coupang.com 내부 API를 세션 만료 없이 자동 호출하는 공용 헤드풀 페처 → (A)매출 자동대조(vendor-summary, ref 18) + (B)RG정산 자동수집(S6-auto). S0~S3 완료(페처·백엔드 ingest+reconcile Harness·데몬 com.ohisell.wing·종합조망 RevenueDriftCard UI), 전부 prod 라이브 검증(ref18 3P+1.83%·RG+7.40% 재현, net_profit 불변). **다음=S4 RG정산 자동수집 흡수.**
- [쿠팡 RG 수수료 회계 자동화](tracks/active/track_coupang-rg-fee-accounting.md) — RG 판매 실청구 수수료(입출고·배송·보관·판매수수료 등)를 윙 내부 API로 옵션 단위 자동수집→종합조망 순이익 반영. **8/8 코드 완료**(S1~S4 대조뷰, S5 회계규칙 잠금, S6-core/auto 옵션단위수집·자동다운로드+scheduler 06:15, S7 net_profit 전액차감 플립(D-16), **S8 과오청구 감사**(사이즈 분류+이상치 스크리닝, GET /rg/fee-audit, prod 22옵션 15플래그)). 전 Sprint prod self-verify 완료. **운영 단계**. 후속(선택): size_mismatch_high 4건 Jino 검토·감사 프론트 UI(미정).
- [쿠팡 RG 재고·발송 관제 (Replenishment)](tracks/active/track_coupang-rg-replenishment.md) — RG 현재고+일판매속도+입고 리드타임으로 "언제·몇 개 발송" 역산. 목표 FC재고 2~3일치. 6/7 (S1 입고동기화·S2 리드타임추정·S3 일판매속도추정[평일/주말/휴일 신뢰도게이트]·S4 발송역산[요일인지 forward투영·4-status]·S5 rg_replenishment Harness[배치역산·등가성 784/784 라이브 PASS·GET /replenishment-plan]·S6 UI 컬럼[로켓그로스 탭 발송관제 섹션, 커밋 ddcd666] 완료+codex pass+prod 라이브검증·배포 성공). 다음 S7 요일/휴일 세분화 지속 개선(데이터 누적 대기). D-14 수정(입고 Wing 내부 API 연결).

## ⏸ Paused
- (없음)

## ✅ Completed
- [쿠팡 매출·광고 정합성](tracks/completed/track_coupang-revenue-ad-reconciliation.md) — **완료 2026-06-14**. 종합조망 매출·광고·순이익을 쿠팡과 일치 + 계정별 분리. S1~S7(계정분리·2중계상·RG매출편입[52%갭]·net_profit D-3·신선도 reconcile/이중차감 D-12·검산 대시보드·비-PA ALL전환 D-15/커버리지 D-13) + 옵션 30일 확대 + **vendor-summary 프로브로 매출 정합 라이브 1:1 입증**(6/8~6/13 3P+1.8%·RG+7.4%, 전부 문서화 잔차). 자동대조(드리프트 감시)는 → Wing 세션 자동화 트랙.
- [쿠팡 API 전 기능 연결 + 종합 조망](tracks/active/track_coupang-full-integration.md) — 읽기 P1~P7 + 쓰기 W1~W5 전부 prod 배포·라이브 실증 완료(2026-06-04). 잔여: W4·W5 codex 교차검증(별도). ※파일은 active/ 위치 유지(이력).
- [네이버 스마트스토어 커머스 API 전 기능 연결](tracks/completed/track_naver-full-integration.md) — N1~N8 완료(N2 skip). 읽기+쓰기(발주/발송·클레임·판매상태변경) 전부 prod 배포·dry_run 라이브·codex pass (2026-06-05).
