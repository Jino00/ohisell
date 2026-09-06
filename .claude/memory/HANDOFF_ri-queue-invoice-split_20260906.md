# HANDOFF — 확인요청함 금액에서 계산서 발행분 제외 (체인 `1p-계산서` n=5)

- 세션: `e3a9888e` · 2026-09-06 15:38 ~ 16:35 KST · 계정 jino.kim@theohi.com
- 목표이름: **1P계산서 목표** · 트랙: `docs/tracks/active/track_coupang-rocket-1p.md` (10/12 **불변**)
- 작업 위치: 워크트리 `~/.claude-worktrees/Ohiselling/1p-invoice-n5` · 브랜치 `feat/1p-invoice-n5`

## 검증 명령: `ssh sellc.ohitech.co.kr "curl -s localhost:8001/api/overview/rocket-ri-queue | python3 -c \"import json,sys;r=json.load(sys.stdin);print(r['live_no_invoice_count'],r['live_no_invoice_amount'],'|',r['live_invoiced_count'],r['live_invoiced_amount'],'|',r['live_count'],r['live_amount'])\""`
기대: `8 1498085 | 4 3038131 | 12 4536216` (숫자는 수집에 따라 변하지만 **앞 둘의 합 == 셋째**여야 한다)

## §0 목적 (Jino 원문 — 발명 금지)
> "여기 보면 확인피 필요한 발주번호와 금액이 적혀있어. 그런데, 그 발주번호를 보면 일부는 이미 계산서가 발행이 된 상태거든. 그래서, 여기에 보이는 금액은 이미 계산서가 발행된 금액은 제외하고 보여줬으면 좋겠어" (2026-09-06 15:20 KST)

Jino가 선택지에서 고른 처리 (15:3x): **"금액·건수는 빼고, 목록엔 따로 표시"**
— 제목은 8건 1,498,085원, 발행분 4건은 별도 구역으로 내려가되 **확인 버튼·복사줄 유지**.

## §1 ★라이브가 이 화면의 전제를 반증했다
「확인요청함」 상단 안내문 · `compute_rocket_ri_queue` docstring · 라우터 docstring · 모듈 헤더
**네 곳이 전부** *"RI PO의 입고금액은 이미 계산서가 나가 ④지급대기에 포함돼 있다"*고 **전건 단정**하고 있었다.

착수 실측(15:2x KST, prod `compute_rocket_ri_queue(db, None)`):

| | 건 | 입고액 |
|---|---|---|
| 라이브 전체 | 12 | 4,536,216원 |
| ├ 계산서 이미 발행 | **4** | **3,038,131원** |
| └ 계산서 없음 | **8** | **1,498,085원** |

그 단정은 **2026-08-27 하루(그날은 RI 12건 전건이 계산서 보유)의 우연을 규칙으로 굳힌 것**이었고,
그래서 제목 금액이 **「아직 계산서가 안 나간 8건의 돈까지 중복이니 무시하라」**로 읽혔다.

## §2 한 것
- 백엔드 `compute_rocket_ri_queue` — 라이브를 `live_no_invoice_*` / `live_invoiced_*`로 가른다.
  **가르는 자는 `has_invoice`(= `invoice_seqs` 비어 있지 않음) 하나뿐**이고 `_pipeline_rows`가 이미 내는 필드를
  백엔드·프론트가 **같이** 쓴다. `live_count`/`live_amount`는 두 덩어리의 합으로 남겨 **검산**에 쓴다.
  ★정산행 미수집(`invoice_rows_missing`)은 **발행된 쪽**이다 — 「미수집」이지 「미발행」이 아니다(원칙22).
- 프론트 `RiQueueTab` — 제목이 `live_no_invoice_*`. 발행분은 **지우지 않고** 「계산서 이미 발행」 구역으로.
  화면이 스스로 검산을 낸다(`data-testid="ri-live-split-checksum"`, **두 카드 밖**).
- 전건 단정 4곳 전부 정정.

## §3 적대 리뷰 — PASS(P1=0), 그러나 최대 산출은 SURVIVED 3종
변이 **16종**(13 KILLED / **3 SURVIVED**). 셋 다 같은 모양 — **만드는 층엔 못이 박혀 있는데 «닿는 층»엔 없다.**

- ★★**라우터 아래로는 아무도 안 보고 있었다** — 새 응답 키 4개를 통째로 `pop`해도 백엔드 **7,981건 전건 통과**.
  프론트는 `fetchRocketRiQueue`를 mock하니 거기서도 안 걸린다 ⇒ **화면 제목이 prod에서 `—건 · —원`으로
  죽어도 CI는 초록**이었다. 이 엔드포인트의 응답을 검증하는 테스트가 저장소에 **0건**이었다.
  ⇒ `backend/tests/test_rocket_ri_queue_router.py` 신설.
- 프론트 가르는 자를 `has_invoice`→`invoices.length`로 바꿔도 50건 전건 통과(백엔드엔 못이 박혀 있는데 프론트엔 없었다).
- 상단 배너를 옛 전건 단정으로 되돌려도 통과 — 유일한 단언이 **신·구 문장에 공통인 부분문자열**이었다.

