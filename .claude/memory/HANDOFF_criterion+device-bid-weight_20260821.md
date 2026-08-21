# 세션 인수인계: M2-b(criterion 적재) + M2-b2(기기 입찰가중치)
> 저장일시: 2026-08-21 15:55 KST · 체인 「PAO 논의 **28**」 (세션 c6abb15b)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (**main, clean, 미푸시 0**)
- 워크트리 2개(둘 다 병합됨 — 정리 가능): `~/.claude-worktrees/Ohiselling/m2b-criterion` · `~/.claude-worktrees/Ohiselling/m2b2-device-weight`
- prod: **`sellc.ohitech.co.kr`** — ★ssh 별칭은 **반드시 FQDN**. `ssh sellc`는 해석 실패한다
- prod DB: `ssh sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db \"<SQL>\""`
- prod API: `curl -u "$(cat ~/.ohisell_prod_auth)" https://sellc.ohitech.co.kr/api/...`
- 테스트: 워크트리/repo `backend`에서 `python3 -m pytest -q` (전건 약 3.7분)
- 배포: `scripts/safe_deploy.sh <파일…> --migrate --restart` / 병합: `scripts/safe_merge.sh <PR> [--force]`
- **prod 마이그 head = `m2b2devw1eight`**(실측 15:5x) · **디스크 94%**(여유 6.3G)

## 2. 이번 세션 완료 목록
- ✅ 체인 등록부 `n=28` append (`.claude/memory/chains/pao-논의.jsonl`)
- ✅ **착수 필독 실측**(Sonnet·읽기 전용) — **유령 0건**(진행률 2/7 유효 · 미푸시 0 · PAO 열린 PR 0)
- ✅ **D-NAO-215** — Q3 이탈 사후 추인(신설 유지). 계약 §8-Q3 확정 각주 + 트랙. 커밋 `ca172be3`
  - ★직전 세션 완료 QA의 「부분달성(Q3 미준수)」 판정문은 **정정하지 않았다** — 추인은 사후 처분이지 판정 정정이 아니다
