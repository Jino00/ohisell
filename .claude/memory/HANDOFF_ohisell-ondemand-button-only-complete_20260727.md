# 세션 인수인계: 쿠팡 브라우저 수집 버튼-only 전환 완결 + RG 수수료율 정정

> 저장일시: 2026-07-27 13:30 KST · repo 루트(main, 워크트리 아님)
> 앞 HANDOFF: PR #68(chrome supervisor 도입) 이후 후속 완결 세션.

## 1. 이번 세션 완료 목록

- ✅ **RG 수수료율 정정 (10.8% → 7.8%)**: WING1 06-15~21 정산 실측(sale_fee VAT포함 303,449 / 매출 3,534,160 = 8.586% = 7.8%×1.1 정확 일치)으로 확정. `seed.py` + prod DB 반영(백업 `/tmp/ohisell.db.bak-rgrate-20260727-004416`). PR #103에 포함.
- ✅ **쿠팡 브라우저 수집 4종(rocket/wing/ohitech) 버튼-only 전환 완결** — PR #68이 남긴 chrome supervisor(KeepAlive 상주) 모델을 폐기:
  - **범인 확정**: "창 닫아도 30초 뒤 부활" 현상 = supervisor의 KeepAlive 재기동. per-fetch 수명(버튼 클릭 → 크롬 기동 → 수집 → 종료)으로 구조 전환.
  - **구현**: `_chrome_argv`/`_launch_chrome`/`_owned_chrome`/`_ChromeOwner` 신설. 소유권 규칙 = "내가 띄운 크롬만 닫는다·adopt(기존 세션 재사용)는 유지·로그인 필요할 때만 창 유지". wing RG 새벽 07시 자동예약(마지막 자동 창 트리거) 제거. supervisor plist 3개 삭제.
  - **codex 교차검증 6라운드**: 수용 12 / 기각 3 / 보류 1. 핵심 진화 — 크롬 소유 판정이 문자열 매칭 → PID 동일성 → **시간순서 판정**(프로세스 시작시각 < lock 생성시각; PID 재사용 시 순서 역전 케이스 대응)로 경화. 테스트 29 → 92개.
  - **PR #103 병합**(`5048a63`). prod 배포(safe_deploy CAS 통과): `collection_status.py`·`coupang_ops.py`·`scheduler_health.py`·`seed.py` + 프론트(신선도 배너).
  - **prod 청소**: `request_ad_cost_refresh` 죽은 행 삭제, `sync_coupang_ad_cost`(00:10, Akamai 403이라 무의미했던 크론) 비활성화.
  - **Mac 로컬**: supervisor plist 3개째 완전 삭제(재부팅 부활 차단 — `launchctl bootout`만으로는 plist가 남아 재부팅 시 부활), poll 데몬 4개 재가동(disabled 상태였던 것 enable 후 bootstrap).
- ✅ **라이브 검증 전 스트림 합격**(Jino 실제 버튼 클릭 기준):
  - ofix 판매분석: 창 13초·14일 push 완료
  - RG 정산: 35행 수집
  - ofix 광고비: SSO 자동재발급 성공·29일 백필
  - ohitech 광고: 로그인 1회 필요·29일 백필 1,457만원
  - 로켓: 발주 486·정산 90·상세 80건
  - 13:31 collection-status 4스트림 전부 fresh 확인. 크롬 부활 0건 확인(재부팅 후에도 재발 없음).

## 2. 확정된 결정사항

- RG1/RG2 commission_rate = **7.8%**(10.8% 아님) — WING1 실측 근거.
- 쿠팡 4종 브라우저 수집은 **버튼 클릭 시에만 크롬 기동** — 상시 supervisor 모델 폐기(재발 방지: plist 완전 삭제).
- Akamai 403으로 무의미했던 자동 refresh 크론(`sync_coupang_ad_cost` 00:10)은 비활성화 상태 유지.

## 3. 알려진 이슈 / 백로그

- **WING2(오하이테크) RG 정산 06-07부터 누락** — wing2 세션이 6/20부터 죽어있음(chip 발행됨).
- **wing 판매분석 3P GMV=0 모순**(chip 발행됨).
- **RG/vendor-summary claim이 성공 전 소비**(재시도 없음) — lease 방식이나 실패 시 롤백 엔드포인트가 필요, 백엔드 계약 변경 사안(codex와 보류 합의 — 이번 스코프 밖).
- `chrome-supervise` no-op 스텁 제거 — 구 plist가 완전히 사라졌음을 재확인한 뒤 진행.
- 세션 쿠키 소실로 재로그인 빈도가 늘면 → 프로필 `Preferences` `restore_on_startup=1` 검토.

## 4. 다음에 할 작업

- [ ] WING2 RG 정산 누락(6/20~) 원인 조사·수리.
- [ ] wing 판매분석 3P GMV=0 조사.
- [ ] RG/vendor-summary claim 실패 시 재시도 계약 설계(백엔드 변경 필요).
- [ ] chrome-supervise 스텁 완전 제거(plist 소멸 재확인 후).

## 5. 새 세션 시작 프롬프트

`.claude/memory/HANDOFF_ohisell-ondemand-button-only-complete_20260727.md` 읽고 이어서. 핵심 = RG 수수료 7.8% 정정 + 쿠팡 브라우저 수집 4종 버튼-only 전환(supervisor 폐기) 완결·PR #103 병합·prod 배포·라이브 4스트림 전부 fresh 확인. 남은 것 = WING2 RG 누락(6/20~)·wing 3P GMV=0 모순·claim 재시도 계약 설계.
