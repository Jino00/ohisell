# 세션 인수인계: 운영 패널 — 판매유형 축 정합 + 임의 기간 선택
> 저장일시: 2026-08-16 23:1x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 이 세션은 **작업 3건**을 했고 **전부 완료 QA 판정을 받았다**(§2). 판정 원문은 §2-1.
> ⚠️**미결 1건이 남아 있다 — §5-1. 라이브 수집 실패이고 내가 조사하지 못했다.**

---

## 1. 프로젝트 위치 및 환경
- 로컬(공유 메인, **main 고정**): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
  - 세션 종료 시점 이 폴더는 `f886f69e`로 **origin/main보다 5커밋 뒤처져 있다**(단순 미pull, 문제 아님)
- 워크트리 2개(이번 세션 산출물, 병합 완료 — 정리해도 됨):
  - `.claude/worktrees/coupang-selltype-axis` (PR #299·#300)
  - `.claude/worktrees/naver-ops-daterange` (PR #301)
- prod: `https://sellc.ohitech.co.kr` — **nginx Basic Auth + IP 허용목록 병행**(`satisfy any`)
  - 자격증명 `~/.ohisell_prod_auth`(600) · ID `dgfrty` · 무작위 24자
- 배포: **반드시** `scripts/safe_deploy.sh` / 병합: **반드시** `scripts/safe_merge.sh`
- 프론트 테스트: `npm run test`(**`npx vitest run` 금지** — 인구조사 가드를 지나침) · 타입: `npx tsc -b`(**`--noEmit`은 이 repo에서 아무것도 검사 안 함**)
- ⚠️**Mac이 대만 시간(CST, UTC+8)**이다. prod·화면은 KST(UTC+9) → 로그 대조 시 **1시간 차**

---

## 2. 이번 세션 완료 목록

### 작업 A — 수집 신선도 경고 2건 해소 + 원가 미연결 규명 (08-13)
- `~/.ohisell/tools/ohitech_ad_fetcher.py` — `_basic_auth()` 신설 + prod 호출 **6곳 전부** `auth=` (이 파일이 전날 Basic Auth 배선 목록에서 누락돼 401 크래시 루프)
- `~/.ohisell/tools/rocket_supplier_fetcher.py` — 나머지 prod 호출 9곳에 `auth=` 추가
- `~/.ohisell/tools/{ad_cost_browser_fetcher,wing_browser_fetcher,ad_settings_collect,promo_file_fetcher}.py` — 전수 스윕, **호출부 33곳 중 32곳** 배선(1곳은 쿠팡 S3 다운로드라 해당 없음)
- `~/.ohisell_ohitech_ad.json` — 자격증명 추가(다른 설정에서 프로그램적으로 복사, 평문 미출력)
- 데몬 6개 재기동(`launchctl kickstart -k`)
- 백업: `.bak-basicauth-20260813-1130` · `.bak-authsweep-20260813-1140`
- 교훈 #288·#289 기록 (`.claude/memory/LESSONS_LEARNED.md`)

### 작업 B — 쿠팡 손익의 판매유형(sell_type) 축 정합 (08-13~14) · PR #299 → #300
- 신설 `backend/app/services/coupang/ad_sell_type.py` — 1P(Retail)/2P(RG)/3P(Wing) 축의 **단일 정의**
- `backend/app/routers/coupang_ops.py` — KPI 광고 쿼리에 `sell_type in (3P,2P)` 적용, `excluded_ad_spend`·`by_sell_type` 신설, 미분류 통으로 «Σ분해 == 총계» 보장
- `backend/app/services/coupang/intelligence.py` — `_WING_SELL_TYPES`가 공용 정의를 참조
- `frontend/src/pages/CoupangOps.tsx` — `SellTypeBreakdown`(판매유형 표 + 1P 각주)
- 신규 테스트: `test_ops_panel_sell_type_scope.py` · `sellTypeBreakdown.test.tsx` · `coupangOpsTodayTab.test.tsx`
- **PR #300 추가 수정**: 라이브 검증에서 「오픽스 3P 매출 0원에 광고비 전액」이 나와, **광고비를 판매유형에 배분하지 않고 미분류로** 돌림 — 원장 `sell_type`이 **판매경로가 아니기 때문**(D-CPP-43: 오픽스 PA의 97.28%가 RG 옵션인데 라벨은 `3P`)

### 작업 C — 운영 패널 임의 기간 선택 (08-14) · PR #301
- 신설 `backend/app/utils/date_range.py` — `preset_range`·`resolve_range` **단일 규칙**(90일 상한·400 거절)
- `backend/app/routers/{naver_ops,coupang_ops}.py` — `date_from`·`date_to` 수용, 「오늘」 판정을 `days == 0` → **`is_today_only = (dfrom == dto == kst_today())`**
- `frontend/src/components/PeriodRangeBar.tsx` — `"15d"` 프리셋 추가, `presetWindow()` 순수 함수 분리, 클릭·하이라이트가 **같은 함수 한 곳**에서 나오게(`recent`/`singleDay` 제거)
- `frontend/src/lib/periodRange.ts` — `customRangeError(range, today, maxSpanDays)` 인자화 + `OPS_MAX_SPAN_DAYS = 90`
- `frontend/src/pages/{NaverOps,CoupangOps,Rocket1PFunnel}.tsx` — 공용 기간 바로 통일
- 신규 테스트 5종: `test_naver_ops_date_range.py` · `test_coupang_ops_date_range.py` · `periodRangeBarPresets.test.ts` · `periodRangeBarClick.test.tsx` · `periodRangeOpsCap.test.ts` · `naverOpsRequestRange.test.tsx`

---

## 2-1. 완료 QA (판정 원문 그대로)

### 작업 A
- **목적(원문)**: 대시보드 상단 「수집 신선도」 경고 2건(오하이테크 로켓광고 · 로켓 발주/정산)을 해소하고, 「원가를 모르는 매출 262,000원」의 정체를 밝혀 처리한다.
- **판정: 달성** — 합격기준 4건 전부 라이브 충족(①②Mac 로그 401 종료·`rc=0` ③`collection-status`가 두 스트림 `state=fresh` ④262,000원=CAFE24 개인결제창, 구조적 미연결로 규명 후 이월). ③은 UI 클릭이 아니라 배너 데이터 소스 직접 조회로 대체(등가성 코드 추적 확인, 화면 렌더링 미관측 — **이후 Jino 스크린샷으로 6건 ✅ 육안 확인됨**). (2026-08-13 11:47 KST, 별도 Sonnet QA)

### 작업 B
- **목적(원문)**: 「쿠팡 운영 패널」의 KPI가 매출 축과 광고 축의 범위를 맞추게 하고, 오픽스 화면을 2P(로켓그로스)/3P(Wing)로 갈라 보이게 한다.
- **판정: 달성** — 합격기준 4건 전건(①KPI·표가 같은 `ad_rows` 쿼리에서 파생돼 축이 갈릴 수 없는 구조 + 원장 대조로 3P 40,361·Retail 536,212 정확 일치 ②오픽스는 Retail 행이 전 기간 0건이라 필터가 연산상 no-op ③`excluded_ad_spend`가 API·화면 각주에 실재 ④`by_sell_type` 3행이 라이브 응답에 존재). ⚠️단 합격기준의 **숫자 자체**(−7,410 / 19,792)는 날짜가 넘어가 동일 시나리오 재관측 불가 — 구조적 한계이며 수정의 결함 아님. (2026-08-14 07:0x KST, 별도 Sonnet QA)

### 작업 C
- **목적(원문)**: `/naver-ops`에서 **임의 기간을 날짜로 직접 고를 수 있게** 한다. (도중 Jino가 *"대시보드 밑의 메뉴에 모두 동일하네? 모두 적용해줘"*로 범위 확장)
- **합격기준(원문)**: ①임의 구간이 그 구간 값을 낸다 ②오늘~오늘 = 「오늘」 버튼과 같은 값(당일 스냅샷 광고 축 보존) ③기존 버튼 5종 값이 안 바뀐다 ④잘못된 입력이 조용히 틀린 값을 내지 않는다
- **판정: 달성** — 4건 전건 라이브 충족(①임의구간 `period`가 요청과 일치·값 산출 ②네이버 `today_snapshot/day_max` 110,466원, 쿠팡 `ad_ref_date=2026-08-13` 94,908원 **완전 일치** ③`days=0/1/7/15/30` 전부 프리셋 창과 날짜 경로가 **바이트 단위 동일 응답** ④from>to·101일·한쪽만이 전부 명시적 400). 판단기준 3개 준수, 「안 함」 이탈 없음. 배포 stale 아님(`.build-stamp` = `73058ad0`). ⚠️**브라우저 실클릭·15/90/1년 프리셋 전수는 미관측(INCONCLUSIVE — 실행 안 됨이지 발견 0건 아님)**. (2026-08-14 10:2x KST, 별도 Sonnet QA)
- **미달 항목**: 없음
- **목적 전환**: 없음(Jino의 범위 확장은 계약에 병기)

---

## 3. 확정된 결정사항 (번복 금지)

1. **광고 원장의 `sell_type`은 «판매경로»가 아니다.** 라벨 `3P`인 옵션이 실제로는 RG로 팔린다(오픽스 PA의 97.28%). → **광고비를 판매유형으로 가르지 않는다.** 매출·수수료·원가만 주문에서 갈린다. 라벨이 가르는 건 «1P냐 아니냐»뿐이고 그 필터(scope)는 유효·라이브 검증됨.
2. **주문 기반 화면은 1P(Retail) 광고비를 담지 않는다** — 1P는 쿠팡 매입이라 매출이 `orders`에 없다. 빼되 **숨기지 않는다**(`excluded_ad_spend` + 화면 각주).
3. **기간 규칙은 `app/utils/date_range.py` 한 곳** — 날짜가 프리셋을 이긴다 · 한쪽만 주면 400 · 90일 상한(`days: le=90`과 같은 값).
4. **「오늘」 판정은 «확정된 기간»으로 한다**(`dfrom == dto == kst_today()`), `days == 0`이 아니라. 날짜로 오늘~오늘을 골라도 같은 광고 축을 타야 하기 때문.
5. **「쿠팡 광고 수정」 화면은 공용 기간 바로 옮기지 않는다** — 이미 기간 UI가 있고, 그 화면 7일/30일은 끝점이 **어제**다(Jino 2026-07-17 확정). 옮기면 확정 결정이 조용히 뒤집힌다. 통일하려면 그 결정의 재심이 먼저.
6. **「개인결제창」(CAFE24 `P00000UY000D`) = B2B 도매 매출**(Jino 확인). 월 1~2건 상시(2026-01~08 10건). 손익에서 분리 필요 — 조사 완료, 별도 계약 대기.

---

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `backend/app/utils/date_range.py` | 기간 파라미터 단일 해석 규칙(90일·400) |
| `backend/app/services/coupang/ad_sell_type.py` | 1P/2P/3P 축 단일 정의 + 미분류 통 |
| `backend/app/routers/coupang_ops.py` | `sales_summary` — 판매유형 분해·기간 파라미터 |
| `backend/app/routers/naver_ops.py` | `sales_summary` — 기간 파라미터·당일 스냅샷 축 |
| `frontend/src/components/PeriodRangeBar.tsx` | 공용 기간 바 + `presetWindow()` |
| `frontend/src/lib/periodRange.ts` | `kstDate`·`customRangeError`·`OPS_MAX_SPAN_DAYS` |
| `frontend/src/pages/CoupangOps.tsx` | `SellTypeBreakdown` 포함 |
| `~/.ohisell/tools/*.py` (리포 밖) | Mac 수집기 6종 — Basic Auth 배선됨 |
| `.claude/anchors/e26a4c69-*.md` | 이 세션 앵커(작업 3건 판정·이월 30건) |

---

## 5. 알려진 이슈 / 주의사항

### 5-1. ★★미결 — 로켓 발주/정산 「판매분석 push」가 prod에서 HTTP 500 (내가 조사 못 함)

Jino가 2026-08-15 07:49에 화면 캡처로 보고했고 **내가 착수하기 전에 세션이 끊겼다.** 2026-08-16 23:1x 실측:

```
2026-08-15 07:48:10 INFO  판매분석 push 성공 500건 → {'ingested': 500, ...}
2026-08-15 07:48:11 INFO  판매분석 push 성공 500건 → {'ingested': 500, ...}
2026-08-15 07:48:11 ERROR 판매분석 push 실패 HTTP 500 — Internal Server Error   ← 3번째 청크
2026-08-15 07:48:45 INFO  run 완료 — 발주 rc=0 / 정산 rc=0 / 발주상세 실패=0 / 프로모션손익 rc=1 / 쉽먼트 rc=0
                          사유: 판매분석 push 실패
```
- 판매분석 **1,409건을 500씩 청크로** push → 1·2번째는 성공, **3번째(409건)에서 prod가 500**.
- **다른 축은 전부 성공**(발주·정산·발주상세·프로모션·쉽먼트 rc=0). 즉 데이터가 통째로 낡은 게 아니라 **판매분석 축 하나**가 막혔다.
- prod refresh-status(2026-08-16 23:1x 실측):
  - 로켓 발주/정산: `last_success_at=2026-08-14T08:39:45`, `last_error_at=2026-08-15T07:48:46`, `last_error="로켓 수집 실패(rc=1): 판매분석 push 실패 [재시도 3회 소진]"`
  - 오하이테크 로켓광고: `last_success_at=2026-08-15T07:49:50`, 오류 없음
  - ofix 광고비: `last_success_at=2026-08-16T05:26:17`, 오류 없음
- Mac 데몬은 **전부 살아 있다**(`launchctl list` 전건 status 0, pid 2043~2078 — Mac 재부팅 흔적).
- **원인 미상**: prod 서버 로그(`~/.pm2/logs` 또는 uvicorn 로그)를 **아직 안 봤다.** 첫 두 청크는 되고 세 번째만 죽는 게 단서 — 특정 레코드·중복키·타임아웃 중 하나로 보이나 **추정이지 확인 아님**.
- **다음 세션 1순위**: prod 백엔드 로그에서 2026-08-15 07:48:11 전후 스택트레이스를 찾을 것. 엔드포인트는 `POST /api/coupang/ops/rocket/sales-analysis`(정확한 경로는 `rocket_supplier_fetcher.py`의 `SALES_INGEST_PATH` 확인).

### 5-2. 그 밖에 반드시 알 것
- **CI는 안 돈다** — GitHub Actions 결제 정지로 모든 run이 `steps:[]` 2~3초 즉시 실패. 빨강은 **코드 신호가 아니다.** PR #301 병합에 `safe_merge.sh --force` 사용(자백: `$TMPDIR/safe_merge.log`).
- **공유 메인 폴더 위험이 또 발생했다** — 교훈 #288·#289가 병행 세션 커밋 `1e53b8a`에 쓸려 들어갔다(메시지엔 #290·#291만). 내용 손실은 없었다. 이 HANDOFF도 지금 **미커밋 미추적** 상태다.
- **적대 리뷰가 3라운드까지 갔다**(작업 C). 원인은 리뷰 부족이 아니라 **1R·2R 수정이 얕았던 것**: 판정 로직을 프론트로 옮기고 순수 함수로 뺐는데 **버튼 클릭이 그 함수를 안 쓰고 있어서** 같은 변이가 되살아났다. 「테스트가 표시용 사본을 못박았다」가 정확한 표현. 3R에서는 인용한 교훈(#283)을 **절반만 적용한 것**까지 잡혔다.
- `test_vendor_item_axis.py::test_health_route_actually_returns_conservation` 1건 실패는 **main에서도 실패하는 기존 건**이다(새 지적으로 올리지 말 것).

---

## 6. 다음에 할 작업 (미완료)

- **이어지는 작업의 목적(원문)**: 없음 — 작업 3건 모두 판정 완료로 닫혔다. 아래는 **새 작업 후보**다.

### 우선순위
- [ ] **(1순위) 로켓 발주/정산 판매분석 push HTTP 500 원인 규명·수리** — §5-1. 라이브 실패이고 손익 축에 영향.
- [ ] **개인결제창(B2B) 손익 분리** — 조사 완료(manual_revenue 재사용 불가 · 채널 축 부적합 · **order/라인 축이 맞다** · `P00000UY000D` 리터럴은 코드에 0건). 계약 초안부터.
- [ ] **광고비의 판매유형 귀속 규칙** — Jino 결정 대기. 옵션이 «실제로 어디서 팔렸는가»(RG 주문 vs Wing 주문)로 귀속할지. 새 머니 규칙이라 모델 단독 결정 안 함. 그때까지 화면은 「가를 수 없다」를 보인다.
- [ ] **prod Basic Auth 5단계(IP 허용목록 해제)** — 승인은 이미 받아 뒀다. 막힌 건 비밀번호 결정(§2-B: fail2ban / 더 긴 비밀번호 / 스킵). 상세: `.claude/memory/HANDOFF_prod-basic-auth-4of5_20260813.md`
- [ ] **앱 설정 화면의 비밀번호 변경 기능** — Jino가 「나중에」. ★선행 조건: **사람용/기계용 자격증명 분리**(안 하면 바꾸는 순간 Mac 데몬 5개가 전부 401).

### 이월 (작업 C 리뷰·QA가 남긴 것)
- 「오늘」 판정이 **클라이언트 시계** 의존 — 자정 넘겨 열어둔 화면에서 「오늘」이 어제로 굳는다. 종전 `days=0`은 매 요청 서버가 재해석했다.
- `resolve_range`가 **미래 날짜를 통과**시킨다(프론트만 막는다).
- 요청이 거절돼도 `data`를 안 지워 **표가 이전 구간 숫자를 유지**한다.
- 백엔드 날짜 규칙이 여전히 **둘**(운영 90일·400 / 변경이력 365일·422).
- 브라우저 실동작 미검증 — 프리셋 클릭·하이라이트·거절 문구는 jsdom·API로만 뒷받침.
- 오픽스 `days=7`에서 **`by_product` 합계가 `summary.profit`과 5,382원 어긋난다**(`by_sell_type`은 정확히 일치). 원인 미상.
- 쿠팡 쉽먼트 **상세 10건이 캡(60)에 걸려** 목록만 적재 — 총 입고 수량 '모름'.
- prod 접근 클라이언트의 **전수 목록이 어디에도 없다**(작업 A에서 파일 하나가 누락돼 사고가 났다).
- `ad_costs.py`·`coupang_report.py`·`rocket_*.py`에 `sell_type` 리터럴이 남아 있다.

---

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_ops-panel-daterange+selltype_20260816.md 읽고 이어서 작업해줘

1순위는 §5-1이다 — 로켓 발주/정산의 「판매분석 push」가 prod에서 HTTP 500이고
내가 조사를 못 한 채 넘겼다. 1,409건을 500씩 나눠 보내는데 3번째 청크에서만 죽는다.
prod 백엔드 로그에서 2026-08-15 07:48:11 전후 스택트레이스부터 찾을 것.

인계 목록은 실측 전엔 믿지 말 것(숫자가 붙은 항목은 그 숫자부터 다시 센다).
작업은 워크트리에서. 교훈·D-NAO 번호는 scripts/next_ids.sh로 받을 것.
```
