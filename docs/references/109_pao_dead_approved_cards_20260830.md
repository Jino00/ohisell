# ref 109 — PAO 「승인됐는데 영원히 안 나가는 카드」 + 승인 배선 규명 (2026-08-30)

> **소관: `docs/tracks/active/track_naver-ad-optimization.md`** (PAO 최적화 엔진)
> 작성: 세션 `eef672ce` — **PAO UI/UX 트랙**. 엔진은 소관 밖이라 **코드·prod·스코프를 일절
> 건드리지 않고** 읽기 전용 조사만 하고 넘긴다.
> 발단: Jino가 성과 화면(관할 밴드)을 보고 *"어제 PAO가 돌리긴 한거잖아. 그렇지 않아?"* →
> *"왜 어제 우리가 돌리는 광고그룹의 실제로 바꾼게 없는지 다시 설명해봐"* → *"결국 버그인거네"*.

## 0. 한 줄

**손은 있는데 묶여 있었다** — 자동으로 나갈 수 있는 종류(입찰)는 스코프 안 그룹에 재료가 없었고,
재료가 있는 그룹엔 갈 권한이 없었으며, 그 사이 **승인만 되고 영원히 못 나가는 카드가 쌓이고 있다.**

## 1. 배경 실측 — 어제(2026-08-29)

캠페인 `cmp-a001-02-000000008425541`이 08-29 **12:53**에 `optimizer` none→ours가 되어 PAO 관할이
됐다(스코프 행은 같은 날 **00:25** 생성, 광고그룹 **1개**만 enabled).

| 층 | 어제 실적 |
|---|---|
| 관할을 가졌나 | **예** — 12:53부터 |
| 판단을 했나 | **예** — 제안 **264건** 생성, **94건 자동 승인**(`auto_op_hr`) |
| 광고 계정에 썼나 | **아니오 — 0건** |

근거: `change_log`의 `flight_pacing` 72건이 **전부 `dry_run=1`**. `dry_run=0`인 유일한 1건은
`external_bid_change`(= 대행사 변경을 우리가 **감지한** 기록이지 우리가 쓴 게 아니다).
승인 94건의 `executed_change_log_id`는 전건 NULL.

★**PAO의 마지막 실집행은 2026-07-30**(14건). 그 뒤 **31일째 0건**.

## 2. ①[최우선] 수리는 main에 있는데 prod에 없다 — 지금도 쌓인다

