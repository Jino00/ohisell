# 세션 인수인계: 매출 정합 트랙 S3+S4 완료 — 취소 신선도 회귀수정 + 순이익 매출기준 정산화
> 저장일시: 2026-06-20 15:47
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 prod: `ssh ubuntu@sellc.ohitech.co.kr` · pm2 `ohisell-backend`(venv `/home/ubuntu/ohisell/backend/.venv/bin/python3` -m uvicorn :8001) · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 프론트 prod: nginx root `/home/ubuntu/ohisell/frontend/dist`, URL `https://sellc.ohitech.co.kr/command-center`
- 배포법: 백엔드=변경파일 scp → `pm2 restart ohisell-backend`. 프론트=`cd frontend && npm run build` → `rsync -az --delete dist/ ubuntu@…:/home/ubuntu/ohisell/frontend/dist/`
- 로컬 테스트: `cd backend && python3 -m pytest tests/test_settlement_revenue_adjust.py tests/test_settlement_revenue_source.py tests/test_intelligence_account_split.py -q` (로컬 Python 3.9 — 일부 타 테스트 `X|Y` 수집에러는 사전존재·무관, prod 3.10 정상. `-k` 전체수집은 abort되니 파일 명시 실행)
- Wing 수집: CDP Chrome 9222(`~/.ohisell/tools/wing_browser_fetcher.py chrome`), 데몬 launchd `com.ohisell.{adcost,wing,rocket}`.
- prod 쿠팡 호출은 서버 IP에서만(로컬 IP 403) → 정산/반품/RG 라이브 검증은 prod에서 `.venv/bin/python3 -c "..."`로.

## 2. 이번 세션 완료 목록
- ✅ **S3 — 취소 신선도 회귀 수정(완료·main 머지, 커밋 `8fd4349`)**: `backend/app/services/coupang/returns_sync.py`·`settlement_sync.py`가 `app.utils.kst.kst_today`를 import해놓고 **미정의 `_KST`를 쓰는 깨진 로컬 `kst_today()` 재정의**로 매 실행 NameError → **6/4~6/20(16일) 반품/취소·정산 자동동기화 중단**(커밋 a2bbd3a 잔재, overview.py는 6/9 수정했으나 이 둘 누락). 수정=깨진 재정의 삭제(import된 정상함수 사용). prod 배포·재시작·수동트리거 라이브검증(returns WING1 반품6/취소18·settlement 137txns·에러0). codex PASS. failures.jsonl 기록.
- ✅ **S4 — 순이익 매출기준 정산화(완료·main 머지, 커밋 `78751ac`~`d759833`)**: Jino 결정 B. 신규 SA `backend/app/services/coupang/settlement_revenue_source.py`(`settlement_net_by_line` (order_id,vid)→net=ΣSALE−ΣREFUND) + 신규 Harness `settlement_revenue_adjust.py`(순수 `compute_line_adjustment` 정산∩active 라인만 스왑·반품 라인별 되돌림 + 계정별 독립 합산 등가성) + `intelligence.py` 배선(`net_profit_pre_nonpa` 직후 `+= settlement_revenue_adjustment`, by_option 불변 D-14). 테스트 `test_settlement_revenue_source.py`(6) + `test_settlement_revenue_adjust.py`(10).
- ✅ **codex 2라운드 대화**: 1차 [P1]×2(① 성숙 판정 주문번호→라인 그레인 ② 반품 도메인 이중차감) **수용→라인그레인 재설계**, 2차 **PASS** + [P2]×2(sale_type IN(SALE,REFUND) 필터·vid 전역유니크 주석) 반영.
- ✅ **prod 라이브 검증(원칙22)**: WING1 6/6~6/20 **정산 82라인 매칭, adjustment=0**(우리 주문기반 net == 쿠팡 정산 net 정확 일치) → net_profit 불변(무회귀). 미성숙(6/12+, 정산 지연)은 폴백. 회귀 테스트 56 통과.
- ✅ **background task 생성**: "쿠팡 동기화 잡 실패 무탐지 알림"(task_38c9925a) — 잡 실패 시 stale 무탐지 방지(이번 16일 사고 재발 방지).

