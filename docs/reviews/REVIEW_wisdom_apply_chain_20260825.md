# 적대 리뷰 — D-NAO-248 B군(적용 사슬) + C군(검색어 학습층 해제)

> 2026-08-25 KST · 체인 `pao-논의` n=51 · 세션 `ea5677c4` · 브랜치 `feat/pao-n51`
> 리뷰어: 별도 서브에이전트(Sonnet, 만든 쪽 ≠ 판단하는 쪽) · 코디네이터가 아님
> 계약 정본: `docs/contracts/CONTRACT_wisdom_global_grain.md`
>
> ★**이 파일이 존재하는 이유**: n=50 완료 QA가 *"적대 리뷰 결과물이 파일로 저장돼 있지 않다 … 읽기 전용 원칙상 재실행 재현도 불가 ⇒ **판정불능**으로 남긴다"*고 적었다. **리뷰가 실제로 돌았어도 기록이 없으면 없는 것과 같다.** 그 하네스 결손의 처방으로 이번부터 보고서를 저장소에 남긴다.

---

## 판정: **PASS (P1 0건)**

리뷰 시점 상태: 백엔드 전건 **6,589 passed / 0 failed**(포그라운드) · 프론트 **868/868** · `tsc --noEmit` **0 에러** · 신규 마이그레이션 **0건**.

---

## 1. 금지선 전수 대조 (계약 §3, 13항)

| # | 금지선 | 판정 | 근거 |
|---|---|---|---|
| 1 | 광고계정 쓰기 0건 · `auto_operate`·`optimizer` 조작 0건 | **O** | `naver_execution_harness.execute` 호출 0건(monkeypatch 테스트로 확인) · 두 필드를 건드리는 코드 grep 0건 |
| 2 | 지혜발 파라미터 변경은 승인된 `param_change`로만 · SPECS 3종만 · lo~hi 안에서만 · 무승인 자동 반영 금지 | **O** | `naver_ad.py:479-500`이 상태 전이 «전»에 `applied_value` 필수·SPECS 멤버십을 400으로 검증. 반영 경로는 전건 `POST /proposals/{id}/status` 경유 |
| 3 | `_coerce`·`rejected` 표면화·`_PARAMS_FROM_DB` 약화·우회 금지 | **O** | `_coerce` 원문 그대로 재사용. 변이 C로 무력화 시 8건 즉시 실패(§2) |
| 4 | 캠페인 다이얼(`gamma`·`target_roas_override`)에 지혜 출구 연결 금지 | **O** | SPECS에 해당 키 없음 · `wisdom_apply`는 SPECS 화이트리스트만 참조 |
| 5 | `max_change_pct`를 SPECS에 추가 금지 | **O** | SPECS 3종 불변, 주석이 제외 이유를 명시 |
| 6 | 입찰·예산·제외의 실집행 개시 금지 | **O** | `param_change`는 `_ACTION_BY_PROPOSAL_TYPE`에 매핑 없음(실행 불가 형태 유지) |
| 7 | 기존 후보 27건·지혜 1건 status·판정문 소급 변경 금지 | **O** | 해당 행 UPDATE 코드 없음 |
| 8 | 실험 배치 관찰의 전역 합산 금지(분리 버킷 + 카운터) | **O** | 관련 코드 무변경 |
| 9 | 캠페인 ID 하드코딩 금지 · 판사 판정 코드 강제 금지 · 기각 사유 지어내기 금지 | **O** | 전부 동적 필터. `_classify_param_suggestion`은 사후 «재분류»일 뿐 판사 verdict를 대체하지 않음 |
| 10 | `search_term` 행의 `d1`(캠페인 폴백) 소비 금지 — `d1_st` status로만 | **O** | `_search_term_direction`이 `outcome["d1"]`을 읽지 않음. 변이 A로 5건 즉시 실패 |
| 11 | `scope`·`param` 클램프 폴백 약화 금지(격상 금지) · 폴백 건수 카운터 표면화 | **O** | `_classify_param_suggestion` fail-closed(부재·오타 전부 conditional). 변이 B로 3건 즉시 실패 |
| 12 | 봉투 lo~hi는 배포로만 — DB가 자기 상한을 못 넓힌다 | **O** | `lo`/`hi`를 받는 엔드포인트 없음(PUT·승인 둘 다 value만 받음) |
| 13 | DB 스키마는 마이그레이션으로만(이번 신규 0건) | **O** | `git diff origin/main --stat -- backend/alembic/` 공백 |

