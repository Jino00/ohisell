# PLAN — 구 대시보드 쿠팡 3P 판매수수료 실측화 (10.8% 플레이스홀더 제거)

> 작성: 2026-06-15 · 엔진: Opus · 상태: **승인 대기**
> 트리거: Jino "수수료 10.8→7.8 정비" → 근본원인 조사 → 방향 B(실측 배선) 승인

## 1. 목표 (What & Why)
구 대시보드(`profit_calculator`)의 **쿠팡 3P(WING1/WING2) 판매수수료**가 시드 플레이스홀더
**10.8% 정률**로 계산된다(카테고리 근거 없음, 정산 실측은 7.8%). 이미 수집해 둔 실측 수수료
(`coupang_revenue_fee`, 종합조망이 쓰는 그 소스)에 구 대시보드만 배선돼 있지 않다.
→ 구 대시보드 쿠팡 3P 수수료를 **실측에 연결**, 종합조망과 **단일 진실 원천** 공유.

## 2. 근본원인 (조사 결론)
- 두 엔진은 **중복이 아니라 범위가 다름**: 구=전채널 시계열 대시보드 / 종합조망=쿠팡 전용 스냅샷.
  → 구 대시보드 은퇴(방향 A)는 네이버·카페24·시계열·랭킹을 파괴 → **기각**.
- 구 대시보드는 이미 실측 대부분 사용(RG 정산=`get_rg_total_by_account` 차감, 네이버=`commission_amount`, 카페24=실제).
  **딱 한 곳, 쿠팡 3P 판매수수료만** 플레이스홀더 10.8%로 남음.

## 3. 확정 결정사항 (D-N)
- **D-A**: 쿠팡 3P 수수료 = 실측(`service_fee + service_fee_vat` = 종합조망 `total_fee`와 동일)
  **있으면 실측, 없으면(미정산) 7.8% 폴백**(product_rev×7.8%). 하이브리드.
- **D-B**: 실측/폴백 판정 그레인 = **(order_id, vendor_item_id)** per 라인 조인.
  → 매출·수수료가 같은 주문일자 버킷에 들어가 recognition_date↔order_date 축 어긋남 회피.
  → 종합조망(recognition_date 집계)과 기간 합계는 일치, 일별 버킷은 주문일 기준(대시보드 일관).
- **D-C**: **스코프 = WING1/WING2(3P only)**. RG/ROCKET/네이버/카페24 **불변**.
  RG 채널의 구 대시보드 _line_commission(10.8%)·RG 정산 별도경로는 본 작업 **건드리지 않음**(별건, §7).
- **D-D**: 판매자 VAT(rev×10/110)·한진배송 1,900 라인 **불변**(이번 스코프 밖, 별개 머니 결정).
- **D-E**: 7.8% = WING1/WING2 폴백 정률. seed.py + 채널 DB 값 10.8→7.8(폴백으로만 쓰임).

## 4. 아키텍처 (원칙18 레고)
```
Agent      : routers/dashboard.py · routers/orders.py (인터페이스 불변)
  Harness  : profit_calculator (구 대시보드 엔진) — 실측 SA 소비 + 7.8% 폴백
             intelligence.py (종합조망) — _agg_fees가 동일 SA 소비하도록 정렬(선택)
    SA(신) : coupang/revenue_fee_source.py — 실측 수수료 단일 소스(SoT)
             · actual_fee_by_order_option(db, dfrom, dto, account_keys) → {(order_id,vid): total_fee}
             · (재사용) by_option 집계 — intelligence._agg_fees가 이걸 호출하도록 리팩터
```
- SA는 다른 SA를 모름. Harness(profit_calculator)가 SA 출력을 라인 계산에 주입(원칙18-6).
- 실측 SA = 종합조망과 **공유** → 같은 수수료 정의 단일 진실(원칙18-4 재사용).

