# 세션 인수인계: 1P계산서 목표 — 열린 파이프라인·확인요청함
> 저장일시: 2026-08-27 21:4x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 체인 `1p-계산서` n=1 · 세션 `46832d30`

## 1. 프로젝트 위치 및 환경
- 저장소: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (공유 메인 폴더 — **이 세션은 미접촉**)
- 작업 위치: 워크트리 `~/.claude-worktrees/Ohiselling/1p-invoice-gap` (브랜치 `feat/1p-invoice-gap`, `origin/main` 1778a828 기준)
- prod: `sellc.ohitech.co.kr` — 백엔드 `/home/ubuntu/ohisell/backend` (활성 `:8011`), 프론트 `frontend/dist`
- 배포: `scripts/safe_deploy.sh` (직접 scp 금지) · 병합: `scripts/safe_merge.sh`
- 프론트 도구: 워크트리에 `node_modules` 심볼릭 링크 필요 —
  `ln -s /Users/jino/.ohisell-node-modules/ohiselling-frontend/node_modules <워크트리>/frontend/node_modules`
- 백엔드 테스트는 시스템 `python3`로 그냥 돈다(워크트리 venv 불필요): `cd backend && python3 -m pytest tests/ -q`
- prod 조회는 파일+scp(인라인 heredoc은 따옴표가 벗겨진다):
  `scp -q q.py sellc.ohitech.co.kr:/tmp/q.py && ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && .venv/bin/python - < /tmp/q.py"`

## 2. 이번 세션 완료 목록
- ✅ **계약 승인** — `docs/contracts/CONTRACT_1p_invoice_gap.md` (Jino 18:53 *"그래"*), 개정 1회(미발송 축 추가, 19:03 *"이것까지 넣어서 종합적으로 보여줘"* → 합격기준 13 → 15)
- ✅ **`backend/app/services/coupang/rocket_pipeline.py` 신설** — PO 그레인·창 없음. 칸 4개 분류(`_stage_of`)·소계·미해명·clamp·신선도·RI 큐·굳은 PO 날짜 목록
- ✅ **엔드포인트 4개** — `backend/app/routers/overview.py`에 `/rocket-pipeline`, `/rocket-pipeline/stage/{stage}`, `/rocket-ri-queue` · `backend/app/routers/coupang_ops.py`에 `/rocket/stale-open-po-dates`
- ✅ **`frontend/src/pages/rocketPipelineTabs.tsx` 신설**(515줄) — `PipelineTab`·`RiQueueTab`
- ✅ **`frontend/src/pages/RocketRecon.tsx`** — 탭 바 3개 신설, 기존 대사 화면을 첫 탭(`tab==="recon"`)으로 감쌈. 기본값 `"recon"`(기존 사용자 무손상)
- ✅ **`frontend/src/lib/api.ts`** — 타입 8종 + fetch 3종(`fetchRocketPipeline`·`fetchRocketPipelineStage`·`fetchRocketRiQueue`)
- ✅ **`tools/rocket_supplier_fetcher.py`** — `_po_query`에 명시 날짜 범위 추가, `_collect_stale_open_po_pages` 신설, `_do_run`에 배선, `STALE_OPEN_PO_DATES_PATH` 상수
- ✅ **테스트** — `backend/tests/test_rocket_pipeline.py` 26종 신규 · `frontend/src/pages/rocketPipelineTabs.test.tsx` 21종 신규
- ✅ **트랙 계약 헤더 부착** — `docs/tracks/active/track_coupang-rocket-1p.md` (프로즈 4/6 → 기계 판독 **10/12**)
- ✅ **적대 리뷰 1R PASS(P1 0)** → P2 3건 처분(2 채택 / 1 기각)
- ✅ **prod 무중단 배포**(다운타임 0초, 마이그레이션 0건) → **PR #507 병합**(CI 3/3 실통과)

