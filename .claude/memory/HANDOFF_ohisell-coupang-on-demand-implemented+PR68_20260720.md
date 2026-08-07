
---

## 추가 (2026-07-22 15:20) — PR #68 병합됨 + 창 임시 차단(데몬 정지)

### 진행
- **PR #68 main 병합 완료**(`6fd3652`). 단, prod 미배포 — prod `scheduler_service.py`에 병렬 세션의 BM 레이어(D-NAO-78/79, PR #79·#80, main 미병합)가 직접 배포돼 있어 **safe_deploy CAS가 차단**(내 코드로 덮으면 BM 크론 죽음). 그 세션이 main 병합해 prod==main 될 때까지 내 배포 보류.
- 2차 리베이스(main 80커밋 전진)로 브랜치 최신화, 마이그 down_revision `f2a3b4c5d6e7`로 재부모화. 백엔드 2772·프론트 77 재검증 통과.

### ★창 임시 차단(운영 조치, prod 코드 무변경, 되돌리기 가능)
Jino "계속 켜졌다 사라져" → 라이브 데몬 로그로 진단: **범인은 크론이 아니라 Mac fetcher 데몬 자동실행 루프**(rocket가 30초마다 23h-auto→세션만료→로그인페이지 튕김).
- **ofix 광고비 크론 disable**: prod 토글 API `PUT /api/scheduler/toggle/request_ad_cost_refresh` → is_enabled=False.
- **3개 데몬 정지**: `launchctl bootout gui/$(id -u)/com.ohisell.{rocket,ohitech-ad,wing}` (14:59 KST). 검증: 4개 데몬 로그 15초 무활동.
- adcost 데몬은 남김(크론 off·flag False라 조용). chrome supervisor 3개(wing/ohitech/rocket-chrome) 남김(백그라운드, 안 뜸).

### ⚠️ 현재 상태 = 쿠팡 브라우저 수집 일시정지
- 발주(supplier)·로켓광고(ohitech)·판매분석(wing) 수집 멈춤. **API 수집(매출/정산 등)은 정상 가동.**
- **수집 재개(임시)**: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ohisell.{rocket,ohitech-ad,wing}.plist` (창 다시 뜸 주의 — 세션 재로그인 필요).
- **정식 마무리**: PR #79·#80 병합 → prod==main → `scripts/safe_deploy.sh`로 내 파일 배포 + `alembic upgrade head` → 데몬 코드가 버튼-only로 바뀌므로 재가동해도 창 안 뜸. 크론 토글도 그때 정리(코드에서 제거됨).