★★**P2-1 = 내가 새로 쓴 카드 안내문이 이 PR이 죽이려던 병을 축소판으로 되살렸다.**
*"이 건들은 계산서가 이미 나가 ④지급 대기에 들어 있습니다"* — 또 전건 단정이다.
리뷰어 실측 반증: 정산행 미수집 건(**178,711원**)은 `_await_payment`가 세는 `{invoice_count:1, amount:2,749,231}`
**어디에도 없다**(④는 정산행이 있고 지급일이 안 지난 계산서만 센다). 같은 구멍이 「지급일이 이미 지난」 건에도 열린다.
⇒ 「계산서 번호가 이미 붙었습니다 / **④에 있다는 뜻은 아닙니다**」로 정정.

SURVIVED 3종 + 검산 절단 변이 1종을 **재주입해 전건 KILLED 확인**.
채택 P2 6건 · **이월 1건**(P2-8 픽스처가 실제 API 모양에서 멀어짐 — `poSeqCopyReachesTheUser`·`riConfirmReachesTheUser`).

## §4 착지 (완주)
- PR **#757** 머지 `55d9831e` — `safe_merge.sh` 경유, CI **3/3 실통과**, `--force` 미사용
- 백엔드 2파일 **무중단 배포**(:8011→:8001, 다운타임 **0초**) · 마이그 **0건**(prod head `anomw1s2a` 불변)
- 프론트 배포 **CAS 통과**, `--force-frontend` **미사용** · 스탬프 `55d9831e` · 번들 `assets/index-cwUkMgNE.js`
- 배포 전제 3/3: 클론 shallow **아님** · diff에 마이그 0건 · prod 스탬프 `ba72f1cb`가 내 HEAD의 조상
- 회귀: 백엔드 **7,983 passed** · 프론트 **1,489 passed**(102 파일) · `tsc --noEmit` 0 error
- 라이브(16:28 KST): `live_no_invoice 8건 1,498,085원` / `live_invoiced 4건 3,038,131원` / **검산 OK**
- 번들 문구 실측(python `str.count()` — minify라 `grep -c`는 언제나 1): 신규 8종 전건 존재, **옛 전건 단정 0회**

## §5 완료 QA (별도 기 · 읽기 전용 · 원문 그대로)
- **판정(앵커 합격기준): 달성** — *"5개 전부 API·번들 코드 근거로 확인됨. 단 3개 항목(제목·붙여넣기·신설 구역)은
  「화면에 뜬다」는 표현인데 실제 렌더 화면(basic auth 뒤)은 못 봤고 번들 코드+API 데이터 대조로만 확인했음을 병기"*
- **판정(Jino 지시 원문): 달성** — *"선택한 처리(금액·건수 빼고 목록엔 따로 표시, 확인 버튼 유지, 붙여넣기 줄 8/4 분리)가
  코드·API 양쪽에서 정확히 구현·라이브 반영됨"*
- ⇒ 종합 **달성** · **과잉·부실 주장 0건**
- ⚠️QA가 확인 못한 것: `/rocket-recon`을 **사람 눈으로 렌더링해 보지 못함**(nginx basic auth, 자격증명 입력 금지) ·
  번들 `built_at`과 스탬프 `ts`가 초 단위로 다름(원인 미규명) · 실제 파일명은 `.deploy-stamp`가 아니라 `.build-stamp`

## §6 다음 세션
1. **P2-8 픽스처 현실화** — `poSeqCopyReachesTheUser`·`riConfirmReachesTheUser`의 `riRow`가 `has_invoice`·`invoice_seqs`를
   안 담아 **부재(undefined→falsy)에 기대어 초록**이다. 백엔드가 `has_invoice` 송출을 멈춰도 두 파일은 구분 못 한다.
2. 트랙 **S5**(종합조망 로켓배송 뷰·원가 매핑 UI) — 이 트랙에서 유일하게 남은 기능 슬라이스, 세 세션째 「다음 후보」다.
3. n=4가 남긴 것: 변화 카드가 «직전 회차»만 노출 · 확인 성공이 원장에 당일 미반영 · 남은 RI를 Jino가 supplier 직접 처리.

## §7 이월 (전부 하네스 규율 소관 — 주기 구조 감사)
- `chain.sh`는 **주 워크트리 루트**(= iCloud 공유 메인 폴더, **2026-08-23 체크아웃**)의 등록부에 쓴다. 거기엔
  `1p-계산서.jsonl`이 아예 없어서 첫 호출이 **n=1 유령 등록부**를 새로 만들었다. 지우고 `--dir`로 다시 열어 n=5.
  ⇒ **이 저장소에서 `chain.sh`를 쓸 땐 `--dir "$PWD/.claude/memory/chains"`를 붙여라.**
- iCloud 공유 폴더에서 `git worktree add`가 `fatal: mmap failed`로 죽는다(오늘 3회 중 2회 실패, 3번째 성공).
  `git clone --no-local`은 2분 안에 안 끝난다.
- `review-surface-mutation.sh`가 **완료 QA 위임에 오발화**(QA는 읽기 전용이라 변이 주입 금지인데 훅은 위임문에
  인용된 「적대 리뷰」 낱말만 센다). **2026-08-28 [5a99b32c]에 이어 재현 2회째** — 계약 §6 계수 대상.
