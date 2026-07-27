# 세션 인수인계: 운영패널 수수료=판매유형별 쿠팡 총비용 (+ in-transit 유령 버그픽스)
> 저장일시: 2026-06-19 16:11
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 프론트: `cd frontend && npm run build` → 배포 `rsync -az --delete dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/` (nginx, 재시작 불필요).
- 백엔드 테스트: `cd backend && .venv/bin/python -m pytest -q` (★venv=`backend/.venv`). ⚠️프로젝트가 iCloud Drive + 타 인스턴스 동시 pytest 시 파일 I/O stall(전체 스위트 5분+) — 영향모듈만 타깃 실행 권장.
- prod: `ssh sellc.ohitech.co.kr` (User=ubuntu, ssh config 별칭). PM2 `ohisell-backend`(:8001). **git 아님 → scp+pm2 reload / rsync dist 배포.** DB=`~/ohisell/backend/ohisell.db`.
- prod 조회: `ssh sellc.ohitech.co.kr 'curl -s http://localhost:8001/api/...'` / `sqlite3 ~/ohisell/backend/ohisell.db "..."`
- git: HEAD=`cfe06e7`(push 완료, origin/main 동기화). 브랜치 main.

## 2. 이번 세션 완료 목록
- ✅ **[버그픽스·배포완료] RG 재고 in-transit 유령 중복 계상** — 커밋 `cfe06e7`(push+prod 배포 완료).
  - 증상: /inventory vii 95536607339 현재고 91·발송중 100·**유효재고 191(유령 +100)**.
  - 근본원인: `backend/app/services/coupang/in_transit_estimator.py` `_fetch_freshness`가 쿠키 status 무시·`last_success_at` 나이(2일)만으로 fresh 판정. WING1 쿠키 만료(red, 6-17 16:54) 후 sync 중단→입고 1070255256631250944 판매개시(6-18 08:36) 놓침. 마지막 성공 2일내라 stale 입고가 fresh 통과 → Open API 현재고(orderable_qty=91, synced 6-18 20:40)가 이미 흡수한 100을 재계상.
  - 수정: fresh는 `status=='green'` 계정만 인정(red/unknown=차감 스킵, 안전방향 D-12). last_fetch_at은 status무관 최근성공 노출.
  - 검증: 회귀 4건(`backend/tests/test_in_transit_estimator.py` 16/16). prod 라이브 effective_stock **191→91**·in_transit_fresh true→false·API 200. failures.jsonl 기록.
  - ★운영 후속: Wing 쿠키 갱신(`POST /api/coupang/ops/inbound/cookie`) 시 status green→in-transit 정상 재개 + 판매개시 stowing_at 포착(유령 영구 해소). 현재 양쪽 쿠키 red라 전 옵션 in-transit=0(안전).
- ✅ **[설계·계획 완료, 코드 0줄] 운영패널 "수수료"=판매유형별 쿠팡 총비용 (트랙 full-integration D-18)**
  - 발단: Jino "수수료 왜 아직 추정 7.8%냐". 조사: `coupang_ops.py` `sales-summary`의 `fee_rate_map`이 빈 dict라 전 옵션 flat 7.8%(3P 판매수수료, VAT제외). 2P는 쿠팡이 ~19.5%+ 가져가는데 누락.
  - 근본: 판매유형별 비용모델(D-17 BEP)은 설계·승인됐으나 **미구현**. 2P 전액은 `intelligence._agg_rg_settlement_fees`+`apply_rg_net_profit_flip`(D-16)에 있으나 종합조망 net_profit에만(엔진 2개 평행).
  - 확정(D-18a/b/c): 이익=매출−원가−쿠팡수수료−광고−물류비(한진3P). 3P=판매수수료+VAT, 2P=판매수수료+VAT+풀필먼트(입출고·배송·보관)+RG광고, 1P=공급가−원가−광고(범위밖). 한진=별도 물류비. 미정산 보관·RG광고=추정+표기.
  - 라이브 잠금: VAT=service_fee_ratio×1.1(판매수수료에만). 2P 구성 전부 `coupang_rg_settlement_fee.fee_type`(sale_fee/warehousing/delivery/storage/ad_sales)에 존재.
  - **계획서 작성 + plan-eng-review 완료**(8 findings 전부 반영): `docs/PLAN_coupang-seller-cost-by-saletype.md` (S1~S5, 끝에 GSTACK REVIEW REPORT).
- ✅ 기록: 트랙 D-18(a/b/c) + 사용자 원문 인용 + 계획서 + claude-progress.txt + failures.jsonl + 학습.

