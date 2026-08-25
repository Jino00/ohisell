# 세션 인수인계: **「지혜 순환 목표」 계약 승인 (D-NAO-248·249)**

> 저장일시: 2026-08-25 10:2x KST · 체인 `pao-논의` **n=49** · 세션 `1d272f8c`
> 앞 문서: `HANDOFF_pao-ignition-contract_20260825.md`(n=48)
> **코드 변경 0줄 · prod 배포 0건 · 광고 계정 쓰기 0건** — 이번 세션은 전부 «조사와 계약»이다

---

## 0. ★★우리가 향하는 곳 — PAO 북극성 궁극 목표 (원문 그대로)

> **정본**: `docs/references/82_pao_north_star_20260819.md` §1 (D-NAO-208)

**PAO의 궁극 목표는, 네이버 광고·스마트스토어 API가 주는 모든 지표와 4등급 성과등급 사이의 «개연성(연관)»을 홀드아웃 검증까지 통과한 지식으로 확정하고(①), 논문에서 채택한 기법과 이 트랙이 이미 세운 실행 구조(레인·가드레일·고삐·확장압력) 위에서(②), 우리가 운영하는 모든 광고 형태를 총이익 절대액이 최대가 되는 방향으로(③, D-NAO-59) 자동화 운영하며(④), 운영의 매 행위와 결과를 일기→반성→지혜 승격의 학습 사슬에 태워 어느 지점을 배우면 성능이 더 오르는지를 스스로 찾아, 신호가 허락하는 최단 주기로 성능개선을 반복 시도하는 것(⑤)이다.**

### 트랙 최상위 목표 (D-NAO-59, Jino 원문 2026-07-19)
> *"무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야."*

### ★★Jino가 이번 세션에 «목표 상태»를 원문으로 정의했다 (2026-08-25 09:20)
> *"나는 우리의 자료가 옵시디언, LLM wiki를 통해 학습이 되고 지혜로 올라간 뒤에 다시 그 학습된 지혜가 우리의 광고에 적용이 되었으면 해. 물론 광고에는 한가지 방법만 있는게 아니기 때문에 다양한 시도를 해봐야 해서 꼭 지혜로 올라온 방법만 사용되는건 안되겠지만, 최소한 우리가 지혜는 얻어야 발전이 있고 로직계선이 될거잖아?"*

**진행률 2/7 (M0·M1) — 이번 세션 불변.** ④자동화 **착수 0이 22세션째**.

---

## 1. 프로젝트 위치 및 환경

- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- ⚠️ **공유 메인 폴더는 origin/main보다 300커밋 이상 뒤처져 있다.** 코드·문서는 `git show origin/main:<경로>` 또는 워크트리로 읽어라.
- 이 세션 워크트리: `~/.claude-worktrees/ohiselling/pao-n49` · 브랜치 `docs/pao-n49`(머지됨) → `docs/pao-n49-land`(후속)
- prod: `sellc.ohitech.co.kr` · 백엔드 포트 **8011**(nginx upstream `ohisell_backend`) · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- prod 읽기: SQL을 파일로 써서 scp 후 `ssh sellc.ohitech.co.kr 'sqlite3 -readonly "file:...?mode=ro" < /tmp/q.sql'`(heredoc은 따옴표를 먹는다) · API는 `curl -s "http://127.0.0.1:8011/api/..."`
- ⚠️ **훅 오탐 9회** — `track-progress-sync.sh`가 공유 폴더의 stale 트랙 사본을 읽어 워크트리 확인줄을 못 본다. 공유 사본에 쓰면 앞선 세션 확인줄이 되돌려질 위험 + 살아 있는 세션 침범 위험이라 **안 건드렸다**(n=48과 같은 판단).

---

## 2. 이번 세션 완료 목록

