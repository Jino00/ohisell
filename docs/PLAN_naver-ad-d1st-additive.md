# PLAN — 측정 정합 S4: `d1_st` additive (검색어 grain D+1 소급 채점)

> 계약: 「측정 정합」 S4 (S1=D-NAO-175 장부 입구 가드 → D-NAO-176 콘솔 편입 입구 → S3=8/13 사슬 실관측 종결 후 착수).
> 설계: Fable(2026-08-13). **✅Jino 승인 = D-NAO-178**(2026-08-13 10:1x) — §7 열린 질문 전건 종결, §8에 결정 기록.
> 배경 실측(2026-08-13 08:35 라이브): 13일 만의 첫 소급 표본 diary 4371(검색어 「골프」 제외, `cmp-a001-02-000000008902804`)이 `outcome_json.d1 = {"cost": 43084, "clk": 29, "conv": 122000, "roas_c": 3.5753}`로 채점되고 wisdom 후보 id=27(`good 1/bad 0`, pending)이 생겼다. **그런데 「골프」의 30일 누적 광고비는 31,411원이다 — d1 하루 43,084원은 검색어가 아니라 캠페인 전체의 성과다.** 원인은 `diary_outcome._grain_and_target`(diary_outcome.py:41-48)의 `target_type=="search_term"` 캠페인 폴백. 학습 엔진이 「search_term_exclude는 good」을 남의 성적표로 배우기 시작했다.

---

## §1 목표 / 이번에 안 하는 것

### 목표 (이것만)

`target_type=="search_term"`인 diary 행의 `outcome_json`에 **검색어 grain의 D+1 결과를 `d1_st` 키로 덧붙인다.** 원료는 `NaverSearchTermDaily`(검색어 grain 실측, P2-S1/SS1). 기존 `d1`(캠페인 grain)은 한 글자도 건드리지 않는다 — `d1`은 이제부터 「조치 시점의 캠페인 배경 성과」로 읽고, 조치 자체의 성적은 `d1_st`가 담는다.

부속 1건: **오염 차단** — `wisdom_candidates.harvest_candidates`가 `target_type=="search_term"` 행을 수확하지 않게 skip한다(§6-D에서 근거). 이것은 `d1_st`를 먹이는 게 아니라 **잘못된 `d1`을 그만 먹이는** 것이다.

### 이번에 안 하는 것 (인접하지만 범위 밖)

| 항목 | 어디로 |
|---|---|
| wisdom이 `d1_st`를 판정에 소비(`_outcome_window`/`_outcome_direction` 개조) | **S8** — 과거 승률 의미가 바뀌므로 별도 결정 |
| `d7_st` | S8 또는 S6 이후 — d1_st가 라이브로 증명된 뒤 |
| 콘솔 43건 편입 | **S5** — ✅2026-08-13 09:56 완료(01. 버디필름 42건 신규 + 「골프」 시각 채움) |
| 8/17 골프 첫 성적표·`leaking` 시 Slack 알림 | **S6** |
| 레버 개방 안건 | **S7** |
| 기존 `d1`의 캠페인 폴백 자체를 고치는 것(4371 재기입 포함) | 안 한다 — 멱등 가드가 stale 판별 근거(§3) |
| EXPKEYWORD 수집 지연 자체의 개선 | 별건 (비동기 자기치유 패턴은 현행 유지) |
| 일기 action 표기 분열(`search_term_exclude` vs `exclude_search_term`) 통합 | 별건 설계(승률 리셋 문제, D-175 이월) |
| 콘솔 제외 「유형(일치/…)」 축의 원장 반영 | 별건 (D-176 발견 이월) |

---

## §2 판단기준

