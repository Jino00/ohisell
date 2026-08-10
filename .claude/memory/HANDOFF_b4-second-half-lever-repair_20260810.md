# 세션 인수인계: B-4 나머지 절반 — 거부→수리→절체(D-NAO-170) + 그룹 판정의 전 소재 집행(D-NAO-171)
> 저장일시: 2026-08-10 18:4x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md`
> 직전 HANDOFF: `HANDOFF_wisdom-layer+bep-baseline-fix_20260810.md`(그 파일 §6 C의 「B-4의 나머지 절반」이 이번 작업)

## 1. 한 줄 요약
B-4(「쓰기가 옥션에 닿는가」)의 나머지 절반을 닫았다. **코드는 prod에 살아 있고, 라이브 부품 검증은 통과했으나 end-to-end는 못 봤다 — 「합격」이라 부르지 않았다.** 막힌 이유가 이 인계의 핵심 발견이다(§4).

## 2. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (공유 메인 = **main 고정**)
- prod: `sellc.ohitech.co.kr` · 백엔드 `/home/ubuntu/ohisell/backend` · DB 정본 `backend/ohisell.db`
- **★pm2 포트: 이번 세션에서 8011 → 8001로 바뀌었다**(무중단 배포). 항상 `pm2 list`로 확인.
- prod python: `.venv/bin/python3` · **임시 스크립트에 `from app.database import SessionLocal` 필수**(.env 로드 — 없으면 네이버 서명 빈 문자열 → 403)
- 테스트: `cd backend && python3 -m pytest -q` (현재 **5202 passed**, 약 2분 45초)
- 배포 `scripts/safe_deploy.sh` · 병합 `scripts/safe_merge.sh` · 번호 `scripts/next_ids.sh`

## 3. 완료 목록 (전부 push 완료)
- ✅ **D-NAO-170** 거부 = 데이터 수리 신호 — `9291112`
- ✅ **D-NAO-171** 그룹 판정 → 활성 false 소재 **전부** 집행 — `9291112`
- ✅ **적대 리뷰 P1-1 수용·수정** — `218c375` (P2 채택 6·이월 4)
- ✅ **D-NAO-169 원장 소급 기재** + 교훈 #216~#218 — `cf0cb16`
- ✅ 라이브 상태·완전 정지 사실 기록 — `dad5612`
- ✅ prod 배포(무중단 8011→8001) · `docs/TRACKS.md`·`claude-progress.txt` 갱신

## 4. ★★다음 세션이 반드시 알아야 할 것 — PAO는 「완전 정지」다
인계 문서들이 **`auto_operate=0`만** 적어 왔는데 **틀렸다(불완전했다)**. 2026-07-30에 7개 캠페인 전부 `optimizer: ours → none`으로도 내려갔다(커맨드 센터 관리주체 스위치).

그래서 **`naver_execution_harness.execute`가 실행 직전 `OptimizerGuardError`로 거부한다 — 사람이 콘솔에서 승인해도 쓰기가 안 나간다.** 사고가 아니라 설계대로다. 2026-07-27 `change_log`가 미리 규정해 뒀다:
> *"킬스위치는 auto_operate=0만으로는 위임/콘솔 경로를 못 막으므로 완전 정지 시 optimizer=none도 함께 내릴 것."*

**→ PAO 관련 변경의 「라이브 합격기준」을 설계할 때 이걸 먼저 확인할 것.** 「콘솔 Confirm으로 검증하겠다」는 계획은 현재 상태에서 실행 경계 앞에서 죽는다(이번에 그렇게 죽었다). 대안은 실행 경계 **아래**의 부품별 라이브 검증이고, 그 경우 「합격」이라 부르지 않는다(전역 §2).

## 5. 확정된 결정사항
- **★D-NAO-170**: 그룹입찰 死 거부 시 가드가 쥔 라이브 소재 목록을 예외(`GroupBidDeadError`)에 실어 보내고, harness가 `naver_adgroup_product`에 **upsert** → **다음 회차에 기존 라우터가 스스로 소재로 절체**한다. **새 재라우팅 경로를 만들지 않는다**(기계는 이미 있었다 — 없던 건 기계의 «입력을 고치는 손»이었다).
  - **upsert인 이유**: update-only면 소재 행이 **아예 없는** 그룹이 영구 동결된다(`_derive`가 group 폴백 → 영원히 그룹으로 보내고 영원히 거부). 2026-07월 03 캠페인 9일·79건 무접촉이 그 모양이었다.
  - **fail 방향**: write-back은 **fail-open**(수리 «가속기»이지 안전장치가 아니다). 단 **탐지 실패 시엔 덮지 않는다 = fail-closed**(§6 참조).
- **★D-NAO-171**: `source='ad'` 그룹의 집행 대상은 **활성 useGroupBidAmt=false 소재 전부**. 각자 기준 ±15% **승수** 스텝이라 소재 간 **비율이 보존**된다(상품별 차등은 우리가 만든 구조가 아니다).
  - **★봉투 계수를 하나도 안 바꾼 것이 성립 조건**이다 — 쿨다운·자동하향 상한·누적 상향 상한이 전부 소재(entity) 단위인데 전 소재가 한 회차에 **락스텝**으로 스탬프되므로 그룹 실효 기준 케이던스가 자동으로 단일소재 그룹과 같아진다.
  - **캡 2층**: 결정 캡 `_MAX_AD_AUTO_EXEC_PER_LANE`=5는 **그룹당 한 자리**(틀리는 단위는 판정이지 소재가 아니다), blast radius는 **쓰기 캡** `_MAX_AD_WRITES_PER_LANE`=15. 초과분은 Confirm 대기 강등(드롭 아님).
  - **되돌림**: `_GROUP_STEP_ALL_ADS=False` → max-only 복귀. 그 위는 `AD_BID_ROUTING_ENABLED=False`가 ad 라우팅 전체를 덮는다(층이 다르다).
- **집행 대상·스텝 기준은 라이브 `get_ads` 1콜**(라우팅 판정 `source='ad'`는 DB 파생 유지) — API 콜 N→1이고, `naver_adgroup_product`가 `(adgroup_id, mall_product_id)` 유니크라 같은 상품의 소재가 여럿이면 첫 하나만 남아 **조용히 빠지는** 문제를 우회한다.

## 6. ★적대 리뷰가 잡은 P1 (내 결함 — 같은 실수 방지)
초판은 외부변경 탐지 앵커(`ad_edit_tm`·`ad_apply_tm`)를 의도적으로 동결했는데, **같은 탐지기가 비교하는 값 3종**(`ad_bid_amt`·`use_group_bid_amt`·`ad_user_lock`)은 라이브로 덮었다.
→ 다음 07:45 sync의 `prev_by_ad`가 이미 라이브와 같아 `_diff_ops`가 빈 리스트 → **`ad_edit`으로 강등** → `auto_up_base_bid`의 `op_type=="bid_change"` 필터에 안 걸림 → **자동 상향 2.0× 누적 상한의 기준점이 재설정되지 않는다**(codex 3R[P1]이 닫았던 「2,000 기준점에 머물러 400을 4,000까지 되돌림」 구멍의 재개봉). BEP 하한은 부모 그룹 30일 집계라 개별 소재를 못 막아 **대체 브레이크가 없다**.
→ **수정**: 덮기 **전에** `ad_external_change.run` 실행(추가 API 콜 0 — 가드가 넘긴 원본에 `edit_tm`이 있다). 여기서 탐지가 가능한 이유가 **앵커 동결**이다(게이트 ②③이 「양쪽 editTm 존재 ∧ 상이」를 요구). **탐지 실패 시엔 덮지 않는다** — 수리 실패는 「다음 sync까지 지연」이지만 사건 마스킹은 영구 소실이고 돈 경로다.

## 7. 핵심 파일
| 파일 | 역할 |
|---|---|
| `backend/app/services/naver_ad/naver_sa_writer.py` | `GroupBidDeadError`(관측을 실어 나르는 예외) · `_reject_if_group_bid_is_dead` |
| `backend/app/services/naver_ad/naver_execution_harness.py` | `_writeback_live_ad_observation` · `LEVER_MISMATCH_MARKER` · `_execute_update_bid` except 분기 |
| `backend/app/services/naver_ad/auto_operator.py` | 팬아웃 라우팅(`ad_exec_targets`) · `_GROUP_STEP_ALL_ADS` · `_MAX_AD_WRITES_PER_LANE` · `group_slot_taken` |
| `backend/tests/test_naver_ad_lever_mismatch_repair_d170.py` | D-NAO-170 전용(폐루프 회귀 = 수리 후 `source` 뒤집힘) |
| `backend/tests/test_naver_ad_ad_level_bid_b3.py` | 팬아웃·캡·킬스위치·hold 사유 회귀(파일 끝 두 블록) |
| `scripts/wisdom_audit.sh` | §3에 `[LEVER_MISMATCH]` 건수 쿼리 배선 |

## 8. 라이브 검증 상태 (정확히 이만큼만 참이다)
**통과(네이버 쓰기 0건, 7일 지출 0원 그룹 `grp-a001-02-000000044743918`에서 divergence 재현)**
- 가드가 그룹입찰 PUT 거부 + 소재 8건을 예외에 실음
- write-back 8행 · **앵커 불변** · 원래 값과 일치 · 탐지가 덮기 전에 실행됨
- **라우터 절체 `group` → `ad`**(max_ad_id·effective 1900)
- 팬아웃 결정: 8소재 각자 −13~−15%, 비율 6.333 → 6.231 보존
- 네이버 그룹 bidAmt **1550 → 1550 불변**

**미검증(= 다음에 재개하는 세션이 첫 회차에 관찰할 것)**
- ① `change_log`에 `[LEVER_MISMATCH]` + `failed`(KST 타임스탬프 일치)
- ⑤ 소재별 제안 N건 + 각 **재조회 실측** −15% + 그룹 실효 −15% + 비율 보존
- ⑥ 즉시 재시도 시 쿨다운 차단  ⑦ 쿨다운 후 콘솔 UP 원복
- **재개 첫 회차엔 눌린 판정 207그룹이 쏟아진다** — 캡(5결정/15쓰기)이 1차 방어. 캠페인 단위 단계 개방 여부는 Jino 결정 사항.

## 9. 알려진 이슈 / 주의사항
- **★B-1 가드(D-NAO-169)는 이제 활성이다** — 2026-08-10 17:21:55 다른 세션의 무중단 재배포로 프로세스가 갈리며 켜졌다(종전 인계의 「재시작하면 켜진다」가 실현됨). `changed_at`↔`executed_at` 30분 초과 어긋난 `change_log` 쓰기는 예외로 거부된다.
- **★번호는 원장에 즉시 적어라** — `next_ids.sh`는 **트랙 원장을 스캔한다.** D-NAO-169가 구현·배포·인계까지 되고 원장에만 없어서 도구가 **169를 다시 내주려 했다**(다섯 번째 충돌 직전, 우연히 잡힘). 교훈 #216.
- **병행 세션 활발** — 오늘 `claude/cost-drift-wiring`·`claude/3p-option-fee-rate`가 prod에 배포했다. 배포 전 `git fetch` + `deploy-manifest.jsonl` 확인 습관.
- **팬아웃 이득의 실측 크기**: 소재 2개+ 죽은 그룹 **61개** 중 **36개**가 「max만 내리면 차순위가 새 max」에 걸린다. 그리고 **61개 전부**에서 max-only는 나머지 **182소재의 지출을 판정과 무관하게 유지**한다 — 이쪽이 더 큰 몫이다. 소재 1개 **160그룹**은 영향 0.
- prod 잔여 상태 정리 확인 완료(검증용 행 8건 원복 일치 · 제안 `expired` · 임시 스크립트 삭제 · 서비스 200).

## 10. 다음에 할 작업 (미완료)
### 🔵 기술 부채 (PAO)
- [ ] **B-2 집계 정본 헬퍼** — 교훈 #195(계정 광고비·ROAS **2배 오집계**) 방지. sentinel/실단위 **택일** + 검산식을 공용 함수로. 조회·분석 경로엔 아직 가드가 없다.
- [ ] **B-3 BEP 기준선 표면화** — 어느 계수로 판정했는지 화면·로그에. 08-10에 1.836→1.711로 바뀌었는데 화면은 아무 말도 안 한다.
- [ ] **지식 부채 1건** `claimed-vs-wired-is-the-default-state` — 주간 감사 §3이 처분 예정. **이번 세션이 그 패턴의 교과서 사례를 둘 냈다**(인계가 「소재로 안 보낸다」고 했는데 실제로는 보내고 있었다 · 인계가 `auto_operate`만 적어 `optimizer=none`을 가렸다).
- [ ] D-NAO-132 **P0-a**(스마트스토어 실시간 판매 → CPC 배선) — B-4 뒤 순서.

### ↗️ 이번 범위 밖으로 미룬 것 (앵커 `## 이월`에도 있음)
- [ ] **`update_keyword_bid`가 `useGroupBidAmt: False`를 항상 전송**(`naver_sa_writer.py:316`, `:348`에서 false 전환을 성공 조건으로 검증) — 소재에서 금지선인 「강제 전환」의 **키워드판**일 수 있다. 의도된 의미론일 가능성도 있으나 **코드에 검토 흔적이 없다.** Fable 발견.
- [ ] DB 행 없는 소재의 자동 UP 영구 차단(전제 미확인·fail-closed라 돈은 안 샘) · UP 팬아웃의 판정 grain 미스매치(봉투 무변경의 결과) · SA API 레이트리밋 여유(확인 안 됨) · `result["approved"]`/`executed` 그레인 변화의 하류 소비자(확인 안 됨)
- [ ] CD3 `_standing_probes` ad grain 되돌림 확장 · rank-step 소재 확장(잠금 재설계 필요)

### 🟣 Jino 결정 대기 (기술 아님, 이월)
- [ ] **오늘출발 무료배송** 3~5종 2주 시험(1순위 23종 선정 완료) · **TPU 캠페인** · **03 일예산 원복** · **대행사 통보 여부**

## 11. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_b4-second-half-lever-repair_20260810.md 읽고 이어서 작업해줘
```
