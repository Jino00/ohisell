# ref 110 — PAO 승인 배선 전수 규명: 「제외·승격에 자동 승인이 안 붙는 것」은 의도인가 배선 누락인가 (2026-08-30 KST)

> 발단: 세션 `eef672ce`(PAO UI/UX 트랙)가 Jino 질문 *"어제 PAO가 돌리긴 한거잖아"*를 조사하다 발견해 **엔진 트랙으로 이관**한 4건 중 ②(`docs/references/109_pao_dead_approved_cards_20260830.md`).
> 이 문서는 그 ②만 다룬다. ①(죽은 카드 배포)은 같은 세션이 처리했고 트랙 확인줄에 있다. ③(스코프 위치)은 Jino 결정이라 손대지 않았다.
> 성격: **읽기 전용 규명 — 코드·prod 쓰기 0건.** 처방·추천 없음(어디에 스코프를 걸지, 무엇을 열지는 Jino 몫).
> 상위: D-NAO-278 · 트랙 `docs/tracks/active/track_naver-ad-optimization.md` · 북극성 §7(액셀·브레이크 대칭)·§6 M4.

---

## §0. 세 문장 요약

1. **인계의 전제 절반이 틀렸다** — *"제외·승격은 실행이 열려 있는데 승인이 안 붙는다"*에서 **승격은 실행이 열려 있지 않다.** `search_term_promote`는 `_ACTION_BY_PROPOSAL_TYPE`에 매핑 자체가 없고, 코드가 그 부재를 **의도된 fail-closed**로 명문화해 뒀다.
2. **답은 「배선 누락」이 아니라 「의도」다 — 단 성격이 셋으로 갈리고, 셋 중 둘에 실질 문제가 있다**: 승격은 «사람이 볼 수 없는 곳에서 만들어 만료»되고(580건 중 승인 0), 레거시 제외(`negative_keyword`)는 **위임 영구 제외 목록에서 빠져 있다**(지금 안 터지는 이유는 설계가 아니라 런타임 값이 빈 배열이라서다).
3. **북극성 §7이 경고한 비대칭이 검색어 축에서 실현돼 있다** — 브레이크(제외)엔 자동 발사 경로가 **실재**하는데(파워링크), 액셀(승격)엔 **실행 손 자체가 없다.**

---

## §1. 인계 전제의 정정 — 「실행이 열려 있다」가 가리킨 것

인계가 인용한 `naver_execution_harness.py:269-271`:

```python
OPEN_ACTIONS: frozenset[str] = frozenset(
    {"add_negative_keyword", "update_bid", "set_user_lock", "update_budget", "exclude_search_term"}
)
```

여기 든 제외 계열 둘(`add_negative_keyword`·`exclude_search_term`)은 **action** 이름이고, 이들에 매핑되는 proposal_type은 `negative_keyword`·`search_term_exclude`다. **`search_term_promote`는 이 목록에도, 매핑 표에도 없다.**

`_ACTION_BY_PROPOSAL_TYPE` 전문(`naver_execution_harness.py:178-193`):

| proposal_type | action |
|---|---|
| `negative_keyword` | `add_negative_keyword` |
| `search_term_exclude` | `exclude_search_term` |
| `bid_up`·`bid_down`·`growth_bid_up`·`bid_up_servo`·`bid_up_rank`·`bid_up_explore`·`bid_up_cold` | `update_bid` |
| `pause`·`resume` | `set_user_lock` |
| `budget_up`·`budget_down` | `update_budget` |
| **`search_term_promote`** | **없음** (파일 전체 grep 0건) |

⇒ 「제외·승격」을 한 덩어리로 묶은 것이 인계의 오류다. **둘은 다른 층에 있다.**

---

## §2. 세 갈래 — 각각 무엇이 의도이고 무엇이 문제인가

### 2-1. 승격 `search_term_promote` — 실행 손이 «설계상» 없다. 문제는 다른 데 있다

