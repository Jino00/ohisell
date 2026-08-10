# 세션 인수인계: 오하이테크 3P — ①②④ 완료, ③ 차단 원인 규명

> 저장 2026-08-10 21:2x KST · 트랙: 쿠팡 손익 정합 (`docs/tracks/active/track_coupang-promo-pnl.md`)
> **다음 세션이 할 일은 §6이다. §5-1을 «가장 먼저» 읽을 것 — 한 줄이 세 가지를 막고 있다.**

---

## 1. 환경

- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (루트=main 고정, 작업은 워크트리)
- prod: `ssh sellc.ohitech.co.kr` · DB `/home/ubuntu/ohisell/backend/ohisell.db` (1.7GB)
- **prod 파이썬은 `/home/ubuntu/ohisell/backend/.venv/bin/python3`** — 시스템 python3엔 sqlalchemy가 없다
- **백엔드 포트는 고정이 아니다** — 블루-그린이 8011↔8001을 번갈아 쓴다. 현재 **:8001**. `ss -ltnp | grep 800`으로 확인
- `python3` (not `python`) · 테스트 `cd backend && python3 -m pytest tests/ -q` (약 2분 40초, 5,218건)
- CI lint 게이트는 eslint: `npx eslint . --max-warnings 54`(errors 0 필수). 새 워크트리는 `npm ci` 먼저
- 프론트 타입체크: `npx tsc --noEmit -p tsconfig.app.json`

---

## 2. 이번 세션 완료 목록

### D-CPP-32 — 3P 수수료를 「순매출 × 옵션 요율 × 1.1」로 (PR #273 병합·prod 배포·라이브 합격)
- **신규** `backend/app/services/coupang/option_fee_rate.py` — 옵션별 요율 SoT + `fee_reconciliation`(전제 감시)
- `intelligence.py`·`profit_calculator.py` — 두 엔진 모두 요율 기반으로 전환
- `revenue_fee_source.py` — REFUND 부호를 저장값이 아니라 `sale_type`이 지도록 + `refunded_order_options`
- `CommandCenter.tsx` — `FeeBasisCard`(과세표준·요율 근거 등급·대조 결과)
- 라이브: WING2 순이익 498,063.30 → **474,469.82**, WING1 −8,049,682.30 → **−8,059,875.16** (둘 다 예측치와 **0.00원 일치**)

### D-CPP-33 — 두 엔진 차이 분해 + 종합조망 세 결함 (PR #276 병합·prod 배포·라이브 합격)
- `intelligence.py` `_agg_returns` — 유령 반품 억제(긍정 불변식) + **사실 축/돈 축 분리**(`return_qty` vs `deductible_qty`)
- `intelligence.py` — `payable_vat` 배선(매입세액에 원가·수수료·배송비·광고·비-PA·**rg_total**) · 배송 «수입» 계상 · `_orphan_return_stats`
- `product_pnl.py` — 원장에 `shipping_income_3p`·`payable_vat` 편입(보존식 회복)
- `CommandCenter.tsx` — 순이익 카드 설명 문구를 사실로 교체 + VAT 환급 분기
- 라이브: 7월 WING2 두 엔진 차 −31,084.00 → **−27,397.18**, **잔차 0**

### 부수
- `scripts/safe_deploy.sh` — 레거시 재시작이 «성공이라 말하며 아무것도 안 하던» 것 수리(pm2 이름 불일치를 `2>/dev/null`이 삼킴)
- 교훈 **#212~#215**(D-CPP-32) · **#224~#227**(D-CPP-33) · 진행 로그 2건 (PR #277 병합)

---

## 3. 확정된 결정사항 (번복 금지)

- **D-CPP-32**: 정산 통보는 «금액»이 아니라 «요율»을 알려줄 뿐이다(라이브 661건 전수: `service_fee = round(sale_amount × service_fee_ratio)`, `vat = round(fee × 0.1)`). 요율은 옵션당 상수(시기별 변동 0건). 3P 수수료 = **(3P매출 − 반품차감) × 그 옵션의 요율 × 1.1**, 요율 미상이면 채널 정률 7.8%.
  - Jino 원문: *"수수료는 정해진 수수료가 있으니까 그걸 때면 되는거 아니야?"*
