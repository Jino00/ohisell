# PLAN — 스프린트 PX: 파워링크 검색어 자동 제외 + in-out 재심사 루프

> 설계=Fable(2026-07-22, 라이브 실측 기반) · 구현=PX1~PX3 Opus / PX4 Sonnet · GATE 적대 리뷰 필수 · codex는 07-23 소급(한도 회복일).
> 배경: Jino 지시(D-NAO-78 세션) — **"성과 기반 자동운영이어야지 수백 개 검색어를 하나하나 Confirm 못 한다"** → SS3-A(파워링크 제외)를 Confirm-only에서 성과(비용) 기반 자동으로 전환. 전환 귀속 불가(파워링크 구조적)는 **in-out 재심사 루프**(잘못 자른 것 자가 교정)로 보완. SS 계획서 §난제 5의 후속.

---

## §0 방향 고정 (변경 금지)

1. **목적함수 불변(D-NAO-1/59)**: 총이익 극대화. 제외 = 손실 검색어 절단으로 낭비 비용 회수.
2. **in-out 생태계(전략 v2 §1④)**: 제외 = 사형이 아니라 상태 전이. 주기 재심사·자동 복귀. 오컷은 감수하되 재심사가 자가 교정(Jino 승인 방향).
3. **집행 범위 불변(BM §0 금지선과 동일)**: 자동 실쓰기는 `auto_operate=1`(ours) ∧ 파워링크(WEB_SITE) 캠페인만. **대행사 캠페인엔 절대 실쓰기 없음** — 브리핑만.
4. **SS4 승격 경로 무변경**: 이 스프린트는 제외(SS3-A)만 건드린다. 승격(promote)은 제안·영구 Confirm 그대로 — 내일(07-23) BM 관문 ④(SS4 교차 관측)를 오염시키지 않기 위한 격리이기도 함.
5. **쇼핑 분기 무변경**: 쇼핑 제외는 API 불가(SS §실측-0) — 브리핑 유지.
6. **봉투 유지**: 일일캡·킬스위치·전건 change_log·되돌림 경로 실증(`delete_restricted_keywords` 왕복 검증 완료).

## §0.5 라이브 실측 (2026-07-22 — 이 설계의 근거, 재조사 불필요)

**현행 게이트가 ours 파워링크에서 구조적으로 0건인 3중 차단(전부 prod 실측):**

| # | 차단 | 실측 |
|---|---|---|
| 1 | clk≥10 도달 불가 | ours 파워링크=P_Test 1개. 14일 9,382 검색어 중 **최대 clk=5**(81개만 clk≥1, 계 88,860원). clk≥10 헤드 22개는 **전부 대행사 캠페인**(갤럭시 파워링크 16개 46만원 등, 계 57만원/14d) |
| 2 | 화이트리스트 전량 보호 | P_Test 비용 상위 검색어 전부 "아이패드/아이폰" 포함 → 브랜드 토큰 substring 보호에 전건 걸림 |
| 3 | margin fail-closed | P_Test 전 그룹 `adgroup_unit_price` **unavailable**(아이패드 상품군 원가 미확정 — naver_product_bep has_cost=1은 지문방지 PET 계열만, margin ~10,500원대) → cost≥공헌이익 게이트 전 그룹 스킵 |

**추가 실측:**
- P_Test는 "아이패드 강화유리"가 아니라 **아이패드 필름류 캠페인**(그룹: _종이질감 다수·_6H BLC 다수). "아이패드종이질감필름" 검색어는 종이질감 그룹에선 정상 의도 — 검색어 단독으론 낭비 판정 불가.
- **그룹 grain 전환은 가용**(naver_ad_daily 상세행): 예) `grp-…841583`(10세대_종이질감) 30d cost 101,474·conv 7·amt 111,300(ROAS 1.10) / `grp-…841582`(7-8-9세대_종이질감) 30d cost 19,767·**conv 0**(손실 그룹 실존). → **그룹 순손실 프록시 성립**.
- naver_ad_daily의 `adgroup_id='__backfill__'` sentinel 행은 그룹 집계에서 반드시 제외(2배 함정 기존 교훈).
- pending 제외 제안 0건(위 3중 차단의 결과 — "표본 게이트 미달=정당 절제"의 실체).

