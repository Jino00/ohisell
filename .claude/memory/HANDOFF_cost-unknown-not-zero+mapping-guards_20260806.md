# 세션 인수인계: cost-unknown-not-zero + mapping-guards
> 저장일시: 2026-08-06 (KST, 최초 저장) · **2026-08-06 밤 갱신**(통합 손익 대조원장분 추가, D-NAO-158) · 트랙: 네이버 SA 광고 최적화 (이 세션은 손익 정합 성격 — 광고 집행 축 아님)
> 새 대화 시작 시 이 파일을 먼저 읽을 것. 직전 인계는 `HANDOFF_today-ad-axis-regression+snapshot-guard_20260806.md`(같은 날 저녁).

## 1. 프로젝트 위치 및 환경
- 로컬 경로(이 세션 작업 워크트리): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/cost-unknown`
  (루트는 **main 고정** — 브랜치 작업은 `.claude/worktrees/`)
- 브랜치: `claude/cost-unknown` · PR #229 (`https://github.com/Jino00/ohisell/pull/229`, 커밋 `7ecccb3`·`01043d3`·`330a5f9`·`c77a7df`·`1c747a1`, 전부 push 완료·**prod 배포 완료**)
- prod: `https://sellc.ohitech.co.kr` · 서버 `sellc.ohitech.co.kr:/home/ubuntu/ohisell`
- 배포: **`scripts/safe_deploy.sh` 만** (백엔드 `--restart` / 프론트 `--frontend`)
- 테스트: `cd backend && python3 -m pytest tests/ -q` → **4937 passed**(누적 신규 20건: 1차 16건 + 통합 손익 대조원장분 4건). `frontend`: `tsc --noEmit` 0 에러였으나 **실제 빌드(`tsc -b`)는 1건 실패했었다** — 아래 §2 참조, 수정·재검증 완료.
- D-NAO·교훈 번호는 `scripts/next_ids.sh`로 받았다: **D-NAO-156·157·158**, **교훈 #155·#156·#157·#158·#159**.

## 2. 이번 세션 완료 목록

**출발점**: Jino가 SellC에서 스마트스토어 판매 손익을 정확히 계산하는 작업 중이었다. 직전
HANDOFF §6은 1순위를 「순위 서보 D-NAO-124」로 적어 뒀는데 그건 광고 **집행** 축이라 손익
정확도와 다른 축이었고, Jino가 "왜 또 이렇게 멀리 온거야"라고 지적해 손익 축으로 복귀했다.
(라이브 근거: 07-31~08-06 자동운영 approved 0건 — 제어 축은 꺼져 있다.)

**발견**: 손익 화면이 **원가 미상 상품을 이익률 94~96%로** 표시하고 있었다. 원인은
`cost_map.get(pid, 0)` — 원가를 못 찾으면 0원 원가로 접혔다.
- 30일 실측: 6개 상품·공급가 매출 956,545원(전체의 2.5%), 원가 있는 상품 평균 원가율 22.1%
  → 이익 약 21만원 과대