**의도의 근거(원문 인용)**

- `search_term_ss_lane.py:14-15` — *"proposal_type=search_term_promote는 naver_execution_harness의 _ACTION_BY_PROPOSAL_TYPE에 **절대 등록하지 않는다**(등록하면 미구현 executor를 부르게 되어 위험 — **매핑 부재 자체가 fail-closed**)."*
- `search_term_ss_lane.py:1-15` — *"(SS4) 전환 검색어 승격 후보는 pending 제안(**영구 Confirm·실행 손 없음**)으로 생성한다"* / *"승격(SS4)은 **실행 손 자체가 없다(L3 스코프)** — 정식 키워드 등록은 Jino 콘솔 밖 수동만."*
- `search_term_ss_lane.py:746-760`(`_create_promote_proposal` docstring) — *"approval_source는 **항상 None**(자동 승인 절대 금지 — 생성류는 §0 4)."*
- `proposal_writer.py:43-48` — *"정식 등록 쓰기 손 자체가 L3 스코프라 실행 매핑이 없다(**wisdom_promoted와 동일 모양 — 보고·열람 전용**, Jino가 콘솔 밖에서 수동 등록)."*

⇒ **의도는 명문화돼 있고 배선도 그 의도대로다.** 「배선 누락」이 아니다.

**★그런데 실측이 다른 문제를 드러낸다** (prod, 2026-08-30 11:3x KST)

| 관측 | 값 |
|---|---|
| `search_term_promote` 총 생성 | **580건** (expired 300 + pending 280) |
| 생성 속도 | **하루 20건 균일**(08-18~08-29 12일 전건 20/일) |
| 승인된 것 | **0건** (`approval_source` 전건 NULL) |
| 만료 TTL | **14일** — `proposal_pipeline.py:47` `_PROPOSAL_EXPIRY_DAYS = 14`, 만료 대입은 `proposal_pipeline.py:544` **1곳뿐** |
| 콘솔 분류 | **정보성**(`proposal_writer.INFORMATIONAL_PROPOSAL_TYPES`에 포함) |

★★**여기가 핵심이다**: 승격 후보는 **정보성으로 분류되어 콘솔의 «실행형» 필터(`informational=false`)에서 빠진다.** 그 필터는 D-NAO-47이 *"실행형은 실행형으로 질의한다"*며 만든 것이고(`routers/naver_ad.py:371-381` docstring), 정보성이 실행형을 목록에서 밀어내는 것을 막으려는 설계였다. **그 설계의 부작용으로, 승격 후보 580건은 실행형 목록을 보는 사람 눈에 원리적으로 안 들어온다.**

⇒ 300건 만료는 「사람이 결재를 유보했다」가 아니라 **「사람이 볼 수 없는 곳에서 만들어져 14일 뒤 자동 소멸했다」**이다. 두 문장은 처분이 다르다 — 전자는 정상이고 후자는 **재료가 새는 것**이다(북극성 §4-4 「학습 재료」·D-NAO-251 증거 유실과 같은 결).

⚠️ **TTL 표가 둘로 갈라져 있다**: `proposal_pipeline.py:60-68`의 `_INFORMATIONAL_EXPIRE_DPLUS`(정보성 차등 TTL)에 `search_term_promote`가 **없어서** 14일 폴백을 받는다. 즉 「정보성」 목록과 「정보성 TTL」 목록의 **원소가 다르다.**

### 2-2. 제외 — 쇼핑·의미단위 `search_term_exclude` (5 pending) — 의도, 근거 실측 완비

`delegation_gate.py:53-68` `delegable_types()`가 **명시적·영구 제외**한다. 원문:

