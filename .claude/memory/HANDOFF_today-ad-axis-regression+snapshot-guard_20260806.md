# 세션 인수인계: today-ad-axis-regression + snapshot-guard
> 저장일시: 2026-08-06 20:5x (KST) · 트랙: 네이버 SA 광고 최적화
> 새 대화 시작 시 이 파일을 먼저 읽을 것. 직전 인계는 `HANDOFF_frontend-refetch+deploy-cas_20260806.md`(같은 날 오후).

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
  (루트는 **main 고정** — 브랜치 작업은 `.claude/worktrees/`)
- prod: `https://sellc.ohitech.co.kr` · 서버 `sellc.ohitech.co.kr:/home/ubuntu/ohisell`
- 배포: **`scripts/safe_deploy.sh` 만** (백엔드 `--restart` / 프론트 `--frontend`, 프론트는 빌드가 스탬프를 심는다)
- 테스트: `cd backend && python3 -m pytest tests/ -q` (현재 **4916 passed**)
- ★prod에서 원클릭 조회 시 주의: 라우터 prefix는 `/api/naver/ops`(하이픈 아님). 경로는 추측 말고
  `grep -n 'APIRouter(prefix' backend/app/routers/<파일>.py`로 확인(`failures.jsonl` 기록됨).

## 2. 이번 세션 완료 목록
**PR #227 병합**(`c178a24`) · prod 배포·라이브 검증 완료. 커밋 3개(`084526b`·`a86f239`·`9f08ebf`).

- ✅ **★「오늘」 광고비 합산 축을 관측 최대치로** — `backend/app/routers/naver_ops.py`
  **조사 중 prod가 이익을 108,268원 과대 표시하고 있는 것을 발견했다.** NAVER `/stats` 당일 누적이
  **뒤로 간다**(아래 §5 실측). 손익 쿼리가 「최신 배치 합」을 써서 후퇴값을 확정으로 냈다.
  → **캠페인별 당일 최대 누적 합**(`ad_basis.basis="day_max"`). 후퇴는 조용히 보정하지 않고
  `regressed_by`·`latest_cost`로 응답에 실어 **호박색 배너**로 표면화.
  배포 전 `ad_spend=398,102 profit=334,961.64` → 배포 후 `506,370 / 226,693.64`.
- ✅ **`pending` — 새벽 0은 «0원»이 아니라 «모름»** — 당일 어느 슬롯에도 값이 없으면 pending.
  pending이면 **광고비·이익·이익률 값 자체가 「—」**(색도 안 칠함). 원인·시간대는 단정하지 않는다.
- ✅ **시간별 스냅샷 수동 실행 가드** — `hourly_snapshot.py`·`scheduler_service.py`·`routers/scheduler.py`
  같은 (날짜,시각) 슬롯에 **값이 있으면** skip + 사유(API 호출 전). **전부 0인 슬롯은 「있음」으로
  세지 않는다.** 잡 반환값을 트리거 응답에 실어 skip을 구분해 보여준다.
- ✅ **자정 경계 회귀 차단**(이 세션이 만든 것) — fetch 후 `kst_today()` 재확인 → 바뀌면 적재 취소.
- ✅ **부분 적재 관측** — `partial`·`campaigns_expected` + 경고 로그(자동 복구는 이월).
- ✅ **`scripts/next_ids.sh` 교훈 번호 정규식**(3번째 이월 해소) — 139에서 멈추던 것. 제목 형식이
  `## 139.`/`## #140` 둘인데 하나만 봤다. + 형식 드리프트 감지 + 3자리 상한 접근 경고.
- ✅ **적대 리뷰 3렌즈**(회귀·표면·스크립트, codex 쿼터 소진 대체) — **P1 5건 전부 반영**.
- ✅ D-NAO-154·155 · 교훈 #153·#154 · `failures.jsonl` 2건.

## 3. 확정된 결정사항
- **「오늘」 광고비 = 캠페인별 당일 관측 최대 누적의 합**(D-NAO-154). 최신 배치가 아니다 —
  누적은 정의상 뒤로 갈 수 없고, 원천은 실제로 뒤로 간다. 부분 적재에도 강하다.
  하루가 끝나면 최대치=최종 누적이라 PR #225의 축 교차검증(675,090 vs 675,089)은 그대로 성립.
