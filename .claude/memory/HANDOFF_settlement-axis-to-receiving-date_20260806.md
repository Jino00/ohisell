# 세션 인수인계: 로켓1P 계산서 귀속 축 전환 — 작성일→실입고일 (2026-08-06 밤)
> 저장 2026-08-06 밤 KST · 트랙: **쿠팡 손익 정합** (전 인계: `HANDOFF_rocket-1p-parity-live+asn-receivable_20260805.md` §6 "미수금 연령(aging)"·같은 날 "저녁" 세션(claude-progress.txt)의 "계산서 귀속 축을 실입고일로 전환"을 이 세션이 실행함)
> 워크트리 `claude/po-receiving-date`(커밋 7개, push 완료) · **PR #228** · prod 배포·라이브 검증 완료

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (main 고정, 작업은 워크트리)
- 이 세션 워크트리: `.claude/worktrees/po-receiving-date` (브랜치 `claude/po-receiving-date`)
- prod: `ssh sellc.ohitech.co.kr`, 백엔드 포트 8001, DB `/home/ubuntu/ohisell/backend/ohisell.db`, 배포는 `scripts/safe_deploy.sh`만
- supplier 정찰/페처 Chrome: CDP 9225(로켓 supplier 전용, `~/.ohisell_rocket_fetcher.json`)

## 2. 이번 세션 완료 목록
- ✅ 로켓1P 발주 **실입고 시각** 수집 (D-CPP-19) — `receivingStartedAt/receivingFinishedAt`를 파서가 버리던 것 → 담기 시작. Mac 페처 수정 0·추가 API 호출 0. 원천 511건 ↔ DB 전건 일치
- ✅ 계산서 축 일별 매출 귀속을 **작성일 → 실입고일**로 전환 (D-CPP-20) — 새 테이블 `coupang_rocket_settlement_item` + 파서(`backend/app/clients/coupang/rocket_supplier.py`) + ingest(`backend/app/routers/coupang_ops.py`) + 백필 스크립트(`tools/rocket_settlement_item_backfill.py`)
- ✅ 백필 480/487 계산서 · 9,926 라인 · 2025-07-23~2026-08-05, 총액 보존 확인(931,151,153 → 931,151,153, 차이 0원)
- ✅ 마이그레이션 2건 배포(`f4a9c2e70b58` 실입고 시각, `b7d1e4f92a06` settlement_item 테이블), `safe_deploy.sh --migrate --restart` 2회, 무중단 다운타임 0초
- ✅ PR #228 본문을 D-CPP-19(방향 확정)에서 D-CPP-19·20(구현+백필 완료)로 갱신, claude-progress.txt 세션 항목 추가

