# PLAN — 스프린트 SS: 검색어 ROAS 레이어 (D-NAO, 전략 v2 §3 로드맵 3번)

> 설계=Fable · 구현=SS1 Sonnet / SS2·SS3 Opus(GATE 적대 리뷰 + codex 왕복, 원칙19) · SS4 Sonnet(제안만).
> 배경: ref 34 대행사 대조에서 "검색어 ROAS 판정·그룹 분리"가 최대 갭(전략 v2 §4). 검색어 성과 수집은 이미 가동(`naver_search_term_daily`, 07-04~, 15만행) — **전환 데이터만 공백**(전략 v2 §2 감지층 "검색어 전환 수집만 공백 → SS"). 이 스프린트가 그 공백을 메우고 BEP 기반 제외 자동화까지 배선한다.

---

## §0 방향 고정 (변경 금지 — 이 스프린트 내내)

1. **목적함수 불변(D-NAO-1/59)**: 총이익 극대화. 검색어 제외는 "target ROAS를 못 지키면서 매출도 못 늘리는(=클릭당 확정 손해) 검색어"를 잘라 낭비 비용을 회수하는 것 — ROAS 최대화가 아니라 **손실 검색어 절단으로 총이익 방어**. 승격(SS4)은 총이익 성장 레버.
2. **in-out 생태계(전략 v2 §1④)**: 제외 = 사형이 아니라 **상태 전이**. 기억 보존·주기 재심사·자동 복귀 여지를 남긴다(사람 대행사의 영구 컷과의 차별점). 제외 검색어도 change_log·상태 테이블에 남겨 롤링 큐레이션.
3. **표본 게이트 절대 준수**: 검색어 판정은 일~주 리듬(전략 v2 §1④ "검색어/키워드 in-out=일~주, 표본 한계"). 소수 클릭으로 제외 금지 — 최소 클릭 표본 게이트가 SS2 핵심 보호막.
4. **생성류는 영구 Confirm(전략 v2 §2 안전층)**: SS4 파워링크 키워드 등록은 **제안만**, 실쓰기는 영구 Jino Confirm. 절대 자동 발사 금지.
5. **쇼핑 API 제외 불가 = 확정 실측(아래 §실측-0)**: SS3 실행 배선은 쇼핑/파워링크를 분기한다. 이 분기는 프로브 증거에 근거 — 임의 통합 금지.
6. **개방 순서 존중(D-NAO-16)**: 제외키워드는 개방 순서 1단계(제외키워드→정지재개→입찰→예산)로 이미 가장 앞. 단 **자동 실쓰기 개방은 파워링크에 한정**, 봉투(§1) 전제.

---

## §0.5 SS0 실측 결과 (이 스프린트 착수 근거 — 재조사 불필요)

- **검색어×전환 보고서 확정**: `SHOPPINGKEYWORD_CONVERSION_DETAIL`(reportTp enum). 15컬럼·헤더없음, `AD_CONVERSION`(13컬럼) 대비 **+2 오프셋**:
  | col | 의미 | AD_CONVERSION 대응 |
  |---|---|---|
  | 0 | 일자 | CONV_COL_DATE=0 |
  | 2 | 캠페인ID | CONV_COL_CAMPAIGN=2 |
  | 3 | 그룹ID | CONV_COL_ADGROUP=3 |
  | 4 | 검색어 텍스트 | CONV_COL_KEYWORD=4(여기선 keyword_id) |
  | 10 | 기기(M/P) | CONV_COL_ACTION=10 ← **오프셋 주의** |
  | 11 | 직접1/간접2 | CONV_COL_DIRINDIR=9 |
  | 12 | 전환유형(purchase/add_to_cart) | CONV_COL_ACTION 대응 |
  | 13 | 전환수 | CONV_COL_CNT=11 |
  | 14 | 전환매출 | CONV_COL_VALUE=12 |
  > **±2 오프셋은 SS1의 최대 실수 위험** — `naver_sa_ad_fetcher.py:33-54`의 기존 `CONV_COL_*`를 재사용하면 안 된다. SS 전용 `STCONV_COL_*` 상수 신설.
- **파워링크 검색어×전환은 무용**: `AD_CONVERSION_DETAIL`은 검색어 컬럼이 항상 `'-'`(확장검색 버킷). 파워링크는 검색어별 전환 귀속 불가 → **SS2 판정은 쇼핑(SHOPPINGKEYWORD_CONVERSION_DETAIL)만**. 파워링크 검색어는 성과(clk/cost)만 있고 전환 없음 → 제외 판정에 전환게이트 적용 불가(§난제 5).
- **원시 성과 보고서 전환 컬럼은 죽어 있음**: `SHOPPINGKEYWORD_DETAIL` col15는 전부 0 고정 확정 → 전환은 반드시 별도 CONVERSION 보고서로.

