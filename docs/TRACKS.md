# TRACKS — 활성/완료 메가 프로젝트 인덱스

## 🟢 Active
- [쿠팡 RG 수수료 회계 자동화](tracks/active/track_coupang-rg-fee-accounting.md) — RG 판매 실청구 수수료(입출고·배송·보관·판매수수료 등)를 윙 내부 API로 옵션 단위 자동수집→종합조망 순이익 반영. **6/7** (Phase1 S1~S4 대조뷰, S5 회계규칙 잠금, **S6-core 옵션단위 수집 완료**: vendor_item_id grain·엑셀 파서(헤더명 동적매핑)·수동 업로드·이중계상 가드, codex 4R pass, prod 라이브 vs_status_api 완전일치·net_profit 불변). 다음 S6-auto(자동 다운로드, download-list body 캡처 대기)/S7(net_profit 플립). 현재 작업 중인 트랙.
- [쿠팡 RG 재고·발송 관제 (Replenishment)](tracks/active/track_coupang-rg-replenishment.md) — RG 현재고+일판매속도+입고 리드타임으로 "언제·몇 개 발송" 역산. 목표 FC재고 2~3일치. 6/7 (S1 입고동기화·S2 리드타임추정·S3 일판매속도추정[평일/주말/휴일 신뢰도게이트]·S4 발송역산[요일인지 forward투영·4-status]·S5 rg_replenishment Harness[배치역산·등가성 784/784 라이브 PASS·GET /replenishment-plan]·S6 UI 컬럼[로켓그로스 탭 발송관제 섹션, 커밋 ddcd666] 완료+codex pass+prod 라이브검증·배포 성공). 다음 S7 요일/휴일 세분화 지속 개선(데이터 누적 대기). D-14 수정(입고 Wing 내부 API 연결).

## ⏸ Paused
- (없음)

## ✅ Completed
- [쿠팡 API 전 기능 연결 + 종합 조망](tracks/active/track_coupang-full-integration.md) — 읽기 P1~P7 + 쓰기 W1~W5 전부 prod 배포·라이브 실증 완료(2026-06-04). 잔여: W4·W5 codex 교차검증(별도). ※파일은 active/ 위치 유지(이력).
- [네이버 스마트스토어 커머스 API 전 기능 연결](tracks/completed/track_naver-full-integration.md) — N1~N8 완료(N2 skip). 읽기+쓰기(발주/발송·클레임·판매상태변경) 전부 prod 배포·dry_run 라이브·codex pass (2026-06-05).