### 2-A. P2 재실행 — 모집단을 직접 재서 인계 숫자를 정정
- 창 **2026-08-17~08-23**(최신 완전일 기준): 실집행 캠페인 **22개**(n=48의 「24개」는 창이 달랐다)
- `naver_entity` 캠페인 **46**(on 26·off 20) · `naver_campaign_settings` **9행 전건 `auto_operate=0 ∧ optimizer='none'`** · `naver_adgroup_scope` **0행**
- ★**관할을 두 자로**: 캠페인 수 **2/22 = 9.1%** / **지출액 1,412,721 ÷ 3,724,882 = 37.9%**. 배제 후 점화 가능은 **1/22(지출 31.0%)**
- **배제표**: `03.아이폰_강화유리`(memo *"03(MOP) vs 04(우리) 철학 A/B의 MOP 열"*) · `04.아이폰_지문방지`(같은 A/B의 «우리 열»인데 **off** ⇒ **A/B는 이미 한쪽만 돈다**)
- ★**오배제 방지**: `rationale`의 'MOP' 17건은 전부 `entity_sync 감지: 외부(MOP/사람)…` **동기화 상용구**였다
- **대행사 접촉 14일 = 4개, 전부 WEB_SITE**(마지막 08-21) ⇒ **SHOPPING엔 14일 대행사 변경 0건**

### 2-B. 광고그룹 이동 — **불가 확정**(세 경로 수렴)
- swagger `PUT /ncc/adgroups/{id}?fields=` enum에 **캠페인 참조 없음**(`nccCampaignId`는 `#required-create`만) · 우리 코드에 생성·이동 함수 0 · prod 전 기간 같은 `adgroup_id`가 다른 `campaign_id`로 나타난 행 **0건**
- API 계층 전체에 「부모 변경」 오퍼레이션이 없고, 소재만 **"copy"**(이동 아님)
- ⇒ **대안 표면화**: `naver_adgroup_scope`(D-NAO-244, 현재 0행)가 **새 캠페인 없이 광고그룹 단위 관할 분리**를 이미 한다

### 2-C. TPU 광고그룹 실측 — 이상치 하나가 33%
- 광고그룹 **68개**(on 63·off 5), ★**on인데 30일 실적 0인 것이 5개** ⇒ 표본 쌓이는 건 **58개**
- **Z폴드8와이드 단독 30일 비용 2,873,824원 = 33.1%**, 상위 3개 **49.0%** ⇒ 「반씩 나눈다」가 원천 불가
- 라이브 로스터(`GET /api/naver/ad/scope/roster`, 창 08-04~08-24 21일, HTTP 200 1.88초, `profit_status=ok` **58/58**): **TPU 총이익 −760,151원 [구간 −1,549,827 ~ +658,521]** — ★**구간이 0을 가로지른다**
- **흑자 23개 +344,458 / 적자 34개 −1,104,609**. **Z폴드8와이드 −494,600 = 캠페인 적자의 65%**(비용 31%·ROAS 1.39 vs **BEP 1.97**)
- 죽은 축: `pc/mobile_bid_weight` 68건 전건 100 · `qi_grade` adgroup 68건 공백·keyword 91,172행 전건 4
- 「신규 캠페인 초기 CPC 불이익」 → **관측 안 됨**(신규 722~912원 vs 계정 1,332·TPU 1,436) ⚠️표본 캠페인 1개·클릭 696건

### 2-D. ★「준비됐나」 실행률 보고 (Jino 08:03 질문)
- **①안전하게 켤 준비 = 됐다** ②**켠 걸 판정할 준비 = 절반** ③**그룹 단위로 배울 준비 = 안 됐다**
- 5요소: ①개연성 **4/36=11%** ②논문 가동 **1/13** ③총이익 자는 라이브·**상품 BEP 231/1,013=22.8%** ④자동화 **0**(마지막 우리 실쓰기 **2026-08-11 09:18:40**) ⑤학습 **정지**
- ★**완성률과 가동률이 갈라져 있다**: 집행층·학습사슬·성적표·스코프 게이트 전부 배포 완료인데 **가동 0**
- ★**제안 적체가 확증**: pending **272**·expired **4,985**(08-24)인데 **approved는 07-30에서 멈춤** ⇒ **엔진은 지금도 생각하는데 손이 묶여 있고 생각한 것은 만료로 버려진다**
- ★**표본 실측(28일)**: 주 15전환↑ 광고그룹 = **계정 5/438** · **TPU 2/58**