## §실측-0 쇼핑 제외 쓰기 프로브 (2026-07-21, prod 가역 왕복 — 결정적)

| 대상 | adgroupType | 요청 | 결과 |
|---|---|---|---|
| `grp-a001-02-000000059830952` | SHOPPING | POST restricted-keywords `["제외프로브임시9999"]` (type=KEYWORD_PLUS_RESTRICT) | **HTTP 400, code 3728** — `"This is a campaign type that does not support keyword plus impression restricted keywords."` (쓰기 미발생, before/after 모두 `[]` — 원복 불필요) |
| `grp-a001-01-000000070111142` | WEB_SITE | 동일 왕복(writer 정규 경로) | **성공** — created `rst-a001-00-000001865538666` → delete → `probe_kw_remaining=[]` (원복 확인) |

**결론(확정)**: ncc restricted-keywords API로 **SHOPPING 그룹 제외키워드 쓰기 불가**. 파워링크(WEB_SITE)는 정상. 따라서:
- **SS3-A (파워링크)** = `add_restricted_keywords` API 자동 제외(봉투 §1 하에서 자동 발사 허용).
- **SS3-B (쇼핑)** = **제외 후보 브리핑 + Jino 콘솔 수동**. API 자동화 불가.

> **단, 미결(§실측 잔여)**: 400 사유는 **type-specific**(KEYWORD_PLUS_RESTRICT). 2025-02-20 네이버 공지는 "쇼핑검색광고 제외 검색어 유형 추가"를 안내(콘솔에 존재) — 쇼핑이 **다른 restrict type**를 지원할 가능성은 프로브가 배제하지 못한다. `_RESTRICT_TYPE` 외 enum은 swagger에서 확인 안 됨(추정 금지). SS3-B는 이 미결을 "콘솔 수동"으로 안전 봉인하되, SS 후속에서 (a) 콘솔 쇼핑 제외의 실제 API 트래픽 리버스 (b) 브라우저 자동화 경로를 별도 검토(스코프 밖 백로그).

---

## §1 안전 봉투 (SS3-A 파워링크 자동 실쓰기의 전제)

| # | 가드 | 내용 |
|---|---|---|
| 1 | **전환 검색어 제외 절대 금지** | purchase 전환수 ≥1인 검색어는 어떤 경우에도 제외 후보 진입 불가(fail-closed). in-out 원리상 전환은 "살아있는 증거" |
| 2 | **최소 클릭 표본 게이트** | 누적(rolling N일, §난제 2) `clk ≥ _SS_MIN_CLICK` ∧ `conv_purchase_cnt = 0` ∧ `cost ≥ _SS_MIN_COST`일 때만 후보. 제안 초기값 `clk≥10 ∧ cost≥ (product_bep 공헌이익 × 1)` — **수치는 SS1 수집 후 실분포로 캘리브레이션**(단정 금지, §실측 2) |
| 3 | **핵심어 화이트리스트** | 상품명/브랜드 핵심어(예: 아이폰·맥세이프·강화유리·지문방지 등)는 제외 금지. 검색어가 화이트리스트 토큰을 포함하면 후보 제외(오컷 방지) — 리스트는 product_master 매핑 기반 + Jino 확정 |
| 4 | **일일 신규 제외 캡** | SS3-A 자동 실쓰기 1일 신규 제외 ≤ `_SS_DAILY_EXCLUDE_CAP`(제안 10~20). BX(탐색)의 D-NAO-71 "수량캡 제거"와 달리 **제외는 되돌림 비용이 있는 비대칭 액션**(잘못 자르면 매출 소실)이라 캡 유지 — 봉투 축소는 Jino 승인 후 |
| 5 | **그룹당 제외 슬롯 한도** | 제외키워드 그룹당 최대 개수는 공식 숫자 문서 확인 안 됨(공지 "개수 변동 없음"만). 한도 도달 시 롤링 큐레이션(최저 기여 제외어 회수 후 신규 삽입) — **한도 수치는 SS3-A 라이브 실측**(70 vs 140, 전략 v2 §3) |
| 6 | **되돌림 함수 존재 확인** | `delete_restricted_keywords`(writer:198) 실재 확인됨(프로브 왕복 실증). SS3-A는 등록 전 삭제 경로가 살아있음을 전제 |
| 7 | **킬스위치·전건 기록** | 킬스위치 1방 전체 정지 존중 · 전 제외/복원 change_log(`NaverChangeLog`, 신규 event_type/action, rationale `[검색어제외]`) · 콘솔 즉시 역조작 가능 |