## §1 판정 규칙 (PX1 — 파워링크 전용 게이트, 쇼핑과 분리)

파워링크(source='expkeyword') 제외 후보 = 아래 **전부** 충족(fail-closed):

| # | 게이트 | 값(초기) | 근거 |
|---|---|---|---|
| 0 | **스코프**: 캠페인 `auto_operate=1` ∧ WEB_SITE | — | §0 3. 대행사 캠페인 후보는 별도 브리핑 채널로만(§4) |
| 1 | rolling 창 | **30일**(쇼핑 14일과 분리, `_PL_WINDOW_DAYS`) | 실측 1: 저볼륨 롱테일 표본 누적. 보존 365일이라 가능 |
| 2 | 최소 클릭 | **clk ≥ 5** (`_PL_MIN_CLICK`) | 실측 1: 14d 최대 5 → 30d 창에서 5~10 도달 가능. 쇼핑 10과 분리 |
| 3 | 최소 비용 | cost ≥ margin×1, **margin unavailable 시 `_PL_FALLBACK_MIN_COST=10,000원` 폴백** | 실측 3 해소. 폴백값=확정 원가 상품군 공헌이익 하한(~10,500원) 정렬. "공헌이익 한 단위를 태웠는데 귀속 증거 0" |
| 4 | **그룹 순손실 프록시** | 그 adgroup의 naver_ad_daily 30d (conv_direct+indirect_amt)/cost < 그룹 target ROAS(`resolve_adgroup_target_roas`, product_bep→account_default 폴백) ∧ cost>0. sentinel(`__backfill__`) 제외 | "부모 순손실" 근사(EXPKEYWORD에 부모 키워드 없음 — 그룹이 최소 grain). **그룹이 BEP 이상이면 그 그룹의 확장 검색어는 집합적으로 벌고 있는 것 → 자르지 않음**(오컷 방지 1차) |
| 5 | **화이트리스트 파워링크 변형** | 보호 = 검색어가 **그 그룹 매핑 상품의 "제품형 토큰"**(종이질감·저반사·무광 등)을 포함할 때. **디바이스/브랜드 토큰(아이폰·아이패드·갤럭시·맥세이프 등 `_PL_DEVICE_TOKENS` 스톱리스트)은 보호 토큰에서 제외** | 실측 2 해소: 확장검색어는 거의 전부 디바이스명 포함이라 브랜드 substring 보호=자동 제외 사문화. 제품형 토큰 보호는 유지(그룹 핵심 의도어 오컷 방지 2차). 그룹 매핑 상품 부재 시 전역 `_SS_WHITELIST_TOKENS`에서 디바이스 토큰만 뺀 집합으로 폴백 |

- 전환 보호(§1 1 기존): naver_search_term_daily 전환 컬럼은 파워링크에서 구조적 0이지만 게이트는 유지(비용 0). 장래 데이터 생기면 자동 보호.
- 쇼핑(source='shopping') 판정은 **기존 그대로**(창 14일·clk≥10·margin fail-closed·기존 화이트리스트) — 회귀 0.

## §2 in-out 재심사 상태기계 (PX2·PX3)

신규 테이블 `naver_search_term_exclusion`(alembic, head `d9e0f1a2b3c4` 뒤):
`id, campaign_id, adgroup_id, search_term(300), restrict_kwd_id(50, nullable), status(excluded|probation|restored), cycle(int, 최초 1), excluded_at, last_transition_at, next_review_at, probation_until(nullable), cost_at_exclusion(int), created_at/updated_at` + Unique(adgroup_id, search_term).

```
[후보 판정(§1 통과)] ─(자동 실쓰기: add_restricted_keywords)→ [excluded]
    cycle=n, next_review_at = 오늘 + min(30×n, 90)일
[excluded] ─(next_review_at 도래, 재심사 개방: delete_restricted_keywords)→ [probation]
    probation_until = 오늘 + 14일 (재노출 관찰창)
[probation] ─(probation_until 도래, §1 재판정)→
    ├─ 다시 후보(여전히 §1 전부 충족) → [excluded] cycle=n+1 (백오프 30→60→90 cap)
    └─ 후보 아님(그룹 회복·비용 미달 등) → [restored] (행 보존=기억. 이후 §1 재충족 시 일반 경로로 재제외, cycle 승계)
```