### 2-E. ★★★학습 병목의 «뿌리» 규명 (Jino 질문이 짚었다)
> Jino 08:1x: *"배움이 왜 캠페인별로 나뉘지? PAO 전체적인 구조여야 하는거 아니야?"*
- 후보 시그니처 `wisdom_candidates.py:175` = **`{campaign_id}|{action}|{day_class}|{season}|{iphone_window}`** — 캠페인 ID가 키의 첫 자리
- 후보 **27건 = 캠페인 7 × 액션 10 × 환경 3**으로 파편화. 캠페인을 빼면 **27→14**
- ★★**결정적 증거**: 승격된 **유일** 지혜는 `bid_up|weekend|summer` **71회**(bad 68)인데, **같은 액션·같은 계절의 평일판은 4캠페인 합 91회(bad 86)**이면서 **45/38/5/3으로 갈려 전부 rejected** ⇒ **근거가 더 두꺼운 쪽을 못 배웠다**
- 승격 지혜 문장에 캠페인 ID가 박혀 **D-NAO-65 ③「개별 캠페인 하드코딩 금지」와 모순**

### 2-F. ★지혜 «활용»의 세 절단면 (Jino 08:3x 질문)
| 경로 | 상태 | 성적 측정 |
|---|---|---|
| `param_change` 제안 | 제안 **2314**(07-26) → **rejected**, 그 뒤 0건 | ✅ id 추적 |
| 브리핑 프리픽스 주입 | **가동 중**(`expert_briefing_builder.py:53`) | ❌ **자유 텍스트라 원리적 불가**(`wisdom_scorecard.py:50` 자백) |
| Obsidian 볼트 export | 크론 09:05 가동 | ❌ 사람이 읽는 것 |
- ★**`naver_proposals`에 `decided_at`·`decided_by`·`decision_note` 컬럼이 없다** ⇒ 그 기각의 사유가 **관측 불가**
- ★**승인해도 자동 적용이 없다**(`naver_ad.py:300` *"승인해도 자동 적용 없음"* · *"적용은 Jino가 콘솔/설정에서 수동"*)

### 2-G. ★★D-NAO-248·249 — 계약 승인 (Jino 09:57 *"승인"*)
- 정본 `docs/contracts/CONTRACT_wisdom_global_grain.md` v1.1 · 저자 **Fable 5차 개정** · 세션 병합·실측 정정 4회
- **목표이름: 지혜 순환 목표** · 북극성 **M2 슬라이스 + §5-3 부품 ②**
- 미결 4건 전부 권고값: ①**(b′)** 전역 단일 grain + `by_campaign` 분해 병기 ②풀링 경계 **명시 컬럼**(`experiment_batch`) ③기존 지혜 1건 **존치** ④**캠페인 유형을 경계로**
- **적용층 = A(승인=적용)**, 화이트리스트 `guardrail_params.SPECS` **3종만**, 캠페인 다이얼 불개방
- **(iv) 출구 성격 분기**: 무조건부 → 파라미터 / **조건부 → 브리핑**
- **값(크기)은 사람이 승인 카드에서 확정** — 키·방향은 판사
- **검색어 학습층 해제**(`d1_st` status 소비 = D-NAO-178이 지정한 **S8 출구의 집행**)
- **탐색 몫 = 관측만**(숫자 하한은 게이트 신설이라 안 만든다)
- 합격기준 **16항**(A군 배움 7 + B군 적용 7 + C군 검색어 2) · 예산 **Sonnet 4세션 상한**