---

## §2 구조 (원칙18 — 재사용 최대)

```
[SS1 수집] sync_naver_search_term_job (기존 07:40 크론, scheduler_service.py:322)
  └─ ingest_search_term_daily (search_term_ingest.py, 기존)
        ├─ [기존] SHOPPINGKEYWORD_DETAIL / EXPKEYWORD 성과 수집
        └─ [신규] ingest_search_term_conversion: SHOPPINGKEYWORD_CONVERSION_DETAIL 수집
              └─ [신규] fetcher.fetch_search_term_conversion (STCONV_COL_* 상수, ensure_reports_built 자기치유)
                    → naver_search_term_daily에 전환 컬럼 UPDATE(성과행에 병합) or 별도 적재

[SS2 판단] search_term_judge_sa (신규 순수함수 SA, 표본 게이트·화이트리스트·BEP 판정)
  ├─ [재사용] campaign_target_resolver.resolve_adgroup_target_roas(db, adgroup_id) → BEP ROAS
  ├─ rolling N일 누적 집계(검색어 grain)
  └─ 산출: 제외 후보 리스트(source별: shopping/파워링크) + 승격 후보(전환 검색어)

[SS3 실행] harness 배선 (분기)
  ├─ SS3-A 파워링크: naver_sa_writer.add_restricted_keywords (자동, approval_source='ss_exclude')
  │     └─ [재사용] guardrail_gate·killswitch·change_log·봉투 §1
  └─ SS3-B 쇼핑: 브리핑 산출(Slack/커맨드센터) → Jino 콘솔 수동 (자동 실쓰기 없음)

[SS4 승격] search_term_promote_sa (신규, 제안만)
  └─ 전환 검색어 → 파워링크 정식 키워드 등록 제안 → 영구 Jino Confirm (자동 발사 금지)
```

**재사용**: `sync_naver_search_term_job`·`ingest_search_term_daily`·`ensure_reports_built`·`_download_tsv`·`campaign_target_resolver`·`guardrail_gate`·`NaverChangeLog`·killswitch·`add_restricted_keywords`/`delete_restricted_keywords`.
**신규(최소)**: ①`STCONV_COL_*` 상수 + `fetch_search_term_conversion` ②전환 컬럼 마이그레이션 ③`ingest_search_term_conversion` ④`search_term_judge_sa` ⑤SS3 harness 배선(분기) + `ss_exclude` 승인원 ⑥`search_term_promote_sa`(제안).

---

## §3 페이즈

### SS1 — 수집층 (Sonnet)
- **마이그레이션**(alembic head = `e7f8a9b0c1d2` 기준 신규 리비전): `naver_search_term_daily`에 전환 컬럼 추가.
  - 컬럼: `conv_purchase_cnt`, `conv_purchase_amt`, `cart_cnt`, `cart_amt` (전부 Integer/Numeric, default 0, nullable=False).
  - **직·간접 분리 판단(설계 결정)**: `AD_CONVERSION` 관례(fetcher:53 CONV_COL_DIRINDIR, 간접은 별도 집계)를 따라 **직접만 게이트에 쓰되, 컬럼은 직접만 저장**하는 대신 — 제외 판정은 보수적으로 **직접+간접 합산 전환수**를 "전환 있음(제외 금지)" 판정에 쓴다(전환이 간접이어도 그 검색어는 살아있는 증거 = 오컷 방지 우선). 매출(amt)은 직접+간접 합산 저장. → 컬럼은 합산 1쌍(cnt/amt)으로 단순화하되, **직접전환수는 승격(SS4) 신호로 별도 필요** → `conv_direct_cnt` 1컬럼 추가로 5컬럼. 최종: `conv_purchase_cnt`(직+간 합), `conv_direct_cnt`(직접만), `conv_purchase_amt`(직+간 합), `cart_cnt`, `cart_amt`. (근거: 제외 게이트=보수적 합산, 승격 신호=직접전환 품질.)
