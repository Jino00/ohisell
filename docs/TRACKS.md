# TRACKS — 활성/완료 메가 프로젝트 인덱스

## 🟢 Active
- [네이버 스마트스토어 커머스 API 전 기능 연결](tracks/active/track_naver-full-integration.md) — **N1~N8 완료**(N2 skip). 읽기(정산·이익정밀화·문의·상품·판매자) + 쓰기 N6(발주/발송)·N7(클레임 12)·N8(상품 판매상태). 전부 prod 배포·dry_run 라이브·codex 통과. N8은 판매상태 변경만(옵션재고/가격/수정/등록 제외, D-11). 실쓰기(클레임/판매상태 dry_run=false)는 실데이터라 Jino 건별 결정.

## ⏸ Paused
- (없음)

## ✅ Completed
- [쿠팡 API 전 기능 연결 + 종합 조망](tracks/active/track_coupang-full-integration.md) — 읽기 P1~P7 + 쓰기 W1~W5 전부 prod 배포·라이브 실증 완료(2026-06-04). 잔여: W4·W5 codex 교차검증(별도). ※파일은 active/ 위치 유지(이력).