### ★구현하다 잡은 결함 4건 (전부 조용히 틀릴 자리였다)
1. **시간대 규약이 한 테이블 안에서 섞여 있다.** `po_created_at`·`receiving_finished_at`=UTC naive(JSON `+00:00` 유래)인데 **`shipped_at`은 KST naive**(발주상세 DOM 셀 「발송일」 유래 — tz가 없어 `_to_dt_utc_naive`를 그대로 통과), `synced_at`도 KST(`rocket_supplier_sync.py:62,109`의 `kst_now()`). 근거는 분포 실측 — `shipped_at` 992건 중 **965건이 16시대**(16:00 발송 마감), `max(synced_at)` 16:19 > `datetime('now')` 10:01(UTC). 관행대로 +9h를 걸었더니 **마지막 수집일이 「내일」**로 나왔고, 그대로 갔으면 신선도 판정이 전건 오답이 되어 화면의 핵심 기능이 통째로 뒤집혔다. ⇒ 헬퍼를 `_kst_date_str`(UTC용)·`_kst_naive_date_str`/`_kst_window_naive`(KST용)로 갈랐다.
2. **입고는 발송의 하한인데 안 썼다.** ASN 라인 0건인데 전량 입고된 PO가 실재(123977085 161,100원 · 127009073 9,540원). `shipped=0`을 「안 보냄」으로 읽어 그 둘이 「영영 못 보내는 분」에 계상 ⇒ 225,840원/4건 → **55,200원/2건**.
3. **재훑기 필터가 RI를 빼고 있었다.** `SETTLED_STAGE_STATUSES`(={CI,RI})를 그대로 썼더니 이 기능을 만든 원인인 굳은 RI 8건이 정확히 그 필터에 걸려 사라졌다 ⇒ `!= "CI"`.
4. **기본 조회 범위 120일도 같은 병.** 그 8건이 2025-10~2026-01 발주라 통째로 잘렸다(실측: 120일=22개 날짜 중 4개·발주 8건 누락 / 400일=22개 전부·41건) ⇒ 기본 400일.

## 2-1. 완료 QA
> 별도 서브에이전트(Sonnet, 읽기 전용). **판정 원문 그대로** — 미달도 그대로.

- **작업 목적(정본 원문)**: 계약 §1 — *"Jino가 「8/20 이후 발송분 중 세금계산서 미발행 금액」이나 「발주받고 아직 발송 안 한 금액」을 물으면 지금은 세션이 prod DB를 수동 조회해야 답이 나온다. … 되면: `/rocket-recon`에서 ①발주가 돈이 되기까지의 열린 파이프라인 전 구간을 상시 확인하고 ②살아 있는 RI 건만 골라 볼 수 있다."*
- **합격기준(원문)**: 계약 §4 체크박스 15개 (S1 8 · S2 4 · S3 3)

### 판정 — 대조 3건 각각
- **판정(계약 §4): 부분달성** — S1 8/8 달성 · S2 3/4 달성(1개 미달) · S3 0/3(계약이 허용한 이월) (2026-08-27 21:31 KST)
- **판정(Jino 지시 원문): 부분달성** — ①미발행 내역 보기 달성 · ③종합 보기 달성 · ②는 **절반**(「모아서 볼 수 있나」 달성, 「**내가 누를 수 있게**」의 실행부는 산출물에 없다 — 계약 §1이 승인 시점에 스코프에서 뺐고 S3 정찰을 선행 조건으로 뒀다. 계약 위반은 아니나 원문 대비 미착수분) (21:31 KST)
- **판정(트랙 궁극 목표): 판정불능** — 트랙 목표는 「1P를 **종합조망(Command Center)**에 편입해 3P/RG와 나란히 매출·순이익을 본다」인데 이 작업은 `/rocket-recon` 내부의 **운영 정합성** 축이고 종합조망 파일은 diff에 0건. *"전진했다고 단정할 라이브 증거가 없다(방해하지도 않는다)."* (21:31 KST)
- **종합 판정: 부분달성**

### 항목별 (QA 원문 요약 — 명령 → 관측 → 판정)
**S1 8/8 달성** (전부 `curl http://127.0.0.1:8011/api/overview/...` 라이브, 2026-08-27 21:2x KST)
1. 탭 바 + 기존 화면 무손상 — `git diff`로 기존 블록이 로직 변경 없이 `tab==="recon"` 안으로 이동 확인 → 달성
2. 칸 4개 + 소계 — `stages` 4개 · `pre_invoice_subtotal.amount=25176910` 계약 부록 A-1과 **원 단위 일치** → 달성
3. 칸 클릭 → PO 목록 — `po_date/confirmed_amount/shipped_amount/received_amount/stage_amount/invoice_seqs` 전부 포함 → 달성(④는 계산서 그레인이라 설계상 비클릭)
4. 8/20~ 필터 → **`po_count=21, amount="9319638.00"`** 정확히 일치 → 달성
5. 부분 잔여 2건 — `139899792="9540.00"`, `140113364="161100.00"` → 달성
6. 미해명 별도 줄 — `{po_count:137, amount:"8939475.00", confirmed:false}` + 배포 번들에 「확정 아님 — 구별 불가」 리터럴 존재 → 달성
7. 최신/굳음 분리 + 배지 — `await_confirm.fresh=665315/stale=2242502(24건)` 등 + 배포 번들에 「이후 미확인」 존재 → 달성
8. clamp 자백 + 신선도 — `clamp.over_received={2, 170640}`, `freshness` 4필드 전부 값 있음 → 달성

