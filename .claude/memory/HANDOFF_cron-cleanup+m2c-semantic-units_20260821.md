# 세션 인수인계: 크론 정리(D-NAO-219) + M2-c 의미 단위 판정층(D-NAO-220)
> 저장일시: 2026-08-21 19:0x KST · 체인 「PAO 논의 **29**」 (세션 `20532846`)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (**main, 미푸시 0**)
- prod: **`sellc.ohitech.co.kr`** — ★ssh 별칭은 **반드시 FQDN**
- prod DB: `ssh sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db \"<SQL>\""`
- ★**prod에서 앱 코드를 읽기 전용으로 돌리는 법**(이번에 확정 — 라이브 판정층 실측에 필수):
  `ssh sellc.ohitech.co.kr 'cd /home/ubuntu/ohisell/backend && set -a; . .env 2>/dev/null; set +a; PYTHONPATH=/home/ubuntu/ohisell/backend ./.venv/bin/python3 <script>'`
  ⚠️`/home/ubuntu/ohisell/venv`는 **없다**. 인터프리터는 `backend/.venv/bin/python3`(pm2 `pm_exec_path`로 확인).
  ⚠️**prod엔 `tests/`가 배포되지 않는다** — 전건 테스트는 로컬/워크트리에서만 가능(이번 QA의 ⑥ 판정불능 사유).
- 배포: `scripts/safe_deploy.sh <파일…> [--migrate] --restart` / 병합: `scripts/safe_merge.sh <PR> [--force]`
- **prod 마이그 head = `m2b2devw1eight`**(불변 — 이번 세션 스키마 변경 0건) · **디스크 94%**(여유 6.2G) · DB 2.5G

## 2. 이번 세션 완료 목록
- ✅ 체인 등록부 `n=29` append
- ✅ **착수 필독 실측**(Sonnet·읽기 전용) — 유령 0건 / **부분오류 4건**(§5-1)
- ✅ **D-NAO-219 — 크론 정리 1차**: 후보 3건 중 **1건만 정지**. 근거 `docs/references/86_cron_consumption_audit_20260821.md`
- ✅ **D-NAO-220 — M2-c**: 의미 단위 판정층 + 쇼핑 제외 pending 배선. PR **#319**(`a3cdf39d`)·**#320**(`4fda6b73`) 병합·배포. 근거 `docs/references/87_m2c_semantic_units_live_20260821.md`
- ✅ 완료 QA **2건 × 3대조 = 판정 6줄**(§2-1)
- ✅ 커밋 6건 · **전부 push 완료**(미푸시 0) · main 위

## 2-1. 완료 QA (판정 원문 그대로 — 미달·판정불능 포함)

### 작업 목적(정본 원문 — 트랙 계약 헤더 `목표:`)
*"무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야. 이게 우리가 만든 MOP프로그램의 최종 목적이고 목표야."* (Jino 2026-07-19 · D-NAO-59)

### ① 크론 정리 (앵커 보존본 `.claude/anchors/20532846-…--cron-cleanup-closed.md`)
- **판정(트랙 궁극 목표): 미달** — QA 원문: *"총이익 최대화라는 궁극 목표에 이 세션이 기여한 관측 가능 증거가 없다(양수도 음수도 아닌 순수 0). 「판정불능」이 아니라 「미달」로 적는다 — 증거에 못 닿은 게 아니라 증거가 «없다는 것»이 명확히 관측됐기 때문이다."*
- **판정(Jino 지시 원문 "M2-b 배포 → 크론 정리"): 달성** — 두 부분 라이브 확인.
- **판정(앵커 합격 ⓐ~ⓓ): 달성** — 넷 다 QA 독립 재확인.
- 침범 0건(소급 불가 4잡 전부 `is_enabled=true` 라이브 확인).

