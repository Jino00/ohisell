# PLAN — BM(벤치마크) 학습 레이어 (D-NAO-78·79, 트랙 §학습 범위 확장)

> 설계=Fable(승인 완료) · 구현=아래 Phase별 모델 배분(스키마·diff 핵심=Opus / 배선·표면화=Sonnet).
> 배경: 네이버 SA 계정 45캠페인 중 우리(자동운영)는 4개만 운영, 나머지는 대행사가 운영(볼륨
> 캠페인 — 예: 갤럭시 파워링크 7일 59만원·ROAS 1.76). 지금까지 판단·학습 루프는 ours 4개만
> 소비했다. D-NAO-78로 **학습 범위를 계정 전체(대행사 포함)로 확장** — 대행사가 전 캠페인을
> 어떻게 셋팅했는지 **구조·성과·매일 변화작업**을 관찰·학습해 우리 캠페인 성과에 반영한다.
> 이 레이어는 **관찰 전용**(네이버 API 쓰기 0)이라 리스크가 없다.
>
> 선행 문서: 트랙 `docs/tracks/active/track_naver-ad-optimization.md`(D-NAO-78·79),
> 전략 `docs/STRATEGY_naver-ad-v2.md`, 실측 `docs/references/22_naver_sa_p2s1_recon.md`
> (45캠페인 = WEB_SITE 12·그룹 513·키워드 90,150 등 전수 실측), ref 25 갭 분석·ref 26 논문 서베이.

---

## §0 방향 고정 (변경 금지 — 이 스프린트 내내)

### D-NAO-78 (Jino 확정 원문 인용)
> "너가 보는 데이터는 우리가 운영하는 캠페인이 아니고 전체의 광고를 보고 학습해. 대행사가 전체
> 캠페인을 어떻게 셋팅했는지 구조·성과·매일 변화작업을 파악해서 우리 캠페인 성과에도 도움."

- **학습 관찰 범위 = 계정 전체 45캠페인**(대행사·MOP·ours 전부). 판단·학습 입력이 ours 4개만
  소비하던 갭을 메운다. **단, 실행(쓰기)은 여전히 optimizer='ours'만** — 관찰 범위 확장이
  집행 범위 확장이 아니다(D-NAO-13 불변).

### D-NAO-79 (Jino 승인 원문 인용 — 계획서 명문화 의무)
> 주 산출물 = **예외 브리핑**, 전체 리포트 = 온디맨드. 사람 소비 3단:
> ① 매일 = **예외 브리핑**(SA-2 산출, "오늘 볼 것 N가지" · **이상치만**)
> ② 주간 = 벤치마크 요약(SA-3)
> ③ 온디맨드 = 드릴다운.
> 초기 2~3주만 Jino가 전체 검증 → 확신 후 예외 전용으로 전환.
> 브리핑은 상설 표면화(아침 채널). **§완료기준에 "예외 브리핑이 주 UX"를 못박는다.**

### 금지선 (이 스프린트 내내 불변)
1. **네이버 API 쓰기 호출 0.** SA-1/2/3 전부 GET(읽기)만. 어떤 경로에서도 POST/PUT/DELETE 금지.
   실행 손(naver_execution_harness·naver_sa_writer)을 import조차 하지 않는다(원칙18-1 단일 책임).
2. **회계 불활성.** BM 산출물은 매출/BEP/ROAS 회계 합계에 절대 안 섞인다 — 관측·프라이어 신호일 뿐.
3. **집행 범위 불변.** 관찰은 45캠페인, 쓰기는 ours만. BM이 대행사 캠페인에 개입하는 경로 없음.
4. **프라이어는 optional 입력.** BM 산출을 소비하는 기존 SA(B-X·IU-R·SS4·L2)는 BM 부재 시에도
   기존대로 동작한다(원칙18-6/8 — BM 입력은 있으면 강화, 없으면 폴백). BM이 기존 판단의
   **필수 의존이 되면 안 된다**(fail-open).
5. **fail-open 전면.** 관찰·열람 전용 잡이라 어떤 실패도 아침배치 catch-up 체인·집행 잡을
   막지 않는다(vault_export·diary 관례 계승).
6. **"됐다"는 라이브 증거로만(원칙22).** 각 Phase는 격리 통과 ≠ 합격. 아래 각 Phase의
   "라이브 합격 시나리오"를 prod 크론 1회 실행의 실데이터로 확인한 뒤에만 완료 처리한다.

---

## §1 구조 도표 (Harness + 실제 파일 경로 매핑)

