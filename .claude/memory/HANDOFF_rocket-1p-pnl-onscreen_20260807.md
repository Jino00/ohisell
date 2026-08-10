# 세션 인수인계: 로켓1P 매출·손익 화면 (D-CPP-23·24)
> 저장일시: 2026-08-07 21:2x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: `docs/tracks/active/track_coupang-promo-pnl.md` (쿠팡 손익 정합)

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
  - ★**루트는 main 고정**(pre-commit 훅). 브랜치 작업은 `git worktree add .claude/worktrees/<이름> -b claude/<브랜치> origin/main`
- 화면: https://sellc.ohitech.co.kr/rocket-1p-revenue (사이드바 「쿠팡 로켓배송(1P) → 매출·손익(납품가 축)」)
- API: `/api/overview/rocket-1p-revenue?from=&to=&limit=`
- 배포: `scripts/safe_deploy.sh <파일…> --restart` / `--frontend` · 병합: `scripts/safe_merge.sh <PR>`
- prod DB: `sellc.ohitech.co.kr:/home/ubuntu/ohisell/backend/ohisell.db` (sqlite, **읽기만**)
- main HEAD(세션 종료 시): `0ca6e5a` · 워크트리 잔여 0 · 루트 브랜치 `main`

## 2. 이번 세션 완료 목록
PR **#240 · #242 · #251 · #253 · #254 · #256** 전부 병합·prod 배포·라이브 검증 완료.

- ✅ `backend/app/services/coupang/rocket_1p_revenue.py` — 계산 원자를 **「날짜×옵션」**으로 내리고
  손익(`pnl`)·일별(`daily`)·작업목록(`uncosted`) 전부 그 원자를 접어서 만든다.
  → **일별 합 = 옵션별 합 = 기간 타일**이 원 단위까지 일치(라이브 확인).
- ✅ `backend/app/services/coupang/rocket_1p_channel_pnl.py` — `price`/`cost` CTE 단일 정의화,
  `promo_burden_by_day_option()` 신설(옵션·일별 분담금은 여기서 접어 씀), `_promo_burden_by_day`에 vendor 인자.
  ★`compute_rocket_1p_summary_row` 출력은 **한 원도 안 바뀜**(prod DB에 구·신 나란히 7창×2축 IDENTICAL).
- ✅ `backend/app/services/coupang/rocket_cost_map.py` — 후보 제안에 `already_mapped_count`(「공용 N」).
- ✅ `frontend/src/pages/Rocket1PRevenue.tsx` — 손익 사다리 · 일별 손익 표 · 옵션표(원가·순이익·이익률·BEP RoAS)
  · 원가 미연결 작업목록(이유별) · 제외 SKU 재검토 목록 · 기간 UI 교체.
- ✅ `frontend/src/components/PeriodRangeBar.tsx` **신설** — 「조회 조건」 기간 바 공용화.
  `RocketRecon.tsx`도 같은 컴포넌트를 쓰게 바꿈(렌더 결과 동일). 두 페이지의 `isoKST`/`daysAgo`를
  제거하고 테스트 있는 `lib/periodRange.kstDate`로 통합.
- ✅ `frontend/src/pages/CommandCenter.tsx` — 원가 매핑 후보에 「공용 N」 배지.
- ✅ 테스트: `test_rocket_1p_revenue.py` 12건 → **52건**, `test_rocket_supplier.py` +1.
  backend 4,992 passed · frontend 238 · tsc 0 · lint error 0 / warning **54(CI 상한, 유지)**.
- ✅ 문서: 트랙 **D-CPP-23·24**, 교훈 **#168~#171**, `claude-progress.txt`,
  메모리 토픽 `subset-profit-overstates-margin.md` 신설.

## 3. 확정된 결정사항 (번복 금지)
- **D-CPP-23** — 매출 화면에 손익을 올린다. 단 **원가 확인분만** 더한다(`pnl.basis='costed_subset'`).
  원가 미상을 0으로 넣으면 그 매출이 통째로 이익이 된다. 커버리지·미연결 목록을 함께 낸다.
- **D-CPP-2 불변** — 손익 축은 **우리 매출(납품가) 하나뿐**. 소비자 매출(쿠팡가)은 손익에 안 들어간다.
  이 모듈은 조회 전용이고 종합조망 `net_profit`에 결합 안 됨(참조 금지 가드 테스트 유지).
- **분담금을 모르면 손익을 내지 않는다**(0으로 접지 않음). 판정은 엔진(`rocket_1p_channel_pnl`) 한 곳.
- **BEP RoAS = 매출 ÷ (매출−원가−분담금)**. 공헌이익 ≤ 0이면 수치 대신 **None**(어떤 RoAS로도 흑자 불가).
- **「신규」 판별자 = 발주 첫 등장일**(`po_created_at`), 지평은 **조회 창과 분리**(기본 90일,
  `ROCKET_1P_NEW_SKU_DAYS`). "안 팔리던 게 이제 팔린다"는 신규가 아니다.