- **D-CPP-33**: 반품차감은 «매출에 잡힌 주문»의 반품에만 적용된다(고아·매출제외는 억제). 단 **억제는 돈 축만** — 사실 축(`return_qty`·반품률)은 그대로 둔다.
- **D-CPP-33**: 종합조망도 납부세액을 뺀다(Jino 2026-08-04 결정을 이 엔진에도 적용). RG 정산액은 **매입세액에 포함**한다 — 종합조망 매출이 RG를 편입하므로 매출만 넣고 매입을 빼면 편측이 된다.
- **D-CPP-33**: 배송 «수입»은 순이익에만 더하고 **매출 축에는 넣지 않는다** — 종합조망 매출은 쿠팡 판매분석과 1:1 대조하는 축이다.
- **RG 비대칭은 이번에 안 고친다**(확인만): 구 대시보드에 RG 매출만 더하면 RG 원가·수수료가 없어 이익이 거꾸로 부푼다.

---

## 4. 핵심 파일

| 파일 | 역할 |
|---|---|
| `backend/app/services/coupang/option_fee_rate.py` | 옵션별 수수료 «요율» SoT + 전제 감시(`fee_reconciliation`) |
| `backend/app/services/coupang/intelligence.py` | 종합조망 엔진 — `_agg_returns`·`_agg_seller_shipping_3p`·`compute_command_center` |
| `backend/app/services/profit_calculator.py` | 구 대시보드 엔진 — `_line_commission`·`payable_vat`·`get_rg_total_by_account` |
| `backend/app/services/coupang/revenue_fee_source.py` | 정산 실측 수수료(부호는 `sale_type`이 진다) |
| `backend/app/services/product_pnl.py` | SKU 원장·보존식(새 항목은 여기 버킷에 반드시 편입) |
| `frontend/src/pages/CommandCenter.tsx` | `AccountView`(3P)·`RocketView`(1P)·`FeeBasisCard` |
| `tools/wing_browser_fetcher.py` | Wing 판매분석(vendor-summary) 페처 — ③의 본체 |

---

## 5. 알려진 이슈 / 주의사항

### 5-1. ★★한 줄이 세 가지를 막고 있다 — nginx IP 허용목록 (최우선)

**prod 로그가 지목한 사실**: `203.239.246.21`(이 Mac의 나가는 IP)이 403을 **1,369건** 받았고 허용목록에 없다.
`115.23.234.145`(Jino 노트북)는 허용돼 있어 200을 받는다.

이것이 동시에 막고 있는 셋:
1. **WING2 판매분석 수집** — 마지막 성공 push `2026-07-27 15:18`(데이터 07-26까지), 첫 403 `2026-07-28 08:38`, 누적 **5,123건**. ③의 전제 데이터가 13일째 안 흐른다.
2. **WING1(오픽스) 정산 수집** — 같은 07-28에 멈춤. 이게 수수료 «요율 학습»을 막아 신제품이 기본 7.8%에 머문다(WING1엔 10.5~10.8% 옵션 실재 → 과소 계상 위험).
3. **무중단 배포** — `zero_downtime_restart.sh` 3단계(공개 URL 경유 확인)가 403이라 실패. 이번 세션은 `--restart-legacy`(다운타임 ~50초)로 3회 우회했다.

**허용목록은 보안 설정이라 모델이 손대지 않는다.** Jino가 실행할 것:
```bash
ssh sellc.ohitech.co.kr "sudo sed -i 's|^deny all;|allow 203.239.246.21;   # Mac 데몬 ingest (2026-07-28 IP 변경으로 차단됐던 것)\\ndeny all;|' /etc/nginx/snippets/ohisell-allowlist.conf && sudo nginx -t && sudo systemctl reload nginx"
```
★적용 전 `curl ifconfig.me`로 IP가 그대로인지 확인할 것. 파일 헤더가 경고하듯 «회전하는 IP»는 넣으면 안 된다.
★적용 후 페처가 스스로 밀린 분을 올리는지, 아니면 수동 재실행이 필요한지 확인할 것.

### 5-2. 배포·머지
- 배포는 `scripts/safe_deploy.sh`만. **무중단이 막혀 있으니 `--restart-legacy`** (이번에 그 경로의 거짓 초록 버그를 고쳤다 — 이제 pid 변경·헬스를 검증한다)
- 프론트 CAS 거부가 뜨면 **덮지 말 것**. 이번에 병행 세션이 미병합 브랜치 상태로 prod 프론트를 올려놔서 한 번 거부됐다 → 그 브랜치가 main에 병합된 것 확인 후 병합·재빌드·재배포로 해소했다
- 병합은 `scripts/safe_merge.sh`. 번호는 `scripts/next_ids.sh`로 받되 **«받는 순간»에 받아라** — 이번 세션에서 D-CPP-30→32, 교훈 #204~207→#212~215로 두 번 재번호했다