```
[BM Harness] — 관찰 전용, 아침 배치(07:37 KST, 07:35 entity sync 직후)
 파일: backend/app/services/naver_ad/bm_harness.py (신규 — SA 조합·프라이어 유통 허브)
 스케줄: scheduler_service.py _ensure_default_states 에 "bm_layer" 레인 신규 등록
 │
 ├─ SA-1 구조 스냅샷러 (매일)
 │    파일: bm_snapshot.py (신규)
 │    입력: naver_entity(07:35 sync 직후 DB, 0 GET) + get_campaigns_full(예산, 1 GET)
 │          + get_adgroups(확장검색 on/off, 45 GET) [+주간: 제외키워드·소재수 GET]
 │    출력: naver_entity_snapshot (신규 테이블 — 캠페인·그룹 grain 일별 history)
 │
 ├─ SA-2 조작 감지기 (어제 vs 오늘 diff)
 │    파일: bm_diff.py (신규)
 │    입력: naver_entity_snapshot D-1 vs D (DB-to-DB, 0 GET — 결정적·리플레이 가능)
 │    출력: naver_agency_op (신규 테이블 — 대행사 조작 이벤트 피드)
 │    감지: 입찰변경·상태flip·키워드 add/del·제외키워드 add/del·소재교체·예산변경
 │    (기존 entity_sync 인라인 외부변경 로깅 패턴을 스냅샷-구동 diff로 일반화·정식화)
 │
 ├─ SA-3 성과 대조기 (구조↔성과 상관 → 벤치마크化)
 │    파일: bm_benchmark.py (신규)
 │    입력: naver_entity_snapshot + naver_ad_daily(07:30 적재, 계정 전체 성과)
 │          + naver_search_term_daily(계정 전체 검색어)
 │    출력: naver_bm_benchmark (신규 프라이어 테이블 — 고성과 구조 벤치마크)
 │
 └─ 산출 소비 (프라이어 유통 — Harness가 optional 입력으로 전달)
      ① 예외 브리핑: bm_briefing.py → ops_diary_entries(observe) → vault_export(Obsidian)
         + slack_notifier(아침 푸시). [D-NAO-79 주 UX]
      ② 프라이어 테이블 naver_bm_benchmark:
         - exploration.py (B-X): 대행사 검증 키워드셋·입찰밴드 = 탐색 프라이어
         - rank_servo.py / bid_rank_curve.py (IU-R): 대행사 입찰→순위 반응 = 서보 프라이어
         - search_term_ss_lane.py (SS4): 대행사 등록 키워드셋 = "사람이 검증한 정답지"
           → 승격 후보 교차(대행사가 이미 키워드로 쓰면 승격 확신도↑)
         - (향후 L2): naver_bm_benchmark 그대로 참조
```

### 재사용 자산 (이미 가동 중 — 새로 만들지 않는다)
| 자산 | 위치 | BM에서의 역할 |
|---|---|---|
| `entity_sync.sync_entities` (07:35) | `naver_ad/entity_sync.py` | 45캠페인→그룹→키워드 전수 수집·`naver_entity` upsert. **SA-1은 이 결과(DB)를 읽어 스냅샷** — 재수집 안 함(0 GET) |
| `naver_entity` | models.py:1924 | 구조 "현재 상태"(history 없음). SA-1 스냅샷 원천 |
| `naver_ad_daily` (07:30, 1년치) | models.py:1451 | SA-3 성과 원천(계정 전체) |
| `naver_search_term_daily` (계정 전체) | models.py:1959 | SA-3·SS4 교차 원천 |
| `naver_change_log` 외부변경 로깅 | entity_sync.py:105~340 | **기존 인라인 diff — 존치**(ours 가드레일 소비). SA-2는 이를 대체하지 않고 스냅샷-구동으로 **일반화**(§6 열린질문 dedup) |
| diary/vault(D-NAO-54 P1~P5) | diary.py·vault_export.py | 브리핑 표면화 채널(신규 채널 안 만듦) |
| `get_campaigns_full`/`get_adgroups`/`get_ads` | naver_sa_ad_fetcher.py | 예산·확장검색·소재수 GET(기존 함수 재사용/최소 확장) |

---

## §2 스키마 설계 (DDL 수준)

### 규모 추정 (ref 22 실측 기반)
- 캠페인 45 · 그룹 ~600(WEB_SITE 513 + SHOPPING/BRAND) · 키워드 90,150(WEB_SITE).
- **키워드 grain 일별 스냅샷은 금지** — 90,150행/일 × 365 = 3,300만행/년(낭비). 대신:
  - **SA-1 스냅샷 = 캠페인 + 그룹 grain 일별**(≈645행/일 × 400일 = 25.8만행 — 가볍다).
  - 그룹 행에 **키워드 집계**(keyword_count·avg_bid·bid_band) 보관.
  - **개별 키워드 상태(입찰·add/del)는 이벤트로만** 남긴다(SA-2 `naver_agency_op`, 변화 시에만).
- 보존: 스냅샷 400일 롤링(365 관례 + 여유), agency_op 이벤트 365일 롤링.