---

## 2. 변이 주입 결과

계약 §5가 **표면 변이 2개를 필수**로 지정했다. 단위 테스트는 「함수가 값을 만드나」를 묻지 **「사람이 그걸 보나」를 못 묻는다.**

| # | 변이 | 좌표 | 잡혔나 | 잡은 테스트 |
|---|---|---|---|---|
| **①필수** | 콘솔 `param_gate` 렌더 블록 무력화(`{false && …}`) — 성적표 소비 현황이 **화면까지 안 닿게** | `NaverAdOptimizationConsole.tsx:1532` | **사망** | `naverAdWisdomScorecardPanel.test.tsx` 2건 |
| **②필수** | 승인 핸들러의 `apply_params()` 호출 무력화 — 승인→KV 반영이 **현황판까지 안 닿게** | `naver_ad.py:539` | **사망** | `test_naver_wisdom_apply.py` 4건 |
| A | `_search_term_direction`의 stopped/leaking → good/bad 라벨 반전 | `wisdom_candidates.py:170-172` | 사망 | `test_naver_wisdom.py`·`test_search_term_execution_chain.py` 5건 |
| B | B7 코드 클램프(`scope != "unconditional"`) 무력화 — 조건부도 격상되게 | `wisdom_apply.py:90` | 사망 | `test_naver_wisdom_apply.py` 3건 |
| C | `_coerce`의 lo~hi 범위 검사 무력화 — 봉투 우회 | `guardrail_params.py:137` | 사망 | `test_guardrail_params_p1.py` 7건 + `test_naver_wisdom_apply.py` 1건 |
| **D** | `_param_direction_events`의 `tighten_up` brake/accel 라벨 반전 | `wisdom_scorecard.py:612-613` | ⚠️**생존**(59건 전부 초록) | 없음 → **상환함, 아래 참조** |
| E | `apply_params` merge 경로의 SPECS 밖 키 필터 제거 | `guardrail_params.py:278` | 생존 | 없음 → P2 기각 |

### ★생존 변이 D의 상환 (코디네이터, 리뷰 직후 · 이월하지 않음)
구판 테스트가 **한 테스트에 up 1건 + down 1건을 같이 넣고 합계 `{brake:1, accel:1}`만** 확인해, **라벨을 통째로 뒤집어도 합계가 그대로**라 통과했다 — 교훈 #181의 「통과하는데 아무것도 안 지키는 테스트」다.
- **왜 이월하지 않았나**: 이 표면은 화면의 「⚠ 브레이크만 조여지고 액셀은 0건 — 표류 경보」로 직결된다(D-NAO-85 재발 감시). 라벨이 뒤집히면 **경보 판정이 반대로 뜬다.**
- **처방**: 테스트를 **단방향 4개**로 분리(`test_symmetry_tighten_up_increase_is_brake_only` 외 3).
- **재주입 검증(주장 아님, 실행분)**:
  - `tighten_up` 반전 → `test_symmetry_tighten_up_increase_is_brake_only`·`..._decrease_is_accel_only` **2건 실패**
  - `tighten_down` 반전 → `test_symmetry_tighten_down_decrease_is_brake_only`·`..._increase_is_accel_only` **2건 실패**
  - 원상복구 후 `grep -c MUTANT` = **0** · `test_naver_wisdom_scorecard.py` **61 passed**

---

## 3. P1 목록

**없음.**

---

## 4. P2 트리아지

