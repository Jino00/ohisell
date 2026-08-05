# 세션 인수인계: 로켓1P 원장 정합 라이브 완성 + ASN 미수금 실측 (2026-08-05 오후)
> 저장 2026-08-05 20:1x KST · 트랙: **쿠팡 손익 정합** (전 인계: `HANDOFF_rocket-1p-axes+promo-file-ingest_20260805.md` §5를 이 세션이 실행함)
> main `701371c` (전부 push됨) · prod·Mac 런타임 배포 완료 · **PR #199 병합됨**

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (main 고정, 작업은 워크트리)
- 이 세션 워크트리: `/tmp/wt-rocket-parity` (브랜치 `claude/rocket-1p-parity-fixes` — PR #199로 병합 완료, 이후 커밋 `2c4a88d`·`82ec4f8`·`265c5a5`+1은 **main에 직접 없음 → 브랜치에 있고 prod에는 배포됨. 다음 PR 필요**)
- prod: `ssh sellc.ohitech.co.kr`, 백엔드 포트 **8001**, DB `/home/ubuntu/ohisell/backend/ohisell.db`, 배포는 `scripts/safe_deploy.sh`만
- supplier 정찰 Chrome: CDP **9223** (recon 전용, 로그인 살아 있음) / 페처 상주는 9225
- 테스트: `cd /tmp/wt-rocket-parity/backend && PYTHONPATH="$PWD/../tools:$PWD" .venv/bin/python -m pytest -q` (**4,790 통과**)

## 2. 이번 세션 완료 목록
- ✅ **ref 44** — 1P 5축 라인 대조("값은 안 틀렸다, 완결성·귀속이 틀렸다") + §8 조치 결과
- ✅ **판매분석 페이지네이션 유실 수정** (`10cd8a5`) — GMV 불안정 정렬, 판정자를 원시 vendorItemId 중복 여부로. 13일 617/623→**623/623**
- ✅ **계산서·발주 13개월 백필** — 계산서 148→**486건**(2025-07-23~, 928,661,363원, 라이브 전 필드 ±0원), PO 930→2,554건, 발주상세 307→**2,553건**(라인합=헤더 전건 일치)
- ✅ **로켓1P leaf 대시보드 배선** (`8948fdc`, PR #199) — 매출=계산서 지급액, 라이브 07-05~08-03 원 단위 일치. 이중계상 방지(레거시 수기 매출·광고비를 갈아끼움/날짜 합집합)
- ✅ **매출 축 토글** (`2c4a88d`) — `rocket_basis=settlement|sales`. 라이브 1,578,000↔3,885,820 전환, 원가 커버리지 27.3% 노출, 임계 미달 시 순이익 "—". 분담금 원천 테이블 없으면 **0이 아니라 모름**
- ✅ **대시보드 성능** (`82ec4f8`) — 조회 시 외부 API 재동기화에 5분 쿨다운. 축 토글 63.7초→**0.1초**. 판매 축 SQL은 24ms로 무죄였음
- ✅ **프론트 레이스 2건** — 세대 카운터 + `syncAndRefresh` 낡은 클로저(ref화). 증상=토글 무시, 실위험=라벨·값 불일치
- ✅ **ref 45** — ASN(발송) 축 정찰(코드 0)·미수금 실측·**§12 정정**·직원용 문의 자료
- ✅ 트랙 파일 D-14·D-15 / TRACKS.md / claude-progress.txt 갱신(정정 포함)

## 3. 확정된 결정사항 (번복 금지)
- **1P 대시보드 매출 = 계산서 `payment_amount`(VAT 포함), 작성일자 귀속** — 공급가 아님(전 채널 VAT 포함 축과 통일, VAT는 payable_vat가 순이익 단계 차감). 되돌리려면 `_REVENUE_COLUMN` 한 줄
- **두 매출 축은 택일** — 합산=이중계상. 기본값=계산서(회계 정본). 모르는 축 값은 조용히 계산서로
- **원가 커버리지 <95%면 순이익 "—"** — 부분 표본에 마진 곱하면 창작 (`ROCKET_1P_COST_COVERAGE_MIN`)
- **모르는 비용은 0이 아니라 None** — 분담금 원천(`coupang_promo_discount_item`)은 **prod에만 있고 main에 없음**(D-CPP-10 브랜치 미병합) → 테이블 존재 검사 후 없으면 순이익 미산정
- **★미수금 판정 기준 = 쿠팡 입고 원장**(`/scm/receive/detail/download` XLSX) — ASN 쉽먼트 화면의 입고수량은 재발송 입고를 못 봐 과대계상함(§12)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/rocket_1p_channel_pnl.py` | 1P 평행 엔진(두 축·커버리지·분담금 방어) |
| `backend/app/routers/dashboard.py` | `rocket_basis` 파라미터·1P 행 갈아끼우기·동기화 쿨다운 |
| `tools/rocket_supplier_fetcher.py` | 판매분석 페이지네이션 수정(중복 판정자·pageSize 100) |
| `frontend/src/pages/Dashboard.tsx` + `components/RocketBasisToggle.tsx` | 축 토글 UI·레이스 가드 |
| `docs/references/44_…parity…md` / `45_…asn…md` | 대조·미수금 근거(45 §12가 정정 정본) |
| `docs/references/data/45_coupang_unreceived_claim_list_20260805.csv` | (구판 209라인 — §12 정정 전. 정본은 아래 Downloads CSV) |
| `~/Downloads/coupang_claim_manifests_20260805/` | **직원 문의 자료**: A4 1장 PDF·원장대조 CSV(159라인)·내역서/라벨 208장 |

## 5. 알려진 이슈 / 주의사항
- **★미수금 정본 = 8,033,970원**(159라인·729개, 1차 청구 권장=하차 확인 130라인 **7,108,720원**). 14.0M·11.4M·13.9M은 전부 **폐기된 중간값** — ref 45 §12만 믿을 것
- **교훈 #141** (아침 세션이 #140을 써서 뒤로 재번호): **같은 원천의 다표면 일치는 교차검증이 아니다** — 쉽먼트 화면·내역서 PDF·추적이 일치한 건 셋 다 우리 입력이라서였고, 진짜 독립 표면(입고 원장)을 넣자 5.76M이 무너졌다. 검사식이 결함과 같은 값을 보면 통과한다(ref 44 §3-2 거짓 초록과 같은 계열)
- 워크트리 브랜치의 `2c4a88d` 이후 커밋들은 **prod에는 배포됐지만 main 미병합** — PR 필요(codex 부채와 함께)
- codex 교차 리뷰 전면 미실행(한도 리셋 **08-09**) — PR #199는 Jino 승인하에 리뷰 스킵 병합, PR 본문에 명기
- 트럭 쉽먼트(`/ibs/shipment/truck/list`) 파라미터 미해명(400) — 사각 1건뿐이라 방치 가능
- ASN 파서 함정: 상세 Table의 박스 셀은 rowspan → 셀 개수(7 vs 6)로 갈라야 함(ref 45 §10)
- 1P 페처는 버튼-only — 계산서 축은 사람이 누를 때까지 하루 뒤처짐

## 6. 다음에 할 작업 (미완료)
- [ ] **오픽스 RG −17,342,298원** (계약 합격기준 ① — 유일하게 미충족). RG 매출을 1P와 같은 평행 엔진으로. 앵커 `855d98bb…md` 참조
- [ ] 원가: 오매핑 31.7%(의심 21건 목록 있음) + 미매핑 178 SKU — **Jino가 SellC 입력 예정**, 자동 매핑 금지(교훈 #117). 입력되면 판매 축 순이익이 자동 점등
- [ ] 미수금 문의 발송(쿠팡 BM·한진택배 인수증) — 자료 완비, **외부 발송=Jino 몫**
- [ ] 워크트리 잔여 커밋 PR + codex 소급 리뷰(08-09 이후)
- [ ] ASN 수집 상시 배선(`coupang_rocket_shipment`/`_item` 신설·마이그레이션) + `/rocket-recon`에 「보낸 수량」 열 — ref 45에 경로·함정 다 있음
- [ ] 미확정 149,056,185원(fill rate 13.3%) SKU별 분석 — 별건(재고·발주 계획)
- [ ] 2025-09 미수금 집중(2.1M) 원인 확인

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_rocket-1p-parity-live+asn-receivable_20260805.md 읽고 이어서 작업해줘
```