### 테이블 1 — `naver_entity_snapshot` (SA-1, 신규 alembic)
```
class NaverEntitySnapshot(Base):
    """대행사 포함 45캠페인 구조의 날짜별 history (SA-1, D-NAO-78). naver_entity는 upsert라
    '현재 상태'만 남아 역사가 없다 → 이 테이블이 매일 07:37 캠페인·그룹 grain 구조를 스냅샷.
    키워드 grain은 저장 안 함(집계 컬럼 + agency_op 이벤트로 대체). 관찰 전용."""
    __tablename__ = "naver_entity_snapshot"
    __table_args__ = (UniqueConstraint("snapshot_date","entity_type","entity_id",
                      name="uq_naver_entity_snapshot"),)

    id:            int  PK
    snapshot_date: Date  NOT NULL index   # KST 스냅샷 날짜(kst_today, ★UTC 아님)
    entity_type:   String(10) NOT NULL    # campaign/adgroup
    entity_id:     String(50) NOT NULL index
    parent_id:     String(50) default ""  # adgroup→campaign_id
    campaign_id:   String(50) default "" index
    campaign_type: String(20) default ""  # WEB_SITE/SHOPPING/BRAND_SEARCH
    optimizer:     String(8)  default "none"  # none/ours/mop (naver_campaign_settings 조인 — 대행사 구분)
    name:          String(300) default ""
    status:        String(10) default "on"    # on/off/deleted
    # ── 구조 지표(SA-3 벤치마크 원료) ──
    daily_budget:  Integer nullable          # 캠페인 dailyBudget(get_campaigns_full, Phase 3)
    bid_amt:       Integer nullable          # 그룹 기본입찰
    extended_search: Boolean nullable        # 그룹 확장검색 on/off(get_adgroups 확장, Phase 3)
    keyword_count: Integer nullable          # 그룹 활성 키워드 수(naver_entity 집계)
    keyword_avg_bid: Integer nullable        # 그룹 키워드 평균 입찰(밴드 산출용)
    negative_kw_count: Integer nullable      # 제외키워드 수(주간 deep GET, Phase 3)
    ad_count:      Integer nullable          # 소재 수(주간 deep GET, Phase 3)
    synced_at:     DateTime server_default=now()  # ⚠️UTC — 시간계산 미사용
```
> nullable 규약: Phase 1은 name/status/keyword_count/avg_bid만 채운다. 예산·확장검색(Phase 3)·
> 제외수·소재수(Phase 3 주간)는 additive nullable — 미수집 시 NULL(하위호환, backfill 불필요).

### 테이블 2 — `naver_agency_op` (SA-2, 신규 alembic)
```
class NaverAgencyOp(Base):
    """대행사(및 계정 전체 외부) 조작 이벤트 1건 (SA-2, D-NAO-78). 스냅샷 D-1 vs D diff로
    산출 — 결정적·리플레이 가능. spec의 agency_ops_log. 예외 브리핑의 원료.
    ★naver_change_log와 분리: change_log는 OUR 제안·집행의 피드백 루프(proposal_id·outcome·
    verify)에 묶여 있어 45캠페인 대행사 노이즈를 섞으면 학습 쿼리가 오염된다."""
    __tablename__ = "naver_agency_op"
    __table_args__ = (Index("ix_naver_agency_op_date_campaign","op_date","campaign_id"),)

    id:          int PK
    op_date:     Date NOT NULL index      # 조작이 감지된 날(= 오늘 스냅샷 날짜)
    detected_at: DateTime NOT NULL        # kst_now() 명시(server_default=UTC 회피)
    entity_type: String(10) NOT NULL      # campaign/adgroup/keyword
    entity_id:   String(50) NOT NULL
    campaign_id: String(50) default "" index
    optimizer:   String(8)  default "none"  # 이 조작 주체 구분(대행사=none/mop, ours 제외 대상)
    op_type:     String(24) NOT NULL      # bid_change/status_flip/keyword_add/keyword_remove/
                                          #  negative_add/negative_remove/creative_change/budget_change/extended_toggle
    before_value: Text nullable
    after_value:  Text nullable
    magnitude:    Float nullable          # 변화 크기(입찰 Δ%·예산 Δ% 등 — 예외 랭킹용)
    is_exception: Boolean default False    # 예외 브리핑에 올릴 이상치 여부(SA-2 필터 판정)
```

### 테이블 3 — `naver_bm_benchmark` (SA-3 프라이어, 신규 alembic)
```
class NaverBmBenchmark(Base):
    """대행사 구조↔성과 상관을 벤치마크化한 프라이어 1행 (SA-3, D-NAO-78). B-X·IU-R·SS4·L2가
    optional 입력으로 소비. 매일 재산출(snapshot 교체) — 최신 벤치마크만 유지."""
    __tablename__ = "naver_bm_benchmark"
    __table_args__ = (UniqueConstraint("bench_kind","bench_key",name="uq_naver_bm_benchmark"),)

    id:          int PK
    bench_kind:  String(24) NOT NULL   # keyword_verified/bid_band/bid_rank_slope/group_structure
    bench_key:   String(120) NOT NULL  # keyword 텍스트 / campaign_type / adgroup:id 등
    value_json:  Text nullable         # 벤치마크 값(밴드 [min,p50,max]·검증여부·기울기 등)
    sample_n:    Integer default 0
    confidence:  Float nullable
    computed_at: DateTime NOT NULL     # kst_now() 명시
```
> ★대안(열린질문 §6-b): `bid_rank_slope`·`bid_band` 프라이어를 별도 테이블 대신 기존
> `naver_learning_state`(scope='benchmark')에 쓸 수도 있다. rank_servo가 이미
> naver_learning_state를 response_prior로 소비(models.py:1856)하므로 **배선 재사용상 유리**.
> 구현 착수 시 Opus가 둘 중 택일(별도 테이블=관심사 분리 / learning_state=배선 재사용).

