# 세션 인수인계: 원가 드리프트 검사기 배선 + prod 디스크 회수

> 저장 2026-08-10 21:0x KST · 트랙: **쿠팡 손익 정합** (`docs/tracks/active/track_coupang-promo-pnl.md`)
> **상태: PR #275 병합 완료(`503a88a`) · prod 배포·라이브 검증 끝 · 디스크 정리 완료.** 다시 적용하지 말 것.
> 선행: `HANDOFF_cost-truth-and-audit-closed_20260810.md`(오전, D-CPP-30) — 이 세션은 그 §7 이월 「검사기를 크론·CI에 배선」을 닫은 것이다.

## 1. 프로젝트 위치 및 환경

- 루트는 **main 고정**, 작업은 워크트리. 이번 워크트리: `.claude/worktrees/cost-drift-wiring`(브랜치 `claude/cost-drift-wiring`, 병합됨 — 정리해도 된다)
- prod: `ssh sellc.ohitech.co.kr` · 앱 `/home/ubuntu/ohisell` · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 배포: `scripts/safe_deploy.sh` (직접 scp 금지) · 병합: `scripts/safe_merge.sh` (`gh pr merge` 직접 호출 금지, `--force` 금지)
- 프론트 개발: `cd frontend && npm run dev` · 테스트 `npm run test` · 타입 `npx tsc -b --force`
- 백엔드 테스트: `cd backend && python3 -m pytest -q` (약 2분 40초, 현재 5,173+ passed)

### ⚠️ 이 세션에서 실제로 당한 것들

- **백엔드 포트를 하드코딩하지 마라.** 블루-그린이라 배포마다 8001↔8011이 바뀐다. 이 세션 중에도
  8001 → 8011(내 배포) → **8001**(다른 세션 재배포)로 두 번 움직였다. 늘 찾아서 쓴다:
  ```bash
  PORT=$(for P in 8001 8011; do [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:$P/api/health)" = 200 ] && echo $P && break; done)
  ```
- **`npx tsc --noEmit`은 아무것도 검사하지 않는다**(루트 tsconfig가 `files: []` + references). `npx tsc -b --force`를 쓴다.
- **새 워크트리는 `npm ci`부터** — 안 하면 vitest가 `vite`를 못 찾고 죽는다.
- **병행 세션이 매우 활발하다.** CI를 기다리는 6분 사이 main이 두 번 앞서가 병합이 거부됐다.
  `safe_merge.sh`가 CONFLICTING을 잡아 줬다 — 그때마다 fetch·merge·push·재시도로 풀었다(`--force` 안 씀).
- **번호도 두 번 겹쳤다.** 교훈 #216~#218을 병행 세션이 선점 → 내 것을 **#219~#223으로 뒤로 재번호**
  (main이 트렁크, 본문 불변). `scripts/next_ids.sh`를 써도 «커밋 직전»에 다시 확인해야 한다.
- **Python 3.10에선 f-string 안에 같은 따옴표를 못 넣는다** — ssh 힙독에서 `f"{x["k"]}"`가 SyntaxError.
- **TestClient는 다른 스레드에서 라우트를 돈다** → 인메모리 SQLite 픽스처에 `StaticPool` +
  `check_same_thread=False`가 **필수**다. 없으면 «no such table»이 난다.

## 2. 이번 세션 완료 목록

### A. 원가 드리프트 검사기 배선 (D-CPP-33, PR #275)

- ✅ **신설** `backend/app/services/cost_truth_audit.py` — 순수 판정 SA(`load_truth`/`classify`/`classify_rows`/`count_verdicts`/`summarize_drift`). **DB·SQLAlchemy 미임포트**라 venv 없이도 임포트된다
- ✅ **이사** 정본 스냅샷 `docs/references/data/` → **`backend/app/data/cost_truth_20260807.json`**
  (prod엔 git 체크아웃도 `docs/`도 없어 종전 경로로는 **배포 자체가 불가능**했다)
- ✅ **수정** `backend/app/services/scheduler_health.py` — `compute_scheduler_health`가 `product_master`를 읽어 판정 → `build_health(cost_drift=...)`. `healthy` 판정에 포함. **fail-soft**(기존 `data_stale`·`disk_low`와 같은 규약)
- ✅ **수정** `backend/app/schemas.py` — `SchedulerHealthOut.cost_drift: dict | None = None` ← **이게 없어서 응답이 지워지고 있었다(§5)**
- ✅ **수정** `frontend/src/components/Layout.tsx` — `buildPipelineHealthBanner`에 원가 분기(`count > 0`일 때만) + **`disk_low` 분기 신설**
- ✅ **수정** `frontend/src/lib/api.ts` — `SchedulerHealthCostDrift`·`SchedulerHealthDiskLow` 타입 신설
- ✅ **수정** `scripts/audit_cost_buffer.py` — 산술을 직접 구현하지 않고 위 SA를 **임포트**(사본 두 벌 금지)
- ✅ **신설** `backend/tests/test_cost_drift_wiring.py` **10건** — 순수 2 · 실 DB 배선 5 · **HTTP 경계 2** · 휴면 가드 1
- ✅ **추가** `frontend/src/components/pipelineHealthBanner.test.ts` — 원가 5건 + 디스크 2건
- ✅ 문서: `docs/references/54_...md` **§9 신설**(배선 근거·배포 체크리스트·1R/2R 리뷰 처분·§9-1 디스크·§9-2 라이브 합격기준)