- **fetcher**: `STCONV_COL_*` 상수(§0.5 표) + `fetch_search_term_conversion(date_from, date_to)` — `fetch_search_term_daily`(fetcher:839) 형태를 그대로 미러(ensure_reports_built 자기치유, `_download_tsv`, grain=(일자,캠페인,그룹,검색어), purchase/add_to_cart 분리 집계, 직접/간접 분리 집계). **±2 오프셋 상수 재사용 금지**(§0.5 경고).
- **collector**: `ingest_search_term_conversion(db, date_from, date_to)` — `_ingest_rows`(search_term_ingest:25) 미러. 성과행(shopping source)에 전환 컬럼 UPDATE 병합(같은 grain UniqueConstraint `uq_naver_search_term_daily` 활용). `ingest_search_term_daily`에 편입(같은 07:40 크론, 3일 창).
- **자기치유 미확인**: SHOPPINGKEYWORD_CONVERSION_DETAIL이 자동 BUILT인지 create_stat_report 필요인지 미확인 → EXPKEYWORD식 "없으면 생성요청·다음 크론 수집" 패턴으로 안전 구현(§실측 1).
- **보존 16일**: SHOPPINGKEYWORD_DETAIL과 동일 → 매일 수집 필수(기존 크론 편입이면 자동 충족).

### SS2 — 판단 SA (Opus)
- `search_term_judge_sa`: 순수함수(DB 읽기 only, 쓰기 없음). rolling N일 누적 집계 → 봉투 §1 게이트 순차 적용 → 제외 후보/승격 후보 산출.
- **BEP 연동**: `campaign_target_resolver.resolve_adgroup_target_roas(db, adgroup_id)` 재사용(반환 `{"target_roas","source"}`, product_bep→account_default 폴백). 검색어 ROAS = `conv_purchase_amt / cost`. 판정: `clk≥MIN ∧ conv=0 ∧ cost≥MIN` → 손실 후보 / `ROAS < target_roas` 저성과.
- **보호 규칙(§1 1·2·3)** 전량 이 SA에서 강제. money-action 게이트와 격리(표본 없는 검색어에 표본 판단 금지).
- 산출은 source별 분리(shopping은 SS3-B 브리핑용, 파워링크는 성과만이라 전환게이트 불가 → §난제 5 처리).

### SS3 — 실행 배선 (Opus · GATE 적대 리뷰 + codex 왕복)
- **SS3-A 파워링크**: harness가 후보를 `add_restricted_keywords(adgroup_id, [keyword])`로 자동 제외. `approval_source='ss_exclude'`(10자, String(12) 적합). guardrail_gate·killswitch 통과. 봉투 §1 4·5 캡·슬롯 한도 강제. change_log 전건.
  - **GATE 최우선 검증**: ①전환 검색어 제외 0건(§1 1) ②캡 초과 실쓰기 0 ③화이트리스트 관통 0 ④killswitch 존중 ⑤`ss_exclude` 외 경로로 제외 자동발사 0(자동발사 경계 누출) ⑥stale 후보(실행 직전 재조회·재검증, D-NAO-13 관례).
- **SS3-B 쇼핑**: 후보를 브리핑(Slack/커맨드센터 표기)만. **실쓰기 코드 경로 없음**(§실측-0 확정). Jino 콘솔 수동 제외.

### SS4 — 승격 후보 (Sonnet · 제안만, 영구 Confirm)
- `search_term_promote_sa`: 파워링크 확장검색('-') 버킷에서 전환(직접전환수 기준) 발생한 검색어 → 정식 키워드 등록 **제안**. 생성류라 영구 Confirm(§0 4·전략 v2 §2). 자동 발사 절대 금지. (키워드 등록 쓰기 손 자체는 L3 스코프 — SS4는 제안 산출까지.)

---

## §난제 (착수 전 못박음)

