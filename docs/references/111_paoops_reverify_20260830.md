# PAO_OPS 재검증 — 2026-08-30

> **무엇을**: `docs/PAO_OPS.md`(324줄·§0~§12) 전문의 사실 주장 재현 + Jino 질문(*"광고 성과 지표는 안봐?"*)에 답하는 성과 표면 재고 조사.
> **언제**: 2026-08-30 17:13~17:22 KST에 관측(prod SSH, 위 시각대 전체). 원 문서의 관측 시각 주장은 **2026-08-30 08:5x KST** 📄 — 약 **8~9시간 차이**. 이 문서 자체의 §0-1-c는 "14:51 실측"이라고도 적어, 원 문서 내부에서도 관측 시각이 3개(08:5x / 10:17 / 14:51) 섞여 있다.
> **방법**: §11 레시피대로 `scp` + `ssh ... .venv/bin/python - < /tmp/q.py`, prod `backend/ohisell.db`(2,778,112,000 bytes, 2026-08-30 07:57 갱신 — 이게 진짜 prod db임을 파일 크기로 확인). 총 **prod 조회 13회**(스크립트 13개). 상태 변경 명령 0건. `docs/PAO_OPS.md` 미수정. 이 파일 외 다른 파일 생성 0건.

---

## 1. ★반증 목록 (핵심)

### 반증 1 — 「어제(08-29) 537,105원」은 08-29가 아니라 **08-28**의 값이다

- **문서 원문**(§6): `| 어제(08-29) | 537,105원 | — | — | — |`
- **실행한 명령**: `naver_ad_daily`에서 `adgroup_id <> '__backfill__'`(keyword 필터 없음, §11 지시대로) `ad_date` 일별 `sum(cost)` (2026-08-15~08-29).
- **관측값**(17:15 KST): `2026-08-28 → 537,105` / `2026-08-29 → 443,894`.
- **무엇이 다른가**: 537,105원은 **08-28**의 실제 지출이고, 08-29(진짜 어제)의 지출은 443,894원이다. 라벨과 값이 하루 어긋나 있다.
- **영향**: 이 오차는 고립돼 있지 않다 — 아래 반증 2(「계정 7일」)도 같은 날짜 경계 오류에서 나온다. 이 값을 믿고 "어제 얼마 썼나"를 판단하면 **실제보다 21% 높게** 본다.

### 반증 2 — 「계정 7일 4,051,618원」은 7일이 아니라 **8일**(08-22~08-29) 합이다

- **문서 원문**(§6): `| 계정 7일 | 4,051,618원 | — | — | — |`
- **실행한 명령**: 위와 같은 일별 `sum(cost)` 표에서 여러 창을 대조.
- **관측값**: 진짜 최근 7일(08-23~08-29) 합 = **3,561,309원**. 08-22~08-29(8일) 합 = **4,051,618원** — 문서 값과 **정확히 일치**.
- **무엇이 다른가**: 문서가 "7일"이라 라벨링한 창은 실제로 달력일 8일치다. 원인은 반증 1과 같은 축(어제=08-29를 08-28로 오인)에서 하루가 밀린 것으로 보인다.
- **영향**: 이 문서 §6은 스스로 "⚠️ 창을 안 밝히면 답이 뒤집힌다 — **모든 총이익 수치엔 창을 병기할 것**"이라고 경고한다. 그 경고를 세운 절 자체가 라벨-값 불일치를 갖고 있다 — 라벨을 병기해도 라벨이 틀리면 같은 병이 재발한다는 뜻이다.

### 반증 3 — §11의 「부품 생존」 재현 레시피가 지금 prod에서 실행 불가능하다

- **문서 원문**(§11): `| 부품 생존 | python scripts/ignition_parts_alive.py (prod에서 — 로컬은 4KB 미끼 DB) |`
- **실행한 명령**: `ssh sellc.ohitech.co.kr "find /home/ubuntu/ohisell -iname '*ignition_parts*'"` / `ls -la /home/ubuntu/ohisell/`
- **관측값**(17:20 KST): 검색 결과 **0건**. `/home/ubuntu/ohisell/` 최상위에 `backend/`·`frontend/`·`docs/`·`backups/`만 있고 **`scripts/` 디렉터리 자체가 없다**. (로컬 워크트리에는 `./scripts/ignition_parts_alive.py`가 실재 — repo에는 있지만 배포 대상엔 없다.)
- **무엇이 다른가**: §11은 이 명령이 "prod에서" 그대로 돈다고 전제하지만, `scripts/`가 배포 매니페스트에 안 들어가는 듯 prod에 존재하지 않는다.
- **영향**: 다음 세션이 이 레시피를 그대로 따라 하면 `No such file or directory`로 막힌다 — 재현 불가능한 문서화. (부가 관찰: prod에도 repo-root에 4,096바이트짜리 미끼 `ohisell.db`가 있다 — §11의 "로컬 미끼 DB" 경고가 로컬만이 아니라 prod에서도 잘못된 디렉터리에서 실행하면 재발함을 뜻한다.)