### alembic (head = f2a3b4c5d6e7)
- Phase별 마이그레이션 3개(테이블별) 또는 1개 통합. `Revises: f2a3b4c5d6e7` 체인.
- 전부 CREATE TABLE(기존 행 무영향) — 회귀 0. 파일 상단 docstring에 트랙·D-NAO-78 근거 명시
  (기존 alembic 관례, f2a3b4c5d6e7 파일 참조).

---

## §3 diff 로직 (SA-2 — 무엇을 조작으로 감지하는가)

입력: `naver_entity_snapshot`의 op_date=D-1 vs D 두 날짜 셋. (스냅샷-구동이라 리플레이·재현 가능)

| op_type | 감지 규칙 | 노이즈 필터 |
|---|---|---|
| `bid_change` | 그룹 bid_amt Δ | \|Δ%\| < 3% = 지터 무시(입찰 반올림). 방향·크기 magnitude 기록 |
| `status_flip` | status on↔off 전이 | deleted↔* 전이는 별도(아래). 재등장 포함 |
| `keyword_add`/`_remove` | 그룹 keyword_count 증감(집계) + agency_op 이벤트 | bootstrap(첫 스냅샷=D-1 부재) 시 전건 억제(D-NAO-50 패턴 계승) |
| `negative_add`/`_remove` | negative_kw_count 증감(주간 grain) | 주간 스냅샷 간 비교라 일별 발화 안 함 |
| `creative_change` | ad_count 증감(주간 grain) | 주간 비교 |
| `budget_change` | daily_budget Δ | \|Δ%\| < 5% 무시. 예산 없음↔있음 전이는 항상 기록 |
| `extended_toggle` | extended_search on↔off | — |
| `campaign_add`/`adgroup_add` | 스냅샷에 새 entity_id 등장 | bootstrap 가드 적용. **항상 is_exception=True**(캠페인/그룹 신설은 대행사의 구조 변경 = 최우선 브리핑 대상) |
| `campaign_remove`/`adgroup_remove` | 스냅샷에서 소실 또는 status=deleted 전이 | deleted 가드로 1회만 기록 |

> ★실전 검증 사례(2026-07-22 실측 — SA-2 수용 기준 픽스처로 사용): 07-21 17:00 대행사 원복 시
> 실제 조작 = 그룹 신설 6건(갤럭시 파워링크에 폴드8/플립8 3그룹 + 01.갤럭시_TPU에 신모델 3그룹)·
> 키워드 85건 일괄 등록·캠페인 userLock 2건(폴드8/플립8 키워드·맥세이프쇼검)·입찰 변경 5건·
> 그룹 잠금 1건. 기존 entity_sync는 키워드 add/입찰/캠페인 status는 잡았으나 **그룹 신설·예산은
> 사각**이었다 — 위 op_type 표가 이 사각을 정확히 메워야 한다.

### 공통 노이즈·정합 필터 (필수)
1. **ours 자기변경 제외.** optimizer='ours' 캠페인의 변경 중 최근 창(예: 48h) 내 우리
   `naver_change_log`(dry_run=False)와 매칭되는 건은 agency_op에서 제외 — 우리 손을 대행사
   조작으로 오인 금지. (매칭 불가한 ours 변경은 "외부 개입"으로 남겨 관측 — 대행사가 우리
   캠페인을 건드렸을 수 있으니 오히려 예외 신호로 승격.)
2. **deleted 엔티티 가드.** 스냅샷에서 사라진 엔티티(status=deleted)는 remove 이벤트로 1회만
   기록, 이후 재발화 금지(일 레인 deleted 404 반복 사고 교훈 계승 — memory: naver-ad-safe-deploy 인접).
3. **bootstrap 가드.** D-1 스냅샷이 없으면(최초 실행) diff를 만들지 않고 스킵(전건 add 폭주 방지).
4. **예외 판정(is_exception).** "오늘 볼 것"만 브리핑에 올린다. 예외 = (a) 대형 변화
   (\|Δ%\| ≥ 임계, 예: 입찰 ±20%·예산 ±30%) (b) 우리 캠페인 인접 대행사 조작(경쟁 신호)
   (c) 고성과 그룹의 구조 변경. 임계는 상수로 두고 §6 열린질문에서 초기값 캘리브레이션.