### 5-3. 남은 부채 (D-CPP-33 적대 리뷰 P2 미처분)
- **`return_suppression`이 화면에 미배선** — 억제 사실이 API에만 있고 화면엔 없다(계약의 「화면 새 기능 안 함」에 걸려 미뤘다). 실토가 성립하려면 렌더링해야 한다
- 금액 양자화가 절반만(`payable_vat`만) — `net_profit` 등이 25자리로 API에 나간다
- 등가성 허용오차 0.01에 여유 0 — 계정이 하나 늘면 테스트가 깨진다
- `suppressed_orphan_rows`가 뺄셈 결과라 음수 가능(prod 중복 0건이라 잠복)
- `fixed_cost` 축 두 엔진 미통일
- **돈 축(`deductible_qty > 0`) 경로는 prod에 케이스가 0건** — 유닛 테스트로만 지켜진다. 부분반품이 처음 발생할 때 `return_deduction`이 실제로 붙는지 확인할 것(2R 리뷰 INCONCLUSIVE 항목)

### 5-4. 데이터 사실 (다음 세션이 오해하기 쉬운 것)
- `orders.selling_price`는 **이미 라인 총액**(쿠팡 `orderPrice`)이다. `× quantity` 하면 수량 2 이상에서만 틀린다 — 앞 세션과 내가 연달아 이걸 틀렸고, 수량 1이 압도적이라 전수 대조가 초록으로 보였다(교훈 #212)
- `coupang_return_item`의 **REFUND 행 `service_fee`는 양수**로 저장된다(문서화된 계약과 반대). 부호는 `sale_type`이 진다
- 반품 53건 중 34건(64%)이 **고아**(원주문이 `orders`에 없음) — 쿠팡 주문 API가 취소분을 안 주기 때문
- 쿠팡은 수수료를 **개당** 반올림한다 — 라인 단위로 계산하면 수량에 비례해 오차가 쌓인다

---

## 6. 다음에 할 작업

- [ ] **§5-1 IP 허용목록 적용**(Jino) → 페처 403이 멎는지·밀린 분이 올라오는지 확인. **이게 되면 ③의 절반이 저절로 풀린다**
- [ ] **③ 정본 매출 축**:
  - [ ] WING2 vendor-summary가 07-26 이후를 백필하는지 확인(안 되면 수동 재실행 `backend/.venv/bin/python3 tools/wing_browser_fetcher.py`)
  - [ ] Wing 판매분석 **옵션별** API 정찰 → 적재 → 대조. 우리는 지금 맨 위 요약 총액만 받고 옵션 목록은 아예 안 받는다
    - Jino가 준 화면 URL: `wing.coupang.com/tenants/business-insight/sales-analysis?...&product_type=NORMAL` (2P·3P 모두·옵션별로 나온다. 화면 실물 옵션 115개)
    - ⚠️기술 제약: 판매분석은 **모바일 origin(`m-wing.coupang.com`) same-origin**으로 불러야 200(데스크톱에서 부르면 CORS 차단)
    - ⚠️CDP: 현재 **9223 응답 없음**, 9224만 살아 있다(Chrome/151). WING2 Chrome이 죽었을 수 있다 → 로그인·2FA는 **Jino가 창에서 눌러야 한다**
    - ⚠️봇 감지(Akamai/Cloudflare) 있으니 **조회만, 천천히**
  - [ ] 코드는 이미 정본을 쓸 준비가 돼 있다 — 종합조망이 정본 없으면 `wing_used=false`로 주문 기반 폴백. **데이터만 흐르면 3P 순이익이 「추정」에서 「정본」으로 바뀐다**
- [ ] **3P 광고비 점검**(아직 아무도 안 봄) — 1P에서 찾은 「안 팔린 날·원가미상 옵션 광고비 누락」이 3P에도 있는지
- [ ] 3P 일별 손익(현재 기간 단위만 있음)
- [ ] §5-3 부채 처분

---

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_ohitech-3p-engine-unified_20260810.md 읽고 §6부터 이어서 진행해줘
```