- 1위가 주력 폴드8 필름 `13687558206`(30일 822,545원)
- 같은 날 광고비에 세운 원칙(«모름»은 0이 아니다, 교훈 #151)의 **미적용 케이스**였다

- ✅ **수정 1 (커밋 `7ecccb3`) — 표면화**
  - 원가·이익·이익률 셋 다 `None`. 매출·수수료는 실측이라 유지
  - «모름» 사유 분리: `unmapped`(활성 매핑 없음 → 매핑 필요) / `zero_cost`(cost_price 0/NULL → 원가 입력 필요)
  - 요약 이익은 계속 계산 + 배너로 표면화(미상은 매출 2.5%라 전체를 가리면 화면이 쓸모없어짐)
  - 라이브 합격: `13687558206` 94.85% → 「—」, 배너 «원가 미상 6개·매출 956,545원 · 매핑 필요 5 · 원가 입력 필요 1», 정상 298개 무변화
- ✅ **수정 2 (커밋 `01043d3`) — 적대 리뷰 P1 3건 반영**(codex는 usage limit, 복구 2026-08-09 16:16 — 실행해 확인함)
  - P1-1 배너 "그만큼 과대" → 지시어가 굵은 숫자(매출)에 붙어 읽힌다. 과대분은 매출이 아니라
    **빠진 원가**이고 액수는 모름 → 방향만 말하도록 수정
  - P1-2 **이익·이익률 카드가 스스로 미상을 말하지 않았다** — 값은 확정 숫자에 파란색(양호)인데
    경고는 카드 밖에만 있었다. 이 PR이 광고비 카드에서 고친 구조를 이익 카드가 그대로 갖고
    있었다. → 강조색 제거 + sub "원가 미상 N개 빠짐 — 실제 이익은 이 값 이하"
  - P1-3 광고비 pending과 겹치면 배너가 없는 값을 두고 "과대"라 단정 → 분기
  - P2 원가 카드 "이보다 큼" → "이 값 이상"(미상 원가가 정말 0일 수도 있다)
- ✅ **수정 3 (커밋 `330a5f9`) — Jino 지시 "근본 수정을 해야 하는거 아니야?" → 원천 차단**
  - **A** `naver_ops.py`: 종전 `min(costed or cands, key=lambda x: x[1])`의 정렬 키가
    **원가가 아니라 product_master.id**였다. 한 채널옵션ID에 활성 매핑이 둘이고 원가가 다르면
    "맞는 쪽"이 아니라 "id 작은 쪽"이 뽑혔고, 그 값은 `cost_map`에 들어가 «모름» 배너에도 안
    잡혔다 — 화면이 자신 있게 틀렸다. → 후보가 갈리면 고르지 않고 사유 `ambiguous`로 «모름».
    음수 원가도 이 경로에서 죽는다(종전엔 fallback이 음수를 골라 "원가 있음"으로 나갔고,
    원가가 음수면 이익을 빼는 게 아니라 더한다)
  - **B** `products.py` `/api/products/upload`: `_active_option_clash` 가드가 add/update엔
    있었는데(D-12) **구형 엑셀 업로드만 뚫려 있었다**(`existing_mapping` 조회가 내
    product_id로만 걸림) → 라이브에 네이버 이중 활성 매핑 **22건**이 그렇게 쌓였다. 일괄
    업로드라 파일 전체를 죽이지 않고 그 행만 skip + 사유
  - **C** `schemas.py` `cost_price` `ge=0` + 업로드 경로 직접 가드(업로드는 pydantic을 안
    거치고 DB에 직접 쓴다)
  - 라이브 실측: 이중 활성 매핑 **22건 존재 / 원가가 갈리는 건 0건 / 음수 원가 0건** → 구조는
    깔려 있었고 아직 안 터졌을 뿐. 배포 후 회귀 0(원가·이익 원 단위까지 동일, `ambiguous=0`,
    정상 298개 유지)
- ✅ **테스트**: 신규 16건(`test_naver_ops_cost_unknown.py` 11 + `test_products_upload_guards.py` 5).
  전체 **4933 passed**, tsc 0.
- ✅ **문서화**: D-NAO-156·157(트랙 파일), 교훈 #155·#156·#157(LESSONS_LEARNED.md),
  `failures.jsonl` 2건, 이 HANDOFF.

### 2-a. (같은 날 밤, 추가 작업) 통합 손익 대조원장도 고쳤다
Jino 지시 22:14 원문: **"통합 손익 대조원장도 같이 고쳐줘"**. 커밋 `c77a7df`(백엔드·prod
배포 완료) + `1c747a1`(프론트 수정·배포 완료).

- **무엇이 틀렸나**: `backend/app/services/product_pnl.py`의 `_agg_marketplace_by_sku`에
  naver_ops와 **같은 버그 클래스**가 있었다 — `cost = (pm.cost_price * o.quantity) if pm
  is not None else _Z`. 마스터는 붙었는데 `cost_price`가 0/NULL이면 원가를 **모르는**
  것인데 0원으로 계산돼 그 SKU 순익이 과대였다. `ProductMaster.cost_price`가
  `nullable=False, default=0`이라 미입력이 조용히 0이 된다.
- **★금액은 한 톨도 바꾸지 않았다** — 여기서 cost를 빼면 `conservation_ok`(Σ SKU귀속+
  Σ잔차==총계)와 권위 엔진(`profit_calculator`) 정합이 **동시에** 깨진다. 모른다는
  사실만 따로 싣고(SKU 행 `cost_known`·`cost_unknown_revenue`, 요약 `cost_unknown_skus`·
  `cost_unknown_revenue`·`cost_unknown_scoped`) 화면이 그 SKU 순익을 「—」로 비우고
  배너로 방향만 말한다.
- **`cost_unknown_scoped`**: 계정 지정(쿠팡 전용) 조회는 마켓플레이스가 스코프 밖이라
  판정 자체를 안 한다 → 그때의 0을 «없음»으로 읽지 않게 표시.
- **라이브 검증(30일 창, 배포 전→후)**: 불균형 컴포넌트 `coupang_1p/cost`·
  `coupang_1p/net_profit` **동일**(안 나빠짐) / `reconciled_net_profit` 70,259,805 →
  70,259,805 / `net_profit_allocated_total` 70,808,362 → 70,808,362 /
  `account_adjustment_residual` −548,557 → −548,557 / naver 3개 컴포넌트 전부
  `conservation_diff=0.00` / 신규 필드 `cost_unknown_skus=1 · cost_unknown_revenue=17,800.00
  · cost_unknown_scoped=true`.
- **★수치 정정**: §5에 이월로 적었던 "네이버 1개 상품·30일 44,500원"은
  `product_channel_mapping` 조인으로 잰 값이고, 손익 엔진이 실제로 쓰는
  `Order.product_id` 조인 경로로는 **17,800원**이다. 후자가 맞는 숫자다 — 아래 §5·§6에
  반영.
- **★미검증**: **SKU 행 단위 「—」 표시는 라이브로 못 봤다.** `coupang_1p/cost`·
  `net_profit` 보존법칙이 깨져 있어(이번 변경과 무관한 **기존** 결함) 모든 조회 창에서
  `by_sku`가 빈 배열이고 SKU 표가 통째로 안 뜬다. 확인된 것은 **배너와 요약 필드까지**다.
  원장이 균형을 회복해야 행 표시를 볼 수 있다.
- 테스트 +4 (`tests/test_product_pnl.py`: 보존법칙 유지·미매핑 이중계상 없음·거짓경보
  없음·계정 스코프). 전체 **4937 passed**.

### 2-b. 내 검증이 거짓 초록이었다 (커밋 `1c747a1`)
- `frontend/` 검증을 `tsc --noEmit`으로 했고 통과했는데, 빌드가 실제로 쓰는 `tsc -b`
  (프로젝트 참조)는 **TS2345로 실패**했다(`ProductConnectionMap.tsx` 743·845행,
  `Argument of type 'string | undefined' is not assignable to parameter of type
  'string'`).
- 드러난 실제 결함: 그 파일의 로컬 `won = (s: string) => ...`가 **undefined를 받으면
  "NaN원"**을 냈다 → `won(s: string | null | undefined)`로 넓히고 없는 값은 「—」를
  반환하게 고쳤다(NaverOps의 `won`과 같은 형태).
- **프론트 CAS가 옛 dist 배포를 거부해 prod는 안전했다** — 가드가 실수를 막았다.
- 교훈(#158): **프론트 타입 검증은 실제 빌드 명령(`npm run build`, 내부적으로
  `tsc -b`)으로 한다.** `tsc --noEmit`은 이 저장소의 프로젝트 참조 구성에서 실질적으로
  아무것도 검사하지 않아 거짓 초록을 낸다.

## 3. 확정된 결정사항
- **D-NAO-156 원가를 모르면 이익도 모른다 — «모름»은 0원이 아니다.** 원가·이익·이익률을
  `None`으로 표시(매출·수수료는 유지), «모름» 사유를 `unmapped`/`zero_cost`로 분리, 요약은
  계속 계산 + 배너 표면화.
  > Jino 원문(제동): *"나는 지금 SellC에서 스마트스토어 판매의 손익을 정확히 계산할 수 있도록
  > 작업중이었는데, 왜 또 이렇게 멀리 온거야?"*
- **D-NAO-157 원가 후보가 갈리면 고르지 않는다 + 이중 활성 매핑은 원천에서 막는다.**
  `ambiguous` 사유 신설, 업로드 경로에 클래시 가드 적용, `cost_price ge=0` 검증.
  > Jino 원문: *"근본 수정을 해야 하는거 아니야?"*
- 두 결정 모두 **트랙 파일 `docs/tracks/active/track_naver-ad-optimization.md`**에 D-NAO-156·157로
  전문 등재됨 — 요약이 아니라 그 파일이 정본.
- **D-NAO-158 (같은 날 밤 — Jino "통합 손익 대조원장도 같이 고쳐줘") 보존법칙이 걸린
  원장에서는 «모름»을 금액으로 표현하지 않는다 — 사실만 따로 싣고 화면이 비운다.**
  `_agg_marketplace_by_sku`의 원가 «모름»→0원 버그를 고치되, D-NAO-156과 달리 **금액은
  한 톨도 건드리지 않는다** — 이 원장은 `conservation_ok`(Σ SKU귀속+잔차==권위 엔진
  총계)가 걸려 있어 값을 빼면 보존법칙과 권위 엔진 정합이 동시에 깨진다. 대신 `cost_known`·
  `cost_unknown_revenue`(SKU 행) + `cost_unknown_skus`·`cost_unknown_revenue`·
  `cost_unknown_scoped`(요약) 필드로 «모른다는 사실»만 별도로 싣고, 표시층이 그 필드를
  보고 값을 비운다(집계 레이어 불변·표시 레이어만 «모름» 반영, 2단 구조).
  > Jino 원문: *"통합 손익 대조원장도 같이 고쳐줘"* (22:14)
  - 트랙 파일에 D-NAO-158로 전문 등재됨 — 요약이 아니라 그 파일이 정본.
  - **원칙(일반화)**: 합계 불변이 «모름» 표면화의 전제일 때가 있다 — 보존법칙이 걸린
    원장에서는 모르는 값을 계산에서 빼면 원장이 깨진다. 금액은 그대로 두고 "모른다는
    사실"만 별도 필드로 실어 표시층이 비우게 한다(교훈 #159).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/routers/naver_ops.py` | 상품 손익 요약 — `_cost_of_line`류 «모름» 판정(unmapped/zero_cost/ambiguous), 원가 후보 선택 로직 |
| `backend/app/routers/products.py` | `/api/products/upload` — 구형 엑셀 업로드, 이중 활성 매핑 클래시 가드 신설 |
| `backend/app/schemas.py` | `cost_price` `ge=0` 검증 |
| `frontend/src/pages/NaverOps.tsx` | 상품 손익 카드(원가·이익·이익률) + «모름» 배너, `ColFilter` 드롭다운(이월 이슈 있음, §5) |
| `backend/tests/test_naver_ops_cost_unknown.py` | «모름» 표면화·ambiguous 가드(11건 신규) |
| `backend/tests/test_products_upload_guards.py` | 업로드 클래시 가드·`cost_price` 검증(5건 신규) |
| `backend/app/services/product_pnl.py` | **통합 손익 대조원장** — `_agg_marketplace_by_sku`에 D-NAO-158 «모름» 표면화 적용 완료(`cost_known`·`cost_unknown_revenue`·`cost_unknown_scoped`), 금액 불변 |
| `frontend/src/pages/ProductConnectionMap.tsx` | 통합 손익 대조원장 화면 — `won()` undefined 가드(커밋 `1c747a1`), «모름» 배너 |
| `backend/tests/test_product_pnl.py` | D-NAO-158 가드 4건 신규(보존법칙 유지·미매핑 이중계상 없음·거짓경보 없음·계정 스코프) |

## 5. 알려진 이슈 / 주의사항 (이월)
- **D. ~~`product_pnl.py:219~235` `_agg_marketplace_by_sku`에 같은 «모름»→0 버그 클래스~~ —
  완료(D-NAO-158, 커밋 `c77a7df`).** 착수 전 추정한 라이브 영향 "네이버 1개 상품·30일
  44,500원"은 **틀렸다** — `product_channel_mapping` 조인으로 잰 값이었고, 손익 엔진이
  실제로 쓰는 `Order.product_id` 조인 경로로는 **17,800원**이다. 후자가 맞는 숫자다.
- **★신규 이월(기존 결함, 이번 세션이 만든 것 아님) — `coupang_1p/cost`·
  `coupang_1p/net_profit` 보존법칙이 이미 깨져 있다.** 그 결과 통합 손익 대조원장의
  모든 조회 창에서 `by_sku`가 **빈 배열**로 나와 SKU 손익 표가 통째로 안 뜬다.
  D-NAO-158 라이브 검증에서 확인한 것은 **배너·요약 필드까지**이고, **SKU 행의 「—」
  표시는 라이브로 보지 못했다(미검증)** — 원장이 보존법칙을 회복해야 행 표시를 볼 수
  있다. 다음 관문 = `coupang_1p` 보존법칙 복구(§6 최우선).
- **활성 매핑 2,766개 중 127개가 cost_price=0** — 팔리면 「—」로 뜬다. Jino 원가 입력 대기.
- **원가 미상 6개 상품(매핑 5·원가입력 1) — Jino 조치 대기**:
  - 매핑 필요: `13687558206`·`13687558207`·`13687558208`(폴드8·플립8 필름 4매입 3종)·
    `13676281943`·`12628761692`
  - 원가 입력 필요: `6891081964`(애플워치 스트랩)
- **`NaverOps.tsx` ColFilter 드롭다운**에서 `"—"`가 `Number("")=0`으로 파싱돼 숫자 목록 중간에
  섞인다(필터 기능 자체는 정상 동작 — 표시 순서만 어색).
- **codex 소급 교차 리뷰(08-09 이후)** — 이번 PR도 자체 적대 리뷰로 대체한 목록에 추가.
- **`CLAUDE.md`의 프론트 스탬프 경로가 옛것이다**(문서 드리프트, 기능은 정상). 문서는
  `frontend/dist/.deploy-stamp`라 적었지만 실제 정본은 prod 리포 루트의 **`.frontend-deploy-stamp`**
  (`safe_deploy.sh:118`, dist 안이면 rsync가 지운다 — dist 쪽은 레거시 폴백으로만 읽는다).
  이번 세션 라이브 확인: `commit=330a5f9…` 정상 기록 = **CAS는 살아 있다.** 직전 인계의
  "프론트 CAS 부트스트랩 구멍" 우려와 헷갈리지 말 것 — 스탬프가 없는 게 아니라 위치가 다르다.
- 이전 이월(직전 HANDOFF §6 그대로, 이번 세션에서 손대지 않음): 자정~02시 `pending` 라이브
  확인 · 당일 광고비 별도 관측 테이블 · 부분 적재 자동복구 · 「검색광고 전환매출」 0원 당일
  미집계 · 워크트리 60여 개 정리.

## 6. 다음에 할 작업 (미완료)
- [ ] **★최우선 — `coupang_1p` 보존법칙 복구**: `coupang_1p/cost`·`coupang_1p/net_profit`이
      깨져 있어 통합 손익 대조원장의 `by_sku`가 모든 창에서 빈 배열이다(§5). D-NAO-158이
      만든 «모름» 표시(SKU 행 「—」)는 이 관문을 넘어야 라이브로 볼 수 있다 — 손익 정확도
      축의 다음 단계.
- [ ] **Jino 데이터 입력 대기**: 원가 미상 6개 상품(매핑 5·원가 1) + cost_price=0인 127개 매핑 중
      실제로 팔리는 것부터.
- [ ] `NaverOps.tsx` ColFilter `"—"` 정렬 위치 정리(부수적, 우선순위 낮음).
- [ ] codex 소급 교차 리뷰(08-09 이후 쿼터 복구 시) — 이 PR #229 포함.
- [ ] 손익 정합 시리즈는 D-NAO-151로 이미 공식 종결 선언됐으나, 이번 세션이 새 결함
      클래스(«모름»→0, 후보 ambiguous)를 찾았으므로 **"종결 이후에도 같은 클래스 결함이 남아
      있을 수 있다"는 전제로 다음 세션 착수 시 목적을 다시 확인할 것**(교훈 #155).
- [ ] (이전 이월, 광고 축) 순위 서보 D-NAO-124 — 착수 여부는 Jino가 다음에 명시할 것. **먼저
      "지금 Jino의 목적이 손익 축인지 집행 축인지"를 확인한 뒤 착수할 것**(이번 세션 자체가
      그 확인 없이 진행하다 제동 걸린 사례).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_cost-unknown-not-zero+mapping-guards_20260806.md 읽고 이어서 작업해줘