- **복귀(=성과 자가 교정)**: probation 중 그룹이 BEP 회복했거나 그 검색어가 더는 §1을 못 채우면 살아남는다 — 전환 귀속 없이도 "시장/그룹 상태 변화"가 복귀 신호. Jino 방향("잘못 자른 것 자가 교정") 구현.
- restored/probation 행이 있는 (adgroup, term)은 신규 제외 시 cycle 승계(+1)·행 upsert(신규 insert 아님).

## §3 실행 배선 (PX2 — 기존 관례 재사용)

- **자동 발사 = exploration BX2 관례 복제**: ss_lane이 §1 통과 후보를 `status='approved'` + `approval_source=APPROVAL_SOURCE_SS_EXCLUDE('ss_exclude')` 제안으로 생성(기존 상수 — 코드에 이미 정의·킬스위치 화이트리스트 선등록됨, "미래 활성화" 주석의 그 시점이 지금) → `naver_execution_harness.execute()`(클레임·킬스위치·guardrail·`_execute_search_term_exclude` 기존 실행자) 같은 08:50 레인에서 즉시 실행.
- 실행 성공 시 WriteResult에서 `restrict_kwd_id`(rst-…)를 회수해 `naver_search_term_exclusion` upsert(→§2). 실행자 반환 경로에 id 노출이 없으면 실행자 확장(after_value에 이미 있으면 파싱 재사용 — 구현 시 실측).
- **재심사 개방(PX3)**: 같은 08:50 레인 스텝 — `next_review_at ≤ today ∧ status='excluded'` 행을 `delete_restricted_keywords`로 개방(change_log `[검색어제외 복귀]`·킬스위치 존중), probation 전이. `probation_until ≤ today` 행은 §1 재판정 후 전이. **복귀도 일일캡 적용**.
- **봉투**: 신규 자동 제외 ≤ `_SS_DAILY_EXCLUDE_CAP=10`/일(기존 실행자 캡 로직 재사용) · 복귀 개방 ≤ 10/일 · 그룹당 제외 슬롯 60에서 롤링 큐레이션(기존 §1 5 관례, 슬롯 조회 후 초과 시 이번 라운드 스킵+브리핑) · 킬스위치 1방 전체 정지 · 전건 change_log.
- Confirm-only 경로 제거: 파워링크 후보를 pending으로 쌓지 않는다(§0.5 "수백 pending" 원인 제거). 스코프 밖(대행사) 후보는 **제안 자체를 만들지 않고** 브리핑만(§4).

## §4 브리핑 (PX4 — Sonnet, BM P5 채널 관례)

1. **자동 제외/복귀 브리핑**: 실행이 있었던 날만 diary(observe)+Slack — "파워링크 자동 제외 N건(검색어·그룹·30d cost)·복귀 M건". 없던 날은 침묵(예외 브리핑 원칙 D-NAO-79).
2. **대행사 파워링크 고비용 검색어 브리핑**: 스코프 밖(비 auto_operate) 캠페인에서 30d cost ≥ 30,000원 ∧ clk ≥ 10 검색어를 주 1회(일요 bm_deep과 같은 리듬 또는 일 레인에서 신규 진입 시만) diary 브리핑 — 실측된 46만원/14d급 낭비를 Jino가 대행사에 전달할 수 있게. 실쓰기 없음.
3. **드릴다운 GET 1종**: `/api/naver/ad/search-term/exclusions` — 상태기계 전 행(status·cycle·next_review_at) + 오늘 제외/복귀. (기존 라우터 prefix `/api/naver/ad`.)

## §5 페이즈·라우팅