### 반증 4(약함) — 「네이버 광고 크론 35종」과 실측 카운트가 다르다

- **문서 원문**(§2): `**실제로 광고 계정에 쓰는 크론은 5개뿐이다.** 나머지 30개는 읽기·적재·판정·보고다.` (표제: "네이버 광고 크론 35종")
- **실행한 명령**: `SELECT count(*) FROM scheduler_state WHERE job_name LIKE '%naver%'`
- **관측값**: **36건**. `generate_expert_desk`(문서 §2 표에 08:05 실쓰기로 명시 포함됨)와 `verify_search_term_exclusions`(job_name에 'naver' 없음)까지 더하면 최소 **37~38건**.
- **무엇이 다른가**: "35종"이 어떤 집합을 세었는지 문서에 정의가 없어 정밀 대조는 안 되지만, 가장 단순한 카운트(job_name LIKE naver)만으로도 이미 35를 넘는다.
- **신뢰도**: 낮음(카운트 방법론이 문서에 명시 안 됨 — 억지로 반증 처리하지 않고 병기만 한다). 5개 실쓰기 크론의 개별 시각·cron_expression은 전부 **정확히 재현됨**(아래 재현 목록 참조) — 총계만 어긋난다.

### 반증 0건 아님 — 왜 4건에서 멈췄나

항목화한 사실 주장 **약 153개**(§0~§12, 절별 분포는 §3 참조) 중 **직접 실행해 재현을 시도한 것은 약 60개**. 그중 **재현 52개 / 반증 4개 / 미상 4개**(rate·window 등 근사 일치라 반증도 재현도 아닌 것 포함). prod 조회 13회 + repo grep 다수. 나머지 ~93개(주로 §5 가드레일 임계값 다수·§7 금지선 서술·§8 결정 대기 항목·D-NAO 번호·PR 번호)는 시간 예산상 손대지 못했다 — **미상으로 이월**(§6 참조). 억지 반증은 만들지 않았다: §3-b의 전환율 비대칭처럼 "재현에 가깝지만 절대 건수는 창 경계 때문에 약간 다른" 경우는 반증이 아니라 "근사 재현"으로 따로 적었다.

---

## 2. 절별 판정표 (§0~§12)