### 2-H. ↗️스코프 밖(Jino 발의) — ADVoost 스터디 → `docs/references/97_advoost_gfa_operability_20260825.md`
- **판정: PAO에서 운영 불가** — ①권한 게이트(GFA API는 베타·공식 파트너 한정, **Jino 08:0x «파트너를 안 준다»로 ⓓ 폐기**) ②표면 부재(PAO 쓰기 9개 중 대응 3~4개, 키워드류 전부 개념 없음) ③**ADVoost는 사람도 입찰을 못 건드린다**
- ★**가설 반증**: GFA 비용 자동 수집은 **SA API의 비즈머니 청구서**이지 GFA 접근권이 아니다(`naver_sa_ad_fetcher.py:254`) — **「청구서 열람권」을 「계정 접근권」으로 읽을 뻔했다**
- ★★**ref 65 §2 숫자 2건 정정**: 「90일 528만원」은 레거시 CSV **317만원** 누락 ⇒ 계열 전체 **845만원(1.6배)** / 「SA 8,748만원」은 **`__backfill__` 미필터** ⇒ **5,663만원(35%↓)** ⇒ **「6%」가 실제로는 8.5~13%**

---

## 2-1. 완료 QA (별도 Sonnet·읽기 전용) — **판정 원문 그대로**

> ⚠️ **이 절은 QA 판정문이 도착하는 대로 원문 그대로 채운다.** 대조 3개(계약 D-NAO-247 §4 / Jino 지시 원문 / 북극성 §1·§6 M4) 각각 판정 + 종합 1값.

*(판정문 삽입 위치 — 미달·판정불능도 그대로 적는다)*

---

## 2-2. 트랙 진행률

- **트랙**: `docs/tracks/active/track_naver-ad-optimization.md`
- **트랙 목표 원문**: "무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야. 이게 우리가 만든 MOP프로그램의 최종 목적이고 목표야." (Jino 2026-07-19 — D-NAO-59)
- **진행률**: 시작 **2/7** → 종료 **2/7** — 달성 M0·M1 / 미달 M2·M3·M4·M5·M6
- **이번 세션이 움직인 항목**: **없음(M 체크박스 기준).** 산출은 **M2 슬라이스의 계약을 세운 것**이다
- **헤더에 남긴 확인 줄**: **17개 누적**(`b658e7f3`~`c549a513`, 정정줄 2개 포함)
- **다음 세션 후보**: D-NAO-248 구현(A군→B군→C군)
- **트랙 종결 여부**: 미도달(2/7)

---

## 2-3. 착지

- **완료 단계**: 커밋 → push → PR **#433** → ⚠️리뷰 생략(기록물만) → **머지 완료 `b6f77e8d`**
- **멈춘 단계**: 없음 (⚠️단 후속 커밋 `c549a513`이 브랜치 `docs/pao-n49-land`에 **미푸시** — 이 HANDOFF와 함께 2차 PR로 나간다)
- **CI**: backend(py3.10) pass 7m38s · backend(py3.14) pass 7m36s · frontend pass 1m6s — **3/3 실통과**, `--force` 미사용
- **리뷰 판정**: `⚠️ 리뷰 생략: 기록물만 — docs/contracts/CONTRACT_wisdom_global_grain.md · docs/references/97_advoost_gfa_operability_20260825.md · docs/tracks/active/track_naver-ad-optimization.md`(코드 **0파일**)
- **착지 전제 검사**: **L1** 체인 등록부 6개 전건 `closed`(⚠️훅의 `[체인] ⛔ sellc-원가-메뉴` 줄은 **stale 오탐**) · **L4** origin/main 0커밋 앞섬 · 커밋은 **경로 지정**(`git add -- <경로>`)
- ★★**`safe_merge.sh`의 «가짜 성공»**: 1차 시도가 `error connecting to api.github.com`으로 실패했는데 **스크립트는 exit 0**으로 끝났다. `gh pr view`로 **PR이 여전히 OPEN**임을 확인해 잡았고 재시도로 병합. ⇒ **「스크립트가 성공을 반환했다 ≠ 병합됐다」** — 이 저장소가 반복해 밟는 「기록됐다 ≠ 실제로 됐다」의 새 변종. **수리는 하네스 소관(이월)**
- ⚠️ **L5로 「main에 세워둔다」 생략** — 로컬 main이 공유 폴더에 체크아웃돼 있다. 다음 세션은 `git switch -c <새> origin/main`

---

## 3. 확정된 결정사항 (번복 금지)