**S2 3/4**
1. RI 목록 필드 → 달성 (12행)
2. 살아있음/굳음 구분 → 달성 (`is_stale`로 4/8 분리, 화면도 **다른 Card 섹션**으로 물리 분리)
3. **미종결 PO 재수집 → 미달**. QA 원문: *"백엔드 엔드포인트는 작동(`dates:[22개], po_count:41` — RP24+PA9+RI8=41 계약 부록과 일치)하고 `tools/rocket_supplier_fetcher.py`에 `_collect_stale_open_po_pages`가 `_do_run` 정규 흐름에 배선됨(코드 확인). **그러나 RI의 8건은 지금도 `synced_date="2026-08-05"` 그대로**(당일 미갱신, CI 전이도 없음) — Mac 페처가 이 세션 중 실행되지 않아 **화면에서 관측되는 결과가 아직 없다**"* → **코드는 배선됐으나 라이브 관측이 안 됨(격리 성공≠충분조건)**
4. 예시 행 139791428 → 달성 (단 QA 주: *"이번 세션의 재수집 신규 효과라기보다 기존 신선분의 재확인"*)

**S3 0/3** — `docs/references/*ri_confirm_recon*` 없음, 관련 커밋 0. 계약 §4 S3 전제(*"Jino Mac이 깨어 있어야 실행 가능 — 불가 시 S3만 이월한다"*)·§1·§7이 명시적으로 허용 ⇒ **감점 대상 아님**, 이월로 기록.

- **미달·미판정 항목**: S2-3(재수집 라이브 미관측) · S3 3항목(계약 허용 이월) · 트랙 궁극 목표 판정불능
- **목적 전환 여부**: 없음(`🔁 목적 전환` 선언 0건). 계약 개정 1회는 Jino 발의(*"이것까지 넣어서 종합적으로 보여줘"*)로 §4에 2항목 추가한 것이고 목표 자체는 불변.
- **QA가 확인 못 한 것(원문)**: 브라우저 직접 렌더링 미확인(배포 번들 문자열 grep + 소스 로직 + 라이브 API 교차검증으로 대체 — 런타임 JS 에러는 이 방법으로 못 잡는다) · QA 시점 백엔드 CI가 IN_PROGRESS였다(★그 뒤 3/3 pass로 확인됨, 아래 2-3) · Mac 페처 실행은 QA 권한 밖 · 적대 리뷰 판정 자체는 재검증 안 함(위임대로 재사용)

## 2-2. 트랙 진행률
- **트랙**: `docs/tracks/active/track_coupang-rocket-1p.md`
- **트랙 목표 원문**: `"우리가 지금까지 Ofix에서 한 일을 OhiTech에서도 구현할 수 있어? 물론 OhiTech는 로켓배송이 추가되지" / "발주한 금액, 납품한 공급가, 정산 금액을 모두 봐야지. 매출은 쿠팡이 발주한 금액이 될꺼고" / "광고비용이 빠지겠지?"`
- **진행률**: 세션 시작 **(헤더 없음 — 프로즈 「4/6」)** → 종료 **10/12**
  - 달성: S1·S2·S3·S4·S4.5a·S4.5b·S4.5c·M1·M2·M3
  - 미달: **S5**(프론트 — 종합조망 로켓배송 뷰/축 + 갱신 버튼 + 원가 매핑 관리 UI + 커버리지% 배지) · **S6**(prod 라이브 self-verify + 적대 리뷰 + 배포)
  - ★숫자가 바뀐 건 **진전이 아니라 계량 방식 전환**이다(자유 % → 체크박스 기계 판독). 본문 체크리스트 12항목을 그대로 옮겼고 항목을 발명하지 않았다.
- **이번 세션이 움직인 항목**: **없음.** S5는 「종합조망 뷰·원가 매핑 UI·커버리지 배지」라 이번에 만든 `/rocket-recon` 탭과 대상이 다르다. 진전 없음도 유효한 기록이다.
- **헤더에 남긴 확인 줄**: `확인: 2026-08-27 21:0x KST [46832d30] — 계약 헤더 신설(lazy 부착). … 진행률 10/12 불변. 산출: 커밋 4c036ebc·442e3984.`
- **다음 세션 후보 항목**: 이 트랙 기준으로는 **S5**(사유: 유일하게 남은 기능 슬라이스, S6은 그 뒤). 단 **체인 `1p-계산서`의 다음 슬라이스는 S3 정찰**이고 그건 트랙 항목이 아니다 — 둘을 섞지 말 것.
- **트랙 종결 여부**: 미도달(10/12)

