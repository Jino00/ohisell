# 세션 인수인계: 적응형 안전 봉투 P1 (D-NAO-172)
> 저장일시: 2026-08-10 22:0x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md`
> **계약서: `docs/PLAN_adaptive-guardrail-envelope.md`** ← P2·P3 설계가 여기 다 있다
> 같은 세션의 앞 작업: `HANDOFF_b4-second-half-lever-repair_20260810.md`(D-NAO-170·171, 배포 완료)

## 1. 한 줄
PAO의 안전 봉투를 **코드 상수 → DB로 조절 가능한 근거 있는 기준**으로. **P1(파라미터 층·현황판·신선도) 코드 완료·커밋**. ⚠️**적대 리뷰 미완주 · prod 미배포 · 라이브 미검증.**

## 2. ⚠️ 새 세션이 **가장 먼저** 할 일
1. **적대 리뷰를 다시 돌려라.** 이 세션에서 띄웠으나 **완주 못 하고 세션이 끝났다** — 미완주 리뷰는 「발견 0건」이 아니라 **INCONCLUSIVE**다(교훈 #123). PASS를 주지 말고 재실행할 것. 리뷰 프롬프트에 넣을 계약·의심 축은 §6에 적어 뒀다.
2. **번호 확인**: 이 세션이 쓴 교훈 **#216·#217·#218**이 병행 세션의 재부여로 밀렸을 수 있다. 세션 종료 시점 `next_ids.sh`가 **교훈 #228**을 가리켰다(내 브랜치 227) — 즉 origin에 #219~#227이 들어왔다. **`grep -n "^## 교훈 #21[678] " .claude/memory/LESSONS_LEARNED.md`로 내 것이 살아 있는지, 중복이 없는지 먼저 볼 것.**
3. 그 다음 배포·라이브 검증(§5).

## 3. 완료 (커밋·push 상태는 §8)
| 커밋 | 내용 |
|---|---|
| `3ca9ef2` | 백엔드 — `guardrail_params.py`(신규) · 게이트 4지점 배선 · 컨텍스트 주입 · `_clamp_step(…, max_pct)` · `GET/PUT /api/naver/ad/settings/guardrail-params` · `KNOWN_CHANGE_LOG_ACTIONS`에 `update_guardrail_params` |
| `f0add56` | 프론트 — 콘솔 「안전 봉투」 섹션(값·**출처 배지**·범위·근거·`rejected` 경고) + 소급채점 신선도 배너 + 저장 조립 순수 함수(`guardrailParamsSave.ts`) |
| (원장) | D-NAO-172 트랙 기재 |

검증: 백엔드 **5235 passed**(+17) · 프론트 **296 passed**(+6) · `tsc -b`·`npm run build` 통과 · **변이 8종 전건 KILLED**.

## 4. 설계 요지 (계약서 §3에 전체)
- **3층**: 코드 상수(최후 폴백) ← DB KV `naver_account_settings.guardrail_params` ← 상황 조정(P2·P3). **마이그레이션 0**.
- **fail-to-current**: KV 없음/파싱 실패/범위 밖 → 그 항목만 코드 상수. fail-closed(0)면 광고가 멈추고 fail-open(무제한)이면 돈이 샌다.
- **범위(min/max)는 배포로만 바뀐다** — DB가 자기 상한을 못 넓히는 것이 되먹임 차단의 **마지막 층**(첫 층은 「풀기는 사람 승인」).
- **되돌림 스위치**: `guardrail_params._PARAMS_FROM_DB = False` 한 줄.
- **★생성↔게이트 정합이 계약이다**: 레인이 15%를 만드는데 게이트가 10%만 허용하면 모든 제안이 「변경폭 초과」로 죽는다 — 파라미터를 조인 순간 **광고가 조용히 전면 정지**한다.
- **현황판의 존재 이유는 값이 아니라 `source`** — 「DB를 고쳤는데 코드 상수가 이기고 있는」 상태를 보이게 한다.

## 5. 남은 일
### P1 마무리
- [ ] **적대 리뷰**(§2-1) → P1 처분 · P2 트리아지
- [ ] **prod 배포** — 백엔드 3파일 + 신규 1 + `models.py` + 라우터, **마이그레이션 없음**. 프론트도 같이(`--frontend`, CAS 주의).
  `scripts/safe_deploy.sh backend/app/services/naver_ad/guardrail_params.py backend/app/services/naver_ad/guardrail_gate.py backend/app/services/naver_ad/naver_execution_harness.py backend/app/services/naver_ad/auto_operator.py backend/app/models.py backend/app/routers/naver_ad.py --restart`
- [ ] **라이브 합격기준**(정지 상태에서도 관측 가능 — 레인이 아니라 **현황판 API**로 본다):
  ① KV에 테스트 값 투입 → 현황판이 `출처=DB`로 표시 → KV 삭제 → `출처=코드상수` 복귀
  ② 현황판에 4개 값·출처·근거가 전부 표시
  ③ 소급채점 신선도 표시(현재 정상이라 stale 배너는 안 뜬다 — 인위적으로 확인하려면 `naver_retro_signal` 최신일을 임시로 뒤로 미뤄야 하는데 **권장하지 않는다**. 코드 경로는 단위 테스트로 덮여 있다)
  ④ PUT 400 메시지가 화면에 그대로 뜨는지(범위 밖 값으로)
- [ ] 교훈 기재(**#216~#218 확인 후** 다음 번호로) — 후보: 「집계 전에 원문 표본을 눈으로 보라(분류 라벨을 먼저 믿지 않는다)」 · 「같은 값이 두 곳에서 계산되면 파라미터화는 정합 장치다」

### P2·P3 (계약서 §4)
- [ ] **P2 조이기 자동화** — `loss_grade`(normal/bleeding/severe) → 일일 하향 상한 3→5회, severe는 스텝 −30%. **소급채점 stale이면 normal로 폴백**(낡은 데이터로 세게 조이지 않는다). 전제 확인됨: 소급채점 이력 **33일치** 보존이라 「3일 연속」 판정 가능.
- [ ] **P3 풀기 제안** — 근거 붙은 제안 → Jino 클릭. 트리거 수치는 착수 시 실측으로 정한다. **P3 전 필요한 실측**: 쿨다운 36건·일일상한 50건이 사후 채점에서 「막힌 방향이 옳았나」.

## 6. 적대 리뷰에 넣을 의심 축 (재실행용)
계약 8개는 계약서 §3·§7에 있다. 의심할 곳:
- `_param()`이 `context`에서 읽는데 **컨텍스트를 안 채우는 호출부**가 있나 → 그 경로만 코드 상수로 돌면 생성↔게이트가 갈라진다.
- `proposal_writer`·`exploration`·`rank_servo`·`account_diagnosis`가 **±15%를 자기들끼리 재현**하나 → 파라미터를 바꿔도 그쪽만 옛 값으로 남는다.
- `Decimal`↔`float` 왕복(`describe`가 float로 내보내고 PUT이 되받는다).
- `_coerce`에 리스트·dict·None이 들어올 때 크래시 경로.
- PUT **전체 치환**의 동시성(두 탭).
- 레인은 회차 시작 1회 읽고 harness는 쓰기 직전마다 읽는다 — **갈라지는 창**이 있나.
- `guardrail_params_retro_freshness`가 라우터 파일에 데코레이터 없이 정의됐다 — FastAPI 등록에 문제 없나.
- PUT의 change_log가 **B-1 가드**(D-NAO-169, `changed_at`↔`executed_at` 30분)를 통과하나.
- 순환 import(`guardrail_params` → `guardrail_gate`, harness → 둘 다).
- 만든 쪽이 KILLED로 보고한 8종 **밖**의 변이를 만들 것: 범위검사 제거 / 파싱실패 시 default 반환 / 부분실패→전체롤백 / 되돌림스위치 무력화 / bool 허용 / rejected 항상 False / 게이트가 파라미터 무시 / `_clamp_step`이 인자 무시.

## 7. ★이 세션이 배운 것 (반복하지 말 것)
- **집계 전에 원문 표본을 눈으로 보라.** 봉투 실측 표를 만들며 **두 번** 틀렸다 — ①정규식이 문자열 `15`를 아무 데나 매치시켜 「±15% 클램프 119건」을 만들어냈다(실제는 경제성 상한) ②`event_type` 필터를 빠뜨려 서술형 일기까지 긁었다. **분류 라벨을 먼저 믿지 않는다.**
- **`±15%`는 가드가 아니라 스텝 정책이다** — 차단 기록 0건인 이유가 그것이고, 이 구분 때문에 "푼다"가 상한과 속도에서 서로 다른 뜻이 된다.
- **변이가 살아남은 게 정보였다**: 「파싱 실패 시 default 반환」은 `get_params`에선 동치인데 `describe`에서 깨진 값이 `source="db"`로 뜬다 — **이 화면이 막으려던 착시 그 자체**였다.
- **설명이 길면 Jino를 잃는다.** 표와 전문용어를 쌓다가 *"뭔소리야"*를 들었다. 선택지는 둘로 줄이고, 추천을 먼저 말하고, 이유는 세 줄로.

## 8. 상태·환경
- **⚠️ push 여부 확인 필요** — 세션 종료 시점에 origin이 앞서 있었다(`git fetch && git status -sb`). 병합 후 **테스트를 다시 돌리고** push할 것.
- prod: `sellc.ohitech.co.kr` · pm2 **`ohisell-backend-8001`**(오늘 8011→8001로 바뀜, 항상 `pm2 list` 확인)
- ★PAO는 **완전 정지**(`optimizer='none'` + `auto_operate=0`) — 시간당 레인이 캠페인 루프에 **진입조차 안 한다**. 그래서 봉투 코드는 지금 라이브에서 안 돌고, 합격기준을 레인 로그로 잡을 수 없다.
- 테스트: `cd backend && python3 -m pytest -q` (~2분 45초) · `cd frontend && npx vitest run`
- 병행 세션이 활발하다 — 배포 전 `git fetch` + `deploy-manifest.jsonl` 확인.

## 9. 이월 (앵커 `.claude/anchors/` 참조)
- `update_keyword_bid`가 `useGroupBidAmt: False`를 **항상 전송**(`naver_sa_writer.py:316`·`:348`) — 소재에서 금지선인 「강제 전환」의 키워드판일 수 있는데 **코드에 검토 흔적 없음**.
- `next_ids.sh`는 트랙 원장만 스캔 → 원장에 안 적힌 번호를 재발급한다.
- `[LEVER_MISMATCH]` 상시 표면 없음(주간 감사 쿼리로만) · `_derive` 동률 max tie-break 비결정
- **B-2 집계 정본 헬퍼**(교훈 #195 2배 오집계) · **B-3 BEP 기준선 표면화**
- 재개 첫 회차 blast radius(눌린 판정 207그룹) — 캠페인 단위 단계 개방 여부는 Jino 결정

## 10. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_adaptive-guardrail-envelope-P1_20260810.md 읽고 이어서 작업해줘
```
