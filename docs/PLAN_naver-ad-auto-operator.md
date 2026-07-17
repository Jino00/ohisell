# PLAN — auto_operator: 자동 운영 서버 이관 + 시간당 밴드 레인 (D-NAO-49)

> 작성: 2026-07-17 (Fable 설계, Jino 결정 D-NAO-49). 트랙: `docs/tracks/active/track_naver-ad-optimization.md`
> 구현: Sonnet TDD, codex 게이트(원칙19). 상태 §7.

## §0 방향 고정

**이것이다**: ①D-NAO-48 4조건 심사·집행을 서버 SA로 코드화(일 레인, 08:50) ②시간당 밴드 관제 실입찰(시간당 레인, 매시 :20) — **둘 다 `auto_operate=True` 캠페인(현재 04 하나)만**. 로컬 08:55 루틴은 보고·감사 전용으로 강등.

**이것이 아니다**: 예산 변경(불가침, D-NAO-42 Jino 게이트), 03(MOP)·다른 캠페인 개입, 시간당 ROAS 판단(물리적 부재 — 전환 간접 65~70%·~1일 정착), 파워링크 키워드 조합 변경, delegation_gate(Ava 레인) 대체·수정 — auto_operator는 **별도 레인**이며 기존 레인은 그대로 둔다.

**게이트 개정 근거**: D-NAO-46 원 게이트(카나리 인과→성적표 신뢰→시간당)를 Jino가 04 한정으로 당김(D-NAO-49, 리스크 고지·수용). 시간당 레인의 안전은 게이트 대신 **가드레일 전량+핫셋 표본 게이트+04 한정**으로 확보.

## §1 아키텍처 (원칙18)

```
naver_auto_operator (Harness, 신규 backend/app/services/naver_ad/auto_operator.py)
 ├─ 일 레인 run_daily_lane(db)    # 08:50 크론 — 그날 pending 실행형 제안 4조건 심사→승인→실행
 ├─ 시간당 레인 run_hourly_lane(db) # 매시 :20 크론 — 핫셋 intraday 밴드 판정→스텝 제안 생성→즉시 심사→실행
 └─ 재사용(SA간 직접 호출 아님 — harness가 조합):
     guardrail_gate(±15%·쿨다운5h·일일상한·스톱로스·BEP)  ← 최종 게이트, 수정 없음
     naver_execution_harness.execute류(실행+재조회 검증+change_log)
     fetch_entity_hh24(datePreset=today 변형)  ← intraday 곡선
     retro-scorecard 집계(bleeding 판정)
```

승인 표기: `approval_source='auto_operator'`(일) / `'auto_operator_hourly'`(시간당) — delegation('delegation')·수동과 구분, 소급채점이 레인별 성적 분리 가능.

## §2 스키마 (마이그레이션 1건)

- `naver_campaign_settings` + `auto_operate Boolean NOT NULL default False` — 자동 운영 대상 스위치. 배포 시 04(cmp-a001-02-000000008514959)만 True 시드. **킬스위치 = 이 플래그 OFF**(Jino "04 자동운영 중지" → 로컬 루틴/콘솔이 즉시 UPDATE).
- 신규 테이블 없음. 제안 유형 신설 없음 — 시간당 레인도 기존 `bid_up`/`bid_down` 재사용(rationale 접두 `[시간당밴드]`, approval_source로 구분) → 기존 실행·채점 경로가 그대로 작동.

## §3 일 레인 규칙 (D-NAO-48 정책의 코드화 — 로컬 루틴과 동일해야 함)

대상: auto_operate 캠페인의 **당일 생성 pending 실행형**(informational=0) bid_up/bid_down/pause.

- **bid_up 승인 4조건(전부 충족)**: ①스텝 클램프 정상(target_bid가 현재가 대비 +15% 이내 — 라이브 현재가 재조회 기준) ②rationale 창 클릭 ≥10 ③그룹 보정ROAS(정착창 D-8~D-2) ≥ target_roas(캠페인 override 우선, 없으면 계정) ④최신 소급채점에서 해당 그룹 bleeding 아님. 하나라도 미충족 → hold(사유 기록).
- **bid_down**: 무조건 승인·실행(안전 방향, ref31 61~88%).
- **pause**: 승인 전 change_log에서 그 타깃 최근 외부/수동 정지 이력 없음 확인(D-NAO-40) 후 실행.
- 승인 후 실행은 execution_harness 경유(가드레일 재판정 포함 — 이중 게이트 의도적).
- 반환: `{reviewed, approved, executed, held:[{id,reason}], failed}`.

## §4 시간당 레인 규칙 (순위·CPC·페이싱만 — ROAS 금지)