## 2-3. 착지
- **완료 단계**: 커밋 → push → PR → 적대 리뷰 → 머지 → **완주**
- **멈춘 단계**: 없음
- **재개 명령**: 해당 없음
- **좌표**: 커밋 `4c036ebc` · `442e3984` · `ec81ba04` · `242d7ecd` · 병합커밋 `4e76c87c`(origin/main 흡수) → PR **#507** → 머지 **`7f7c43a7`**
- **리뷰 판정**: **PASS(P1 0)** — 변이 12종 중 11 KILLED. ★**표면 절단 변이 2종(잔여 금액 셀 렌더 제거 · 굳음 배지 렌더 제거) 모두 KILLED** ⇒ 교훈 #362 재발 없음. 살아남은 1종(M2)이 P2-1을 지목 → 채택.
- **CI**: 3/3 실통과 (backend py3.10 11m25s · py3.14 10m3s · frontend 1m32s), `--force` 미사용
- **배포**: 백엔드 무중단(다운타임 0초, 활성 `:8011`, CAS 3/3, **마이그레이션 0건**) · 프론트(백업 `dist_backup_20260827_2124`, 빌드 `dirty=0`)
- **L5**: 로컬 `main`이 공유 메인 폴더에 잡혀 있어 「main에 세워둔다」 **생략**