## 3. 확정된 결정사항 (트랙 D-N, 번복 금지)
- **D-9 A안(S2)**: 닫힌 과거일 **표시 매출 = Wing GMV(net)**, 읽기전용 오버레이.
- **D-10(S3)**: 취소 3경로(reconcile-by-absence·returns API·Wing GMV). cross-surface 갭(6/16 +56,700)은 앞 둘 다 안 거치고 Wing 판매분석에만 존재 → 닫힌일 S2가 정답, 당일은 gross 추정 불가피. RG는 주문 API에 status/취소 필드 **전무**(라이브확정) → RG net=Wing GMV(S2)가 유일 소스.
- **D-11(S4, 라인 그레인)**: 성숙 = `coupang_revenue_fee`에 그 `(order_id, vid)` 라인 존재. 정산∩active 라인만 스왑(부분 옵션 정산 정확, codex P1#2). 미정산/정산만-있고-active아닌 라인은 폴백/스킵.
- **D-12(S4)**: net_profit **매출기준만** 정산화(쿠팡 정산 실지급=SALE−REFUND). 화면 '🎯 정본매출'=Wing GMV(S2) **유지**(둘 다 표기). 계정 단위 읽기전용 가산보정(RG플립 패턴), by_option 불변. ★정산 net이 환불 반영 → 성숙 라인 반품차감 되돌림(이중차감 0).
- **D-13(S4)**: 3P(WING1·WING2)만 정산화. RG는 매출=Wing GMV(S2) 유지.
- **REFUND 부호(라이브확정)**: `sale_amount`는 양수 미러(SALE 179,000↔REFUND 179,000→net0). net=ΣSALE−ΣREFUND. prod 음수 sale_amount 0건. ("REFUND 음수" 주석은 service_fee 한정.)
- **vid 전역 유니크(D-8)**: 같은 vid 2계정 중복 0건(라이브확정) → unit_price vid 단독키 등가성 보존.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_revenue-wing-truth.md` | ★트랙 단일 진실원천(D-1~13, S2~S4 완료, S5 잔여) |
| `backend/app/services/coupang/settlement_revenue_source.py` | S4 SA — settlement_net_by_line((order_id,vid)→net=SALE−REFUND) |
| `backend/app/services/coupang/settlement_revenue_adjust.py` | S4 Harness — compute_line_adjustment(순수) + 계정별 합산 |
| `backend/app/services/coupang/intelligence.py` | command-center net_profit 조립. S4 보정 배선(net_profit_pre_nonpa 직후) |
| `backend/app/services/coupang/returns_sync.py`·`settlement_sync.py` | S3 수정(깨진 kst_today 삭제) — 반품/정산 동기화 |
| `backend/app/services/coupang/revenue_fee_source.py` | 실측 수수료 SoT(actual_fee_by_order_option, 기존) |
| `backend/app/services/scheduler_service.py` | 잡 cron 등록(returns 45 5·settlement 50 5). 실패 시 last_run_at만 stale(self-heal 없음=S5) |
| `backend/tests/test_settlement_revenue_{source,adjust}.py` | S4 머니룰 테스트 16개 |

## 5. 알려진 이슈 / 주의사항
- **로컬 main이 origin보다 8커밋 앞섬(미push)**. push는 Jino 요청 시만 `git push origin main`.
- 작업트리에 무관한 미커밋 파일 다수(.claude/memory/* HANDOFF들, rocket_supplier_sync.py, track_coupang-* 등) — 이번 작업 무관, 건드리지 말 것.
- 로컬 pytest `-k` 전체수집 시 7개 수집에러(Python 3.9 `X|Y`, 사전존재·무관) → 테스트는 **파일 명시 실행**.
- **`actual_fee_by_order_option`(revenue_fee_source)이 REFUND service_fee를 양수 합산**(3·4월 2행뿐이라 영향 미미) — 별도 점검 후보(이번 미수정).
- 정산 인식 지연 ~8일 → 최근일(6/12+)은 정산 0건 = 폴백(정상, 버그 아님). adjustment≠0은 정산이 우리보다 취소를 더 잡는 성숙일에만 발생(현재 0).
- 종합조망 응답 구조: `result["account"]["summary"]`(account지정) — `settlement_revenue_adjustment`·`settlement_matured_lines` 여기.

## 6. 다음에 할 작업 (미완료)
- [ ] **S5 — CDP Chrome 9222 launchd 상주화 + 반품/정산 cron 잡 self-heal/알림** (재부팅/Chrome종료/잡실패 자동복구·탐지). background task_38c9925a와 통합 가능.
- [ ] (선택) 로컬 main → origin push (Jino 요청 시).
- [ ] (관찰) 정산 성숙 후 adjustment≠0 케이스 모니터 — S4 메커니즘 라이브 효과 확인.
- [ ] (선택) `actual_fee_by_order_option` REFUND fee 부호 점검.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-revenue-wing-truth-S3-S4-done_20260620.md 읽고 track_revenue-wing-truth S5 이어서 작업해줘
```