- **후퇴는 조용히 보정하지 않는다** — `regressed_by`로 화면이 말한다. 조용히 고치면 광고 리포트
  화면(`NaverAdReport`의 clamped 배너)과 숫자가 갈릴 때 답할 근거가 없다.
- **광고비를 모르면 이익도 모른다** — pending이면 세 카드 다 「—」. 값을 0으로 두고 부설명만
  부정하면 한 카드가 자기를 부정한다(훑는 눈에는 큰 숫자가 이긴다).
- **가드의 근거는 페이싱 차분 수학이지 갱신 주기가 아니다**(D-NAO-155). 순서를 거꾸로 두면
  갱신 주기가 바뀌는 순간 근거가 사라진다 — 실제로 이 세션에서 갱신 주기 서술이 뒤집혔다.
- **`misfire_grace_time` 3600 유지**(리뷰 지적 기각) — 줄이면 늦은 실행이 아예 안 돌아 슬롯을 잃는다.
  데이터 오염은 날짜 가드로 막는다.
- **`force`는 코드 경로 전용**(HTTP 미노출). 프로덕션 호출자는 없다 = 부분 적재 복구 경로가 없다는 뜻.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/routers/naver_ops.py` | `sales_summary` — days=0 광고비 축(`ad_basis`: basis·pending·regressed_by·latest_cost) |
| `backend/app/services/naver_ad/hourly_snapshot.py` | 매시 :05 당일 누적 수집 + **슬롯 가드**·날짜 가드·partial 관측 |
| `backend/app/services/naver_ad/hourly_pacing.py` | 슬롯 **차분**으로 증분 — 가드가 지키는 대상. `clamped`가 누적 감소를 센다 |
| `backend/app/routers/scheduler.py` | 트리거 — 잡 반환 dict를 응답에 싣는다(skip 구분) |
| `frontend/src/pages/NaverOps.tsx` | 광고비·이익·이익률 카드 + 후퇴 배너. `adPending`·`adRegressedBy` |
| `frontend/src/components/SchedulerStatus.tsx` | 「지금 실행」 결과 한 줄(시각 병기·160자 절단·spinner 레이스 수정) |
| `scripts/next_ids.sh` | D-NAO·교훈 번호. 두 제목 형식 + 드리프트 감지 |
| `backend/tests/test_naver_ops_today_ad_spend.py` | 후퇴·부분적재·pending 가드(13건) |
| `backend/tests/test_naver_ad_pipeline.py` | 스냅샷 가드·전부0 재채움·자정경계·partial(9건) |

## 5. 알려진 이슈 / 주의사항
- ★★**NAVER `/stats` 당일 누적은 후퇴한다** — 4분 간격 16회 관측(08-06 19:26~20:29):
  19:26~20:00 9회 **506,370**(clk 456·imp 66,760) → 20:04~20:25 6회 **398,102**(clk 366·imp 53,836,
  **17시 슬롯과 원 단위까지 동일 = 3시간 전 상태**) → 20:29 **543,125** 회복·전진.
  최근 14일에 같은 후퇴 **14건**. 진행 중 지표에 단조성을 가정하지 말 것.
  ★**회복도 확인됐다(21:05:29)**: 21시 슬롯 580,878원 적재 → `regressed_by=0` → 배너 소멸.
  즉 후퇴는 약 25분짜리 일시 구간이고, 관측 최대치 방식이 그 구간을 정확히 메웠다.
- ★**"당일치는 시간 단위로 갱신된다"는 서술을 철회했다** — 관측 9회의 결론이었고 16회에서
  반증됐다(같은 20시 안에서 두 번 바뀌고 한 번은 뒤로). 주기를 주장하려면 한 주기를 넘겨 관측할 것.
- ★**가드의 비용이 실측으로 보였다**: NAVER가 20:29에 543,125원을 주는데 21:05 크론까지 화면은
  506,370원이다(최대 59분 뒤처짐). 신선도를 올리려면 **스냅샷과 별개의 관측 테이블**이 필요하고
  그건 스키마 변경이라 별 계약이다(`UniqueConstraint(ad_date, campaign_id, snapshot_hour)` 때문에
  같은 시각에 두 관측을 담을 수 없다).
- **공식 문서엔 갱신 주기·지연 언급이 없다** — 공식 스웨거
  `naver.github.io/searchad-apidoc/assets/json/ncc-report.json`의 `/api/stats`를 직접 읽어 확인.
  화면 문구에 "실측"을 붙이는 근거.
- **`pending` 경로는 라이브 실물을 못 봤다** — 값이 있는 시각엔 재현 불가. **자정~02시에 확인 가능**
  (그때 광고비·이익·이익률이 「—」로 뜨고 문구가 "미집계인지 실제 0원인지 구분 불가"여야 한다).
  단위 테스트로만 고정된 상태다.
- **워크트리가 60개 넘게 쌓여 있다** — 직전 인계가 지목한 `guard-r2`·`runpreview`·`today-ad`는
  이미 없다(정리됨). 대부분 `worktree-agent-*`·`claude/*`로 병합 완료 브랜치. 일괄 정리 전
  각각 `git rev-list --count origin/main..<브랜치>`로 미병합 커밋을 확인할 것.
- 화면 상단 빨간 배너 **"ofix 판매분석 갱신 실패 · 로그인 필요"** — **Jino가 이 세션 스코프에서
  명시적으로 제외**했다("이건 스마트스토어 업무가 아니야. 이건 너가 하지 마").

## 6. 다음에 할 작업 (미완료)
- [ ] **★1순위 = 순위 서보 D-NAO-124 착수 (Jino 지시 2026-08-06 "순위 서보부터 하자")**
      - 선행조건 실측 완료 → **`docs/references/48_rank_servo_prerequisite_measurement_20260806.md` 를 먼저 읽을 것.** ①②③ 충족·착수 가능
      - ★관문은 곡선 적립이 아니라 **전환 표본 희소성**: 사용가능 그룹 **121개**, 그룹당 순위구간별 2~4 관측 → **개별 그룹 곡선 불가**, 풀링/축소 필수
      - 적립된 곡선은 **입찰→순위**(`bid_rank_slope` 56유닛)이고 필요한 것은 **순위→이익**이다 — 다른 곡선이다
      - 이음매 후보: `launch_target_rank` 학습 메트릭이 이미 있어 `rank_servo`가 무개조 소비 가능
      - 설계=Fable(구조), 구현=Opus/Sonnet. **D-NAO-124 설계는 확정이므로 변형 금지**(트랙 금지선)
- [ ] **자정~02시에 `pending` 라이브 확인**(이번 세션의 유일한 미검증 합격기준)
- [x] ~~21:05 이후 `regressed_by` 회복 확인~~ **완료(21:05:29 라이브)**: `ad_spend=580,878 · regressed_by=0.00 · latest=580,878`
      → 배너 소멸. **후퇴 → 관측 최대치 유지 → 회복 → 배너 소멸** 한 바퀴가 라이브에서 전부 확인됐다.
- [ ] 당일 광고비 신선도용 **별도 관측 테이블**(스키마 변경 → 별 계약). 가드는 최대 59분치를 버린다
- [ ] 부분 적재 자동 복구 경로(`force`에 프로덕션 호출자 0. 라이브 빈도는 0 — 7일 185슬롯 전부 46행)
- [ ] 「검색광고 전환매출」 0원도 당일 미집계를 확정처럼 낸다(전환은 D+1) — 이번 원칙의 미적용 케이스
- [ ] skip이 `last_run_at`·`last_status="ok"`를 전진시킴 — skip ⟹ 슬롯 존재라 거짓 초록은 아니나
      워치독(`WATCHDOG_JOBS`) 편입 시 재검토
- [ ] `next_ids.sh` 3자리 상한({1,3}) — #1000부터 같은 조용한 스테일. 900 접근 경고만 넣어둠
- [ ] (이전 이월) `auto_sync_orders` 253초 무로그 · 프론트 CAS 부트스트랩 구멍(prod에 스탬프 없으면 통과)
- [ ] (이전 이월) codex 쿼터 복구(~08-09) 후 자체 리뷰로 대체한 PR들의 소급 교차 검토 여부 — Jino 판단
- [ ] 워크트리 60여 개 정리 여부 판단

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_today-ad-axis-regression+snapshot-guard_20260806.md 읽고 이어서 작업해줘
