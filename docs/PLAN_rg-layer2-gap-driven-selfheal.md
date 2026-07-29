# PLAN — 쿠팡 RG 정산 층2(옵션 엑셀) 결손 주도 자가치유

> 작성: 2026-07-27 KST · 기준 main `1a01a93` (워크트리)
> 선례: 판매분석 자가치유 PR #108(45일 롤링·7일 청크·최신 우선·멱등 병합) — 같은 병(버튼 캐던스 < 주기 생성 속도)의 RG판.
> 배경 실측: WING1 층2 04-20~05-03 3주기 공백, WING2 04-27~05-03 2주기 공백. **층1(계정 수수료)은 두 계정 모두 그 주기가 완전 존재** → 병목은 층2 선택 로직 하나.

## §0 방향 고정 (설계 확정 — 이 스프린트의 금지선)

층2가 매 회차 "최신 1주기"만 받는 한(`rg_max_periods=1`), 버튼/크론 캐던스가 주기 생성 속도보다 느려지는 순간 그 사이 주기는 **영구 공백**이 된다. 창을 넓히는 것(판매분석식)만으로는 부족하다 — 층2는 주기×리포트당 최대 300초 폴링이라 무조건 넓히면 회차가 폭주한다. 그래서 **"결손을 물어보고 결손만 받는다"**.

- **D1. 결손 주도 층2 선택.** prod에 읽기 전용 엔드포인트 신설 — 주어진 account_key·창(days) 안에서 "층1 주기 중 층2 옵션 행이 0건인 주기"를 반환. Mac 페처는 층1 push 직후 이 엔드포인트를 조회해 **결손 주기만** 다운로드 대상으로 삼는다(최신 우선).
- **D2. 회차당 상한.** 새 config 키 `rg_max_targets`(코드 기본 3)로 회차당 주기 수 상한. 결손이 상한보다 많으면 최신 우선으로 자르고 나머지는 다음 회차(자연 롤링). 기존 `rg_max_periods`는 **코드에서 읽지 않는다**(config에 남아 있어도 무시). 최신 미수집 주기는 "옵션 행 0건"이라 결손에 자연 포함 → 별도 특례 없음.
- **D3. 층2 루프 세션 재확인.** 각 주기 다운로드 사이에 `_rg_session_ok(page)` 재확인. 실패 시 남은 주기 중단 + lease 계약의 `login_required` 실패보고 경로(재시도 소진 아님). **이미 받은 주기의 push는 유지.**
- **D4. dup 처리 유지.** `RG_DUP_SKIP`은 현행대로 스킵(실패 아님). 결손 주도라 완주된 주기는 재요청 자체가 없어 dup 노출이 구조적으로 준다. requestId 매칭 전환은 스코프 밖.
- **D5. 문서·설정 정리.** 페처 헤더 주석의 `rg_days`/`rg_max_periods` 낡은 설명 현행화. **엔드포인트 실패(네트워크 등) 시 폴백 = 기존 동작(최신 1주기)** — 수집이 아예 멈추면 안 된다.

### 스코프 밖 (건드리지 않는다)

RG 단독 로그인 경로 신설 · 백엔드 쿠키 경로 크론 재활성화 · PRODUCT_SIZE 리포트 · lease 성공경로 전달 · 04-27 이전 옛 공백의 실제 백필 실행(코드가 `rg_status_days` 오버라이드로 **가능하게만** 유지 — 창을 90으로 주면 옛 주기도 열거·치유되는 경로가 D1과 자연 결합).

## 1. 결손 판정 (백엔드)

grain 사실(기존 코드): 층1 = `vendor_item_id=''` sentinel, 층2 = `vendor_item_id=옵션ID`. 층2 적재는 `_resolve_period_start`로 **층1의 from을 차용**하므로 두 층의 (from,to)는 정확히 일치한다 → 주기 매칭은 `recognition_date_to` 하나로 충분.

**report_type ↔ fee_type 매핑**(신설 상수 `_REPORT_TYPE_FEE_TYPES`, 근거는 기존 코드 두 곳의 교차):

| sellerReportType | 리포트 이름(페처 `CONFIRMED_SELLER_REPORT_TYPES` 주석) | 엑셀 시트(`_SHEET_FEE_TYPE_MAP`) | fee_type |
|---|---|---|---|
| `WAREHOUSING_SHIPPING` | 입출고/배송비 | 입출고비 / 배송비 | `warehousing`, `delivery` |
| `CATEGORY_TR` | 판매수수료 | 판매수수료 | `sale_fee` |
| `STORAGE_FEE` | 보관비 | 보관비 | `storage` |
| `CRETURN_PICKUP_RESTOCKING` | 반품 회수/재입고 | 반품 회수비 / 반품 재입고비 | `return_shipping` |
| `VRETURN_HANDLING` | 반출비 | 반출비 | `return_handling` |

미매핑 리포트(`INVENTORY_COMPENSATION`·`BARCODE_LABELING_FEE`·`PRODUCT_SIZE_COMPARISON`·`VRETURN_SHIPPING`)는 파서가 없어 결손 판정 불가 → 응답에 `unmapped_report_types`로 밝히고 **결손으로 세지 않는다**. 요청 전부가 미매핑이면 `covered_fee_types=[]` → 페처는 판정 불가로 보고 폴백(최신 1주기).

