# 세션 인수인계: wing 3P GMV=0 조사 완결 + PR 정리
> 저장일시: 2026-07-27 13:59 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(워크트리): `Ohiselling/.claude/worktrees/fervent-turing-307a7b` (브랜치 claude/fervent-turing-307a7b — **병합 완료, 원격 브랜치 삭제됨**. main==prod 기준)
- prod: `ubuntu@sellc.ohitech.co.kr`, 실DB `/home/ubuntu/ohisell/backend/ohisell.db` (ssh BatchMode 접속 가능)
- wing 페처: `tools/wing_browser_fetcher.py`, 로그 `~/.ohisell_wing_fetcher.log`, 설정 `~/.ohisell_wing_fetcher.json` (account_key=COUPANG_WING1, vendor A01564720=오픽스)
- 배포: `scripts/safe_deploy.sh`만 (직접 scp 금지, D-NAO-49) — 이번 세션은 문서만이라 배포 없음

## 2. 이번 세션 완료 목록
- ✅ **wing 3P GMV=0 "모순" 조사 완결 (코드 변경 0)** — 파싱 버그 아님, 계정 커버리지 차이 확정:
  - 로그 실측: 07-27 13:04 run, 3P=0·RG=2,448,880 (07-20~26), push days=14 = 응답에 NORMAL 행 실재(gmv=0) → 파싱 정상
  - prod DB 실측: `coupang_vendor_summary_daily` NORMAL=0 저장 확인 / **오픽스(채널1) 3P 주문 07-20~26 = 0건** → Wing 3P=0은 정확
  - 종합조망 3P 204,400원 = **쿠팡_오하이테크(채널2, A01029796) 3P 주문 11건** — Wing1(오픽스) 수집 범위 밖
  - 6월 양성 대조: 오픽스 주문 1,917,050 vs Wing 3P GMV 1,886,050 = 98.4% (오하이테크 111,200 원래 미포함) → 오픽스 단독 커버리지 확정
  - Jino 확인: 독립 소스 3개(Open API 주문·revenue-history·6월 대조) 전부 일치. **오픽스 3P는 06-26 기점 실붕괴**(6월 일 19~43만원 → 7월 월 3건/45,100원) = 3P 필름 라인 RG 이관(의도됨, 메모리 `ofix-3p-moved-to-rg`)
  - 정본화 가드 확인: `revenue_canonical.py` 집계뷰(계정 미지정)는 wing_used=False 주문기반 폴백 — 오하이테크 3P가 0으로 눌리는 구멍 없음
- ✅ `.claude/memory/LESSONS_LEARNED.md` **LESSON #33** 추가 (커밋 `f9bff25`)
- ✅ 글로벌 메모리 `coupang-account-ad-structure.md`에 07-27 실측 갱신 추가("오하이테크=1P만"은 stale — 3P 주문 실재)
- ✅ **PR #104 생성·병합** (main `6be7ef4`) — LESSON #33 문서. 원격 브랜치 삭제 완료
- ✅ **PR #88 close (병합 안 함)** — 목적(BM 하니스 테스트 날짜 flaky)이 이미 **PR #91**(`10eb501`, 07-23)로 main에 해결됨(kst_today monkeypatch). 잔여분(run_bm_layer `today` 주입 파라미터)은 prod 미사용 잉여 → superseded 사유 코멘트 남기고 close + 브랜치 삭제. 재오픈 가능
- ✅ 오픈 PR 0건 상태로 정리 완료

## 3. 확정된 결정사항
- **wing 페처 3P GMV=0은 결함 아님 — 수정 금지.** "수집 0 vs 화면 >0" 모순 재제기 시 파싱 의심 전에 **계정/채널 커버리지부터 대조**할 것 (LESSON #33)
- PR #88은 병합이 아니라 close가 정답이었음 (Jino가 "정적 검증만으로 병합" 지시했으나 검증 중 supersede 발견 → close로 전환 보고, 이의 없음)
- 오하이테크(WING2) 판매분석 수집 편입 여부 = **미결, Jino 결정 사항** (제안만 표면화해둠)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `tools/wing_browser_fetcher.py` | wing 판매분석 페처 — `_summarize`(파싱, L492), `_push`(L566, 응답에 있는 유형만 전송) |
| `backend/app/services/coupang/vendor_summary_sync.py` | vendor-summary ingest (NORMAL=3P/RFM=RG) |
| `backend/app/services/coupang/revenue_canonical.py` | 닫힌일 매출 Wing 정본화 — 계정 미지정 시 폴백(L177) |
| `backend/app/services/coupang/intelligence.py` | `_agg_orders`(L101) — 종합조망 3P 매출 출처(orders×coupang 채널) |
| `.claude/memory/LESSONS_LEARNED.md` | LESSON #33 (이번 조사 결론) |
| `docs/references/16_coupang_ad_report_billboard_api.md` | 계정 매핑 근거(A01564720=오픽스) |

## 5. 알려진 이슈 / 주의사항
- **커버리지 갭(미결)**: 오하이테크 3P 매출이 성장 중(7월 27건/481,710원)인데 Wing 판매분석 수집·GMV 정본화 범위 밖. WING2 편입은 Jino 결정 대기
- 글로벌 메모리 인덱스의 "오하이테크=1P 로켓배송만"(06-21)은 stale — 토픽 파일에 갱신 주석 있음
- 이 워크트리 브랜치는 병합·원격삭제 완료 — 새 작업은 새 워크트리에서
- 로그 `~/.ohisell_wing_fetcher.log`는 여러 날짜가 섞여 있음 — grep 시 반드시 날짜 프리픽스로 필터(이번 세션에서 07-22 구간을 오독할 뻔함)
- 백로그(이전 세션 칩, 여전히 미결): WING2 RG정산 50일 누락 · sellc Mac 데몬 403 별건

## 6. 다음에 할 작업 (미완료)
- [ ] (Jino 결정 시) 오하이테크(WING2) 판매분석 수집 편입 설계 — 페처 2계정 순회 or 별도 config
- [ ] 백로그: WING2 RG정산 50일 누락 처리
- [ ] 활성 트랙(naver-ad-optimization)은 이 세션에서 건드리지 않음 — 트랙 작업은 해당 트랙 문서(§0)부터

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

Ohiselling/.claude/worktrees/fervent-turing-307a7b/.claude/memory/HANDOFF_wing-3p-gmv-zero-resolved+pr-cleanup_20260727.md 읽고 이어서 작업해줘