## 5. 변경 파일
| 파일 | 변경 |
|------|------|
| `app/services/coupang/revenue_fee_source.py` (신규) | 실측 수수료 SA: by (order_id,vid) 조회, account 필터 |
| `app/services/profit_calculator.py` | 쿠팡 3P 라인 수수료를 실측 룩업+7.8% 폴백으로 교체(3곳: daily_trend·channel_summary·product_profit). `_line_commission`은 비-쿠팡3P 경로 유지 |
| `app/services/coupang/intelligence.py` | (선택) `_agg_fees`를 신규 SA의 by_option로 위임 — 동작 등가 보장 |
| `app/seed.py` | WING1/WING2 commission_rate 10.8→7.8 |
| `alembic/versions/*_coupang_3p_fee_rate.py` (신규) | `UPDATE channels SET commission_rate=7.8 WHERE code IN ('COUPANG_WING1','COUPANG_WING2')` |
| `tests/test_*` | 머니 fixture: 실측 있음/없음(폴백)/REFUND 음수/계정분리 |

## 6. 완료 기준 (Self-Verify, 원칙14·22)
1. 단위테스트(머니 fixture): 실측있음→실측값, 실측없음→7.8% 폴백, REFUND 음수 반영, WING1/2 계정분리. 전체 그린.
2. **종합조망 vs 구 대시보드 정합**: 정산 완료 기간(예 6/1~6/7)에서 두 화면 쿠팡 3P 수수료 합계 **일치**(잔차=VAT/배송 모델차이로 설명, 신규 불일치 0).
3. **prod 라이브 self-verify**: prod DB 사본/직접조회로 6/8~6/14 구 대시보드 쿠팡 수수료가 10.8%→실측으로 바뀌고 최근 미정산일은 7.8% 폴백 적용 확인.
4. 비-쿠팡(네이버·카페24)·RG·로켓 수치 **불변**(회귀 0).
5. `/codex review` **pass** (머니로직 게이트, 원칙19). 대화형 검증.

## 7. 명시적 제외(스코프 밖, 별건 추적)
- 구 대시보드 RG 채널 수수료 정합(10.8% 잔존) — RG 수수료 회계 트랙 소관.
- 판매자 VAT·한진배송 모델을 종합조망과 통일할지 — 별개 머니 결정(필요 시 Jino 별도 승인).
- 구 대시보드/종합조망 UI 통합 — 하지 않음(범위 다름).

## 8. 진행 절차
구조 승인(Jino) → `/model sonnet` 구현(SA→Harness→seed/migration→tests) → `/codex review` pass
→ prod self-verify → 커밋 → Failure Memory(있으면).

## 9. 완료 기록 (2026-06-15, 커밋 b4848ec)
- 구현 완료: SA + _line_commission 3P 분기 + 3집계함수 주입 + seed + alembic o9p0q1r2s3t4 + 테스트 15건.
- **codex 2R PASS**(원칙19): #2 수용(account_key 키 스코프), #1 기각 합의(REFUND 음수 SoT 일치).
- 테스트 206 그린(15 신규).
- **prod 사본 self-verify(원칙22)**: [A] 3대시보드함수+종합조망 무오류 / [B] WING2 3월 구10.8%→신8.49%(실측) / [C] **실측보유 192라인 불일치 0**(라인 단위 실측 정확 차감) / [D] 비-3P 불변.
- 실측 데이터 분포: 과거(3월) 풍부 / 최근(6월) 희박 → 최근 라인은 7.8% 폴백이 주 효과(10.8% 제거).
- **상태: 구현·검증 완료, prod 미배포** (브랜치 feat/coupang-3p-fee-actualization). 배포=prod `alembic upgrade head` + PM2 `ohisell-backend` 재시작(프론트 무변경). Jino 결정 대기.

## GSTACK REVIEW REPORT
| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Codex Review | `/codex review` | Independent 2nd opinion | 2 | PASS | 2 findings (1 fixed, 1 rejected-by-consensus) |

- **CODEX:** P1 #2(account 스코프) 수정 적용 / P1 #1(REFUND 부호) 기각 — codex 합의. 잔여 [P1] 0.
- **VERDICT:** CODEX CLEARED — 머니로직 게이트 통과. 배포 대기(Jino).