| 페이즈 | 내용 | 모델 |
|---|---|---|
| PX1 | judge 파워링크 게이트 분리(§1, 순수함수+테스트) | Opus |
| PX2 | 상태 테이블 마이그·자동 발사 배선·restrict_kwd_id 회수(§2·§3) | Opus |
| PX3 | 재심사 루프(개방·probation·백오프)(§2·§3) | Opus |
| PX4 | 브리핑·드릴다운(§4) | Sonnet |
| GATE | 적대 리뷰(별도 에이전트) → 전체 pytest 회귀 0 → safe_deploy → 라이브 검증 | — |

**GATE 최우선 검증**: ①스코프 누출 0(대행사 캠페인 실쓰기 절대 0 — 테스트로 고정) ②캡 초과 실쓰기 0 ③킬스위치 존중(제외·복귀 양쪽) ④SS4 승격·쇼핑 분기 회귀 0(byte-동일 수준) ⑤전환 보호 유지 ⑥probation 상태에서 중복 제외 없음 ⑦sentinel 행 오염 0.

## §검증 (원칙22 라이브 합격 시나리오)

1. 배포 후 prod에서 judge 1회 실행(read-only) → 파워링크 후보가 §1 게이트 순서대로 산출/차단되는 근거 로그 실측.
2. 익일 08:50 레인: 자동 제외 실쓰기 발생 시 change_log dry_run=0·`[검색어제외]`·API 재조회로 restricted-keywords 반영 확인·상태 행 excluded 생성. 후보 0이면 게이트별 차단 사유 실측으로 "정당 절제" 판정(§0.5 3중 차단이 해소됐는데도 0인지 구분).
3. 재심사는 최초 30일 후 도래 — 유닛으로 시간 주입 검증 + 상태 행 next_review_at 실측.
4. 대행사 브리핑 diary 실기록 확인.

## §체크리스트

- [x] PX1 judge 파워링크 게이트(Opus) — `cd44911`
- [x] PX2 상태 테이블+자동 발사(Opus) — `373bee4`(alembic `e0f1a2b3c4d5`, restrict_kwd_id=WriteResult.created_ids 실측 회수, 슬롯=상태 테이블 근사)
- [x] PX3 재심사 루프(Opus) — `c51028f`(킬스위치 OFF=상태기계 전면 동결·가장 보수적 해석)
- [x] PX4 브리핑·드릴다운(Sonnet) — `07f58c5`(예외 브리핑=실행 있던 날만·대행사=일요 주간·GET /search-term/exclusions)
- [x] GATE 적대 리뷰 + 전체 테스트 회귀 0 — **PASS(P1 0·P2 3건)** → P2 전건 수정 `48f4516`(복귀 캡 재카운트 백스톱·upsert 경합 수렴·그룹명 제품형 토큰 보호·summary GROUP BY) + 중복 상수 정리 `192537f`. 최종 **2866 passed·회귀 0**. 잔여 P3-1(개방 후 commit 실패 orphan=failed change_log로 관측 가능)·P3-3(lane skip 직접 테스트)은 기록만.
- [x] safe_deploy 배포 + 라이브 검증(§검증) — **2026-07-22 13:52 KST** 7파일 CAS 통과·alembic `e0f1a2b3c4d5`·재시작·부팅 200. **라이브 judge 실측**: 자동발사 대상 1건(`아이패드종이필름`, 10세대_종이질감 그룹, 30d clk20·cost22,854·그룹 ROAS 1.10<target — 첫 자동 제외는 익일 08:50 레인) / 대행사 브리핑 17건(갤럭시S26필름 15.7만/30d 등·실쓰기 0) / 쇼핑 0·승격 378(기존 로직 불변). 3중 구조 차단(§0.5) 해소 실증.
- [ ] 익일 08:50 레인 첫 자동 제외 라이브 관측(change_log dry_run=0·상태 행 excluded·restricted-keywords API 반영)
- [ ] codex 소급 리뷰(07-23 09:30 예약됨, BM·SS 소급과 함께 — challenge 모드)
- [ ] (후속) 아이패드 상품군 원가 확정 시 margin 폴백→실측 margin 자동 전환(코드 변경 불필요 — 게이트 3이 이미 margin 우선)