1. **핫셋 선정**: auto_operate 캠페인의 그룹 중 ①최근 7 정착일 클릭 ≥10(소표본 게이트 상속) ②당일 imp>0. 예상 규모 04 기준 1~3그룹(콜 그룹당 1).
2. **intraday 곡선**: 그룹 id로 hh24+datePreset=today(ref 32 §4 — 당일 경과 시간대 곡선). 최근 3개 완료 시간대의 imp-가중 avg_rank·CPC 산출(imp 합 < 30이면 그 시간대 묶음은 판단 보류 — 시간당 소표본 방어).
3. **판정**(우선순위 순, 하나만):
   - **DOWN**: 가중 avg_rank < 2.5(과열 밴드 — 마진 압축 실측 구간) **또는** 당일 그룹 CPC > 최근 7일 그룹 CPC × 1.5(trigger_cpc_spike 임계 재사용) → −1스텝.
   - **UP**: 가중 avg_rank > 4.0(밴드 하단 이탈) **그리고** 그룹 보정ROAS(정착창) ≥ target **그리고** 당일 소진 페이싱 저속(시간대 기대 누적 대비 — hourly_pattern/완결도 곡선 소비 가능하면 사용, 없으면 선형 기대) → +1스텝.
   - 그 외 hold.
4. **스텝**: 현재가 ×(1±0.15) 클램프 + 10원 반올림(생성기 스텝 클램프와 동일 규약, _MAX_CHANGE_PCT 단일소스 import).
5. **실행 경로**: proposal 생성(bid_up/down, rationale `[시간당밴드] rank=…, cpc=…, pacing=…`) → 즉시 4조건 아닌 **시간당 자체 조건**으로 승인 → execution_harness 실행(guardrail_gate가 쿨다운 5h·일일상한·BEP·스톱로스 최종 차단 — 쿨다운이 그룹당 하루 최대 ~4회 자연 제한).
6. **정지 조건(레인 자체 fail-closed)**: 당일 캠페인 소진 > 직전 7일 일평균 ×3 → 그 날 시간당 레인 전체 hold+경고 로그. intraday 조회 실패 → 해당 그룹 skip(추정 금지).

## §5 크론·강등·경계

- 크론: `run_naver_auto_operator_daily` "50 8 * * *"(catch-up 목록 포함 — 미발화 시 따라잡기 안전) / `run_naver_auto_operator_hourly` "20 * * * *"(catch-up 제외 — 시간성 소멸, 다음 시각이 곧 재기회). 기존 job 래퍼 패턴(자체 세션·예외 격리).
- **로컬 08:55 루틴 강등**: 집행 조항 삭제 → 서버 change_log·held 목록·17E 카나리·03vs04 A/B 리드아웃 **보고 전용**(+킬스위치 실행 대리).
- 경계: 쓰기는 execution_harness 경유만(초크포인트 유지), 03 불가침, 예산 불가침, sentinel 규약, kst_now()만, 시간당 레인에서 ROAS·BEP 신규 판단 금지(가드레일의 기존 BEP 차단만).

## §6 다음 후보(스코프 밖)

- 시간당 레인 성적 분리 리포트(approval_source별 소급채점 rollup) — 데이터 쌓인 뒤.
- 핫셋 확장(다른 캠페인) = auto_operate 플래그만 켜면 됨 — 단 켜는 건 Jino.
- delegation_gate(Ava 레인)와의 통합 정리 — Ava 수리 후.

## §7 체크리스트

- [x] A0: 마이그레이션(auto_operate) + 모델
- [x] A1: 일 레인(4조건 심사·집행) TDD
- [ ] A2: 시간당 레인(핫셋·intraday·판정·실행) TDD
- [ ] A3: 크론 2개+catch-up 배선
- [ ] codex review PASS
- [ ] 라이브 합격(§8)
- [ ] 로컬 루틴 강등 + PR + 문서

## §8 라이브 합격 시나리오 (원칙22)

1. 배포 후 04 auto_operate=True 시드 확인, 크론 2개 등록 확인.
2. 시간당 레인 수동 1회: 핫셋 선정·intraday 조회·판정 로그 완주(hold여도 합격 — 판정 근거 로그 필수). 실행 발생 시 change_log(dry_run=0)+네이버 라이브 재조회 일치.
3. 다음 정시 :20 크론 자연 발화 확인.
4. 익일 08:50 일 레인 자연 발화 — 그날 pending 심사 결과가 로컬 루틴 수동 심사와 동일 판정인지 대조(강등 전 마지막 이중 확인).
5. 가드레일 차단 로그 정상(쿨다운 중 재시도가 실제 차단되는지 1회 관찰).