## 3. 확정된 결정사항
- **칸끼리 금액이 안 겹친다.** 소계는 ①②③뿐. ④지급대기는 **계산서 그레인**이라 더하면 이중계상. RI(확인요청함)도 이미 ④에 포함 ⇒ 파이프라인 합계에 **넣지 않는다**.
- **미종결(RP·PA) / 종결단계(RI·CI)를 가른다.** 기준은 기존 `rocket_recon.SETTLED_STAGE_STATUSES`(전수 실측 정의)를 그대로 쓴다. 종결단계의 발송>입고는 **미해명**(`confirmed:false`)이고 **소계 합산은 금지선**.
- **입고는 발송의 하한이다** — 「안 보낸 양」의 자는 `max(발송, 입고)`. 이 규칙은 `_pipeline_rows`의 `effective_ship` **한 곳에서만** 정의한다.
- **모르는 상태 코드는 «판정 불가»** — `KNOWN_STATUSES`(RP/PA/RI/CI) 밖은 어느 칸에도 안 넣고 `unknown_status`로 센다.
- **신선도 기준선은 「오늘」이 아니라 「마지막 수집일」** — 수집이 버튼-only(D-17)라 매일 안 돈다.
- **재훑기 대상 날짜는 백엔드가 정한다** — 페처가 정하면 판정이 두 곳에 생겨 갈라진다. 발주일 범위 필터만 쓴다(발주번호 배열·상태 필터는 값 형식 미검증 → 틀리면 조용히 빈 결과).
- **쓰기는 이 계약에서 뺐다** — 「거래명세서확인」 전이의 주체가 미상이라 S3 정찰이 선행 조건.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/contracts/CONTRACT_1p_invoice_gap.md` | **정본 계약** — §4 합격기준 15개, 부록 A에 착수 실측 전부 |
| `backend/app/services/coupang/rocket_pipeline.py` | 파이프라인 집계 Harness (칸 분류·미해명·clamp·RI 큐·굳은 날짜) |
| `backend/app/routers/overview.py` | `/rocket-pipeline`, `/stage/{stage}`, `/rocket-ri-queue` |
| `backend/app/routers/coupang_ops.py` | `/rocket/stale-open-po-dates` |
| `frontend/src/pages/rocketPipelineTabs.tsx` | 탭 둘의 화면 전부 |
| `frontend/src/pages/RocketRecon.tsx` | 탭 바 + 기존 대사 화면 |
| `tools/rocket_supplier_fetcher.py` | `_collect_stale_open_po_pages` (굳은 미종결 발주 재훑기) |
| `backend/tests/test_rocket_pipeline.py` · `frontend/src/pages/rocketPipelineTabs.test.tsx` | 신규 26 + 21종 |
| `docs/tracks/active/track_coupang-rocket-1p.md` | 트랙 — 계약 헤더 신설(10/12) |

## 5. 알려진 이슈 / 주의사항
- ★**`shipped_at`·`synced_at`은 KST 저장, `po_created_at`·`receiving_finished_at`은 UTC 저장.** 이 저장소의 다른 코드에서도 같은 함정이 있을 수 있다. `_to_dt_utc_naive`는 **tz가 있을 때만** 환산하므로 이름을 믿지 말고 **원천이 JSON인지 DOM인지**로 판단할 것.
- **`coupang_rocket_settlement_item`(계산서 라인) 수집이 2026-08-06에 멈춰 있다.** 라인 단위 대조는 원리적으로 불가 — PO헤더↔계산서헤더 축만 쓴다.
- **미해명 8,939,475원은 확정 숫자가 아니다.** 덜 보냄·반송·진짜 미수금이 구별 불가로 섞여 있고, 가르는 열쇠(발주상세 「입고 메세지」·「회송 정보」·「변경 이력」)를 수집하지 않는다. 2026-08-05에 이 값만 믿고 5,763,290원 과대계상한 전례.
- **`/api/overview/rocket-pipeline/stage/await_payment`는 400이다** — ④는 계산서 그레인이라 PO 목록이 없다(프론트도 비클릭). 「계산서 목록 보기」 요구가 나오면 여기가 출발점.
- 워크트리에 `node_modules`가 없다 — 위 §1의 심볼릭 링크 필요.
- 착수 시 훅이 띄운 `⛔ 살아 있는 세션` 2줄(pao-논의 n=49 · sellc-원가-메뉴 n=2)은 **전부 오탐**이었다(공유 메인 폴더가 origin/main보다 크게 뒤짐). 생존 판정은 `git show origin/main:.claude/memory/chains/<체인>.jsonl`로 할 것.

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문)**: Jino 2026-08-27 — *"그리고 거래명세서확인요청 내용을 SellC에서 모아서 볼 수 있나? **이걸 모아서 내가 누를 수 있게 해줘**"* / (딥링크인지 직접 처리인지 물었을 때) *"딥링크는 뭐야? **내가 원하는건 SellC에서 눌러서 처리하는거야**"*
- **남은 슬라이스**:
- [ ] **S3 — 쓰기 정찰 (1순위, 묻지 말고 진행)**. 전제: Jino Mac이 깨어 있고 supplier 로그인 유효. 발주상세 라이브 DOM에서 ①「거래명세서확인」 전이의 **주체**(벤더 버튼인가 쿠팡 자동인가) ②버튼이면 **메서드·엔드포인트·페이로드**를 판정해 `docs/references/NN_ri_confirm_recon_*.md`로 남기고, 「후속 계약이 성립 가능한가」 한 줄 판정을 **채팅 화면**에 보고. ★**정찰은 관찰과 기존 GET만 — 확인 버튼을 실제로 누르지 않는다**(계약 §3 금지선). "벤더 버튼이 아니다"도 유효한 종결.
- [ ] **S2-3 라이브 재관측 (S3와 같이 하면 된다 — Mac이 깨어 있어야 하는 것이 같다)**. 「발주 갱신」 버튼 1회 → 확인요청함 탭에서 굳은 8건의 `synced_date`가 당일로 갱신되거나 상태가 CI로 전이되는지 확인. 백엔드 `/rocket/stale-open-po-dates`는 이미 22개 날짜·41건을 정상 응답한다.
- [ ] (S3 결과가 「벤더 버튼」이면) **쓰기 실행 계약 초안** — `_write_guard.py:26-84`의 `guarded_write` 패턴(dry_run 기본 + `CONFIRM_LIVE_WRITE` 토큰 + WARNING 감사 로그)을 백엔드 게이트로 두고 Mac 페처는 실행기로만. **쓰기 명령에 자동 재시도 금지**(`refresh_contract`의 lease 재시도는 읽기용 설계라 같은 확인을 두 번 누른다). 되돌릴 수 없는 회계 확정이라 **Jino 승인 지점**.
- [ ] (트랙 별건) **S5** — 종합조망 로켓배송 뷰/축 + 갱신 버튼 + 원가 매핑 관리 UI + 커버리지% 배지. 트랙 10/12를 움직이는 유일한 남은 기능 슬라이스. ★체인 `1p-계산서`의 슬라이스와 **다른 것**이니 섞지 말 것.

★**3문 검사 결과**: S3·S2-3은 ①목표·범위를 안 바꾸고 ②되돌릴 수 없지 않으며(읽기 전용 정찰·수집) ⇒ **묻지 말고 진행**. 쓰기 실행 계약만 §1 승인 지점(되돌릴 수 없는 회계 확정 + 계약 「안 함」 변경).

## 7. 새 세션 시작 프롬프트
```
/session-relay 1p-계산서
```
또는:
```
.claude/memory/HANDOFF_1p-invoice-pipeline_20260827.md 읽고 이어서 작업해줘
```