커밋 **`bd8e7572`**(2026-08-29 21:46, *"엔진이 승인하는 «문»을 하나로 — 스코프 밖 죽은 카드
119건의 원인"*)가 origin/main에 있으나 **prod 미배포**다.

`auto_operator.engine_approve` docstring이 원인을 스스로 적어 뒀다:

> 스코프 검사(`_scope_hold_reason`)가 **레인마다 각자** 있었고 실제로는 일 레인 2곳에만 있었다.
> 나머지 세 레인(시간당·탐색·스파이럴)은 스코프 밖 제안을 그대로 `approved`로 커밋했고,
> harness가 쓰기 직전에 거부해(`ScopeGuardError`) **영원히 실행되지 않는 approved 카드**가 남았다.

검증(읽기 전용):
```bash
ssh -o BatchMode=yes sellc.ohitech.co.kr \
  "grep -c 'def engine_approve' /home/ubuntu/ohisell/backend/app/services/naver_ad/auto_operator.py"
# → 0   (main엔 있음)
```

미배포 코드 파일 4개(+테스트 4개):
```
backend/app/services/naver_ad/auto_operator.py
backend/app/services/naver_ad/naver_execution_harness.py
backend/app/services/naver_ad/cold_start_bid_lane.py
backend/app/routers/naver_ad.py
```

**지금 쌓인 양** (2026-08-30 11:3x KST 실측):

| approval_source | 건수 |
|---|---:|
| `auto_op_hr` | 103 |
| `explore_op` | 30 |
| **합계** | **133** |

그중 **39건이 오늘(08-30) 새로 생겼다.** 수리가 prod에 없는 한 매일 늘어난다.

```sql
select approval_source, count(*) n from naver_proposals
where status='approved' and executed_change_log_id is null group by 1;
```

★**해로움의 성격**: 광고 계정 실쓰기는 0이라 **돈 손해는 없다.** 대신 docstring이 적은 대로
**콘솔이 그 카드들을 「실행 가능」으로 표시**한다(`real_write_blocker`가 전건 None).
원장이 「승인됐다」와 「승인됐지만 영원히 못 나간다」를 **구별하지 못한다.**

**할 일**: 배포 + 기존 133건의 처분(그대로 둘지·상태를 바꿀지).
`scripts/safe_deploy.sh <파일들> --restart`.
⚠️ prod에 미배포 마이그레이션이 또 있으면 가드가 막는다 — 그때는 `--migrate`가 필요하고 그건
**다른 트랙의 마이그레이션을 대신 올리는 것**이라 Jino 판단이 필요하다(2026-08-30 선례: 원가 트랙의
`pgprice1s1a`가 PAO UI/UX 트랙의 배포를 막았고 Jino가 ⓐ「같이 올린다」로 지시했다).

## 3. ② 제외·승격은 실행이 열려 있는데 승인이 안 붙는다 — 의도인가 배선 누락인가

`naver_execution_harness.py:269-271`:
```python
OPEN_ACTIONS: frozenset[str] = frozenset(
    {"add_negative_keyword", "update_bid", "set_user_lock", "update_budget", "exclude_search_term"}
)
```
제외 계열이 **실행 열림 목록에 있다.** 그런데 실제 제안 상태(2026-08-01 이후):

| proposal_type | status | 건수 | 최근 |
|---|---|---:|---|
| `negative_keyword` | pending | 11 | 08-29 |
| `search_term_promote` | pending | **280** | 08-29 |
| `search_term_promote` | **expired** | **300** | 08-15 |

자동 승인은 **입찰에만** 붙는다(최근 5일: `bid_up` 85 + `bid_down` 15 = `auto_op_hr`,
`bid_up_explore` 20 = `explore_op`. 제외·승격 **0건**).

⇒ **`search_term_promote` 300건이 아무도 안 눌러 만료됐다.** 의도된 사람-결재 유보인지, 자동운영
레인이 그 종류를 대상에서 빠뜨린 배선 누락인지 **규명이 필요하다.**

참고: 어제 스코프 **안** 그룹에 나온 제안 6건이 전부 이 계열이었다 —
제외 후보 5건(각 **82,753 / 48,526 / 36,020 / 32,705 / 29,402원** 쓰고 전환 귀속 없음),
승격 후보 1건(*"대행사 검증 키워드 교차 — 사람이 이미 등록한 정답지, 확신도↑"* + 직접전환 1건).
자동으로 나갈 수 있는 유일한 종류(입찰)는 그 그룹에 **제안 0건**이었다(클릭 6·전환 0으로 표본 부족).

## 4. ③ 스코프가 거래 거의 없는 그룹 1개에 걸려 있다

`naver_adgroup_scope` 행은 전체 **1건** — `grp-a001-02-000000070523564`(role=NULL, enabled=1).

| 날짜 | 광고비 | 클릭 | 전환 | ROAS |
|---|---:|---:|---:|---:|
| 08-22 | 64,349원 | 48 | 6 | 1.57 |
| 08-24 | 41,041원 | 33 | 2 | 0.82 |
| 08-26 | 9,609원 | 13 | 3 | 5.25 |
| 08-27 | 15,854원 | 19 | 1 | 1.06 |
| 08-28 | 7,586원 | 9 | 0 | 0.00 |
| 08-29 | 5,254원 | 6 | 0 | 0.00 |

같은 캠페인의 **다른 8개 그룹**엔 어제 입찰 제안 94건이 나와 자동 승인까지 갔다(스코프 밖이라 막힘).

⇒ **엔진이 판단은 활발한데 손댈 수 있는 자리엔 할 일이 없다.** 스코프를 어디에 걸 것인가는
D-NAO-244 계약 사항이므로 **Jino 승인** 없이 바꾸지 말 것.

## 5. ④ 부수 발견 2건 (고치지 않음)

- **`pao_scope_roster.py:351`** — `in_scope`를 `bool(sr and sr["enabled"])`로 계산해
  **D-NAO-244 진리표를 안 따른다.** 진리표는 「`auto_operate` ON + 스코프 행 없음 → 전 그룹 ON」인데
  이 코드는 전 그룹 False로 본다. 현재 prod엔 스코프 행이 있어 증상이 안 나지만,
  `adgroup_scope.py` docstring이 경고한 **「조건식 복제가 갈라진다」의 실현**이다.
  화면 `/naver-ad/scope`가 이 값을 쓴다.
- **`routers/naver_ad.py:986` 부근** — `adgroup_scope` PUT writer가 **갱신에도** `before_value=None`을
  쓴다. 되감기(`ownership_timeline`, ref: 성과분리 목표)가 「그 시점엔 행이 없었다」로 읽어
  **과거 관할을 넓게** 재구성할 수 있다. prod는 생성 1행뿐이라 아직 미발현.

## 6. 착수 시 주의

- 이 문서를 쓴 세션은 위 항목에 **코드·prod·스코프를 일절 건드리지 않았다.** 조사는 전부 읽기 전용.
- ①의 배포는 되돌릴 수 있으나 prod 변경이다. ③은 광고 집행 범위를 바꾸는 결정이라 Jino 승인 필요.
- 착수 전 `docs/references/82_pao_north_star_20260819.md` **§목차를 훑고** 해당 M의 관련 절을
  **전부** 읽을 것(D-NAO-227 — §1·§6만 읽어 샌 사고가 있었다).

## 7. 관련

- 성과 화면 관할 밴드(이 조사의 출처): `docs/contracts/CONTRACT_pao_performance_ownership_split.md` ·
  `docs/tracks/active/track_pao-ui-ux.md` · PR #574·#577
- 관할 판정 단일 소스: `backend/app/services/naver_ad/ownership_timeline.py`
- 진리표 정본: `backend/app/services/naver_ad/adgroup_scope.py`(D-NAO-244)