### ② M2-c
- **판정(트랙 궁극 목표): 부분달성** — QA 원문: *"언젠가 쓸 수 있는 판정층은 생겼지만 「총이익이 늘었다」는 아직 성립하지 않는다"*(계정 전체 `auto_operate=0`이라 의사결정 변화 **0건**).
- **판정(계약 §4 S1 ③⑤⑥): 부분달성** — ③**달성**(prod 독립 재현) · ⑤**판정불능**(관측 시점 미도래) · ⑥**판정불능**(QA가 재실행 못 함 — prod에 `tests/` 미배포·워크트리 venv 부재. **「실행 안 됨」이지 「발견 0건」이 아니다**).
- **판정(계약 §6 완료 정의): 부분달성** — 「ⓒ 산출 0이면 0으로 기록」 **위반 없음**(산출 10건, 신규 상수 2개는 **생성 상한이지 판정 게이트가 아님**을 QA가 코드로 확인).
- 「안 함」·금지선 침범 **0건** · 목적 전환 **없음**.
- **QA 미확인 4건**: ⑥ 재실행 불가 · ⑤ 시점 미도래 · 변이 독립 재현 안 함 · **`search_term` rejected 125→141(18:3x→18:5x) 원인 미조사**.

## 2-2. 트랙 진행률
- **트랙**: `docs/tracks/active/track_naver-ad-optimization.md`
- **트랙 목표 원문**: §2-1의 작업 목적과 동일(D-NAO-59)
- **진행률**: 시작 **2/7** → 종료 **2/7** — 달성 M0·M1 / 미달 M2·M3·M4·M5·M6
- **이번 세션이 움직인 항목**: **없음**(M2의 슬라이스 1개 + 트랙 밖 크론 정리). ★**M2 체크박스는 안 찍었다** — M2 = ref 65 S1 ①~⑥ + S2 ①~⑤ 전체인데 닫힌 것은 a·b·b2·**c**뿐이고 **S1 ①②④⑥·S2 전부**가 남았다. 증거: PR #319 `a3cdf39d` · PR #320 `4fda6b73` · ref 86·87
- **확인 줄**: 4건 누적(16:1x 착수 / 16:3x 크론 완료 / 16:3x 크론 QA / 18:4x M2-c 완료 / 18:5x M2-c QA)
- **트랙 종결 여부**: **미도달**(2/7)

## 3. 확정된 결정사항
- **D-NAO-219** — 크론 정리 1차: `sync_naver_criterion`(10:37) **정지**. 유지 2건은 사유 병기.
  - ★**판단 기준은 「의미 있나」가 아니라 「나중에 되찾을 수 있나」**다.