## 3. 확정된 결정사항 (번복 금지)
- **계산서 축 일별 매출 귀속의 정본 = 실입고일**(작성일 아님) — 계산서 1건이 평균 5.8 PO를 묶고 작성일은 실입고일보다 −4~+7일 흔들려 계산서 헤더만으로는 날짜를 못 정한다. PO 입고금액 비율 배분도 근거 없음(금액 맞는 계산서 0건이었다)
- **날짜·금액 정본은 `/scm/receive/detail` 라인**(입고상세내역) — 배분·추정 없이 라인마다 입고일자·금액을 그대로 쓴다
- **라인 없는 계산서는 작성일 폴백**(0으로 접지 않음, 원칙22) — 역발행 차감분은 쿠팡이 SKU 단위로 안 쪼개 주므로 원천에 애초에 라인이 없다(코디네이터 판정, `docs/references/44_*.md` §8-3)
- **요약과 일별 추이가 같은 귀속 규칙을 쓴다** — 두 표면이 다른 축을 쓰면 다시 "다표면 일치가 교차검증이 아니다"(교훈 #141) 함정에 빠진다

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/alembic/versions/f4a9c2e70b58_add_po_receiving_timestamps.py` | 발주 실입고 시각 컬럼 마이그레이션 |
| `backend/alembic/versions/b7d1e4f92a06_add_rocket_settlement_item.py` | `coupang_rocket_settlement_item` 테이블 마이그레이션 |
| `backend/app/clients/coupang/rocket_supplier.py` | 계산서 라인 파서(헤더 토큰 매칭·단위당→합계 정규화) |
| `backend/app/models.py` | 발주 실입고 시각 필드 + `RocketSettlementItem` 모델 |
| `backend/app/routers/coupang_ops.py` | settlement-item ingest 엔드포인트(`expected_total` 대조·`truncated` 반환) |
| `backend/app/services/coupang/rocket_supplier_sync.py` | 발주 목록 페처 응답에서 실입고 시각 파싱 |
| `backend/app/services/coupang/rocket_1p_channel_pnl.py` | 계산서 축 일별 매출 귀속 로직(라인 있으면 실입고일, 없으면 작성일 폴백) |
| `tools/rocket_settlement_item_backfill.py` | 1회성 백필 스크립트(함정 3개 주석에 기록) |
| `docs/tracks/active/track_coupang-promo-pnl.md` | D-CPP-19·20 결정 기록 |

## 5. 알려진 이슈 / 주의사항
- **함정 3개**(백필 스크립트·파서 양쪽에 기록됨):
  1. 페이징이 `page`만으로는 20/48행에서 조용히 멈춘다 — 폼의 `totalCount`를 2페이지부터 같이 실어야 전량이 온다
  2. 원천의 「단가·공급가액·세액」은 **단위당**이고 「총 단가」만 합계다 — 그냥 SUM하면 5배 넘게 틀린다(437,583 vs 실제 2,263,522원 사례 확인)
  3. 라인 0건이면 요약 표 자체가 렌더되지 않아 표 개수가 줄고 인덱스가 밀린다 — 헤더 토큰("SKU 번호"+"총 단가") 매칭으로 고정
- **90일 창 밖 옛 PO는 실입고일이 비어 있다** — 전체 2,563건 중 442건만 채워짐(2026-05-09~08-06). 과거분은 다음 수집에서도 자동으로 안 채워짐(원천이 최근 창만 보여줌)
- **수집이 버튼 트리거뿐이라 화면이 낡을 수 있다** — 2026-08-03 승인된 설계라 이번 세션은 그대로 유지하기로 함(상시 배선은 스코프 밖)
- ⚠️ `tools/rocket_settlement_item_backfill.py`에 커밋되지 않은 로컬 diff가 남아 있을 수 있다(라인 0건 계산서를 "실패"가 아니라 "라인없음(차감계산서)"으로 분류하는 사후 정리) — 백필 자체는 이미 완료·검증됐으므로 기능에 영향 없음. 다음 세션에서 필요하면 검토 후 별도 커밋

## 6. 다음에 할 작업 (미완료)
- [ ] **원가 브리지 전환** — Jino 원가표 입력 대기(측정은 ref 47에 보존). 원가 커버리지 79.26%, `rocket_product_cost_map` confirmed 184건 중 183건이 이름 유사도 자동 확정(교훈 #117: 자동 매핑 금지 위반 상태). 결정적 브리지로 바꾸면 매출의 55.1%가 불일치 — Jino 원가표 도착 전엔 전환 보류
- [ ] **오픽스 RG 배선** — 라이브 −13,869,712원(계약 합격기준 유일 미충족). 갈림길 2개, Jino 답변 대기
- [ ] **미수금 연령(aging) 재판정** — 실입고일이 이제 생겼으니 계산 가능(전 세션 §6에서 막혀 있던 항목)
- [ ] 계산서 라인이 SKU 그레인이라 **원가 결합 경로가 열림** — 라인 단위 원가 매칭으로 정합도를 더 올릴 수 있는지 다음 세션에서 검토

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_settlement-axis-to-receiving-date_20260806.md 읽고 이어서 작업해줘
```