> *"SS3(검색어 제외): **자동 발사 절대 금지**(PLAN §0 4·§3 SS3-A "자동 승인원 절대 배선 금지"). exclude_search_term은 OPEN_ACTIONS·_WRITE_EXECUTORS에 있어(**콘솔 Confirm 실쓰기용**) 위 집합에 들어오지만, 위임(Ava agree 자동승인) 경로로 새면 사람 Confirm 없이 자동 제외된다 — rank-step/explore와 동일 철학으로 **영구 제외**"*

★**단 갈래가 둘이다** — 같은 `search_term_exclude`인데 **광고 유형에 따라 자동/수동이 갈린다**:

| 후보 출처 | 좌표 | status | approval_source | 실행 |
|---|---|---|---|---|
| **POWERLINK/WEB_SITE** | `search_term_ss_lane.py:_autofire_exclude` 237-291 (호출부 718·923) | **`approved` 즉시** | `ss_exclude` | **그 자리에서 `execute()` 호출(274행)** |
| SHOPPING | `_create_shopping_exclude_proposal` 784-804 | `pending` | None | 사람 Confirm |
| 의미 단위(semantic) | `_create_semantic_exclude_proposal` 807-828 | `pending` | None | 사람 Confirm |

이 비대칭의 근거는 n=70이 이미 실측해 뒀다(`docs/PAO_OPS.md` §3-b): **파워링크 30일 78,952행 중 전환>0이 0건(0.00%)** — 구조적으로 못 재므로 잘못 잘라도 잃을 게 없다. **쇼핑 316,273행 중 1,521건(0.48%)** — 잘못 자르면 진짜 매출을 잃는다.

⚠️**주석이 배선과 반대다(문서 결함, 동작 결함 아님)**: `search_term_judge.py:34-37`이 `SEARCH_TERM_EXCLUDE_TYPE` 옆에 *"**어디에서도** 자동 승인(status='approved' + approval_source=이 값)을 배선하지 않는다"*라고 적어 뒀는데, `search_term_ss_lane.py:268`이 정확히 그것을 한다(파워링크). 파워링크 자동은 D-NAO-180·181 이후 의도된 동작이므로 **낡은 것은 주석 쪽**이다. 이 트랙의 상습병(「문서가 배포 동작과 반대」)의 또 한 번.

### 2-3. 제외 — 레거시 `negative_keyword` (11 pending) — ★구멍이되 «휴면»

**두 사실이 어긋난다.**

ⓐ 자동운영 엔진은 이 유형을 **아예 안 본다**: `grep -n "search_term\|negative_keyword" backend/app/services/naver_ad/auto_operator.py` → **0건**(파일 4,016줄 전체). 레인별 화이트리스트도 전부 입찰·예산뿐이다:

| 레인 | 좌표 | 대상 |
|---|---|---|
| 일 레인 | `run_daily_lane:1075` (쿼리 1118-1132) | `_DAILY_LANE_PROPOSAL_TYPES = ("bid_up","bid_down","pause")` (`auto_operator.py:55`) + `target_type != "ad"` |
| 예산 봉투 | `_run_budget_envelope_lane:950` | `proposal_type == "budget_up"` ∧ `rationale LIKE '[예산봉투]%'` ∧ `budget_auto_eligible IS TRUE` |
| 시간당 | `run_hourly_lane:3266` | **큐를 안 읽는다** — intraday 곡선을 판정해 `bid_up`/`bid_down`/`bid_up_servo`/`bid_up_rank`를 인라인 생성(3736~) |
| 탐색 | `_run_exploration_for_campaign:2595` | 큐 아님(`exploration_candidates()`) · 생성 타입 `bid_up_explore`(2847) |
| 스파이럴 | `_fire_vitality_revive:2935` | 큐 아님 · 생성 타입 `bid_up`(2974-2975) |