- **D-NAO-220** — M2-c 배선. 계약 §6 슬라이스(새 승인 지점 아님 — M2 계약은 D-NAO-214로 승인됨).
  - ★**구현이 발명한 상수 2개**(`_SS_SHOPPING_EXCLUDE_CAP`·`_SS_SEMANTIC_EXCLUDE_CAP`, 각 20) **사후 등재**. **직전 D-NAO-217과 같은 패턴의 재발**이다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/references/86_cron_consumption_audit_20260821.md` | 크론 5경로 재검증 + 라이브 증거 + **되돌리는 명령** |
| `docs/references/87_m2c_semantic_units_live_20260821.md` | M2-c 라이브 실측 + 리뷰 3라운드 + **내일 08:50 기준선** |
| `backend/app/services/naver_ad/semantic_units.py` | 신규 — 최장일치 분절(`build_vocab`·`segment`·`build_index`) |
| `backend/app/services/naver_ad/search_term_judge.py` | `judge_semantic_units()` 신설 · `judge_search_terms` 반환에 `semantic_units` 키만 추가(기존 4키 불변) |
| `backend/app/services/naver_ad/search_term_ss_lane.py` | 674 옛 전제 제거 · `_has_open_or_executed`에 `target_type` 필수 인자 · 의미 단위 경로만 `_has_valid_adgroup` |
| `docs/PLAN_naver-m2-l2-wiring.md` | **M2 계약 정본**(D-NAO-214). §4 합격기준=ref 65 원문 · §6 슬라이스 a/b/b2/**c**/d/z |
| `.claude/anchors/20532846-…--cron-cleanup-closed.md` | 크론 정리 앵커(판정 3줄 보존) |
| `.claude/anchors/20532846-…md` | M2-c 앵커(판정 3줄 + 이월) |

## 5. 알려진 이슈 / 주의사항
1. **인계 주장 부분오류 4건**(착수 실측): ①크론 총계 56→**57** ②끄기 후보 `conversion_maturity_snapshot`은 **크론 잡이 아니다**(테이블명) ③워크트리 `m2a-pooling` **부재** ④"main clean"은 틀림.
2. ★★**`is_enabled`는 런타임 게이트가 아니다** — `start_scheduler()`(:2498) 등록 시점과 catch-up(:2255)에서만 검사한다. **DB 직접 UPDATE는 화면만 정지로 보이고 APScheduler는 재시작 전까지 계속 발화**한다. ⇒ **크론 on/off는 언제나 `PUT /api/scheduler/toggle/{job_id}`.** 그리고 응답의 `live_registered:null`은 **정지 여부를 말해주지 않는다** — `/api/scheduler/status`의 `next_run_time`으로 따로 재라.
3. ★**`naver_criterion_daily`는 롤링 창이 아니다** — 367일치(2025-08-19~2026-08-20)가 밀도 그대로 순증했다. `CRITERION_RETENTION_DAYS=365` 상수가 있는데 purge가 안 돈다(미확인). **정지는 순증만 멈추고 디스크(496MB)는 안 줄인다.**
4. ★**정지 되돌리기에 시한이 있다** — 3일 넘는 정지는 자동으로 안 메워지고 **365일 넘기면 영구 소실**. 명령은 ref 86 §3.
5. ★★**격리 성공은 충분조건이 아니다 — 이번 슬라이스에서 두 번 나왔다**: ①적대 리뷰가 워크트리에 prod 데이터가 없어 `auto_operate` 스코프 결함을 「설계대로」로 판정 ②QA가 prod에 `tests/`가 없어 ⑥을 재실행 못 함.
6. ★**M2-c 수확 지대의 정체**: 화이트리스트를 재적용해도 산출이 0이 아니었다 — 「사생활」(517개 검색어)이 통과한 이유는 화이트리스트 토큰이 **「보호필름」**이라서다. ⇒ **상품명의 부분어인데 화이트리스트와 절단면이 다른 것**이 살아남는다.
7. ★**ref 65 예언 적중** — 잔여에 **「액정」이 3개 그룹 상위**(사전 구멍이 실제로 드러났다). 「23」·「25」는 숫자 조각(사전이 숫자를 배제하는데 잔여엔 남는다).
8. ★**CI 빨강은 결제 정지**(`steps=0`·2~4초 — 이번에 두 PR 모두 직접 확인). 병합은 `--force`, 자백은 `$TMPDIR/safe_merge.log`. ⚠️**확인 없이 `--force` 습관이 붙으면 진짜 빨간불도 같은 손짓으로 지나간다.**
9. ⚠️**`git checkout --`은 커밋 안 된 다른 수정을 통째로 지운다** — 구현자가 변이 원복 중 실제로 P1 수정을 날렸다(하네스 알림으로 발견·재작업). 변이 검증은 **메모리 백업/재기록**으로.
10. ⚠️`_build_whitelist`(search_term_judge)와 `_tokens_from`(semantic_units)이 **토큰화 규칙을 두 파일에 중복 정의**한다. P1-1의 「잔여 no-op」 불변식이 **이 둘의 동기화에 의존**한다 — 한쪽만 고치면 조용히 깨진다.
11. **다음 구조 감사 트리거 = 08-25 이후**(마지막 `docs/references/69_audit_pao_drift_20260818.md`)

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문)** — 트랙 계약 헤더 `목표:` 줄 그대로:
  *"무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야. 이게 우리가 만든 MOP프로그램의 최종 목적이고 목표야."*
  이번 칸 = **M2 = L2 배선**(ref 65 S1+S2), 계약 `docs/PLAN_naver-m2-l2-wiring.md`(승인됨).
- **남은 슬라이스**: **M2-d**(08-28 이후) · **M2-z** · S1 잔여(①②④⑥)

- [ ] ①**★내일 아침 관측 4건 — 묻지 말고 진행.** 시각이 와야 판정 가능해서 못 한 것들이다.
      **07:35** `naver_entity.pc_bid_weight IS NOT NULL` 행수(기대 ≈871/1,013) — **M2-b2 합격 ① 판정불능 해소**(오늘 실측 0건 확인)
      **08:12** `sweep_naver_adgroup_criterion` `last_run_at`·`last_status`(★**무인** 첫 발화 — 오늘도 13:36:49 수동값 그대로)
      **08:25** `naver_search_term_exclusion.match_type IS NOT NULL` 행수 + 분포(오늘도 0/3,990)
      **08:50** ★★**M2-c 합격 ⑤ — 이번 세션의 유일한 미판정 항목.** `run_naver_auto_operator_daily` 발화 후
        `SELECT COUNT(*) FROM naver_proposals WHERE target_type='search_term_semantic';` → **기대 ≥1건**(오늘 기준선 **0건**)
        + `status='pending'` ∧ `approval_source IS NULL` + `naver_change_log`에 해당 실행 행 **0건**
        ⚠️**`ss_exclude`∧approved의 「0건」은 쇼핑발로 범위를 좁혀 세라** — 선행 파워링크 1건(`created_at=2026-07-22`)이 있어 원 카운트면 **거짓 미달**이 난다
- [ ] ②**M2-d** — S2 전체. **진입 조건 = M2-a 배포 +7일(2026-08-28 이후) & 추정치 지속 생성 관측.** 그 전엔 원리적으로 판정 불가
- [ ] ③**M2-z** — M2 종결 QA(별도 Sonnet·읽기 전용, S1 ①~⑥·S2 ①~⑤ **11항목 전수** 라이브 대조). **트랙 M2 체크박스는 이 판정으로만**
- [ ] ④**S1 잔여 ①②④⑥ 처분** — M2-z 전에 무엇이 남았는지 확정할 것(①=bidWeight 판독은 M2-b에서 부분 달성, ②④는 M2-a에서 달성 주장, ⑥은 회귀)
- [ ] ⑤**크론 정리 2차(선택)** — 이번에 유지한 2건은 사유가 확정됐다. 남은 후보는 `run_naver_learning_loops`의 **나머지 4개 루프**(`proposal_scoreboard`·`estimate_calibrator`·`hourly_pattern`·`bid_rank_curve`) 소비 여부 **미조사**(ref 86 §6-2)
- [ ] ⑥**이미 쌓인 496 MB의 처분**(DELETE+VACUUM) — 크론 정지는 순증만 멈춘다. **디스크 94%** 상태의 판단이라 별건
- [ ] ⑦**`conversion_maturity`가 쌓기만 하고 안 쓴다** — 유일 소비처가 `MATURITY_CORRECTION_ENABLED=False`(2026-07-29 보류, 곡선 퇴화 미해소). 소급 불가라 끄지 않았으나 이 상태 자체가 부채
- [ ] ⑧**토큰화 규칙 중복 정의 해소**(§5-10) — 잔여 no-op 불변식이 여기 걸려 있다
- [ ] ⑨**사전 구멍 「액정」 처분** — 잔여 상위. 사전에 넣을지는 **화이트리스트 정책 재심**과 같은 층의 결정
- [ ] ⑩**「관련어인데 적자」의 처분**(제외/하향/구제 레인) — 계약 §8-Q4 미결. **이제 후보가 실재하므로 결정 대상이 생겼다**
- [ ] ⑪**`search_term` rejected 125→141**(18:3x→18:5x) 원인 미조사 — QA 이월
- [ ] ⑫신설 카운터(`shopping_over_cap`·`semantic_*`)가 크론 `log.info`(scheduler_service.py:909-917)에 안 실린다(기존 `promote_over_cap`도 동일 — 이 diff만의 결함 아님)
- [ ] ⑬**워크트리 7개 잔존** — `c10-product-meta`·`dashboard-rg-revenue`·`m2b-criterion`·`m2b2-device-weight`·`m2c-semantic-units`·`rocket-sales-500`·`shopping-rollback`. 병합 여부 미대조
- [x] ~~PR #319·#320 병합~~ **완료**(`a3cdf39d`·`4fda6b73`, 둘 다 `--force`, 자백 기록)
- [x] ~~미푸시 커밋 정리~~ **완료** — ahead 0, main 위

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_cron-cleanup+m2c-semantic-units_20260821.md 읽고 이어서 작업해줘
```
(체인을 이어받으려면: `/session-relay PAO 논의` — 이번이 **29**번이었다)