- **D-NAO-248 「지혜 순환 목표」 계약 승인**(Jino 2026-08-25 09:57 *"승인"*). 정본 `docs/contracts/CONTRACT_wisdom_global_grain.md`
- **D-NAO-249 D-NAO-54 금지선의 «해석» 확정** — 「승인 경로 유지」는 「승인 후 수동」을 뜻하지 않는다. **A(승인=적용)는 금지선을 어기지 않는다**. 여전히 금지: 무승인 자동(B·C) · 화이트리스트 밖 · 봉투 밖 · 광고 API 직접 쓰기
- **광고그룹은 캠페인 사이를 이동할 수 없다** — API·코드·라이브 세 경로 수렴. 재생성 시 성과 이력·제외키워드·심사 리셋, 상품 원가·BEP는 승계
- **ADVoost는 PAO에서 운영 불가** — 파트너 자격은 **Jino 08:0x «우리에게 파트너를 주지 않아»로 영구 차단**. 남는 결정은 ⓒ지출 재검토·ⓔ상품 필터 재료뿐
- **옵시디언·LLM wiki = 운영 일기·지혜(광고용)** 확정(Jino 09:41). 개발 위키(`docs/wiki`)는 광고에 안 닿는 별개 기제
- ⚠️**번호 함정 실증**: `scripts/next_ids.sh`가 **249를 냈으나 오독**(확인줄의 「다음 가용 D-NAO-248」을 실부여로 셈). `git grep D-NAO-248 origin/main` 교차 확인 결과 실부여 최댓값 **247** ⇒ 계약 248. **다음 세션도 반드시 grep 교차 확인할 것**

---

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `docs/contracts/CONTRACT_wisdom_global_grain.md` | ★**승인된 계약 정본(D-NAO-248)** — 합격기준 16항 |
| `docs/references/97_advoost_gfa_operability_20260825.md` | ADVoost/GFA 스터디 + ref 65 §2 숫자 정정 |
| `backend/app/services/naver_ad/wisdom_candidates.py:174-177` | ★시그니처 조립(캠페인 축) — 이 계약의 본체 |
| `backend/app/services/naver_ad/wisdom_candidates.py:151` | D-NAO-178 `search_term` skip — C군이 해제할 자리 |
| `backend/app/services/naver_ad/diary_outcome.py:78~302` | `d1_st` 층(status 4값: stopped/leaking/ambiguous/no_data) |
| `backend/app/services/naver_ad/guardrail_params.py:40~110` | ★화이트리스트 `SPECS` 3종 + 봉투 + `_PARAMS_FROM_DB` |
| `backend/app/routers/naver_ad.py:1050·1085·1128` | GET/PUT `/settings/guardrail-params` + **change_log 기록** |
| `backend/app/services/naver_ad/wisdom_apply.py` | 소비층 — `propose_param_changes`·`active_wisdom_prefix` |
| `backend/app/services/naver_ad/wisdom_judge.py:42-60` | 판사 스키마(`param` 자유 텍스트 — enum 강제 대상) |
| `backend/app/services/naver_ad/adgroup_scope.py:106` | `blocked_by_scope` — 두 게이트가 읽는 단일 술어 |
| `backend/app/services/naver_ad/pao_scope_roster.py` | 라이브 로스터(광고그룹별 총이익 구간) |

---

## 5. 알려진 이슈 / 주의사항

### 5-A. ★세션이 «네 번» 틀렸고 네 번 다 남이 잡았다
| 내 주장 | 실제 | 잡은 쪽 |
|---|---|---|
| `pool_all`은 죽은 코드 | **살아 있다**(prod 24,907행, 크론 09:30) — 북극성 §4(08-17)를 실측 없이 인용 | Fable |
| SHOPPING엔 배울 grain이 없다 | **검색어 성과 grain이 4배 두껍다**(313만행) | **Jino** |
| 자기참조 위험은 A안에서도 남는다 | **탐색 병행이 해소**(87%:0% 실측) | **Jino** |
| KV엔 이력이 없다 | **PUT이 change_log에 before/after를 남긴다**(`naver_ad.py:1128~`) | Fable |
★ 그리고 **오늘 최대 발견 둘도 Jino 질문에서 나왔다** — 「배움이 왜 캠페인별로 나뉘지」(학습 병목의 뿌리) · 「지혜 사용 방법까지 들어있지」(활용의 세 절단면).