---

## §4 프라이어 배선 지점 (산출물을 어느 기존 SA가 소비하는가)

**전부 optional 입력·fail-open(§0 금지선 4).** 배선은 "읽기 추가"만 — 기존 판단 로직 대체 금지.

| 소비 SA | 파일 | 소비 방식 |
|---|---|---|
| 탐색 B-X | `exploration.py` | `bench_kind='keyword_verified'`(대행사 등록 키워드셋)·`'bid_band'`를 탐색 후보 우선순위·초기 입찰 프라이어로 읽음. 대행사가 이미 검증한 키워드/밴드면 탐색 확신도↑ |
| 순위 서보 IU-R | `rank_servo.py`·`bid_rank_curve.py` | `bench_kind='bid_rank_slope'`(대행사 입찰→순위 반응)를 response_prior로 읽음. 이미 naver_learning_state를 소비하므로(§2 대안) 그 경로에 편승 가능 |
| SS4 승격 교차 | `search_term_ss_lane.py` | ★핵심 교차: 승격 후보 검색어가 `bench_kind='keyword_verified'` 셋에 있으면 = "사람(대행사)이 이미 키워드로 등록한 검증된 검색어" → 승격 rationale에 교차 플래그 + 확신도 가점. (승격은 여전히 제안만·영구 Confirm — SS4 §0 불변, 자동발사 없음) |
| (향후) L2 | — | `naver_bm_benchmark` 그대로 참조. 이번 스코프 밖 |

배선 원칙(원칙18-8): 소비 SA의 함수 시그니처에 `bm_prior=None` optional 파라미터를 추가하고,
Harness가 값을 주입한다. SA는 값 유무에 따라 다른 결과를 내되 **None이면 기존과 동일**.

---

## §5 브리핑 표면화 위치 (D-NAO-79)

**결정: 기존 운영 일기(D-NAO-54 P1~P5) 채널 재사용 — 신규 UI 안 만듦.**

| 소비 단(D-NAO-79) | 채널 | 구현 |
|---|---|---|
| ① 매일 예외 브리핑(주 UX) | Obsidian(vault_export) + Slack 아침 푸시 | SA-2 예외(is_exception=True)를 `ops_diary_entries`(event_type='observe', actor='system', action='agency_op')로 기록 → `vault_export`가 당일 노트에 "대행사 오늘 조작 N건" 섹션 렌더 + `slack_notifier.notify`로 아침 푸시. **예외 0건이면 "예외 없음"** 1줄 |
| ② 주간 벤치마크 요약 | Obsidian(vault_export 주간 섹션) | SA-3 `naver_bm_benchmark`를 주 1회(예: 월요일) 벤치마크 요약 마크다운으로 vault_export |
| ③ 온디맨드 드릴다운 | sellC 콘솔 라우터 엔드포인트 | `NaverAdOptimizationConsole.tsx` + 신규 GET 라우터(agency_op·snapshot·benchmark 조회). 상설 배너 아님 — 필요 시 열어보는 드릴다운(초기 스코프 최소, Phase 5) |

- vault_export/slack은 **이미 아침 채널로 가동**(vault_export 09:05·slack_notifier 제안 푸시)이라
  BM 브리핑은 그 채널에 섹션/메시지만 추가하면 된다(신규 인프라 0).
- 브리핑 생성은 vault_export(09:05)·diary_reflection(08:35)보다 먼저 실행돼야 픽업된다 →
  BM 레인 07:37에서 예외를 ops_diary_entries에 기록해두면 하류가 자연히 소비(§6 크론).
- sellC 상설 배너는 **초기 스코프 밖**(D-NAO-79 "초기 2~3주 전체검증→확신 후 예외 전용",
  예외 전용 확정 후 배너 필요성 재평가). 온디맨드 드릴다운 엔드포인트만 Phase 5에 둔다.

---

## §6 Phase 분할 (각 Phase = 구현+검증 단위)

> 모델 배분: 스키마·diff 핵심 로직 = **Opus**, 배선·표면화·enrich = **Sonnet**.
> 각 Phase 완료 = 아래 "라이브 합격"을 prod 크론 1회 실데이터로 확인(원칙22). Phase마다
> `/codex review` pass(원칙19). Phase 완료 시 트랙 파일 D-N 갱신.

### Phase 1 — 스키마 + SA-1 스냅샷 (Opus)
- 구현: `NaverEntitySnapshot` 모델 + alembic + `bm_snapshot.py`(SA-1). naver_entity(07:35 sync 후
  DB)를 읽어 캠페인·그룹 grain 일별 스냅샷 upsert(0 GET). optimizer는 naver_campaign_settings
  조인. keyword_count/avg_bid는 naver_entity 키워드 행 집계. `bm_harness.py` 골격 + scheduler
  "bm_layer" 레인 07:37 등록.
