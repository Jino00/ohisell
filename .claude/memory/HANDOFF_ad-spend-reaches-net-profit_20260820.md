# 세션 인수인계: D-22 광고비가 순이익에 닿는다 (로켓1P 계산서 축 누출 수리)
> 저장일시: 2026-08-20 (prod deploy-manifest 기준 22:07~22:09Z)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (공유 main 폴더 — **작업은 워크트리에서**)
- 이번 작업 워크트리: `~/.claude-worktrees/Ohiselling/rocket-ad-in-net` (브랜치 `claude/rocket-1p-ad-in-net`)
- 실행: 백엔드 `backend/` FastAPI · 프론트 `cd frontend && npm run dev`
- 검증 명령: 백엔드 `cd backend && python3 -m pytest tests -q` · 프론트 **`npm run build`**(★`tsc --noEmit -p tsconfig.json`은 이 repo에서 0개 파일을 검사한다 — 교훈 #326) · `npm test`
- URL: prod `https://sellc.ohitech.co.kr` (Basic Auth 자격증명 `~/.ohisell_prod_auth`)
- prod DB: `ssh sellc.ohitech.co.kr` → `/home/ubuntu/ohisell/backend/ohisell.db` (★인라인 heredoc은 따옴표가 벗겨진다 — `ssh host 'bash -s' < 파일`)
- 배포: **반드시** `scripts/safe_deploy.sh` (이번엔 마이그레이션 없어 `--migrate` 불필요)

## 2. 이번 세션 완료 목록
- ✅ **원인 규명** — `backend/app/services/profit_calculator.py` `group_summary_by_company`가 자식의 `ad_spend`는 무조건 부모에 더하는데 `_add_net()`은 `net_profit=None` 자식을 건너뛴다. 로켓1P가 계산서 축에서 net=None이라 **광고비 597,888원(8/18)이 광고비 칸에만 남고 순이익 어디에서도 안 빠졌다.**
- ✅ `profit_calculator.py` — `net_contribution(row, revenue)` 신설(집계층 단일 판정), `_add_net`이 이를 사용, `_agg_block`에 `unmapped_revenue`·`net_floor_ad` 추가, `_finalize`가 `net_scope`/`net_floor_ad`/`net_basis_revenue`/`unmapped_revenue` 방출. 상수 `NET_SCOPE_FULL|AD_ONLY|PARTIAL`.
- ✅ `backend/app/services/coupang/rocket_1p_channel_pnl.py` — 계산서 축에서 `net = -광고비`(scope=ad_only, 분모 0). 광고비 0 이하면 종전대로 `None`. 판매 축은 `unmapped_revenue = revenue − revenue_costed`.
- ✅ `backend/app/routers/dashboard.py` — `_channel_rows()`·`_kpi_totals()` 신설. **`/kpi`가 요약표와 같은 경로를 쓰고 `rocket_basis`를 받는다**(종전엔 `calculate_daily_trend`를 따로 돌아 로켓1P를 통째로 제외).
- ✅ `backend/app/schemas.py` — `GroupedSummaryRow`에 4필드, `DashboardKPI`에 2필드(경고 주석 포함).
- ✅ `frontend/src/lib/netScope.ts`(신규) — 행 뱃지 판정 순수 함수. `Dashboard.tsx`가 사용.
- ✅ `frontend/src/components/RocketBasisToggle.tsx` — 라벨 「판매」→「판매(납품가)」, 계산서 축 하한 자백 뱃지.
- ✅ `frontend/src/lib/api.ts`·`Dashboard.tsx` — 타입 추가, KPI에 `rocket_basis` 전달, 행 뱃지 렌더.
- ✅ 테스트 신규 — backend `test_ad_spend_reaches_net_profit.py`(7건)·`test_dashboard_net_scope_http.py`(3건, **HTTP 경계**)·음수 광고비 1건 = 11건 / frontend `netScope.test.ts` 9건.
- ✅ 적대 리뷰 1R FAIL(P1 2) → 처분 → 2R **PASS**(P1 0). 변이 누적 14종, 생존 0.
- ✅ 배포 — 백엔드 무중단(다운타임 0초) `64b0d705`, 프론트 `844e0314`(백업 `dist_backup_20260820_0708`).
- ✅ **PR #313** 생성 (OPEN·미병합).
- ✅ 문서 — 트랙 D-22 · `docs/TRACKS.md` · `claude-progress.txt` · 교훈 **#328·#329·#330** · `failures.jsonl` 1줄.

## 2-1. 완료 QA
- **작업 목적(정본 원문, 앵커 `목표:`)**: 대시보드에서 「광고비는 합계에 올라가는데 순이익에서는 안 빠지는」 구조를 없앤다 — 두 매출 축(계산서/판매=납품가) 어느 쪽을 골라도 광고비가 순이익에 반영되고 카드·표·행이 서로 검산된다
- **합격기준(원문)**: prod 라이브 8/18 — ⓐ settlement 축 로켓 leaf net=-597888 / 오하이테크 -411062.64 / 전체 -28253.13 ⓑ 전체 profit_rate=-1.39 ⓒ sales 축 전체 순이익 1,083,231 불변 ⓓ /api/dashboard/kpi(settlement)의 순이익이 ⓐ 전체와 일치 ⓔ 화면 자사몰 행에 「원가 미상 64.9%」 경고 ⓕ 축 토글이 「계산서/판매(납품가)」로 표시 + 계산서 축 안내문
- **판정**: **달성** — ⓐ~ⓕ 전항목 라이브 API·배포 번들 대조 일치, 「안 함」 6개 항목 위반 0건, 배포 신선도 확인됨 (별도 Sonnet QA 기, 읽기 전용 / 관측 22:10~22:13Z)
- **항목별(QA 원문 요약)**:
  - ⓐ `curl .../channel-breakdown?...rocket_basis=settlement` → 로켓 leaf `-597888.00` / 오하이테크 `-411062.6363…` / 전체 `-28253.1275…` → **달성**
  - ⓑ 같은 응답 total `profit_rate: "-1.39"` → **달성**
  - ⓒ `...rocket_basis=sales` → total `1083231.145145…` → **달성**
  - ⓓ `curl .../kpi?...rocket_basis=settlement` → `-28253.1275…` = ⓐ 전체와 동일값 → **달성**
  - ⓔ API 자사몰 leaf `unmapped_revenue 180000 / product_revenue 277300` = **64.911%** + 배포 번들(`assets/index-Dw03HjaM.js`)에 렌더 함수 실물 확인 → **달성**. ※브라우저 실렌더는 안 함(인증정보 URL 노출 회피) — 코드+데이터 대조로 판정.
  - ⓕ 번들에 `["settlement","계산서",…],["sales","판매(납품가)",…]` + 계산서 축 하한 안내 뱃지 확인 → **달성**
- **미달·미판정 항목**: 없음
- **목적 전환 여부**: 없음 (`🔁 목적 전환` 선언 0건)

## 3. 확정된 결정사항
- **D-22 / Jino 원문(2026-08-19)**: *"3안처럼 2가지 옵션을 보여주자. 한 옵션은 계산서축, 다른 한 옵션은 우리 손익 (납품가 축)으로. 여기에 광고비는 비용으로 나간거기 때문에 계산서축에서도 적용이 되어야 해"* / 원가 미상 매출은 *"행 단위 경고 표시"*.
- **하한 판정은 집계층 한 곳(`net_contribution`)에만 둔다** — producer마다 기억하게 두면 한 곳만 빠져도 샌다(1R P1-1이 수동매출 분기에서 같은 결함을 찾았다).
- **이익률 분모는 「손익을 실제로 잰 매출」**(`net_basis_revenue`)이지 표의 총매출이 아니다.
- **KPI 카드와 요약표는 같은 백엔드 경로를 쓴다** — 경로가 둘이면 언젠가 갈라진다.
- 두 축은 여전히 **택일**(더하면 이중계상) · 원가 미상 매출에 추정 원가를 넣지 않는다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/profit_calculator.py` | `net_contribution`(하한 단일 판정)·`_add_net`·`_finalize`·`group_summary_by_company` |
| `backend/app/services/coupang/rocket_1p_channel_pnl.py` | 로켓1P 평행 엔진 — 계산서 축 하한, 판매 축 정식 손익 |
| `backend/app/routers/dashboard.py` | `_channel_rows`(카드·표 단일 경로)·`_kpi_totals`·`/kpi`·`/channel-breakdown` |
| `backend/app/schemas.py` | `GroupedSummaryRow`·`DashboardKPI` — ★필드 삭제 시 화면 경고가 통째로 사라진다 |
| `frontend/src/lib/netScope.ts` | 행 뱃지 판정(순수 함수 — 테스트가 붙는 곳) |
| `frontend/src/components/RocketBasisToggle.tsx` | 축 토글 + 하한 자백 뱃지 |
| `backend/tests/test_dashboard_net_scope_http.py` | HTTP 경계 — `response_model`이 필드를 지우는지 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **PR #312(D-NAO-207)가 미병합인 채 prod 프론트에 배포돼 있었다.** 이번 프론트 배포 시 CAS가 거부했고, 규칙대로 `feat/dnao207-product-ad-allocation`을 **병합·재빌드**해 올렸다(그냥 덮으면 그 세션 `NaverOps.tsx` 화면이 지워진다). 그래서 **PR #313이 그 브랜치를 포함**한다 — **PR #312를 먼저 병합하는 것이 순서상 맞다.**
- ⚠️ 적대 리뷰를 위임했으면 **그 워크트리는 리뷰어 것이다.** 같은 워크트리에서 내 pytest를 돌렸다가 리뷰어의 변이를 읽어 실패 25건+ 무더기가 났다(`failures.jsonl` 2026-08-20). 검증은 `git worktree add --detach`로 격리.
- `/kpi`와 `/channel-breakdown`이 프론트에서 병렬 호출인데 동기화 쿨다운 락은 하나만 통과시킨다 → 쿨다운 만료 직후 첫 로드에서 두 응답의 **동기화 세대**가 갈릴 수 있다(값 계산 규칙은 이제 같은 함수라 동일).
- 광고비가 음수(환급·크레딧)로 `ad_costs`에 실제 기록되는지는 **미확인**. 가드와 테스트는 넣었다.
- 세션 훅 KST 표기와 prod deploy-manifest UTC가 약 7시간 어긋난다 — 시각을 인용할 땐 **prod 자체 시계**를 쓸 것.
- CAFE24는 고객 무료배송이라 배송비 수입 0이 정상(`_delivery_income` 문서화된 설계). 8/18 cafe24 `orders.shipping_cost` 9,500원은 매출이 아니라 우리 비용이다.
- 기존 무관 실패 1건: `test_vendor_item_axis.py::test_health_route_actually_returns_conservation` — base `c7fa81fc`에도 재현되는 별건.

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문)**: (D-22 자체는 종결됐다. 아래는 파생 항목이며 새 작업 단위다.)
- **남은 슬라이스**:
- [ ] **PR #312 병합 → 그 뒤 PR #313 병합** (순서 중요 — main에 없는 것이 prod에 살아 있는 상태를 먼저 해소)
- [ ] Jino 몫: 자사몰 「개인결제창 180,000원」 상품 원가 연결 — 연결되면 자사몰 이익률 67.4%가 실제값으로 내려간다
- [ ] Jino 결정 대기(어제 이월): 「배송손익」 열 신설 — 배송매출 201,818 vs 물류비 249,878 = −48,060원이 이익에 섞여 안 보인다
- [ ] 선택: `/kpi`·`/channel-breakdown` 동기화 세대 정합(현재는 값 규칙만 통일)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ad-spend-reaches-net-profit_20260820.md 읽고 이어서 작업해줘