1. **항상 「모른다」를 명시 값으로 기록한다** (`no_data`/`ambiguous`), 왜냐하면 D-174(`unverifiable`)·D-175(`margin_lost` 부호역전)·D-176(`already_excluded`) 사흘 세 결함이 전부 「모르는 것을 아는 것으로 센」 같은 모양이었다(교훈 #283).
2. **항상 보고서 실재를 「0」의 전제로 요구한다**, 왜냐하면 「비용 0=성공」과 「데이터 아직 없음」이 같은 숫자로 보인다 — `search_term_ingest._conversion_report_dates`가 이미 같은 이유로 「보고서 있음=진실, 없음=보존」을 택했다.
3. **항상 기존 키(`d1`/`d7`/`retro`)는 불변**, 왜냐하면 `"d1" not in outcome` 멱등 가드가 「언제 채점됐나=무엇을 알고 채점했나」의 근거다. 4371의 잘못된 `d1`도 결함의 증거로 보존한다.
4. **항상 `source` 축은 분리 기록**하고 전환·ROAS는 절대 합산하지 않는다, 왜냐하면 expkeyword 전환은 구조적으로 0(SS0 §0.5 확정 — 파워링크 검색어 전환 귀속 불가)이라 합치면 ROAS가 희석된 거짓 숫자가 된다.
5. **검색어 제외의 판정 규칙은 캠페인 규칙의 정반대**로 새로 정의한다(비용 정지=성공), 왜냐하면 현행 `_outcome_direction`은 cost=0을 `neutral`로 버려 **완벽한 성공이 판정 불가로 떨어진다.**
6. **항상 라이브 크론이 자연히 채우게 한다**, 왜냐하면 이 트랙은 8/12→13에 강제 backfill의 유혹을 참았기 때문에 진짜 표본 1건을 얻었다.

---

## §3 금지선

1. **강제 `backfill_outcomes` 실행 금지**(미래 시각 주입 포함) — 원료가 없으면 `cost:0`이 영구히 굳는다. 합격기준을 억지로 충족시키는 어떤 수동 주입도 금지.
2. **기존 `d1`/`d7`/`retro` 키의 재기입·수정·삭제 금지.** diary 4371 포함.
3. **`wisdom_candidates._outcome_window`/`_outcome_direction` 수정 금지** — d1_st 소비는 S8.
4. **`d1_st`에 `roas_c` 필드 금지** — 캠페인 판정 규칙(`roas_c >= target`)이 이 키를 실수로 먹는 사고를 구조적으로 차단한다. 판정은 `status` 문자열로만.
5. **shopping+expkeyword의 전환·ROAS 합산 저장 금지.** (비용 합산은 허용 — 비용은 두 source 모두 실돈으로 동질.)
6. **보고서 실재 게이트 없는 0 기입 금지** — 해당 날짜·source의 원장 행이 하나도 없으면 그 source는 `present:false`이지 0이 아니다.
7. **읽기 + diary 테이블 쓰기만** — `ss_lane`·`naver_sa_writer` 등 실쓰기 경로 접근 금지. 마이그레이션 없음(`outcome_json`은 JSON 텍스트).
8. **prod 데이터 수기 수정 금지** — wisdom 후보 27의 처분(§7)은 Jino 결정 후에만.

---

## §4 합격기준 — 라이브 증거 시나리오

코드 존재·pytest 초록은 합격이 아니다. 아래 전부가 **prod에서 관측**되어야 한다.

| # | 시나리오 | 무엇이 어디서 관측되면 합격 |
|---|---|---|
| ① | **4371 정합** | 배포 후 첫 08:35 스윕 뒤 prod 조회: diary 4371 `outcome_json`에 `d1`(43084 — **불변**)과 `d1_st`가 **공존**. `d1_st.by_source.shopping.present == true`. 값을 미리 확정하지 않는다 — **d1과 d1_st가 다른 숫자라는 사실 자체**가 오귀속 제거의 증거다. |
| ② | **거짓 0 부재** | `d1_st`가 기입된 전 행 스캔: 필요 source가 `present:false`인데 `status=="stopped"`인 행 **0건**. |
| ③ | **오염 정지** | 배포 후 08:45 `run_naver_wisdom` 결과에 `skipped_search_term_grain` 카운터가 노출되고, 신규 search_term 실집행이 생겨도 wisdom tally가 늘지 않는다. |
| ④ | **기존 불변** | 배포 전후로 `d1`/`d7`이 이미 기입된 전 행의 값 diff **0건**(D-175 합격기준③과 같은 전건 대조). |
| ⑤ | **해석문 통과** | 배포 후 08:35 reflection 해석문이 `d1_st`를 지어낸 수치 없이 서술(§5-3의 프롬프트 1줄이 「비용 0=의도된 성공」으로 읽히는지 확인). |

억지 충족 금지(§3-1): ①이 안 나오면 원인을 파고들지, 값을 만들어 넣지 않는다.

---

## §5 구현 순서 (파일 단위 · 각 단계가 깨는 것)

**0. 사전 검증(코드 0줄, prod 읽기 전용)** — 추측을 실측으로 바꾸는 단계:
- 「골프」 30일 31,411원이 `NaverSearchTermDaily`에서 **정확 문자열 일치** 쿼리로 재현되는지(매칭 규칙 B의 전제 검증 — 안 되면 정규화 축 재설계).
- 8/12(=4371의 d1_day)의 `source='shopping'` 행이 실재하는지(합격① 예보).
- diary 4371의 `adgroup_id` 컬럼이 실제 채워져 있는지(코드상 `record_execution`이 넘긴다 — search_term_execution.py:298. prod 행에서 재확인).
- 깨는 것: 없음.

**1. `backend/app/services/naver_ad/diary_outcome.py`** — 본체:
- `_st_match(db, entry, d)`: (campaign_id, adgroup_id, search_term 매칭, d1_day) → 매칭 행 집합. 매칭 규칙은 §6-B.
- `_st_window(db, entry, d1_day)`: 매칭 결과 → `d1_st` dict (스키마 §6-A).
- `_backfill_row` 분기: `entry.target_type=="search_term" and entry.target_id`이고 `age>=2, "d1_st" not in outcome`일 때 — **필요 source의 보고서가 실재하면 기입**, 부재하면 이번 스윕은 건너뛰고(키를 안 씀 → 다음 날 재시도), **age>=5면 `status:"no_data"`로 확정 기입**(마감 근거: 07:40 수집창이 `[T-3, T-1]`(scheduler_service.py:502-503)이라 d1_day는 age 4 스윕까지만 수집 기회가 있고, EXPKEYWORD 생성요청→다음 크론의 +1일 지연을 포함해도 age 4가 마지막 — age 5부터는 원리적으로 더 올 데이터가 없다).
- `backfill_outcomes` totals에 `d1_st_filled`/`d1_st_no_data` 추가.
- 깨는 것: `reflection_loop` 반환 dict에 키 추가(소비자는 로그뿐 — additive 안전). d1/d7/retro 경로 무변경.

**2. `backend/app/services/naver_ad/wisdom_candidates.py`** — 오염 차단:
- `harvest_candidates` 루프 서두에 `if entry.target_type == "search_term": totals["skipped_search_term_grain"] += 1; continue`.
- 깨는 것: search_term 행이 wisdom에 안 잡히게 됨(**의도** — S8까지 동결). 기존 후보 27의 tally는 안 건드림.

**3. `backend/app/services/naver_ad/diary_reflection.py`** — 해석문 오독 방지 1줄:
- `_SYSTEM`에 「`d1_st`는 검색어 제외 조치의 검색어 단위 사후 결과이며 **비용 0이 의도된 성공**, `no_data`는 판단 불가」 취지 1문장 추가. outcome dict 전체가 LLM에 통과되므로(diary_reflection.py:67), 설명 없이 흘리면 cost:0을 실패로 서술할 위험.

**4. 테스트** — 정확매칭/50자 절단 다의/보고서 부재 skip→재시도/age 5 no_data 확정/기존 d1 불변/wisdom skip. 전체 회귀 0.

**5. 배포·관측** — `--restart-legacy`(nginx 허용목록 문제로 무중단 불가). **다음 날 08:35 전에 배포**해야 4371이 첫 스윕에서 자연 기입된다. 배포 후 §4 ①~⑤ 순서로 관측, 결과를 트랙 문서에 기록.

---

## §6 난제 A~E에 대한 답

### A. 「성공」의 정의 — 결정: **판정을 `roas` 축에서 떼어내 `status` 4값으로 새로 정의한다**

```
stopped   = 필요 source 보고서 실재 + 매칭 비용 합 0     (제외가 돈을 끊었다 = 성공)
leaking   = 보고서 실재 + 정확매칭 비용 > 0              (제외가 안 먹혔다/재유입 = 실패·경보감)
ambiguous = 50자 절단 다의 매칭 + 비용 > 0               (누구 돈인지 모른다)
no_data   = age 5까지 필요 source 보고서 부재             (모른다 — 0이 아니다)
```

- 근거: 현행 규칙(«roas_c ≥ target = good»)을 재사용하면 완벽한 성공(비용 0)이 `_window_metrics`의 `roas_c=None` → `_outcome_direction`의 `neutral`로 떨어진다 — 측정 대상과 규칙이 정반대다. 검색어 제외의 목적함수는 「손실 검색어 절단으로 낭비 비용 회수」이므로 성공 지표는 비용 정지다.
- **회수액은 신고하지 않는다**: 「비용 0 → 절감액 전액 회수」 산식은 D-NAO-175에서 이미 부호역전 사고를 낸 바로 그 오류다(BEP 유무만으로 +21,000/−119,000). 「골프」는 사전 매출 0원이라 `margin_lost`가 구조적으로 0 클램프 — 금액 환산은 S6 성적표의 일이고, `d1_st`는 **관측 사실(비용·클릭·전환·상태)만** 담는다.
- 주의 1건(문서화만): `stopped`는 「제외가 막았다」와 「그날 아무도 그 검색어로 안 왔다」를 구분하지 못한다. 그 구분은 08:25 생존감시(`verify_search_term_exclusions`, live_state)의 몫 — d1_st는 돈을, 생존감시는 장치를 본다. 두 신호의 교차 해석은 S6.
- 버린 대안: 검색어 ROAS 기반 판정(제외 전 대비 개선) — 제외의 성공은 「그 검색어에서 더 못 벎」이 아니라 「더 안 씀」이므로 축이 틀렸다.

`d1_st` 스키마(신규 키, 마이그레이션 없음):

```json
"d1_st": {
  "window": "2026-08-12",
  "match": {"term": "골프", "mode": "exact|prefix50", "matched_terms": 1},
  "by_source": {
    "shopping":   {"present": true,  "imp": 0, "clk": 0, "cost": 0, "conv_amt": 0},
    "expkeyword": {"present": false}
  },
  "cost_total": 0,
  "status": "stopped"
}
```

### B. 매칭 키 — 결정: **정확 일치 우선, 50자 절단만 접두 매칭, 다의는 상한 판정만**

- `len(target_id) < 50` → `search_term == target_id` 정확 일치. `len == 50` → `search_term LIKE target_id || '%'` 접두 매칭에 `mode:"prefix50"` 표기.
- 범위는 항상 `(campaign_id, adgroup_id)`로 한정 — 제외는 그룹 단위 장치(콘솔 그룹당 70건 상한)이고 diary가 `adgroup_id`를 이미 갖고 있다.
- 접두 매칭이 여러 검색어를 잡으면: **매칭 집합 전체의 비용 합은 진짜 비용의 상한**이다. 상한이 0이면 절단된 원문의 비용도 확실히 0 → 다의여도 `stopped` 판정 가능. 상한이 양수면 누구 돈인지 모르므로 `ambiguous` — 「0」이 아니라 「모른다」로 표면화.
- 정규화(공백·대소문자)는 **하지 않는다** — 제외 후보가 같은 테이블에서 나왔으므로 문자열은 동일해야 정상이며, §5-0에서 31,411원 재현으로 검증한다. 재현 실패 시에만 정규화 축을 별도 설계(추측으로 정규화를 넣으면 「모르는 매칭 실패」가 조용히 「0=성공」이 된다).
- 버린 대안: 전 검색어 유사도 매칭 — 이름 유사도 자동 매핑 금지(교훈 #117)와 같은 계열이라 기각.

### C. `source` 축 — 결정: **분리 기록·전환 비합산·비용만 합산, 필요 source는 30일 기왕력으로 정한다**

- `by_source`로 shopping/expkeyword를 따로 담고 각자 `present` 플래그를 갖는다. 전환·ROAS는 합산 안 함(expkeyword 전환 상시 0 — 합치면 희석). `cost_total`만 present source에 한해 합산.
- **필요 source** = 제외 시점 기준 직전 30일(원장 `cost_at_exclusion`과 동일 창)에 이 (campaign, adgroup, term)이 실적을 낸 source 집합. adgroup은 쇼핑/파워링크 중 하나이므로 실무적으로 단일 source로 정해진다(「골프」=shopping). 기왕력이 비면 두 source 모두 present여야 판정.
- EXPKEYWORD 지연: 보고서가 자동 생성되지 않아 생성요청→다음 크론 수집 — D+1 완결은 **확인 안 됨**이 맞다. 그래서 이 설계는 D+1 완결을 전제하지 않는다: 필요 source `present:false`면 **키를 쓰지 않고 다음 스윕 재시도**, age 5에 `no_data` 확정. 「비용 0=성공」과 「데이터 아직 없음」이 같은 숫자로 보이는 함정을 `present` 게이트가 끊는다.
- 버린 대안: d1처럼 무조건 즉시 기입 — 멱등 가드와 결합하면 거짓 0이 영구히 굳는다.

### D. additive의 경계 — 결정: **먹이기(소비)는 범위 밖, 오염 차단(수확 skip)은 범위 안**

- `_outcome_window`/`_outcome_direction`은 안 건드린다(금지선 3). d1_st를 소비시키려면 «검색어 제외의 good이 무엇인가»를 승률 체계에 편입해야 하고, 그것은 과거 승률의 의미 변경 = S8의 별도 결정이다.
- 그러나 **순수 기록만 하면 계약 목적에 미달한다**: wisdom은 `d7|d1` 하드코딩이라 d1_st에 오염되지 않지만, 뒤집으면 **search_term 행의 잘못된 캠페인 grain `d1`을 계속 먹는다**는 뜻이다. 진실을 아무도 안 읽는 칸에 적으면서 거짓을 계속 학습시키는 것은 「측정 정합」이 아니다. 그래서 harvest 단계에서 `target_type=="search_term"` skip을 S4에 포함한다 — 판정 규칙 변경이 아니라 **알려진 거짓 입력의 차단**이고, 과거 tally를 재해석하지 않으며(후보 27 불변), S8에서 skip을 걷어내면 그대로 복원된다(가역).
- d1_st의 가치 실현 시점: ① **즉시** — 08:35 해석문 컨텍스트(§5-3 프롬프트 1줄과 함께) ② **S6(8/17)** — 골프 첫 성적표의 검색어 grain 증거 ③ **S8** — wisdom 전환의 원료 ④ 그리고 합격기준 자체가 사람 관측이다.
- 버린 대안: skip 없이 완전 additive — 오염 지속. / `_grain_and_target` 폴백 자체 수정 — `d1` 의미 변경이라 금지선 2·3 위반.

### E. 4371과 후보 27 — 결정: **d1은 보존, d1_st는 자연 소급, 27 처분은 Jino 결정(마감 8/27)**

- **4371의 `d1`(43084)**: 멱등 가드 그대로, 재기입 안 한다. 이 값은 「캠페인 폴백 결함이 실재했다」는 라이브 증거이고, 재기입하면 stale 판별 근거가 무너진다.
- **4371의 `d1_st`**: **소급 기입되지만 강제가 아니다.** 소급 스캔은 60일 하한 안의 전 행을 매 스윕 훑으므로(diary_outcome.py:167-171), `"d1_st" not in outcome`인 4371은 배포 후 첫 08:35 스윕에서 자동으로 잡힌다. 원료(8/12 shopping 검색어 데이터)는 8/13 07:40에 이미 수집됐다 — 정규 크론이 실데이터로 채우는 것이지 억지 backfill이 아니다(금지선 1과 양립). 이것이 합격기준 ①이다.
- **wisdom 후보 27**: `good 1/bad 0`의 그 1은 남의 성적표(캠페인 d1)다. §5-2의 skip으로 추가 tally는 멈추지만, **숙성 게이트가 「pending & TTL 14일 경과 or occurrences≥3」**(wisdom_judge.py:63-67, 2026-08-13 코드 확인)이라 first_seen 8/13 기준 **8/27경 TTL 숙성으로 LLM 판사에 올라간다** — 거짓 근거 1건짜리 후보가 승격 심사를 받는 마감이 실재한다. 처분은 prod 데이터 수기 수정이므로 Jino 결정 사항(금지선 8).
- 버린 대안: 27을 코드로 자동 무효화 — 「측정이 잘못됐으니 결과를 지운다」를 코드가 소급 판단하는 선례가 되고, S8의 재해석 여지(d1_st로 재채점)를 없앤다.

---

## §7 열린 질문 — Jino 결정 필요 (모델 결정은 위에서 이미 내렸다)

1. **wisdom 후보 27 처분** (★마감 2026-08-27 — TTL 숙성으로 LLM 판사행): 권고는 `hidden`(수기 1행). `rejected`는 터미널이라 S8 재채점 때 같은 시그니처가 영구 봉인되고, 방치하면 거짓 근거 1건으로 승격 심사를 받는다. `hidden`은 §5-2 skip 하에서 재등장이 없어 사실상 동결이며, S8에서 skip을 걷으면 정규 경로로 부활한다. — 셋 중 택일.
2. **harvest skip(§5-2) 포함 승인**: 설계상 근거는 §6-D에 있으나, 계약 문언(「S4 = d1_st additive」)의 경계를 1파일만큼 넘는다. 포함/제외를 확정. (제외 결정 시에도 d1_st 기입부는 그대로 성립 — skip만 S8로 이월.)
3. **배포 시점**: `--restart-legacy` 50초 다운타임 + Mac IP 대만 전환 문제가 여전하다면 어느 시간대에 내리는지. (08:35 전 배포가 관측 리듬상 유리 — §5-5.)

---

## §8 Jino 결정 (2026-08-13, D-NAO-178) — §7 종결

Jino 원문: *"그래. 이 내용을 승인하고 여기서 handoff하고 다음 세션에서 이어하자"*

| §7 질문 | 결정 |
|---|---|
| ① 후보 27 처분 | **`hidden`** — 영구 기각(`rejected`) 아님. S8 재채점 여지 보존. |
| ② harvest skip 포함 | **포함한다** (Jino 원문: *"포함한다"*). |
| ③ 배포 시점 | 다음 세션에서 판단. 08:35 전이 유리하다는 §5-5는 유효. |

### ★추가 확정 — `d1` 기입 문턱 `age>=2` → `age>=4` (S4에 포함)

Jino 질문: *"한 번 채점한 값은 안 고치는 게 규칙인데, 이게 안전한건가?"*

- **멱등 자체는 옳다** — 매일 재채점하면 승률이 움직이는 표적 위에 쌓이고, 「언제 채점했나=무엇을 알고 채점했나」라는 stale 판별 근거가 무너진다.
- **그러나 안전한 건 첫 쓰기가 옳을 때뿐이고, 현행 문턱은 시간 하나뿐이다.** 원료 `naver_ad_daily`는 3일 창으로 사후 정정되는데(scheduler_service.py:324) `d1`은 **첫 정정 직후 한 번 보고 굳는다** — 정정이 2번 더 올 수 있는 시점. age 4면 그 날 07:30 수집이 해당 날짜의 마지막 기회이므로 창이 닫힌 값을 본다(§5-1의 `no_data` age 5 마감과 같은 계산).
- **기존 값 재기입이 아니라 앞으로의 쓰기 시점만** 바꾸므로 금지선 2와 충돌하지 않는다.
- **「먼저 정정 폭을 재보자」를 기각한 근거**: `ad_daily_ingest.py:42`가 재수집 시 그 날짜 행을 통째로 `delete` 후 재삽입한다 → **정정 이력이 원리적으로 남지 않는다.** 소급 측정이 불가능하므로 「측정 먼저」는 사실상 「몇 주간 이른 값을 계속 굳히기」다. 손해가 비대칭이다(늦춰서 손해=학습 2일 지연·회복 가능 / 안 늦춰서 손해=거짓값 영구 동결·어느 행인지도 모름).
- **미검증 전제(구현 첫 단계에서 전수 확인)**: `d1` 소비자가 wisdom 수확·해석문 둘뿐이라는 것. 지연에 민감한 소비자가 더 있으면 이 항목을 재판단한다.

### ★구현 순서 제약 — skip 먼저, hidden 나중 (코드 실측 2026-08-13)

`hidden`은 **터미널이 아니다**. `_TERMINAL_STATUSES = {"promoted", "rejected"}`(wisdom_candidates.py:30)이고, `hidden` 후보는 같은 시그니처로 **새 diary 행**이 오면 `pending`으로 **부활**한다(wisdom_candidates.py:160 — 의도된 Ebbinghaus 재노출 설계).

- diary 4371 자신은 `source_entry_ids`에 이미 있어 재스캔으로는 부활하지 않는다(`if entry.id in ids: continue`).
- 그러나 **skip 배포 전에** 같은 캠페인(`cmp-a001-02-000000008902804`)에서 새 검색어 제외를 `record_execution`으로 기록하면 후보 27이 되살아나고 tally가 는다.
- → **순서: ②skip 배포 → ④후보 27 `hidden`.** 반대로 하면 창이 열린다.

### 확정 범위 5건 (다음 세션 착수 단위)

1. `d1_st` 추가 — 검색어 grain, `status` 4값, `present` 게이트 (§6-A·B·C)
2. 검색어 행 wisdom 수확 skip (§5-2)
3. `d1` 기입 문턱 `age>=2` → `age>=4` (§8)
4. 후보 27 → `hidden` (**3·2 배포 이후에**)
5. `diary_reflection._SYSTEM` 1줄 (§5-3)