ⓑ **그런데 위임 게이트의 「영구 제외」 목록엔 `negative_keyword`가 없다.** `delegation_gate.delegable_types()`가 빼는 것은 `RANK_STEP_TYPES`·`EXPLORATION_STEP_TYPES`·`COLD_START_STEP_TYPES`·`SEARCH_TERM_EXCLUDE_TYPE` 넷뿐이다. `negative_keyword`는 매핑(`add_negative_keyword`)이 있고 그 action이 `OPEN_ACTIONS`·`_WRITE_EXECUTORS` 둘 다에 있으므로 **구조적으로 위임 자동승인 대상 집합에 포함된다.**

⇒ **같은 행위(제외 키워드 추가)를 하는 두 유형 중 한쪽만 잠겨 있다.**

**지금 안 터지는 이유는 설계가 아니라 런타임 값이다** — prod 실측(2026-08-30 11:4x KST):

```sql
SELECT * FROM naver_account_settings;
-- key=expert_delegated_types, value_json=[], updated_at=2026-07-16 23:42:32
```

**빈 배열**이고 **2026-07-16 이후 바뀐 적이 없다.** 위임 게이트는 어떤 유형에 대해서도 발화하지 않는다. ⇒ 이 구멍은 **휴면**이다. 다만 콘솔에서 `expert_delegated_types`에 `"negative_keyword"`를 넣는 순간 SS3의 「자동 발사 절대 금지」 철학이 **다른 이름의 같은 행위로 우회된다.**

★ 참고 — 두 제외 파이프라인의 존재는 n=70이 이미 갈라 뒀다(트랙 확인줄 2026-08-30 11:1x): ①`search_term_ss_lane`(PAO 고유·`search_term_exclude`·08:50) ②레거시 `proposal_writer`(`negative_keyword`·08:00·**전 기간 실행 0건**). 이 문서는 그 둘의 **위임 노출도가 다르다**는 축을 추가한다.

---

## §3. 북극성 §7 대칭 검사 — 검색어 축은 비대칭이다

| 축 | 브레이크(억제) | 액셀(확장) | 대칭? |
|---|---|---|---|
| 입찰 | `bid_down` 자동 승인·실행 | `bid_up`·`bid_up_explore`·`bid_up_servo`·`bid_up_rank`·`bid_up_cold` 자동 | **대칭** |
| 예산 | `budget_down` 매핑 있음 | `budget_up` 자동(봉투 레인, `budget_auto_eligible` 조건) | 대칭(단 예산 개방은 스코프 밖) |
| **검색어** | 제외 — **파워링크 자동 발사 실재**(`_autofire_exclude` 즉시 approved+execute) | 승격 — **실행 손 자체가 없음**(매핑 부재 = fail-closed) | ★**비대칭** |

북극성 §7 원문: *"자동화는 브레이크(차단·정지)가 액셀(확장)보다 만들기 쉬워서 방치하면 반드시 ROAS 방어로 기운다(D-NAO-85 실측)."*

⇒ **검색어 축이 그 문장의 정확한 실현이다.** 억제 쪽은 손이 있고 자동으로 돌기까지 하는데, 확장 쪽은 손이 아예 없다. 그리고 그 확장 후보는 하루 20건씩 만들어져 사람 눈에 안 보이는 채 14일마다 소멸한다.

★**이건 결함 고발이 아니라 대칭 검사 결과 기록이다.** 승격 executor를 만드는 것은 L3 스코프 확장이고, 정식 키워드 등록은 되돌리기가 제외보다 어렵다(잘못 등록하면 비용이 즉시 나간다). **무엇을 열지는 Jino 결정**이고 이 문서는 사실까지만 적는다(북극성 §8-① 「optimizer 해제 범위」와 같은 층).

---

## §4. 카나리 교착 — 왜 「점화했는데 총알이 없다」인가

2026-08-29 12:53 점화(D-NAO-275) 이후 실집행 0건의 기제는 두 사실의 곱이다.