| 건 | 처분 | 근거 |
|---|---|---|
| D — 방향 라벨이 개별 방향별로 검증되지 않음 | **채택 → 즉시 상환** | 위 §2 참조. 이월 대신 같은 세션에서 갚고 변이 재주입으로 사망 확인 |
| E — merge 경로에서 SPECS 밖 옛 키가 안 걸러짐 | **기각** | `get_params()`·`describe()`가 항상 `SPECS.items()`만 순회 ⇒ 여분 키는 **어떤 실행 경로에서도 읽히지 않는다**(죽은 데이터). 데이터 위생 이슈일 뿐 기능·안전 결함 아님 |
| 판사 `scope` 프롬프트의 `sibling_buckets`↔`scope` 연결 약함 | **이월** | §5 참조 |
| 프론트 `buildApplyValue`가 spec 부재 시 `Infinity` 통과 가능 | **기각** | `<input type="number">`가 "Infinity"를 유효값으로 파싱하지 않아 실사용 도달 불가에 가깝고, 도달해도 `JSON.stringify(Infinity)`=`null` ⇒ 서버가 「applied_value가 필요합니다」 400으로 거부(상태 전이 없음) |

---

## 5. 판사 `scope` 프롬프트 평가 (계약 §5 지정 검사 항목)

프롬프트가 `sibling_buckets`(같은 액션의 다른 환경·유형 후보 승률)를 **promote/reject 판정 문맥에만** 붙여 두었고, `scope` 지시 문단은 물리적으로 분리돼 있으며 `sibling_buckets`를 다시 언급하지 않는다. ⇒ 판사가 형제 버킷을 promote/reject에만 쓰고 **scope는 직관으로 채워도 지시 위반이 아니다.**

더 구체적으로: `_sibling_buckets()`(`wisdom_judge.py:105-127`)는 `action`만으로 필터링하고 `campaign_type`/`experiment_batch`를 안 건다. 그 파일 헤더가 명시한 「절대 안 섞임」 경계를 넘나드는 형제가 재료에 섞일 수 있는데, 프롬프트 어디에도 *"campaign_type/experiment_batch가 다른 형제는 재현성 증거로 보지 말라"*는 경고가 없다. 필드 자체는 sibling dict에 실려 있어 신중한 판사라면 스스로 가를 수 있지만 **프롬프트가 그 구분을 안내하지 않는다.**

**실질 위험도는 제한적**이다 — ①코드 클램프가 `scope=='unconditional' ∧ param∈SPECS`일 때만 제안을 «생성»하고 ②그 제안조차 사람이 값을 직접 입력해 승인해야 반영된다(D-NAO-249). **최종 방어선은 사람이다.** 다만 scope 오판이 반복되면 「실제로는 조건부인 지혜가 계속 unconditional 후보로 사람 앞에 올라오는」 소음이 쌓인다. ⇒ **P2 이월.**

---

## 6. 원상복구

리뷰어 변이 7종 + 코디네이터 상환 검증 변이 2종, **전부 원문 복원 확인**. `grep MUTANT` 0건 · `git status --short`가 PR diff와 정확히 일치.

---

## 7. 리뷰어가 확인 못한 것 (원문 유지)

- **광고계정·DB 실쓰기 여부를 prod에서 직접 확인하지 않았다** — 코드 정적 분석·테스트만 근거.
- **프론트 `naverParamChangeApproval.test.ts`·`naverSymmetryFormat.test.ts`의 개별 케이스 전문을 라인별로 읽지 않았다** — 실행 결과(54건 통과)만 확인, 각 assertion의 엄밀도는 미검토.
- **백엔드 신규 테스트 코드 전문을 라인 단위로 읽지 않았다** — diff 요약·실행 결과·표적 변이로 커버리지를 추론. **§2 D와 같은 「우연히 통과하는」 약한 assertion이 더 있을 가능성을 배제할 수 없다.**
- **B3/B6의 `auto_operator` hourly 레인 전체 소비 경로**는 격리 테스트로만 확인, 라이브 미관측.
- **판사 프롬프트가 실제 LLM 응답을 어떻게 이끌어내는지** 실증하지 않았다 — §5는 프롬프트 텍스트의 정적 평가.
- 기록물(체인·북극성·트랙 문서) diff는 코드가 아니라 검토 생략.