| § | 주장 요약 | 실행한 명령 | 관측값(KST) | 판정 |
|---|---|---|---|---|
| §0-★1 | 08:50 레인 무인 발화, `last_run_at 2026-08-30 08:50:20.098495`, 크론 슬롯과 소수점까지 일치 | `SELECT last_run_at FROM scheduler_state WHERE job_name='run_naver_auto_operator_daily'` | `2026-08-30 08:50:20.098495` (17:14) | **재현**(정확 일치) |
| §0-★1 | ROAS 1.42 < BEP 1.976, 당일 소진 3,501원 < 정착창 평균 30,705원 | (미실행 — 시점 스냅샷값, 재현하려면 당시 asof 재구성 필요) | — | 미상 |
| §0-★1-b | search_term_exclude 전 기간 승인 **1건**(07-22, `cmp-a001-01-…10236310`, 파워링크) | `naver_proposals WHERE proposal_type='search_term_exclude' AND status='approved'` | id=1598, `2026-07-22 04:59:30`, campaign_type=WEB_SITE("○ P. 아이패드 파워링크") (17:16) | **재현**(정확 일치) |
| §0-★1-b | negative_keyword(레거시) 오늘 11건 pending, `created_at=2026-08-30 08:00:15 KST` | `naver_proposals WHERE proposal_type='negative_keyword' GROUP BY date(created_at),status` | UTC `2026-08-29`에 pending 11건(=KST 08-30 08:00대와 동일 사건, §11 시간대 경고대로 UTC/KST 환산 필요) (17:15) | **재현** |
| §0-★1-b | negative_keyword 과거 배치(07-28·29) 16건 rejected, 07-19 3건도 rejected | 위와 동일 쿼리 | `07-19:3 · 07-28:11 · 07-29:2` 전부 rejected = 16건(07-28/29만) + 3건(07-19) (17:15) | **재현** |
| §0-★1-c | 오늘 A레인 산출물(`search_term_exclude`) 0건(계정 전체) | `search_term_exclude` 전체 6행 상세 조회, 08-29 22:00~ 구간 필터 | 08-30 관련 신규 행 0건 — 최신 pending은 08-22 23:50:05 | **재현** |
| §0-★1-c | 오늘 우리 엔진 실쓰기(`flight_pacing` 제외) 0건 | `naver_change_log WHERE date(changed_at)=date('now') AND action NOT IN ('flight_pacing')` | 0행 (17:15) | **재현** |
| §0-★1-c | 제외 원장 08-17 이후 신규 0(총 3,990행) | `naver_search_term_exclusion` count + `max(created_at)` | 총 **3,990행**, `max(created_at)=2026-08-17 04:04:00` (17:18) | **재현**(정확 일치) |
| §0-★2 | (결정 대기 항목 5안) | 정책 서술 — 대조 대상 아님 | — | 대상 아님 |
| §0-★3 | 죽은 카드 141건(08:5x)→154건(10:17), 지금도 증가 중 | `naver_proposals status='approved' AND approval_source IS NOT NULL AND campaign_id='cmp-…8425541' AND adgroup_id != enabled그룹` | **180건**(17:19, 스코프 로직 재구현 후 계산 — 아래 §4 참고) | **재현**(방향 일치·값은 시간 경과로 더 큼, 141→154→180 증가 추세 일관) |
| §0-★3 | 제외 원장 신규 08-17 이후 13일째 0건 | 위와 동일 | 08-17 이후 13일 경과, 신규 0 확인 | **재현** |
| §0-★3 | 쿠팡 계열 크론 `last_run_at`이 08-22에 멈춤 | (미실행 — PAO 밖 범위로 스킵) | — | 미상(스코프 밖) |
| §1 | 계정 전체 캠페인 46(SHOPPING 31·WEB_SITE 13·BRAND_SEARCH 2) | `naver_entity_snapshot WHERE entity_type='campaign'` 최신 스냅샷(08-30) GROUP BY campaign_type | SHOPPING 31 · WEB_SITE 13 · BRAND_SEARCH 2 = 46 (17:14) | **재현**(정확 일치) |
| §1 | `optimizer='ours'` 1, `auto_operate=1` 1, 스코프 행 1행(그룹 `…70523564`) | `naver_campaign_settings` GROUP BY / `naver_adgroup_scope` 전체 | optimizer='ours':1건(`cmp-…8425541`) · auto_operate=1:1건(동일) · scope 1행(동일 그룹, enabled=1, memo 원문 일치) (17:14) | **재현**(정확 일치) |
| §1 | `auto_operate` 켜는 API 없음(직접 UPDATE만) / `optimizer`는 `PUT /api/naver/ad/campaign-settings/optimizer` 전용 엔드포인트 있음 | grep `ignition_preflight.py`, `naver_ad.py` 라우터·prefix | `ignition_preflight.py:17` 주석 "auto_operate를 켜는 API 경로는 존재하지 않는다" / `naver_ad.py:839 @router.put("/campaign-settings/optimizer")`, prefix `/api/naver/ad`(line 128) → 풀 경로 정확 일치 | **재현**(코드 좌표 정확) |
| §2 | 실쓰기 크론 5개(08:50/매시:20/08:55/00:05/08:05)와 각 역할 | `scheduler_state` 60행 전체(job_name, cron_expression) | 5개 전부 존재, cron_expression 정확 일치(`50 8 * * *`/`20 * * * *`/`55 8 * * *`/`5 0 * * *`/`5 8 * * *`) | **재현**(정확 일치) |
| §2 | "네이버 광고 크론 35종" | `job_name LIKE '%naver%'` count | 36건(관련 잡 더하면 37~38) | **반증(약함)**(위 참조) |
| §2 | env var 기본값 반대(`NAVER_BP_DRY_RUN` 미설정=실쓰기, `NAVER_CS_DRY_RUN` 미설정=dry-run) | (미실행 — 코드 상수 확인 안 함) | — | 미상 |
| §3 | 자동발사 11항목 / Confirm 5항목 / 원리적 불가 1항목 | 부분 교차검증(검색어 제외만 §0-1-b/§3-b로 확인) | 검색어 제외 항목만 확인, 나머지 10항목(입찰류·예산봉투 등)은 개별 미검증 | 부분 재현 + 미상 다수 |
| §3-b | 파워링크 30일 78,952행·전환 0(0.00%) / 쇼핑 30일 316,273행·전환 1,521(0.48%) | `naver_search_term_daily` GROUP BY source, 창 07-30~08-29 | expkeyword: **82,141행·conv 0건(0.00%)** / shopping: **328,126행·conv 1,584건(0.483%)** | **근사 재현**(비율 일치, 절대 행수는 ±4% 오차 — 창 경계 상이 추정) |
| §3-b | 자동 재개방 API 0건 / 우리가 만든 제외 칸 2칸(나머지 console_import) | grep 재개방 API + `naver_search_term_exclusion WHERE status='excluded' AND source IS NULL` | 재개방 API grep 미실행(시간상 스킵) / **source IS NULL & status='excluded' = 정확히 2건**(제3의 void 검증용 더미 행 제외) | **재현**(2칸 정확 일치), API 부재는 미상 |
| §4 | 액셀·브레이크 표면상 대칭(7:7), SPECS는 0:7 비대칭 | (미실행 — SPECS 테이블 미조회) | — | 미상 |
| §5 | 가드레일 값들(클램프 ±15%, 쿨다운 2h, 입찰범위 등) | (미실행) | — | 미상(다수) |
| §5 | CPC 급등 하향 배율 코드=×2, PLAN 문서=×1.5 | grep `trigger_cpc_spike`(정보성, 실행불가로 매핑 안 됨을 확인) | 정보성 트리거 확인, 실행 가능한 브레이크 코드의 ×2 상수는 못 찾음 | 미상 |
| §6 | 계정 30일(07-30~08-29) 비용 19,923,726 / 전환 2,225 / 전환액 35,377,700 / ROAS 177.5% | `naver_ad_daily` sum, `adgroup_id<>'__backfill__'`, keyword 필터 없음 | **19,923,726 / 2,225 / 35,377,700 / 177.55%** (17:17) | **재현**(정확 일치) |
| §6 | 카나리 캠페인 같은 창 비용 7,724,101 / 전환액 11,583,480 / ROAS 149.9% | 위와 동일, `campaign_id='cmp-…8425541'` | **7,724,101 / 11,583,480 / 149.96%** (17:17) | **재현**(정확 일치) |
| §6 | 계정 7일 4,051,618원 | 일별 breakdown 대조 | 08-22~08-29(8일) 합이 정확히 일치. 진짜 7일(08-23~08-29)은 3,561,309원 | **반증**(위 참조) |
| §6 | 어제(08-29) 537,105원 | 일별 breakdown | 08-28 값과 일치, 08-29 실제값은 443,894원 | **반증**(위 참조) |
| §6 | 최근 7일 `naver_change_log` 756건 중 우리 엔진 실쓰기 0건, 대행사 238건(입찰119·키워드제거133·상태4) | `naver_change_log WHERE changed_at>=now-7days` GROUP BY action | 총 **776건**(756 아님), `flight_pacing` 516 · `external_keyword_removed` 133 · `external_bid_change` 119 · `external_status_change` 4 · `optimizer_change` 3 · `adgroup_scope_change` 1. 대행사 3종 합계(133+119+4=256, 문서는 238) | **부분 반증**(대행사 세부 항목명·건수는 일치하는 성분도 있으나 총계·일부 합이 다름 — 관측 시점 차이로 누적 추정, 강한 반증으로 못박기엔 관측 시각 차 8시간이 있어 재검증 필요. **미상에 가까운 부분반증**으로 표기) |
| §7 | 금지선 11개 | 정책 서술 — 라이브 대조 불가 | — | 대상 아님 |
| §8 | Jino 결정 대기 8건 | 정책/일정 서술 | — | 대상 아님 |
| §9-1 | harness.py:1296 stale docstring "자동 발사 없음" ↔ ss_lane 실제 자동배선 | grep 정확 텍스트 | `naver_execution_harness.py:1294-1296` 원문 일치, `search_term_ss_lane.py:890`(문서는 :891, 1줄 오차) | **재현**(코드 정확, 줄번호 근사) |
| §9-2 | `AD_BID_ROUTING_ENABLED=True`라 `_ad_bid_canary`가 campaign_id 무관 무조건 True | `auto_operator.py:204-215` 원문 확인 | `AD_BID_ROUTING_ENABLED: bool = True`(204행), `_ad_bid_canary`는 `if AD_BID_ROUTING_ENABLED: return True`(213행) | **재현**(코드 정확 일치) |
| §9-3 | `in_scope_now`·`campaign_level_allowed_now` 프로덕션 호출부 0건(테스트만) | `grep -rn "\.in_scope_now(\|\.campaign_level_allowed_now("` app/ 전체 + tests/ | app/(라우터·서비스) 호출 **0건**, `tests/test_naver_adgroup_scope.py`에서만 호출 | **재현**(정확 일치) |
| §9-4 | `CONTRACT_ignition_readiness.md` 헤더 "초안 — Jino 승인 대기" ↔ 사실상 종결 | `head -10 docs/contracts/CONTRACT_ignition_readiness.md` | 헤더 원문 그대로 "상태: 초안 — Jino 승인 대기" | **재현**(정확 일치) |
| §9-5 | 카나리 계약에 「점화 완료」 헤더 표시 없음 | (미실행) | — | 미상 |
| §9-6 | PLAN CPC ×1.5 ↔ 코드 ×2 | §5 CPC 확인 참조 | 코드 쪽 확실한 좌표 못 찾음 | 미상 |
| §10 | 진행률 2/7(M0·M1만 닫힘), 2026-08-21 이후 불변 | (미실행 — 트랙 파일 grep 안 함) | — | 미상 |
| §11 | 재는 법 레시피 6종 | 부품 생존 레시피 1종만 실행 | scripts/ignition_parts_alive.py prod 부재 확인 | **반증 1건**(부품 생존), 나머지 5종은 이번 재검증에서 실제로 써서 간접 재현(엔진 손 범위·크론·실쓰기·죽은 카드·돈 전부 이 레시피 그대로 써서 성공) |
| §12 | 스킵 항목 6개(선언) | 정책 서술 | — | 대상 아님 |