| | 스코프 «안» 그룹 (`grp-a001-02-000000070523564`, 1개) | 스코프 «밖» 그룹 (같은 캠페인 나머지) |
|---|---|---|
| 나오는 제안 | `negative_keyword` 5 · `search_term_promote` 1 (08-29 이후) | `bid_up`·`bid_down`·`bid_up_explore` |
| 자동 승인 | **안 붙는다**(§2-1·§2-3) | 붙는다 |
| 실행 | — | **스코프 가드가 막는다** → 죽은 카드 133건 |

⇒ **자동 승인이 붙는 종류는 스코프 밖에만 나오고, 스코프 안에 나오는 종류는 자동 승인이 안 붙는다.** 어느 한쪽만 고쳐도 총알이 안 생긴다.

★그 그룹에 입찰 제안이 0건인 이유는 **표본 부족**이다(08-29 클릭 6·전환 0). 거래가 줄고 있다:

| 날짜 | 광고비 | 클릭 | 전환 | ROAS |
|---|---:|---:|---:|---:|
| 08-22 | 64,349원 | 48 | 6 | 1.57 |
| 08-24 | 41,041원 | 33 | 2 | 0.82 |
| 08-27 | 15,854원 | 19 | 1 | 1.06 |
| 08-28 | 7,586원 | 9 | 0 | 0.00 |
| 08-29 | 5,254원 | 6 | 0 | 0.00 |

(출처: 인계 `ref 109` — 이 세션이 재측정하지 않았다. **인용 시 창을 병기할 것.**)

---

## §5. 확정 사실 목록 (좌표 병기 — 다음 세션이 재현할 것)

1. `search_term_promote`에 실행 매핑 **없음** — `naver_execution_harness.py` grep 0건. 의도 명문화 `search_term_ss_lane.py:14-15`.
2. 승격 580건 생성 / 승인 **0** / 만료 300 — TTL 14일 `proposal_pipeline.py:47`, 만료 대입 1곳 `proposal_pipeline.py:544`.
3. 승격은 `INFORMATIONAL_PROPOSAL_TYPES`에 있으나 `_INFORMATIONAL_EXPIRE_DPLUS`(`proposal_pipeline.py:60-68`)엔 **없다** — 두 목록의 원소가 다르다.
4. 파워링크 제외는 **자동 발사**(`search_term_ss_lane.py:237-291`, 즉시 execute 274행) / 쇼핑·의미단위는 pending.
5. `search_term_judge.py:34-37` 주석이 4와 **상충** — 주석이 낡음.
6. `delegation_gate.delegable_types()`(`delegation_gate.py:53-68`)가 `search_term_exclude`는 빼고 **`negative_keyword`는 안 뺀다.**
7. prod `naver_account_settings.expert_delegated_types = []`(2026-07-16 23:42:32 이후 불변) ⇒ 6의 구멍은 **휴면**.
8. `auto_operator.py` 전체에 `negative_keyword`·`search_term` 문자열 **0건**.
9. `delegation_gate._eligible`(99-106)이 `target_type=='ad'`를 거른다(`skipped["ad_confirm_only"]`) — D-NAO-65 근거.
10. 엔진 승인원 대입 지점 전수 8곳: `auto_operator.py:424`(engine_approve, 단일 문)·3749·3770·3777·3784 / `delegation_gate.py:187` / `search_term_ss_lane.py:268` / `probe_revert.py:623` / (사람) `routers/naver_ad.py:524`.

## §6. [미상] · 이 문서가 답하지 않는 것

- **승격 후보 580건의 품질** — 「승격했으면 이익이었을까」는 재지 않았다(홀드아웃 없이 집행에 넣지 않는다, 북극성 §7).
- **정보성 필터가 실제 콘솔 화면에서 승격을 가리는가** — 코드 경로로는 그렇지만(§2-1) 프론트 기본 필터 값은 확인하지 않았다.
- **`negative_keyword` 11건이 어느 그룹 것인가** — 스코프 안/밖 분해 미실시.
- 처방 일체(무엇을 열지·스코프를 어디에 걸지) — Jino 결정(북극성 §8-①·§5-3 ③).
