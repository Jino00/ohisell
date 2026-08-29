# Track: PAO UI/UX

<!-- TRACK-CONTRACT v1 -->
목표: "이 작업은 PAO의 UI/UX만 다루는 트랙으로 독립운영할꺼야" — 첫 작업: "전체/PAO가 돌리는광고/PAO가 돌리지 않는광고/ 이렇게 나눠줄 수 있어?" · 방식은 「방법 B — 날짜별 실제 담당」 (Jino, 2026-08-29)
안함: 광고 최적화 엔진 자체(제안·실집행·입찰·제외·스코프 변경) **→ 소관: docs/tracks/active/track_naver-ad-optimization.md**
안함: 수집·스냅샷·이력 적재 스키마 변경(오늘 adgroup 그레인, 2026-07-11 이전 이력 복원 포함) **→ 소관: docs/tracks/active/track_naver-ad-optimization.md**
안함: 밴드별 총이익 환산·목적함수 정의 **→ 소관: docs/tracks/active/track_naver-ad-optimization.md**
안함: pao_scope_roster.py:351 진리표 미적용 결함 수리 **→ 소관: docs/tracks/active/track_naver-ad-optimization.md**
합격:
- [ ] 3밴드+전환일·모름 보조칸 카드가 뜨고 항등식 「전체=관할+비관할+전환일+모름」이 화면 숫자로 성립
- [ ] 30일(07-30~08-28) 조회: PAO 관할 0원 · 전환일 472,580원/2일(07-30 402,644원·5캠페인 + 08-24 69,936원)
- [ ] 비-0 검증: 90일 조회에서 2026-07-29가 PAO 관할 522,960원으로 잡힌다(캠페인 통째 관할 실사례). 창 PAO 총계 610,964원/5일
- [ ] 반증: PAO 밴드에 2,170,514원 또는 7,607,618원이 나오면 미달
- [ ] 90일 조회: 2026-07-11 이전이 「모름(이력 없음)」 25,153,015원(43.8%)으로 분리 표기, 해석불가 건수 노출
- [ ] 오늘 카드 「밴드 미제공 — 확정 전」 라벨, 오늘치 밴드 혼입 0
- [ ] 캠페인 목록 밴드 필터 동작(판정 기준일 명시)
- [ ] 판정·재구성은 단일 모듈, 하니스 내 optimizer/auto_operate 조건식 grep 0건
- [ ] 테스트: 진리표 4행+전부-disabled · ∧반례 · 3포맷 파싱 · 해석불가 집계 · 항등식
상태: 활성 (계약 승인 2026-08-29 21:35 — Jino "승인, 진행해")
확인: 2026-08-29 21:36 [eef672ce] — 트랙 신설·계약 승인. 밴드 판정 단일 소스 착수.
<!-- /TRACK-CONTRACT -->

## §1 목표

PAO가 «무엇을 맡고 있(었)고 그 성과가 얼마인지»를 Jino가 화면에서 읽게 하는 트랙이다.
엔진의 판단을 만들지 않는다 — 엔진의 사실을 **날짜별 당시 기준으로** 보이게 한다.

관할 정의의 정본은 **세 축의 ∧**이다: `optimizer=='ours'` ∧ `auto_operate` ∧ D-NAO-244 그룹 진리표.
이력 원천은 `naver_change_log`(`changed_at` = KST).

첫 계약: `docs/contracts/CONTRACT_pao_performance_ownership_split.md` (성과분리 목표).

## §2 이 트랙이 아닌 것

엔진 로직·스키마·수집·이력 적재는 전부 `track_naver-ad-optimization.md` 소관이다. UI 작업 중 엔진
결함을 발견하면 **고치지 않고** 앵커 `## 이월`에 소관 트랙을 지목해 적는다.

합격 체크박스는 승인된 계약분만 담는다 — 미래 UI 아이디어로 M을 부풀리지 않는다(자리표시자 금지,
전역 §6). 이 트랙이 원리적으로 종결 가능해야 하기 때문이다.

## §3 진행 기록

- 2026-08-29 [eef672ce] 트랙 신설. Jino 지시로 PAO UI/UX를 독립 운영. 첫 계약 초안 작성 —
  승인 대기. 착수 조사에서 확정한 것: 성과 화면에 소유권 필터가 없음(`metrics_aggregator.py:79-83`)
  · 「PAO가 돌린다」는 세 축의 ∧ · 관할 이력이 `naver_change_log`에 존재(2026-07-11~, 7,705행 중
  optimizer_change 18 · auto_operate_change 3 · adgroup_scope_change 1) · 현재 관할은 오늘
  만들어졌다(스코프 행 08-29 00:25, optimizer none→ours 08-29 12:53).