- 완료 기준: 스냅샷 테이블에 오늘자 45캠페인 + ~600그룹 행, keyword_count 합계가 naver_entity
  WEB_SITE 90,150과 정합. 파일 상단 한 줄 주석·30줄 함수 분리·fail-open.
- **라이브 합격**: prod 07:37 크론 1회 실행 후 `SELECT count(*), sum(keyword_count) FROM
  naver_entity_snapshot WHERE snapshot_date=오늘` → 캠페인 45·그룹 수·키워드 합계가 당일
  naver_entity와 일치(stale 아님 — synced_at 타임스탬프로 오늘 실행 확인).

### Phase 2 — SA-2 diff + agency_op (Opus)
- 구현: `NaverAgencyOp` 모델 + alembic + `bm_diff.py`(SA-2). 스냅샷 D-1 vs D diff(§3 규칙),
  노이즈 필터(ours 자기변경 제외·bootstrap·deleted 가드)·is_exception 판정.
- 완료 기준: bootstrap 날 0 이벤트, 이후 실제 변화만 이벤트화. ours 최근 change_log와 매칭되는
  건 제외됨(유닛 테스트로 확인).
- **라이브 합격**: 스냅샷 2일치 쌓인 뒤(D-1·D 존재) 크론 1회 → 그날 대행사가 실제 바꾼 게
  있으면 agency_op 행이 생기고 **수동 대조**(네이버 광고관리 UI 또는 naver_change_log 외부변경)와
  일치. ours 변경이 agency_op에 안 섞였는지 확인. (변화 없는 날 = 0행이 정상 — 원칙22:
  0행을 "실패"로 오판 금지, D-1/D 스냅샷 존재를 먼저 확인.)

### Phase 3 — 차원 보강 (예산·확장검색·제외키워드·소재수) (Sonnet)
- 구현: (a) 일별 = `get_campaigns_full`(예산, +1 GET) + `get_adgroups` 래퍼에 확장검색 필드
  추가(+45 GET). (b) 주간 = 제외키워드 GET(신규 fetcher 함수 — restricted-keywords GET)·
  `get_ads` 소재수(그룹별). 스냅샷·diff를 이 차원으로 확장.
- **GET 예산·rate limit 배려**: 일별 마진 GET ≈ 46(campaigns_full 1 + adgroups 45) — entity_sync가
  이미 쓰는 수천 GET 대비 무시 가능. 주간 deep(제외·소재) ≈ 600그룹 × 2 = ~1,200 GET/주 →
  주 1회(예: 일요일 09:20, 크론 한산 시간대)·`_get` 기존 backoff(429/5xx 지수 재시도) 재사용·
  느린 변화 차원이라 일별 불필요. **느린 차원=주간, 빠른 차원(입찰·상태·예산)=일별** 원칙.
- 완료 기준: 스냅샷 행에 daily_budget·extended_search(일별), negative_kw_count·ad_count(주간).
  일별 GET 실측 ≤ 예산.
- **라이브 합격**: 일별 크론 후 스냅샷에 예산·확장검색 채워짐(NULL 아님, 특정 캠페인 값이
  네이버 UI 실측과 일치). 주간 크론 1회 후 제외수·소재수 채워짐. 일별 GET 카운트 로그로 실측.

### Phase 4 — SA-3 벤치마크 + 프라이어 배선 (Opus)
- 구현: `NaverBmBenchmark` 모델(또는 naver_learning_state 대안 §2-b) + `bm_benchmark.py`(SA-3).
  스냅샷 + naver_ad_daily + search_term_daily로 (고성과 그룹의 키워드 수·입찰밴드·검증
  키워드셋·bid_rank_slope) 산출. §4 배선: exploration·rank_servo·search_term_ss_lane에 optional
  `bm_prior` 입력 추가(fail-open).
- 완료 기준: 프라이어 테이블 채워짐. SS4 승격 후보에 대행사-키워드 교차 플래그가 rationale에
  나타남(교차 없으면 기존과 동일 — 폴백 확인).
- **라이브 합격**: 크론 후 naver_bm_benchmark에 keyword_verified 셋(대행사 등록 키워드) 존재.
  08:50 SS4 레인 다음 실행에서 실제 승격 후보 중 대행사 키워드셋에 포함된 건이 교차 가점
  플래그를 받았는지 콘솔/diary에서 확인. B-X·IU-R는 bm_prior=None일 때 기존과 동일 결과(회귀 0).

### Phase 5 — 예외 브리핑 표면화 (Sonnet)
- 구현: `bm_briefing.py` — SA-2 예외를 ops_diary_entries(observe)로 기록 + vault_export에 BM
  섹션·주간 벤치마크 요약 렌더 추가 + slack_notifier 아침 푸시. 온디맨드 드릴다운 GET 라우터
  (agency_op/snapshot/benchmark 조회) + 콘솔 최소 뷰.
- 완료 기준: 예외 브리핑이 Obsidian 당일 노트·Slack에 표면화. 예외 0건이면 "예외 없음" 1줄.
  **예외 브리핑이 주 UX**(전체 리포트는 온디맨드 드릴다운으로만).