- **자동 매핑 금지**(교훈 #117 유지). 후보 유사도 0.55~0.81이고 1순위가 틀린 기종을 가리킨다.
- **D-CPP-24** — 매핑 완료 후 실측: 이번 주 순이익 1,952,831원(12.4%), **Z폴드8 3종 적자 −578,822원**.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/rocket_1p_revenue.py` | 화면 데이터 SA — 「날짜×옵션」 원자 → totals/pnl/daily/options/uncosted |
| `backend/app/services/coupang/rocket_1p_channel_pnl.py` | 손익 엔진(정본) — CTE·분담금 판정·`compute_rocket_1p_summary_row` |
| `backend/app/services/coupang/rocket_cost_map.py` | 원가 매핑(상품번호↔internal_sku) + 후보 제안 |
| `backend/app/routers/overview.py` | `/api/overview/rocket-1p-revenue` |
| `frontend/src/pages/Rocket1PRevenue.tsx` | 매출·손익 화면 |
| `frontend/src/components/PeriodRangeBar.tsx` | 「조회 조건」 기간 바(공용 — RocketRecon과 공유) |
| `frontend/src/lib/periodRange.ts` | ★프론트의 **유일한** 타임존 코드(`kstDate`) |
| `backend/tests/test_rocket_1p_revenue.py` | 52건 — 대부분이 «0으로 접지 않는다» 회귀 |
| `docs/tracks/active/track_coupang-promo-pnl.md` | 트랙(D-CPP-N) |

## 5. 알려진 이슈 / 주의사항
- ★**`basis`는 앞으로도 `costed_subset`일 가능성이 높다.** 「원가 제외(ignored)」 SKU가 팔리는 한
  커버리지 100%는 원리적으로 안 나온다(현재 11~15개 판매 중). 데이터 부실이 아니다.
- ★**부분집합 이익률을 전체에 곱하지 말 것** — 메모리 토픽 `subset-profit-overstates-margin` 참조.
  실측 29.6%(커버리지 42%) → 12.4%(97%). 빠지는 쪽이 **마진 얇은 신제품**이라 항상 높게 나온다.
- **판매분석은 롤링 약 2개월**(현재 2026-06-01~) + **당일·전일치 없음**. '오늘·어제' 프리셋은 대개 빈다.
- **매출 음수 행이 있다**(반품만 있고 판매 없는 옵션·창). 계산은 정상. 8월 9건.
- `OHI-TGLASS-IP17PRO`는 **이름표가 틀렸다** — 붙은 12개가 아이폰12·13·14·16. 납품단가가 전부
  10,740원이라 원가 공유는 일관되나 이름만 보면 오해한다. (「공용 N」 배지로 오해는 막았고, 이름 정정은 미착수.)
- 광고비가 화면에 **세 값**으로 나온다(계정 총액 / 옵션 Billboard 부분집합 / 판매 없는 옵션분).
  각각 왜 다른지 화면이 설명하지만, 이 구조를 모르면 헷갈린다.
- 프로모션 원천 **7건 중 4건만** 할인액 보유. 나머지 기간 조회 시 손익 대신 사유를 낸다(정상).

## 6. 다음에 할 작업 (미완료)
- [ ] **★Jino 결정 — Z폴드8 3종 처리.** 광고 축소 / 납품가 인상 / 원가 절감 / 프로모션 종료 대기.
      개당: Z폴드8 공헌이익 2,814원 vs 광고비 3,595원 · Z플립8 3,987원 vs 5,917원.
      프로모션 `686180`(7/23~**8/15**) 종료만으로 계산상 셋 다 흑자 전환(플립8 BEP 2.68→1.53).
      **8/16 이후 재측정해서 실제로 전환됐는지 확인할 것.**
- [ ] 「원가 제외」 SKU 15개 재검토(1,794,660원) — 샘플·증정이던 게 정상 판매로 바뀌었는지.
- [ ] `OHI-TGLASS-IP17PRO` 이름 정정(실물 지식 필요 — Jino).
- [ ] 30일 창에 남은 미연결 12건(소액, 47,760원 이하).
- [ ] codex 교차 리뷰 소급(08-09 이후 한도 회복 시) — 이번 세션은 **자체 적대 리뷰 3렌즈**로 대체(Jino 승인).
- [ ] 스탬프 CAS 개선 후보: `dist` **내용 해시** 병기 — squash 병합으로 SHA가 소멸하면 오탐한다(교훈 #168).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_rocket-1p-pnl-onscreen_20260807.md 읽고 이어서 작업해줘