**판정 규칙**: (주기 × report_type)에 대해, 그 리포트가 커버하는 fee_type **전부**에서 층2 옵션 행이 0건이면 결손. (일부만 0건은 결손으로 세지 않는다 — 그 주 배송비가 실제로 0일 수 있고, 그러면 영구 재다운로드 루프가 된다.)

**빈 주기 가드**: 커버 fee_type의 **층1 계정 행 amount가 전부 0**이면 애초에 항목화할 게 없다 → 결손 아님. 없으면 "정말로 비어 있는 주기"를 매 회차 다시 받는다.

## 2. 신설 엔드포인트

```
GET /api/coupang/ops/wing/rg-settlement/layer2-gaps
    ?account_key=COUPANG_WING1&days=35&report_types=WAREHOUSING_SHIPPING
    X-Ingest-Token: <AD_INGEST_TOKEN>        # 페처 전용(형제 claim/ingest와 동일 인증)
→ {"account_key","days","report_types","covered_fee_types","unmapped_report_types",
   "periods_checked", "gaps":[{"recognition_date_from","recognition_date_to",
                               "missing_report_types":[...]}]}   # 최신(to) 우선
```

읽기 전용 — DB에 쓰지 않는다.

## 3. 구현 체크리스트

- [x] 백엔드 `rg_settlement_sync.layer2_gaps()` + `_REPORT_TYPE_FEE_TYPES` 상수
- [x] 라우터 `GET /wing/rg-settlement/layer2-gaps` (토큰·account_key 검증은 형제 규칙 재사용)
- [x] 페처 `_prod_rg_layer2_gaps()` — 실패=None → 폴백 신호
- [x] 페처 `_rg_select_targets()` 순수 함수(최신 우선·상한·폴백·리포트별 결손)
- [x] `_do_rg_run` 배선: 층1 push 후 결손 조회 → 대상 선택 → 주기 사이 `_rg_session_ok` 재확인(D3)
- [x] `rg_max_periods` 읽기 제거 / `rg_max_targets` 신설(기본 3)
- [x] 헤더 주석 현행화(D5) + `_rg_status_payload` docstring의 죽은 `rg_days` 표기 정정
- [x] 테스트: 결손 판정·엔드포인트 인증/응답·대상 선택·상한·폴백·세션만료 중단
- [x] 전체 pytest 통과

## 4. 완료 기준

- 전체 pytest 통과(기존 3364 + 신규).
- 코드 경로가 **결손 0건이면 다운로드 0회**로 조용히 끝난다(refresh-complete로 요청 소멸).
- 엔드포인트가 죽어도 수집은 기존 동작으로 계속된다.
- 라이브 합격(배포 후 별도): 버튼 1회에 공백 주기가 최신부터 최대 3개 메워지고, 여러 번 눌러 잔여 공백이 소진되는 것을 prod `coupang_rg_settlement_fee`(vendor_item_id≠'') 행수로 확인.

## 5. 주의

- **PR·배포는 이 스프린트 밖** — codex 리뷰 후 오케스트레이터가 진행.
- config(`~/.ohisell_wing2_fetcher.json` 등)에 남은 `rg_max_periods`는 무해(무시)하지만, 옛 공백 백필을 돌릴 땐 `rg_status_days`를 90으로 1회 올려야 층1이 옛 주기를 열거한다(그래야 결손 목록에 뜬다). 단 창은 **400일이 상한**(엔드포인트 `days` le=400, 페처가 클램프)이다.

### 알려진 한계 (이번 스프린트 미해결 — 별건)

- **반쪽 적재 주기는 영구 불가시.** 결손 판정이 "커버 fee_type **전부** 0건"일 때만 결손이므로, 파서가 시트 하나를 skip해(D-13 미지 시트명·헤더 부재) 한쪽 fee_type만 적재된 주기는 **영원히 결손이 아니고 관측 수단도 없다**. 판정 규칙 자체는 옳다(일부만 0건을 결손으로 세면 그 주 배송비가 실제 0일 때 영구 재다운로드 루프). 응답에 `partial` 목록을 싣는 관측 개선은 소비자가 없어 이번 스코프에서 분리했다(적대적 리뷰 R1 [P2-6], 분리 합의).
- **치유 처리량과 굶주림 경계.** 어떤 주기가 "층1 amount는 nonzero인데 엑셀 옵션이 0행"이면(워크북 파싱 실패 `status="empty"`, 상세 0건 정상 시트, 옵션ID `-`, 종료일 부재) 그 주기는 매 회차 결손으로 재등장한다. 그런 주기가 `k`개면 회차당 실제 치유량은 **`max(0, rg_max_targets − k)`** 이고, 최신 우선 정렬이라 `k ≥ rg_max_targets`면 **진전이 0인데 로그는 조용하다**. 탐지는 §3의 결손/매칭 건수 로그(같은 주기가 매 회차 반복 등장)로 하고, 탈출구는 `rg_status_days`가 아니라 **`rg_max_targets` 일시 상향**(config만)이다. 정상 엑셀에서는 검산 사슬(`sum_detail==sum_summary==status_api_amount`)상 층1 nonzero면 옵션 행이 존재하므로, 이 경로는 이상 케이스에 한정된다.
- D3의 `login_required` 처리는 진입 시 세션 판정(재시도 대상으로 접는 codex 5R[P1] 규칙)과 **비대칭**이다 — 설계 확정 사항이라 그대로 구현했고, 이 비대칭은 codex 리뷰에서 다시 볼 지점.