- **라이브 합격**: 실제 대행사 조작이 있던 날 아침 Obsidian 노트에 "대행사 오늘 조작 N건" 예외
  섹션 + Slack 푸시 수신. 조작 없던 날 "예외 없음". 온디맨드 엔드포인트가 전체 스냅샷/이벤트
  반환.

### Phase 6 — 크론 정착 + catch-up (Sonnet)
- 구현: bm_layer(07:37 일별)·bm_deep(주간) 레인 `_ensure_default_states` 정식 등록. 아침배치
  catch-up 체인에 편입 여부 판정(관찰 잡이라 fail-open이면 catch-up 불필요 — vault류와 동일).
- **라이브 합격**: prod 재시작 후에도 07:37 레인이 자동 발화, 로그에 snapshot/diff 건수. 스케줄러
  health(scheduler_health)에서 레인 정상.

---

## §7 크론 편성 (아침 배치 어디에 끼우는가)

기존 레인(발화 순서):
```
07:00 sa_ad_costs · 07:30 ad_daily(+BEP) · 07:35 entity_sync ← SA-1 원천 · 07:40 search_term
07:45 shopping_product_sync · 07:50 forecast · 08:00 proposals · 08:05 expert_desk
08:10 learning · 08:30 retro · 08:35 diary_reflection · 08:45 wisdom · 08:50 auto_operator+SS4
08:55 probe_settlement · 09:03 probe_learning · 09:05 vault_export · 09:10 keyword_hourly_sweep
(매시: :05 snapshot · :07 trigger_watch · :20 auto_operator_hourly)
```

**BM 편성 결정:**
- **`bm_layer` = 07:37 KST(`37 7 * * *`)** — 07:35 entity_sync(SA-1 원천) 직후, 07:30 ad_daily
  (SA-3 성과) 이후. SA-1(DB 읽기)+SA-2(diff)+SA-3(벤치마크)+예외 브리핑 기록을 한 레인에서
  수행. DB-bound라 빠름. 07:40 search_term과 3분 간격(SQLite 단일 라이터 충돌 회피 — BM은
  짧은 트랜잭션).
- 브리핑이 07:37에 ops_diary_entries에 기록되면 하류 diary_reflection(08:35)·vault_export(09:05)가
  자연 픽업. Slack 푸시는 07:37 레인 말미에서 직접 발송.
- **Phase 3 주간 deep(제외키워드·소재수 ~1,200 GET)** = **`bm_deep` 별도 레인, 일요일 09:20
  (`20 9 * * 0`)** — 아침 집행 레인이 다 끝난 한산 시간, keyword_volume(일요일 09:00)과 겹치지
  않게. GET 무거워 일별 07:37에서 분리(07:40 search_term 지연 방지).
- catch-up: 관찰·fail-open 잡이라 아침배치 catch-up 체인(forecast→proposals 의존 스태거)에
  **넣지 않는다**(blast radius 밖 — 놓쳐도 다음날 스냅샷이 이어짐, vault류 관례).

---

## §8 확정한 6개 결정사항 요약

| # | 항목 | 결정 |
|---|---|---|
| 1 | **스키마** | 신규 3테이블: `naver_entity_snapshot`(캠페인·그룹 grain 일별, 키워드는 집계+이벤트) · `naver_agency_op`(조작 이벤트) · `naver_bm_benchmark`(프라이어). 키워드 grain 일별 스냅샷 금지(3,300만행/년 회피). 보존 400/365일 롤링 |
| 2 | **diff** | 스냅샷 D-1 vs D(DB-to-DB·리플레이 가능). 감지=입찰/상태/키워드/제외키워드/소재/예산/확장검색. 필터=ours 자기변경 제외·bootstrap·deleted 가드·지터 임계. is_exception 판정으로 예외만 브리핑 |
| 3 | **배선** | naver_bm_benchmark를 B-X(탐색 프라이어)·IU-R(서보 프라이어)·SS4(승격 교차=대행사 검증 키워드셋)·L2(향후)가 optional 입력으로 소비. 전부 fail-open(None=기존 동일) |
| 4 | **표면화** | 기존 diary/vault(D-NAO-54) 재사용. 예외 브리핑=ops_diary_entries(observe)→Obsidian+Slack 아침. 주간=vault 요약. 온디맨드=콘솔 드릴다운 엔드포인트. sellC 상설 배너=초기 스코프 밖 |
| 5 | **Phase** | 6단계: P1 스키마+SA-1(Opus) · P2 SA-2 diff(Opus) · P3 차원보강(Sonnet) · P4 SA-3+배선(Opus) · P5 브리핑(Sonnet) · P6 크론정착(Sonnet). 각 Phase 라이브 합격 시나리오 명시 |
| 6 | **크론** | `bm_layer` 07:37(07:35 entity_sync 직후·일별·DB-bound) + `bm_deep` 일요일 09:20(주간 무거운 GET). catch-up 체인 제외(fail-open 관찰 잡) |

