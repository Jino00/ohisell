# TRACKS — 활성/완료 메가 프로젝트 인덱스

## 🟢 Active
- [쿠팡 RG 재고·발송 관제 (Replenishment)](tracks/active/track_coupang-rg-replenishment.md) — RG 현재고+일판매속도+입고 리드타임으로 "언제·몇 개 발송" 역산. 목표 FC재고 2~3일치. 1/7 (S1 입고동기화 완료+prod 라이브 검증 성공: 6건/47옵션, 리드타임 1.15~4.5일). D-14 수정(입고 Wing 내부 API 연결).

## ⏸ Paused
- (없음)

## ✅ Completed
- [쿠팡 API 전 기능 연결 + 종합 조망](tracks/active/track_coupang-full-integration.md) — 읽기 P1~P7 + 쓰기 W1~W5 전부 prod 배포·라이브 실증 완료(2026-06-04). 잔여: W4·W5 codex 교차검증(별도). ※파일은 active/ 위치 유지(이력).
- [네이버 스마트스토어 커머스 API 전 기능 연결](tracks/completed/track_naver-full-integration.md) — N1~N8 완료(N2 skip). 읽기+쓰기(발주/발송·클레임·판매상태변경) 전부 prod 배포·dry_run 라이브·codex pass (2026-06-05).