1. **±2 컬럼 오프셋**: `CONV_COL_*`(13컬럼) ≠ `STCONV_COL_*`(15컬럼). 재사용 시 device/전환유형/전환수 전부 어긋남 → 반드시 신규 상수. SS1 최대 실수 지점.
2. **rolling N일 창**: 검색어는 저볼륨 롱테일이라 1일 표본으로 clk≥10 도달 드묾 → 누적 창 필요. 초기 N=14(보존 16일 내). 단 창이 길수록 시장 변화 반영 지연 → SS1 실분포 후 확정.
3. **제외 슬롯 한도·롤링**: 그룹당 제외키워드 상한 미확인(70 vs 140). 한도 근접 시 최저 기여 제외어 회수 후 신규 삽입(in-out 큐레이션). 한도는 SS3-A 라이브 실측 전까지 보수적으로(예: 60에서 롤링 시작).
4. **쇼핑 자동화 불가의 반쪽**: 최대 갭(쇼핑 제외)이 정작 API 자동화 불가(§실측-0). SS3-B 브리핑이 가치의 상당분 — 브리핑 품질(우선순위·근거·1클릭 콘솔 링크)에 투자. 완전 자동화는 브라우저 자동화 후속 백로그.
5. **파워링크 전환게이트 부재**: 파워링크 검색어는 전환 귀속 불가(`AD_CONVERSION_DETAIL` 검색어='-'). → SS3-A 파워링크 제외는 **전환게이트(§1 1)를 걸 수 없다** = 위험. 대안: 파워링크는 (a)cost 대비 clk 병리(만성 무전환은 캠페인 grain 전환으로 근사) (b)초기엔 SS3-A도 **파워링크 확장검색 버킷의 저성과 검색어만** 보수적 제외 + Confirm 우선. → **SS3-A 자동 개방은 "전환 귀속 가능한 검색어"에 한정**, 파워링크 확장버킷은 SS4 승격 우선/제외는 Confirm. (설계 결론: 자동 제외의 안전한 대상이 §실측-0로 쇼핑도 파워링크도 각각 반쪽 — §미결 1.)
6. **병행 세션 충돌**: 같은 브랜치에서 코디네이터가 PR 작업 중 — **이 계획서 신설 외 파일 수정·커밋 금지**. SS 구현 착수는 별 세션에서 브랜치 정리 후.

## §실측 필요 (단정 금지)

0. (완료) 쇼핑 제외 쓰기 프로브 → §실측-0.
1. SHOPPINGKEYWORD_CONVERSION_DETAIL 자동 BUILT 여부(EXPKEYWORD식 생성요청 필요한지) — SS1 첫 수집에서 확인.
2. 표본 게이트 임계(`_SS_MIN_CLICK`·`_SS_MIN_COST`) — SS1 수집 후 검색어 clk/cost 실분포로 캘리브레이션. 현재 초기값은 가설.
3. 제외 슬롯 그룹당 한도(70 vs 140) — SS3-A 라이브 왕복 실측.
4. 쇼핑 non-KEYWORD_PLUS restrict type 존재 여부(§실측-0 미결) — 콘솔 API 리버스(후속 백로그).
5. 파워링크 확장버킷 제외의 안전 임계(§난제 5) — 전환 근사 신호 실측 후 자동/Confirm 경계 재판정.

## §검증 (원칙22 라이브 합격)

1. **수집(SS1)**: `naver_search_term_daily` 전환 컬럼에 실데이터 행 적재 확인(prod 실측 — 캠페인 grain AD_CONVERSION 전환수와 그룹 합계 대조, imp/clk 대조 선례 방식).
2. **판단(SS2)**: 실제 저성과(clk≥MIN ∧ conv=0 ∧ cost≥MIN) 검색어 후보가 산출됨을 실데이터로 확인 + 전환 검색어가 후보에서 배제됨(§1 1) 확인 + 화이트리스트 관통 0.
3. **실행(SS3)**: SS3-A 첫 실쓰기(change_log dry_run=0·`[검색어제외]`·재조회 반영 확인) 또는 후보 0이면 정당 절제 명시 / SS3-B 브리핑 실산출. GATE(적대) + codex 왕복(원칙19) PASS 후 배포(safe_deploy).
4. **승격(SS4)**: 전환 검색어 승격 제안 산출 실측(제안만, 실쓰기 없음).

## §체크리스트

- [ ] **SS0** 쇼핑 제외 쓰기 프로브 — **완료(§실측-0: 쇼핑 400 code 3728 / WEB_SITE 왕복 성공·원복)**
- [ ] SS1 마이그레이션(head `e7f8a9b0c1d2` 기준) + `STCONV_COL_*` + `fetch_search_term_conversion` + `ingest_search_term_conversion` 크론 편입 (Sonnet)
- [ ] SS2 `search_term_judge_sa` 순수함수 + 봉투 §1 게이트 + BEP 연동 + 단위 테스트 (Opus)
- [ ] SS3 harness 배선(A 파워링크 자동/B 쇼핑 브리핑 분기) + `ss_exclude` 승인원 + 경계 차등 테스트 — GATE 적대 + codex 왕복 (Opus)
- [ ] SS4 `search_term_promote_sa` 승격 제안(영구 Confirm) (Sonnet)
- [ ] 배포(safe_deploy CAS) + 라이브 합격 실측(수집 행·후보 산출·첫 제외 또는 절제·브리핑)
- [ ] 상수 실측 캘리브레이션(§실측 2·3·5 — 첫 주)