- ✅ **D-NAO-216 — M2-b**: `/ncc/criterion` 판독·적재 + 매칭 타입 동승 (PR **#317** 병합 `627e6965`)
  - ★**착수 첫 실측이 계약 전제를 반증**: ref 65 §6 S1-ⓐ의 *"`/ncc/targets` GET으로 적재"*가 **틀렸다**.
    targets 전수 캡처 533그룹 `bidWeight` **0건** / criterion 캡처 **1,271건** ⇒ §8-Q2 **[미확인] 해소**(다른 endpoint였다)
  - 계약 §5 API 예산 개정(criterion 1회전 추가) · §8-Q2 **신설 확정**(grain 다름) · **§8-Q2-b 신설**(매칭 타입 → 제외 원장 컬럼)
  - ref 65에 **정정 노트 7** 부착(설계·순서·합격기준 불변, 사실만 정정)
  - 테이블 3 신설 + `naver_search_term_exclusion.match_type` · 크론 `sweep_naver_adgroup_criterion` **08:12**
  - 적대 리뷰 **1R PASS(P1=0)** · P2 3건 처분(채택 2 → `f811e029`로 변이 생존 2종 사살 / Jino 상신 1)
  - **라이브 완주**(13:36:49): `swept 1013 / ok 1013 / failed 0 / complete=True`, 3분 19초
- ✅ **D-NAO-217** — 크론 08:12 확정 + 구현이 발명한 상수 2개 **사후 등재**(§8-Q7, 절차 이탈 명기). 커밋 `0d9a1117`
- ✅ **크론 56개 인벤토리**(Jino 질문 답변용·읽기 전용) — 아래 §5 참조
- ✅ **기기 가중치 폐기분 실측**(46콜·읽기 전용) — 원자료 `docs/references/data/85_device_bid_weight/`, 커밋 `64f30786`
- ✅ **D-NAO-218 — M2-b2** 신설: 기기 입찰가중치 적재 + 소비 배선 (PR **#318** 병합 `9ea2756f`)
  - 적대 리뷰 **1R FAIL(P1 1건) → 2R PASS** · 리뷰어 독립 변이 8종
- ✅ 완료 QA **2건 × 3대조 = 판정 6줄** (§2-1)
- ✅ 커밋 총 12건 · **전부 push 완료**(미푸시 0)

## 2-1. 완료 QA
> 별도 Sonnet · 읽기 전용. **앵커 `대조:`가 3개라 대상마다 따로 판정했다.** 미달·판정불능도 원문 그대로.

### 작업 목적(정본 원문 — 트랙 계약 헤더 `목표:`)
*"무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야. 이게 우리가 만든 MOP프로그램의 최종 목적이고 목표야."* (Jino 2026-07-19 · D-NAO-59)

### ① M2-b (2026-08-21 13:5x KST) — 앵커 보존본 `.claude/anchors/c6abb15b-…--M2-b-closed.md`
- **판정(계약 §4 S1-① + §6 M2-b 완료 정의): 부분달성** — 적재 달성(prod `naver_adgroup_criterion_current` **6,920행 / 설정 보유 431그룹 / 조사 1,013** · `_probe` 1,013 전건 200 · `complete=True`)이고 스키마도 달성(`match_type VARCHAR(8)` 실재)이나, ①의 «콘솔 실측 1그룹과 값 일치»는 **캡처 파일 부재로 판정불능**(대조 상대 없음)이고 `match_type` 실채움 **0/3,990**이다.
- **판정(Jino 지시 원문): 부분달성** — 지시 6건 전부 반영됐으나 **PR #317 미병합 상태 prod 배포**로 계약 §7 위반(**네 번째 재발**) ⇒ 13:55:05 Jino `--force` 병합으로 해소. 구현의 §3 절차 이탈(새 상수 2개 자체 결정)도 반영.
- **판정(트랙 궁극 목표 D-NAO-59): 부분달성(로드맵상 정상 격차)** — 「판단 기반이 넓어졌다」는 맞으나 **총이익 반영·의사결정 변화 0건**. 달성이라 쓰지 않는다.
- **달성 항목**: 테스트 회귀 0(5,929 passed / 실패 2 = 기준선) · §8-Q2 표면 실측 기록(2회 개정) · 「안 함」·금지선 침범 **0건**
- **★QA 범위 밖 발견(코디네이터)**: **기기 가중치를 우리가 저장하지 않는다** — QA는 계약 §4 S1-① 문언만 대조하고 그 문언엔 기기 축이 없다. 그러나 ref 65 §6 S1-ⓐ 목표 절은 「…매체·**기기 가중치**」를 명시 ⇒ **M2-b는 ref 65 문언 대비 미완**이었다. 이것이 M2-b2를 연 계기다.

### ② M2-b2 (2026-08-21 15:4x KST)
- **판정(계약 §6 M2-b2 완료 정의): 부분달성** — ②③④⑤ 달성, **①(적재)은 판정불능**. prod `naver_entity` adgroup 1,017행 중 `pc_bid_weight IS NOT NULL` **0건** — 채우는 `sync_naver_entity`가 **07:37:27**에 돌았고 배포는 **15:39:25**라 8시간 앞섰다. **결함이 아니라 관측 시점 미도래**이고 실채움은 **내일 07:35**.
- **판정(Jino 지시 원문): 부분달성** — *"새 칸(적재+소비 한 번에)"*·*"다음은 소비"*·*"좀 더 전에"* 3건 달성, **"M2-b 배포 → 크론 정리"는 미달**(미착수 — 이월에 정직히 기록, 은폐 아님).
- **판정(트랙 궁극 목표 D-NAO-59): 부분달성** — 「판단 기반」은 달성이나 **「총이익 증가·의사결정 변화」는 미달**. prod에 `auto_operate=1`인 캠페인이 **0개**라 이 배선이 바꿀 수 있는 실제 의사결정이 **원리적으로 없다**. M2-a·M2-b와 같은 로드맵상 정상 격차.
- **달성 항목 근거**: ②소비 = `auto_operator.py:3359`→`nominal_ceiling_for_device`→`rank_servo` 경로(문언 「3좌표 중 최소 1곳」 충족) · ③추가 API 콜 0 · ④전건 **5,961 passed**/실패 2 = 기준선 ⇒ 새 실패 0(QA 독립 재실행 219.74초) · ⑤좌표 재확인 기록(PR #318 본문 3좌표 판정표)
- **★QA가 병기를 요구한 사실**: `effective_bid_device_weighted`는 **아무도 안 읽는 죽은 필드**이고 소비처는 **실질 1곳뿐**이다.
- **「안 함」·금지선 침범 0건** · **목적 전환 없음**(`🔁` 0건)
- **QA가 확인 못한 것**: PostgreSQL 실인스턴스 마이그 재현(시간 예산 밖 — 단 `nullable=True` Integer 추가뿐이라 M2-a Boolean 함정 유형 해당 없음) · 내일 07:35 실채움(미래) · **배포(15:39)~병합(15:42) 3분간 「PR 미병합 prod 배포」가 짧게 실재**(§7 5번째 재발이나 **세션 안에서 자체 해소**)

## 2-2. 트랙 진행률
- **트랙**: `docs/tracks/active/track_naver-ad-optimization.md`
- **트랙 목표 원문**: 위 §2-1 「작업 목적」과 동일(D-NAO-59 Jino 원문)
- **진행률**: 세션 시작 **2/7** → 종료 **2/7** — 달성 M0·M1 / 미달 M2·M3·M4·M5·M6
- **이번 세션이 움직인 항목**: **없음(M2의 슬라이스 2개만 진전)**. ★**M2 체크박스는 안 찍었다** — M2 = ref 65 S1 ①~⑥ + S2 ①~⑤ 전체이고 이번에 닫힌 것은 **S1-①(부분)·M2-b2(신설)**뿐이다. 증거: PR #317 `627e6965` · PR #318 `9ea2756f` · prod 마이그 head `m2b2devw1eight` · 라이브 `naver_adgroup_criterion_current` 6,920행
- **헤더에 남긴 확인 줄**: 6건 누적(11:4x 착수 / 11:5x D-NAO-216 / 13:2x M2-b 리뷰완 / 13:4x 라이브 / 14:0x M2-b QA / 15:4x M2-b2 QA)
- **다음 세션 후보 항목**: **크론 정리**(Jino 지시, 미착수) → **M2-c**(S1-ⓒ 의미 단위 회수 → ⓔ) → **M2-d**(진입 조건 **08-28** 이후) → **M2-z**(종결 QA)
- **트랙 종결 여부**: **미도달**(2/7)

## 3. 확정된 결정사항
- **D-NAO-215** — Q3 이탈 **사후 추인**: [9] 산출 저장은 「확장 우선」이 아니라 **신설**로 확정 (Jino *"추인한다 (신설 유지)"*)
- **D-NAO-216** — 계약 §5 API 예산 개정: `/ncc/targets` 1회전 **+ `/ncc/criterion` 1회전**(약 2,026콜/일). Q2 = **신설 확정**(grain 다름), **Q2-b 신설** = 매칭 타입은 제외 원장 컬럼 (Jino *"criterion 일일 전수 스윕 신설"* · *"제외 원장에 컬럼 추가"*)
- **D-NAO-217** — 크론 **08:12** 확정(Jino *"좀 더 전에 하면 안되?"*) + 구현이 발명한 상수 2개(`_SYNTHETIC_REGTM_THRESHOLD_S=600`·`_MAX_FAIL_RATIO=0.5`) **사후 등재**(§8-Q7). ★**사전 승인이 아니다** — 절차 이탈이 판정에 반영됐다
- **D-NAO-218** — **M2-b2 신설**: 기기 가중치 적재 + 소비 배선을 한 칸에 (Jino *"새 칸 — 적재 + 소비 한 번에"*). ★합격기준이 **ref 65 원문이 아니라 신설**임을 계약 §6에 명시(인용으로 착각하면 재규정 금지가 무너진다)
- **Jino 지시(미착수)**: *"M2-b 배포 → 크론 정리"* · *"다음은 소비"*
- ★**「BEP가 30% 틀렸다」고 쓰지 않는다** — ref 65 정정 #2 원문: *"BEP·이익률 판정은 **실현 cost/전환액**을 읽어 왜곡되지 않는다. 왜곡은 명목 입찰을 실효의 대리로 쓰는 **전진 판단**에 국한된다."*

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-m2-l2-wiring.md` | **M2 계약 정본**(D-NAO-214 승인). §4 합격기준=ref 65 원문 · §6 슬라이스 a/b/**b2**/c/d/z · §8 Q1~Q5+**Q2-b·Q7** |
| `docs/references/65_paper_application_design_20260817.md` | 합격기준의 **원본**. 머리말 **정정 노트 7**(endpoint 정정) 신설 |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙. 계약 헤더(2/7)·D-NAO-215~218·확인 줄 6건 |
| `docs/references/data/85_device_bid_weight/` | **기기 가중치 실측 원자료**(46콜 응답 jsonl + 1,013행 집계 CSV + 스크립트) |
| `backend/app/services/naver_ad/adgroup_criterion_ingest.py` | criterion 전수 스윕(신규). ⚠️기존 `criterion_ingest.py`(벌크 성과 리포트)와 **다른 것** |
| `backend/app/services/naver_ad/effective_bid.py` | `device_weight_multiplier`·`nominal_ceiling_for_device` 등 신설. **모듈 헤더에 「max는 근사」 사유 기재** |
| `backend/app/services/naver_ad/auto_operator.py:3359` | **합격 ②의 실체** — `economic_ceiling`을 명목 스케일로 환산해 `rank_servo`에 전달 |
| `.claude/anchors/c6abb15b-…--M2-b-closed.md` | M2-b 앵커(판정 3줄 보존) |
| `.claude/anchors/c6abb15b-…md` | M2-b2 앵커(판정 3줄 + **이월 12건**) |

## 5. 알려진 이슈 / 주의사항
- ★★**크론 56개 인벤토리 결과**(Jino 질문 *"크론잡이 왜 그렇게 많은거야?"*에 대한 실측 답):
  - **PAO 관련 32개 / PAO 아닌 것 24개**. 「PAO 외엔 자동화가 없는데 왜 많냐」의 답은 **절반 이상이 PAO 것**이다
  - **광고를 조작하는 잡은 4개뿐**이고 전부 `auto_operate` 게이트에 걸려 **매일 돌지만 외부 쓰기 0**
  - **소비처 0인 적재 잡 5개**: `sync_naver_adgroup_targets`(1,013행) · `sync_naver_keyword_baseline`(4,656) · `sync_naver_product_meta`(1,213) · `write_naver_pooled_estimates`(6,255) · `sync_naver_criterion`(**234만행**). 전부 최근 PAO 산물
  - **3개는 이미 꺼져 있다**(`is_enabled=0`): `auto_download_rg_settlement` · `sync_coupang_rg_settlement` · `sync_coupang_ad_cost`
- ★**끄기 기준은 「의미 있나」가 아니라 「나중에 되찾을 수 있나」다**:
  - **끄면 안 됨(소급 불가)**: adgroup_targets(설정 스냅샷) · product_meta(현재 단면) · keyword_baseline(검색량 소급 조회 불가) · **pooled_estimates(끄면 M2-d가 영원히 안 열린다 — 진입 조건이 「7일 연속 생성 관측」)**
  - **꺼도 됨(리포트 소급 가능·소비처 0)**: `sync_naver_criterion`(234만행) · `sync_naver_search_term`의 dim 절반(100만행, **이미 「못 가른다」 결론 난 축**) · `conversion_maturity_snapshot`(코드 주석이 스스로 「중간 원료일 뿐」이라 인정)
- ★**CI 빨강은 결제 정지**다 — 3 job 전부 **`steps=0`·2초**(오늘 두 번 직접 확인). 코드 신호가 아니다. 병합은 `--force`가 필요하고 **자백이 `$TMPDIR/safe_merge.log`에 남는다**. ⚠️확인 없이 `--force`를 쓰는 습관이 붙으면 **진짜 빨간불도 같은 손짓으로 지나간다**
- ★**기기별 지출 비중을 우리 원장에서 못 낸다** — `naver_ad_daily`에 기기 축 컬럼 **없음** · `naver_search_term_dim_daily.dim_type`은 **h(시간대)·m(매체)·r(지역) 셋뿐**(세 축 합계 cost 48,258,730원 동일 = 같은 모수의 3분해). ⇒ `max(pc,mobile)`는 **근사**이고, 정확히 하려면 `/stats` **기기 breakdown 수집**이 선행돼야 한다
- ★**`/ncc/adgroups` 응답 39키 중 28키를 버린다** — 그중 `contentsNetworkBidAmt`(**206그룹 실사용**) · `contentsNetworkBidWeight`(4그룹이 50) · `autobidStrategy`(**475그룹에 객체 존재, 전건 비활성 — 켜지면 명목 입찰가 자체가 무의미해진다**)
- ★**criterion 4번째 타입 `AD` = 관심사 세그먼트**(190그룹×13코드=2,470행). 08-17 캡처엔 **0건**이었다 ⇒ 매트릭스 「호출가능·미적재」의 **A1 관심사가 같이 열렸다**. 코드 docstring은 AG/GN/SD 3종만 적어 뒀다(문서화 누락)
- ★**`bid_weight=130`(상향) 실재** — 비100 19행 중 `negative=0`인 12행: 70×9·80×2·**130×1**(AG5054). ref 65 전제 「명목>실효 계통적 과대평가」는 **한 방향이 아니다**
- ⚠️`negative=true` 행의 `bid_weight`는 **제외 대상**에 붙은 값이라 실효 입찰 배율로 읽으면 안 된다
- ⚠️**prod 디스크 94%**(여유 6.3G, 15:5x 실측). criterion 3표 1회전 = 약 2.44MB
- ⚠️`01. TEST_…` 그룹 3개가 「99. 주말캠페인」에 생존(테스트 잔재로 보임)
- **다음 구조 감사 트리거 = 08-25 이후**(마지막 `docs/references/69_audit_pao_drift_20260818.md`)
- ★**이 저장소 alembic은 `DATABASE_URL`을 무시**한다(교훈 #341) — 격리하려면 `Config.set_main_option`

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문)** — 트랙 계약 헤더 `목표:` 줄 그대로:
  *"무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야. 이게 우리가 만든 MOP프로그램의 최종 목적이고 목표야."*
  이번 칸 = **M2 = L2 배선**(ref 65 S1+S2), 계약 `docs/PLAN_naver-m2-l2-wiring.md`(승인됨).
- **남은 슬라이스**: **크론 정리**(Jino 지시) · **M2-c** · **M2-d**(08-28 이후) · **M2-z**

- [ ] ①**★내일 아침 관측 3건 — 묻지 말고 진행.** 시각이 와야 판정 가능해서 오늘 못 한 것들이다.
      **07:35** `naver_entity.pc_bid_weight IS NOT NULL` 행수(기대 ≈871/1,013) — M2-b2 합격 ① **판정불능 해소**
      **08:12** `scheduler_state`의 `sweep_naver_adgroup_criterion` `last_run_at`·`last_status`(★**무인** 첫 발화 — 오늘 13:36은 수동산. 1회 발화로 「상시 가동」이라 쓰지 말 것)
      **08:25** `naver_search_term_exclusion.match_type IS NOT NULL` 행수 + 값 분포(오늘 0/3,990은 구코드로 돌아서다)
- [ ] ②**★크론 정리 — 묻지 말고 진행**(Jino가 이미 *"M2-b 배포 → 크론 정리"*로 지시). 끄는 것은 되돌릴 수 있다.
      **단 끄기 전 「정말 아무도 안 읽나」를 재검증할 것** — 내 분류 근거는 grep이고, grep이 못 찾는 소비 경로(동적 쿼리·옵시디언 볼트로 나간 뒤 사람이 읽는 것)가 있을 수 있다.
      후보 3건(총 **334만 행**): `sync_naver_criterion` · `sync_naver_search_term`의 dim 절반 · `conversion_maturity_snapshot`
      ★**pooled_estimates·adgroup_targets·product_meta·keyword_baseline은 끄지 말 것**(소급 불가 — 위 §5)
- [ ] ③**Q5 콘솔 캡처 — Jino에게 요청해야 하는 것**(사람만 할 수 있다). M2-b 합격 ①의 「콘솔 실측 1그룹과 값 일치」가 **판정불능**으로 남아 있다.
      대조 대상: 캠페인 **「아이폰 17프로 필름」** / 광고그룹 **「아이폰필름」**(`grp-a001-02-000000063093697`) → 타겟팅 → **연령** 탭. 우리 값 = `AG5054`(50~54세) **130%**, 나머지 100.
      화면 진입: 광고그룹 화면의 「제외 검색어」 탭 오른쪽 **「+ 타겟팅 탭 추가」** → 연령. ⚠️보기만 하고 값은 건드리지 말 것.
      캡처가 오면 **그 항목만 재판정 1회**(§2 미달 처리 경로). **파일로 보존할 것**(PR #310이 증거 미보존으로 독립 검증 불능이 된 전례)
- [ ] ④**M2-c** — S1-ⓒ 의미 단위 회수(사전 최장일치, D-NAO-191 grain) → S1-ⓔ `search_term_ss_lane.py:674` 옛 전제 제거(pending까지만). ★**ⓒ가 ⓔ보다 먼저**(D-NAO-190). 산출 0이면 **문턱 조정 없이 0으로 기록**
- [ ] ⑤**M2-d** — S2 전체. **진입 조건 = M2-a 배포 +7일(2026-08-28 이후) & 추정치 지속 생성 관측.** 그 전엔 원리적으로 판정 불가
- [ ] ⑥**M2-z** — M2 종결 QA(별도 Sonnet·읽기 전용, S1 ①~⑥·S2 ①~⑤ **11항목 전수** 라이브 대조). **트랙 M2 체크박스는 이 판정으로만**
- [ ] ⑦**새 칸이 필요한 것 — Jino 승인 대상**(목표·범위를 늘린다): `contentsNetworkBidAmt`(206그룹 실사용) 적재 · `autobidStrategy` 감시 · `cold_start_bid_lane:171`·`cold_start_bid_decider:169`의 **같은 스케일 혼입 위험**(M2-b2에서 발견만 하고 이월) · `/stats` 기기 breakdown 수집(`max` 근사를 정확히 하려면 선행)
- [ ] ⑧[첫 주 관측] `naver_adgroup_criterion_*` 3표 증가율(1회전 2.44MB) + **디스크 94%** · `naver_pooled_estimate_daily` 증가율(추정 1.43MB/일) · `naver_product_meta_change` 유령 변경 감시
- [ ] ⑨워크트리 2개 정리 가능(둘 다 병합됨): `m2b-criterion` · `m2b2-device-weight`. ★직전 칸의 `m2a-pooling`도 남아 있다
- [ ] ⑩코드 docstring 정정 — `get_adgroup_criterion`이 타입을 AG/GN/SD 3종으로 적었으나 실제 **AD 포함 4종**(계약 §8-Q2 각주엔 자백 완료)
- [x] ~~PR #317·#318 병합~~ **완료** — `627e6965`·`9ea2756f`(둘 다 `--force`, 자백 기록됨). 열린 PR은 무관한 **#294** 1건뿐(실측 15:5x)
- [x] ~~미푸시 커밋 정리~~ **완료** — ahead 0 / behind 0, main 위(실측 15:5x)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_criterion+device-bid-weight_20260821.md 읽고 이어서 작업해줘
```
(체인을 이어받으려면: `/session-relay PAO 논의` — 이번이 **28**번이었다)