---

## §9 리스크 · 열린 질문

1. **(a) SA-2 ↔ 기존 entity_sync 외부변경 로깅 dedup.** entity_sync가 이미 외부 입찰/상태/QI/
   키워드 add-del을 `naver_change_log`에 인라인 로깅한다(존치 — ours 가드레일 소비). SA-2가
   같은 변화를 `naver_agency_op`에도 쓰면 이중 기록. **결정 필요(Opus 구현 착수 시)**: agency_op는
   스냅샷-구동으로 **모든 차원**(예산·제외·소재 포함)을 잡고 change_log 인라인은 ours 경로용으로
   존치하되, 두 소스가 겹치는 입찰/상태 변화는 agency_op를 정본으로 삼고 change_log 인라인
   외부변경 로깅을 장기적으로 SA-2로 이관할지(리팩토링) 판단. 초기엔 **병존**(중복 허용, 소비처가
   다름) 권장 — 리스크 최소.
2. **(b) 프라이어 저장소: `naver_bm_benchmark` 신규 vs `naver_learning_state` 재사용.** rank_servo가
   이미 learning_state를 response_prior로 소비하므로 bid_rank_slope 프라이어는 learning_state에
   쓰는 게 배선상 유리. keyword_verified/bid_band 등 나머지는 신규 테이블이 깔끔. **Opus가 P4에서
   택일** — 혼용 가능(slope=learning_state, 나머지=bm_benchmark).
3. **예외 임계 캘리브레이션.** is_exception 임계(입찰 ±20%·예산 ±30% 등)는 초기 추정치.
   D-NAO-79대로 초기 2~3주 Jino 전체검증 기간에 실데이터로 캘리브레이션 → 확신 후 예외 전용.
   초기엔 임계를 낮춰(더 많이 표면화) 검증, 이후 상향.
4. **그룹 수·소재수 실측 미확정.** ref 22는 그룹 513(WEB_SITE)만 명시. SHOPPING/BRAND 그룹 수와
   총 소재 수는 P1 라이브에서 실측 → GET 예산 확정. 주간 deep GET이 예상보다 크면 페이지네이션·
   배치 분할.
5. **대행사 캠페인 optimizer 라벨링 — 해소됨(2026-07-22 Fable 판정).** SA-2 필터의 정확도에
   필요한 것은 **ours 식별뿐**(자기변경 제외 필터) — ours/mop은 naver_campaign_settings로 확정
   가능하고, **그 외 전부(none) = 대행사 관찰 대상**으로 간주하면 충분하다. 별도 'agency' 라벨
   불필요(라벨 신설은 스코프 추가 없이 optimizer 값 그대로 op 행에 기록). Jino 블로커 아님.
6. **SS4 교차의 전환 귀속 한계.** 파워링크 검색어는 전환 귀속 불가(SS §0.5). 대행사 키워드셋
   교차는 "등록됨=검증됨" 신호일 뿐 전환 근거가 아니다 — 승격 확신도 가점은 보조 신호로만,
   전환 게이트를 대체하지 않는다(SS4 §0 불변).
7. **키워드 grain history 부재 트레이드오프.** 개별 키워드 입찰 history를 스냅샷에 안 남기고
   이벤트(agency_op)로만 잡는다. 특정 키워드의 과거 입찰 궤적 조회는 agency_op 이벤트 재구성이
   필요(스냅샷 직접 조회 불가). 벤치마크 목적엔 그룹 집계로 충분하다는 판단 — 향후 키워드
   궤적 분석이 필요하면 별도 설계.

---

## §완료 기준 (스프린트 전체)

- [ ] **예외 브리핑이 주 UX**(D-NAO-79) — 매일 아침 Obsidian+Slack에 "오늘 볼 것 N가지"(이상치만),
      전체 리포트는 온디맨드 드릴다운으로만. 예외 0건이면 "예외 없음". **이 항목이 스프린트의
      핵심 성공 지표.**
- [ ] SA-1 스냅샷이 45캠페인 구조를 날짜별로 보존(라이브 실측 정합).
- [ ] SA-2가 대행사 일일 조작을 정확히 감지(수동 대조 일치, ours 자기변경 미혼입).
- [ ] SA-3 벤치마크가 B-X·IU-R·SS4에 optional 프라이어로 배선(fail-open, None=회귀 0).
- [ ] SS4 승격 교차: 대행사 등록 키워드셋이 승격 확신도에 반영(라이브 후보에서 확인).
- [ ] 네이버 API 쓰기 호출 0(전 Phase 코드 grep로 실행 손 import 부재 확인) — §0 금지선 1.
- [ ] 관찰 전용·fail-open — BM 실패가 집행/아침배치 체인을 막지 않음(주입 실패 테스트).
- [ ] 각 Phase `/codex review` pass(원칙19), 완료 시 트랙 파일 D-N 갱신.