### 5-B. 풀링 커버리지는 WEB_SITE에만 닿는다
`naver_pooled_estimate_daily` grain은 **`keyword` 하나뿐**(24,907행) ⇒ 커버리지 **WEB_SITE 8캠페인**, **SHOPPING 0행**. 단 실집행의 입찰 «크기» 경로는 **인라인 수축**으로 SHOPPING 그룹을 이미 다룬다(`auto_operator.py:1545` `group_agg=campaign_agg` 근사 + fail-closed hold).

### 5-C. 집계 창이 «현재»를 가린다 (재확인)
30일 평균으로 보면 멈춘 캠페인/그룹이 활발해 보인다. **마지막 노출일을 항상 병기하라.**

### 5-D. 기타
- 다음 가용 번호: **D-NAO-250 · 교훈 #357**
- `naver_ad_daily` 최신 `ad_date`는 전일까지(크론 07:50). **결번으로 오독하지 마라**(교훈 #356)
- 반성 크론: 최근 성공 **2026-08-19**, 창 39일 중 결번 21일(재료없음 14·실패 0·**미상 7**)
- ⚠️`sellc-원가-메뉴` 체인은 **closed**다 — 훅의 `⛔` 줄은 stale

---

## 6. 다음에 할 작업 (미완료)

- **이어지는 작업의 목적(원문)**: Jino 2026-08-25 09:20 *"…그 학습된 지혜가 우리의 광고에 적용이 되었으면 해… 최소한 우리가 지혜는 얻어야 발전이 있고 로직계선이 될거잖아?"* — 승인된 계약 **D-NAO-248**을 구현한다.

- **남은 슬라이스**:
- [ ] **D-NAO-248 구현 — A군(배움 7항)**: 시그니처 캠페인 축 해체 + `by_campaign` 분해 + 경계 분리(`experiment_batch` 마이그) + 판사 재료 확장 + 후보 현황 블록 + 소비 현황(A7, `naver_proposals` 결정 메타 마이그)
- [ ] **D-NAO-248 구현 — B군(적용 7항)**: 승인=적용 배선(승인 핸들러 → `PUT /settings/guardrail-params`) + 승인 카드 값 입력(현재값 프리필·lo~hi 클램프) + 출구 성격 분기(B7) + 대칭·탐색 관측(B5) + 게이트 소비 확인(B6)
- [ ] **D-NAO-248 구현 — C군(검색어 2항)**: `harvest_candidates`의 `search_term` 분기를 skip → `d1_st` status 소비로 교체
- [ ] **★구현 첫 시간 확인 항목 3**: ①캠페인 유형 시그니처의 출처 조인 컬럼명(`naver_entity` 추정, 미확정) ②`exclude_search_term` vs `search_term_exclude` **액션 어휘 이원화** grep(캠페인 축을 풀어도 이 둘은 안 합쳐진다) ③**`PUT /settings/guardrail-params` docstring 1줄 개정**(*"시스템은 이 경로를 호출하지 않는다"*가 A층위와 문면 충돌 — 안 고치면 적대 리뷰가 모순으로 잡는다)
- [ ] **점화(D-NAO-247)** — 이 계약 «뒤». 남은 게이트는 **Jino §8-① 카나리 지정** 하나. 오늘 P2 산출(후보표·광고그룹 58개 총이익)이 그 자리에서 그대로 쓰인다
- [ ] **이월**: `track-progress-sync.sh` 오탐(하네스 소관) · **`safe_merge.sh` 가짜 성공**(하네스 소관) · 북극성 ③ *"모든 광고 형태"*에 ADVoost 각주 · ref 65 §2 숫자 2건 원문 정정 · 조건부 가드레일(환경별 문턱) 별건 · SHOPPING adgroup grain 저장형 풀링 별건

---

## 7. 새 세션 시작 프롬프트

```
/session-relay PAO 논의
```