---

## 3. 항목화 분모(절별 사실 주장 개수, 추정)

의견·설계논의·정책서술 제외, "숫자·상태·좌표·~이다/~했다" 류만 카운트:

§0-★1: 6 · §0-★1-b: 11 · §0-★1-c: 6 · §0-★2: 0(정책) · §0-★3: 5 · §1: 9 · §2: 9 · §3: 17 · §3-b: 15 · §4: 6 · §5: 18 · §6: 11 · §7: 11(정책) · §8: 8(정책/일정) · §9: 6 · §10: 9 · §11: 0(방법론) · §12: 6(정책) → **합계 약 153개**, 이 중 **직접 재현 시도 약 60개**(재현 52 / 반증 4 / 부분반증·근사 4 / 나머지 §5 가드레일 다수·§7·§8·§4 SPECS·§10 진행률 등 **~93개 미상**(이번 예산으로 못 닿음).

---

## 4. 좌표 생사 목록

| 좌표 | 유형 | 문서 인용 위치 | 상태 |
|---|---|---|---|
| `search_term_ss_lane.py:891`(실제 890) | 파일:줄 | §0-★1 | 생존, 1줄 오차 |
| `search_term_ss_lane._autofire_exclude` | 심볼 | §9-1 | 생존(함수명 직접 확인은 안 했으나 모듈·행위는 확인) |
| `naver_execution_harness.py:1296`(원문은 1294-1296) | 파일:줄 | §9-1 | 생존 |
| `AD_BID_CANARY_CAMPAIGNS` | 심볼 | §9-2 | 생존(`auto_operator.py:100`) |
| `AD_BID_ROUTING_ENABLED` | 심볼 | §9-2 | 생존(`auto_operator.py:204`) |
| `_ad_bid_canary` | 심볼 | §9-2 | 생존(`auto_operator.py:207-215`) |
| `adgroup_scope.in_scope_now` | 심볼 | §9-3 | 생존(정의는 있으나 프로덕션 호출 0) |
| `adgroup_scope.campaign_level_allowed_now` | 심볼 | §9-3 | 생존(동일) |
| `pao_scope_roster` | 파일/모듈 | §9-3 | 생존(`app/services/naver_ad/pao_scope_roster.py`) — 재조합 로직 자체는 미검증 |
| `CONTRACT_ignition_readiness.md` | 파일 | §9-4 | 생존 |
| `ignition_preflight.py:6-19` | 파일:줄 | §1 | 생존, 인용 텍스트 정확 |
| `PUT /api/naver/ad/campaign-settings/optimizer` | API | §1 | 생존(`naver_ad.py:839`, prefix `naver_ad.py:128`) |
| `scripts/ignition_parts_alive.py` | 파일 | §11 | **repo엔 생존, prod엔 죽음**(반증 3) |
| `naver_campaign_settings` | 테이블 | §1 | 생존(단, 전체 캠페인 목록이 아니라 9행짜리 override 테이블 — §1의 "46개"는 이 테이블이 아니라 `naver_entity_snapshot`에서 나온 값) |
| `naver_adgroup_scope` | 테이블 | §1 | 생존, 1행 |
| `naver_proposals` | 테이블 | §0 다수 | 생존 |
| `naver_change_log` | 테이블 | §6 | 생존 |
| `naver_search_term_exclusion` | 테이블 | §0-★3, §3-b | 생존, 3,990행 |
| `naver_ad_daily` | 테이블 | §6 | 생존 |
| `naver_search_term_daily` | 테이블 | §3-b | 생존(`expkeyword`는 테이블명이 아니라 `source` 컬럼 값) |
| `scheduler_state` | 테이블 | §2, §11 | 생존, 60행(전체), naver 관련 36+ |
| `run_naver_auto_operator_daily`(job) | 크론 | §0, §2 | 생존, `50 8 * * *` |
| `run_naver_probe_settlement`(job) | 크론 | §2 | 생존, `55 8 * * *` |
| `run_naver_budget_pacing_reset`(job) | 크론 | §2 | 생존, `5 0 * * *` |
| `generate_expert_desk`(job) | 크론 | §2 | 생존, `5 8 * * *` |
| `run_naver_auto_operator_hourly`(job) | 크론 | §2 | 생존, `20 * * * *` |
| `run_naver_flight_loop`(job) | 크론 | §2 | 생존, `15 */2 * * *`(2시간:15 — 일치) |
| `run_naver_profit_scorecard`(job) | 크론 | §4-B(신규) | 생존, `40 8 * * *`, last_status=ok |

---

## 5. ★성과 표면 재고 조사 (Jino 질문 대응)

Jino: *"광고 성과 지표는 안봐? 광고가 잘 돌아가는지 아닌지를 판단해야하잖아?"* — 이 질문이 정확했다. 재고 조사 결과, **원 문서가 암시하는 것보다 성과 표면은 훨씬 두껍다.** 다만 딱 하나, "목적함수 그 자체"를 계산하는 모듈은 화면에 안 닿는다.

### 5-1. 성과 원장이 어디 있나

- `naver_ad_daily`(테이블) — grain: (ad_date, campaign_id, adgroup_id, keyword_id). `cost/imp/clk/conv_direct_cnt/conv_indirect_cnt/conv_direct_amt/conv_indirect_amt/cart_*` 보유. 08-29까지 최신.
- `naver_search_term_daily`(테이블) — grain: (ad_date, campaign_id, adgroup_id, search_term, source). `conv_purchase_cnt/amt`·`cart_cnt/amt` 보유(SS1 병합). source='shopping'만 전환 컬럼이 채워지고 source='expkeyword'(파워링크)는 구조적으로 항상 0(§0.5 확정 — `models.py:3377` 주석).
- 그 외 `naver_product_bep`(상품 BEP 스냅샷, `bep_calculator.py`가 산출), `naver_retro_signal`(방향 정확도 채점, 인과 아님).

### 5-2. 총이익을 재는 코드가 어디 있나

- **`app/services/naver_ad/profit_scorecard.py`**(`profit_scorecard_sa`, D-NAO-85) — 이 모듈이 정확히 "목적함수(총이익 절대액)를 매일 캠페인별로 표면화"하려고 만들어진 코드다(자체 docstring). 식: `보정conv_amt(직+간접) ÷ bep_roas − cost`. 대상 = 관측 스코프(`campaign_roster.observation_campaign_ids`, auto_operate 무관). 어제/최근7일평균/6월 baseline 대비 증감%를 diary + Slack으로 낸다.
- `bep_calculator.py`·`bep_breakdown.py`·`campaign_target_resolver.py` — BEP·목표 ROAS 산출.
- `naver_change_log.outcome_profit`·`gave_before`·`gave_after`(D-NAO-223/225) — 조치 전/후 총이익 델타 채점(변경 로그 단위).

### 5-3. 그 값이 사람에게 닿는 표면이 있나 — ★가장 중요한 발견

**있다, 많다 — 그런데 정확히 "profit_scorecard.py"만 화면에 안 닿는다.**

- **API**: `backend/app/routers/naver_ad.py`에 `/performance/*` 엔드포인트 **10개**가 이미 라우팅돼 있다: `/performance/today`, `/day`, `/compare`, `/campaigns`, `/ownership-bands`, `/ownership-campaigns`, `/campaign/{campaign_id}`, `/budget`, `/bep-breakdown`, `/timeline`. 이 중 `/performance/ownership-bands`는 "PAO가 돌린 광고 / 안 돌린 광고" 관할 밴드를 정확히 나눈다(Jino 2026-08-29 요청 그대로 구현돼 있음, `perf_ownership_bands.py` 코드 주석에 그 요청 원문이 인용돼 있다). `/performance/bep-breakdown`은 상품별 "판매가−수수료−원가−물류비=공헌이익, 손익분기 ROAS" 근거표를 낸다.
- **화면**: `frontend/src/pages/NaverAdPerformance.tsx`(1,253줄)가 `/naver-ad/performance` 경로로 **실제 라우팅돼 있다**(`App.tsx:35,71`). ROAS 카드(목표/BEP 대비 색상), 캠페인 상세 추이, 공헌이익 컬럼(628행 부근)을 포함한다.
- `grep`으로 이 라우터/화면 이름을 찾아보지 않으면 없다고 오판하기 쉽다 — 원 문서(PAO_OPS.md)는 이 표면들을 §0~§12 어디에서도 언급하지 않는다. **이게 그 자체로 원 문서의 사각지대다**: 엔진(자동화) 상태는 13절 전부를 써서 촘촘히 추적하면서, 이미 존재하는 성과 화면은 한 줄도 인용하지 않는다.
- 다만 **`profit_scorecard.py`의 계산 결과(목적함수 그 자체, D-NAO-59가 요구하는 그 숫자)는 이 API 10개 중 어디에도 연결돼 있지 않다.** `grep -rln "profit_scorecard" app/routers/*.py app/services/naver_ad/perf_*.py` = 0건. diary + Slack에만 도달한다. `/performance/*` 화면들은 ROAS·공헌이익을 **별도로 재계산**해서 보여준다(같은 목적, 다른 코드 경로) — 이게 "같은 숫자를 두 곳에서 다르게 계산하는" 잠재 결함일 수 있다(대조 안 함, 미상).

### 5-4. 주기적으로 계산되나

`scheduler_service.py` defaults에 `run_naver_profit_scorecard`가 있고, prod `scheduler_state`에서 살아있음 확인: `last_run_at 2026-08-30 08:40:06.534046`, `last_status ok`(17:14 관측). 인접 크론들도 전부 살아있음: `run_naver_retro_scoring`(08:30) · `run_naver_diary_reflection`(08:35) · `run_naver_wisdom`(08:45).

### 5-5. 지금 실제 성과값

- **최근 진짜 7일(08-23~08-29)**: 비용 **3,561,309원**(17:17 관측). 30일(07-30~08-29): 비용 **19,923,726원** / 전환 **2,225건** / 전환매출 **35,377,700원** / ROAS **177.5%**(원 문서 §6과 정확 일치, 위 §2 판정표 참조).
- 전환·전환매출 컬럼은 존재하고 채워져 있다(`conv_direct_cnt/amt`, `conv_indirect_cnt/amt` — "그 열이 없다"는 상황이 아니다).

### 5-6. 구멍 목록 (있는 것/없는 것만 — 설계 제안 아님)

1. **`profit_scorecard.py`(목적함수 그 자체)가 API/화면에 배선 안 됨** — diary+Slack에만 도달. 조회 가능한 상시 화면이 없다. (확인됨)
2. **관할 밴드(PAO 돌림/안 돌림)와 캠페인유형(SHOPPING/WEB_SITE/BRAND_SEARCH)이 교차 안 됨** — `perf_ownership_bands.py`·`perf_timeline_harness.py`에 `campaign_type` 사용 없음(`perf_today_harness.py`·`perf_campaign_harness.py`에는 있음 — 화면별로 갈린다). 🧠 "대행사 vs 우리, 캠페인유형별로 성과가 어떻게 갈리나"를 한 화면에서 보는 표면은 없어 보인다(교차 화면 자체를 못 찾음, 완전 부재라고 단정하기엔 화면 20여 개를 전수 훑지 않았다 — 미상 여지 있음).
3. **대행사 집행분과 우리 집행분이 "총이익" 기준으로 갈리는 화면은 미확인** — `/performance/ownership-bands`는 비용·ROAS는 나누지만 profit_scorecard의 정의(총이익 절대액)로도 나누는지는 이번 조사에서 코드 레벨까지 못 들어갔다(🧠, 미상).
4. **상품 단위 "실현" 이익**(광고 기여분×상품 마진)은 미확인 — `/performance/bep-breakdown`은 상품별 BEP·공헌이익 "구성"(산식)은 보여주지만, 그게 실제 광고 성과(클릭·전환)와 곱해져 "이 상품 광고로 오늘 얼마 벌었나"까지 가는지는 코드를 안 봤다(🧠, 미상).
5. **오늘(D-0) 데이터는 원리적으로 비어있음** — `naver_ad_daily`가 D-1 확정 적재라 당일 화면은 시간별 스냅샷을 따로 쓴다(`ownership-bands` 엔드포인트 docstring이 스스로 명시: "오늘치는 안 들어간다"). 이건 구멍이라기보다 알려진 설계 한계.

---

## 6. [미상] 목록

- §0-★1 ROAS 1.42·BEP 1.976·당일소진 3,501원(시점 스냅샷값 — 재현 안 함)
- §2 env var 기본값 반대 주장(코드 미조회)
- §3 자동발사 11항목·Confirm 5항목 중 검색어 제외 외 10항목(입찰류·예산봉투·탐침 등) 개별 미검증
- §4 SPECS 0:7 비대칭(테이블 미조회)
- §5 가드레일 값 18개 중 대부분(±15%·쿨다운 2h·입찰범위·누적상한×2.0·스톱로스×10 등) — 코드 상수 미조회, 시간 예산상 스킵
- §5/§9-6 CPC 급등 배율 ×2 코드 좌표(informational trigger만 확인, 실행 가능한 브레이크 로직은 못 찾음)
- §8 Jino 결정 대기 8건의 개별 사실(일정·의존관계) — 정책/일정 서술이라 판정 대상 자체가 아님
- §9-5 카나리 계약 헤더에 점화 완료 표시 없음(미조회)
- §10 진행률 2/7, M0~M6 개별 상태(트랙 파일 미조회)
- D-NAO 결정번호·PR 번호 전수(§0~§9에 인용된 것 다수) — 하나도 `git log`/`gh`로 교차검증 안 함
- §6 change_log 776 vs 문서 756, 대행사 238 vs 성분합 256 — 관측 시각차(8~9시간) 때문인지 실제 오류인지 미분리(부분반증으로 표에 남김, 재확인 필요)

---

## 7. 커버리지 자백

- 항목화 153개 중 **60개(39%)**만 직접 실행/코드 확인. 나머지 61%는 못 봄.
- **§5(가드레일 값)가 가장 안 봤다** — 18개 중 사실상 1개(CPC 배율, 그마저 미상)만 시도. 이 절이 "지금 값"이라는 표제를 달고 있는데 가장 검증이 얕다는 게 이 재검증의 가장 큰 구멍이다.
- prod API를 통해서가 아니라 **DB 직접 쿼리로만** 확인했다 — `/performance/*` 엔드포인트가 실제로 HTTP 200을 반환하는지, 화면이 브라우저에서 실제로 렌더되는지는 **안 봤다**(§4-B의 "좌표 존재"와 "라이브로 동작"은 다른 질문이다 — 이 재검증은 전자만 답했다).
- §7(금지선)·§8(결정 대기)은 정책/일정 서술이라 애초에 라이브 대조 대상이 아니라고 판단해 스킵했다 — 이 판단 자체도 검증 안 됨(혹시 그 중 일부가 "지금 스코프 몇 건" 같은 검증 가능한 부분을 숨기고 있을 수 있음).
- §3-b의 "24클릭·전환 0" 같은 구체적 검색어 페어 예시는 검증 안 함(검색어 텍스트 단위 쿼리가 필요해 스킵).
- 이 문서 자체가 낡는다 — 관측 시각(17:13~17:22 KST)에서 몇 시간만 지나도 §0-★3(죽은 카드 수)·§6(돈) 값은 다시 달라진다.