### B. prod 디스크 회수 (Jino 승인)

- ✅ `/tmp`의 `ohisell.db` 사본 **7개 삭제**(`final.db`·`now.db`·`snap3.db`·`snap2.db`·`snap.db`·`t2.db`·`mig_test.db`, 합 11.9GB)
- ✅ 디스크 **95% → 83%**, 여유 **5.3GB → 17GB**, `/tmp` 12GB → 206MB
- ✅ 삭제 전 7개 각각 `lsof` 열림 핸들 0 확인 + ref 54 복원점이 `/tmp` 밖임을 확인

### C. 기록

- ✅ 트랙에 **D-CPP-33** · 교훈 **#219~#223** · `claude-progress.txt` 갱신
- ✅ 메모리: `health-banner-shows-what-judgment-includes.md` 신설 · `cost-truth-is-the-excel-not-the-db.md`의 「크론·CI 미배선」 경고를 해소로 정정

## 3. 확정된 결정사항 (번복 금지)

- **D-CPP-33 — 감시 장치는 «판정이 맞을 때»가 아니라 «사람 눈에 닿을 때» 완성된다.**
  검사기를 **크론이 아니라 앱 안(요청 시점 계산)**에 넣는다:
  ```
  product_master → compute_scheduler_health → build_health
                → GET /api/scheduler/health (`cost_drift`) → 전역 파이프라인 헬스 배너
  ```
  근거(prod 실측): 기존 ohisell 크론 셋 중 **둘이 출력을 `/dev/null`로 버린다**. 크론+로그는
  「아무도 안 부른다」를 「아무도 안 읽는다」로 옮길 뿐이다. 그리고 크론 결과 파일은 그 자체가
  낡을 수 있는데 **«낡음»과 «이상 없음»은 같은 모양**이다(교훈 #123의 재판).
- **판정 산술은 한 벌**(`cost_truth_audit.py`)이고 CLI가 임포트한다. 사본 둘이면 한쪽만 고쳐져
  감시자가 감시 대상보다 낡는다.
- **정본 스냅샷은 `backend/app/data/`에 산다** — 도구의 근거 파일은 «배포되는 자리»에 둔다.
- **원가 0원은 드리프트가 아니다.** `cost_price`는 모델·prod DDL 둘 다 `NOT NULL`이라 «미입력»이
  0원으로 들어오고 `undetermined`로 센다. 드리프트에 합치면 배너가 상시 켜져 **진짜 복귀를 가린다.**
- **배너는 건수 + 버퍼 계열만** 쓴다(한 줄이라 셋을 다 넣으면 드리프트가 묻힌다).
  세 갈래 전부는 API 응답이나 CLI로 본다.
- **`disk_low` 배너 분기 신설**(Jino 승인) — 판정 목록과 표시 목록이 갈라져 있었다.

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `backend/app/services/cost_truth_audit.py` | **순수 판정 SA** — 이 모듈이 산술의 단일 출처다 |
| `backend/app/data/cost_truth_20260807.json` | 정본 스냅샷 69항목(출처 파일명·sha `7ed336b4c55ea71b` 박음) |
| `backend/app/services/scheduler_health.py` | I/O 경계 + `build_health` 순수 판정. `cost_drift` 주입 |
| `backend/app/schemas.py` `SchedulerHealthOut` | ★**여기 필드가 없으면 응답에서 지워진다** |
| `backend/tests/test_cost_drift_wiring.py` | 배선 가드 10건(HTTP 경계 포함) |
| `frontend/src/components/Layout.tsx` `buildPipelineHealthBanner` | 배너 문구(원가·디스크) |
| `frontend/src/components/pipelineHealthBanner.test.ts` | 배너 가드 14건 |
| `scripts/audit_cost_buffer.py` | CLI — 임의 DB 파일(백업본) 점검·종료 코드가 필요할 때 |
| `docs/references/54_cost_truth_and_link_audit_closed_20260810.md` §9 | 배선 근거·배포 체크리스트·리뷰 처분 |

## 5. 알려진 이슈 / 주의사항

### ★★적대 리뷰 1R FAIL — 배선을 만들면서 **HTTP 경계에서 스스로 끊었다**

라우터가 `response_model=SchedulerHealthOut`인데 그 스키마에 `cost_drift`가 없어
**FastAPI가 응답에서 조용히 지웠다.** 서비스층 dict엔 있고 HTTP body엔 없어 배너가 `null` →
드리프트가 있어도 화면은 「이상 없음」. **내 배선 테스트 6건이 하나도 안 죽었다**
(`compute_scheduler_health`의 dict까지만 보고 경계를 안 넘었다).
그 파일 헤더에 내가 인용해 둔 교훈 #208을 **그 파일 자신이 재현했다.**

→ **헬스 응답에 무엇을 추가하든 짝지어야 할 것 넷**: ①`build_health`의 `healthy` 판정
②반환 dict ③**`schemas.py`의 `SchedulerHealthOut`** ④`Layout.tsx` 배너 분기 + 프론트 타입.
그리고 테스트는 **HTTP body를 단언**해야 한다(dict까지만 보면 못 본다).

### 알고 감수한 것

- **드리프트 0건과 «스냅샷이 없어 판정 못 함»이 응답상 같다**(둘 다 `cost_drift: null`).
  fail-soft가 헬스 API 전체를 살리는 대가다. **로그가 유일한 구분자다.**
  → 그래서 배포 시 **스냅샷 JSON을 반드시 같이 올린다**(ref 54 §9 체크리스트).
- **`.filter(cost_price.isnot(None))`는 도달 불가 코드다**(prod DDL이 `NOT NULL`, NULL 행 0건).
  지우지 않고 남기되 «전제가 바뀌면 우는» 휴면 가드 테스트를 옆에 뒀다. **«테스트했다»고 말하지 않는다.**
- **캐시 없음** — 매 헬스 요청마다 스냅샷 재파싱 + `product_master` 949행 스캔. 실측 13ms
  (최악 26ms) · 폴링 5분이라 수용. 캐시를 넣으면 «낡은 판정» 문제를 새로 만든다.

### 디스크

- 지금 **83% / 여유 17GB**로 여유롭지만, **12GB가 AI 세션의 작업 흔적이었다**(prod DB 1.7GB 사본 7개).
  ★**서버에서 DB 사본을 뜨면 지울 시점을 정하고 작업 끝에 확인할 것.**
- `tmpfiles` 규칙에 나이 조건이 없어 `/tmp`는 **자동으로 안 지워진다**(서버 48일 무중단).

## 6. 다음에 할 작업 (미완료)

- [ ] ★**원가표 시스템화(D-CPP-31)** — Jino: *"엑셀은 아무래도 불안하잖아"*. Jino가 순서를 정했다:
      *"진행중이던 작업 마무리 하고 이어서 하자"* → **그 작업이 끝났으므로 이게 다음이다.**
      ★**부자재 구성까지** 옮겨야 필름 단가 변경이 자동 전파된다(원가표는 목록이 아니라 **계산기**다).
      DB 스키마 + 화면이 생기므로 **착수 전 계약 1장 + Jino 승인 1회** 필요.
- [ ] **매핑 엑셀 업로드 시 버퍼 경고** — 탐지(다음 헬스 조회)보다 강한 **예방** 지점.
      `product_mapping_ingest.py`의 `IntegrityReport`에 얹으면 업로드 직후 사람이 본다. 미착수.
- [ ] **원가 매핑 엑셀 v4 재생성** — v3는 08-07 시점. 그걸 올리면 177건이 버퍼로 되돌아간다
      (이제 배너가 잡지만, 되돌아간 뒤에 잡는다)
- [ ] **`OHI-TGLASS-IP17PRO` 12기종 뭉침** — 30일 판매 1,287개, 원가표에 없는 3,500원
- [ ] **`no_bridge` 76건** — 쿠팡 `externalVendorSku` 입력(그중 10건은 실제 판매 중)
- [ ] **정본 파서를 I·C·E열로 확장** — 판정 불가 459 중 **최소 75건이 대조 가능한데 「모름」**
- [ ] **디스크 후속(선택)**: `ohisell.db.bak-lease-202607271852`(1.4GB, 07-27자 잔재) ·
      `ohisell_before_costbuffer_20260810_122857.db`(1.7GB, **ref 54 §6 복원점** — 되돌릴 일 없다고
      판단되면) · `.pm2/logs` 627MB 로테이션 없음(정지 프로세스 로그도 잔존)
- [ ] 승계(D-CPP-28/29): `excluded_order_amount` 화면 미표시 · `excluded→confirmed` 시 사유 소실 ·
      일별 축이 «광고만 쓴 날»을 행으로 표현 못 함 · `ad_uncosted` 라벨 vs 판별자 어긋남
- [ ] 승계: Z폴드8 3종 **8/16 재측정**(프로모션 686180 종료) · 오픽스 RG 매출이 손익 엔진 밖 ·
      파이프 정체 2건(오픽스 정산 07-28 · WING2 vendor-summary 07-26) ·
      네이버 대행사 남은 결정 3건(`01. 갤럭시_지문방지_TPU` 적자 등)

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_cost-drift-wiring+disk-recovery_20260810.md 읽고 이어서 작업해줘
```