## 3. 확정된 결정사항 (번복 금지)
- **D-18 이익 정의**: 이익 = 매출 − 원가 − 쿠팡수수료(쿠팡이 가져가는 모든 비용) − 마켓플레이스광고 − 물류비(한진 3P). "수수료"는 판매유형별 상이.
  - 3P(Wing) = 판매수수료+VAT / 2P(로켓그로스) = 판매수수료+VAT+입출고·배송·보관+RG광고 / 1P(로켓배송) = 공급가−원가−광고(수수료 없음, 범위 밖·RocketView).
- **plan-eng-review 강화 결정(전부 반영)**: ① 새 평행합산 금지 — 기존 `_agg_rg_settlement_fees` 권위 분해 **재사용**(드리프트 차단). ② 보관료 per-unit-per-option 추정 금지(재고보유시간 비례) → account-level 추정/제외+coverage. ③ 정합은 **계정 단위·닫힌 과거 윈도우·정산인식일 기준만**(옵션단위 수렴 주장 철회). ④ S3 `fee_legacy`/`fee_new` 병행출력→prod 검증 후 컷오버(가역성). ⑤ 2P 판매수수료는 RG정산 sale_fee 단일소스·VAT 1회·배치주입(N+1 방지).
- **in-transit freshness D-20**: fresh 판정은 쿠키 status=='green'만 인정(원칙22 교훈).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_coupang-seller-cost-by-saletype.md` | ★다음 작업 계획서(강화판, S1~S5 + 리뷰리포트) |
| `docs/tracks/active/track_coupang-full-integration.md` | 트랙 정본 D-18(a/b/c) + 원문 인용 |
| `backend/app/routers/coupang_ops.py` | `sales-summary`(L584~), `fee_rate_map` 빈 dict(L765)·flat 7.8%(L828), `_rg_fulfillment_per_unit`(L524) |
| `backend/app/services/coupang/intelligence.py` | ★재사용 대상 — `_agg_rg_settlement_fees`(L403)·`_rg_account_breakdown`(L473)·`apply_rg_net_profit_flip`(L502)·`_agg_rg_ad_overlap`(L517) |
| `backend/app/models.py` | `CoupangRevenueFee.service_fee_ratio`(L862, VAT제외율)·`CoupangRgSettlementFee`(fee_type) |
| `backend/app/services/coupang/in_transit_estimator.py` | (배포완료) `_fetch_freshness` status-aware |

## 5. 알려진 이슈 / 주의사항
- **워킹트리 미커밋 = 로켓1P 트랙 작업분**(다른 트랙 세션 몫, 건드리지 말 것): `frontend/src/lib/api.ts`, `CommandCenter.tsx`, `coupang_ops.py`(로켓 부분)+`rocket_supplier_sync.py`, `tools/com.ohisell.rocket.plist`+`rocket_supplier_fetcher.py`, 다수 HANDOFF/track. **이번 세션은 내 스코프(in_transit 2파일)만 cfe06e7로 커밋.** 다음 세션이 coupang_ops.py 수정 시 cross-track 주의(scp 배포·git add 범위).
- prod에 **광고비·Wing 쿠키 둘 다 만료(red)** — 광고비 마지막 수집 6-17, Wing in-transit 전옵션 0. 쿠키 갱신 필요(별건).
- 전체 pytest 스위트는 iCloud+동시인스턴스로 stall — 영향모듈 타깃 실행.

## 6. 다음에 할 작업 (미완료)
- [ ] **운영패널 수수료 D-18 구현 — `/model sonnet` 후 S1부터**: S1 commission_vat_resolver(3P/2P 판매수수료+VAT, 이중계상 fixture) → S2 `_agg_rg_settlement_fees` 공유 리더 추출+미정산 추정(보관 account-level·RG광고 비율) → S3 Harness+sales-summary 병행출력(fee_legacy/fee_new)+배치주입 → S4 prod 병행검증(계정 닫힌윈도우 대조)→컷오버+shipping 풀필먼트 제거+legacy 삭제 → S5 프론트(쿠팡비용/물류비 카드·basis 배지). 각 Sprint 후 검증, 전체 후 codex review(원칙19).
- [ ] (운영) Wing 쿠키 갱신 → in-transit 정상 재개.
- [ ] (다른 트랙) 로켓배송 1P S5 prod 배포+push (codex 게이트 후), RG 수수료 size_mismatch 1건 자동해제 대기.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-seller-cost-by-saletype_20260619.md 읽고 이어서 작업해줘
```
