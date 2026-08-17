# 세션 인수인계: D-NAO-179 배포 전 베이스라인 + 병행 세션 교차검증 (PR #303)

> 저장일시: 2026-08-17 09:4x KST
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md` (D-NAO-179 블록)
> 앞 세션 인계: `HANDOFF_expsearch-blindness-fixed_20260817.md`
> ⚠️ **이 세션은 병행 세션(`PAO 논의13`)과 같은 트랙을 동시에 탔다.** 그쪽 인계서가
> `HANDOFF_expsearch-deployed-live_20260817.md`(커밋 `93791f4f`)다. **두 문서를 같이 읽어야 전체가 보인다.**

## 1. 한 줄

인계 1순위(#303 prod 배포)를 하러 들어갔는데 **병행 세션이 64초 먼저 배포**해 있었다. 그래서 이 세션의 실제 산출은 **배포 전 베이스라인 실측**(스윕이 돌면 영영 못 재는 값)과 **사후 독립 교차검증**이 됐고, 그 과정에서 저쪽이 「설명 안 됨」으로 남긴 미해결 항목 하나를 닫고 stale 리터럴 하나를 고쳤다.

## 2. ★★새 세션이 할 일

**D-NAO-179는 배포·검증까지 끝났다 — 여기서 이어받을 잔여 작업 없다.** 다음 슬라이스는 앞 인계서 §6·§7의 항목들이다(아래 4절).

## 3. 완료 QA (§2 의무 — 판정 원문 그대로, 부분달성 포함)

**앵커 작업 = #303 prod 배포 + 편입이 원장에만 들어가고 일기엔 안 들어감을 라이브 확인.**
별도 Sonnet 판정기(읽기 전용). 앵커: `.claude/anchors/5baf0816-f82b-44ea-8d84-fa3fb1018a1f.md`

> **종합: 부분달성** (달성 ①②③⑤ / 부분달성 ④ / **미달 0**)
> - **①** 배포 전 편입 예상·일기 카운트 실측 기록 → **달성**. 원자료 mtime(07:44~07:49)이 배포 매니페스트 `07:50:34`보다 앞섬을 QA가 대조.
> - **②** `get_restricted_keywords(60531781)` 12건(KP=1 EXP=11) → **달성**. QA가 GET 전용임을 `cat`으로 확인 후 **직접 재실행**.
> - **③** 제외 총계 12→723 → **달성**(「근처」가 아니라 정확히 **723**, errors 0). QA가 526그룹 재census로 79.6초 만에 재현.
> - **④** 편입 후 일기 신규 0건 → **부분달성**. 실질 기준(편입이 일기를 만들었는가)은 충족이나, **내가 쓴 「4,391 불변」 리터럴이 stale**이다 — 무관한 정기 레인 2건(`profit_scorecard`·`probe_learning`)이 총계를 4,393으로 올렸다.
> - **⑤** 생존감시 후 기존 행 `live_state` 불변 → **달성**. 기존 44행 `alive 1 / unverifiable 43` 불변.
> - 금지선·비목표 침범 관측 **없음**(`optimizer='none'` 7행 유지 · 코드 변경 0 · 신규 `source` 값 없음).

**판정을 사후에 올리지 않았다.** ④의 지적이 옳아서 문구만 고치고 판정은 그대로 뒀다(§2 라운드 증식 차단).

## 4. 이 세션이 한 것

### A. 배포 전 베이스라인 — 이 세션의 고유 기여
스윕이 돌면 **영영 못 재는 값**이라 시점이 전부였다. 07:52 확정, 스윕 07:57:48보다 앞섬.
- 일기 **4,391** · 원장 **48**(excluded 44 · void 4) · `source` NULL 3 / console_import 45
- 스윕 범위 실측: 창 2026-07-18~, cost>0 → **385그룹**(WEB_SITE 190 / 비대상 195), 전 그룹 campaign_id 보유 → `unattributable` 0 예상
- 라이브 API 읽기 전용 투영으로 **편입 예상 105건 / 14그룹**, `regTm` 범위 밖 **0건** → `rejected` 0 예상
- **사후 대조: 투영 105 = 실제 105, 14그룹 전건 일치, 불일치 0.** rejected도 0.

### B. ★인계서의 「수백 건」은 이 스윕엔 해당 없다
711건은 **계정 전수 1,013그룹** 기준이고, `detect_new_exclusions`는 **「최근 30일 cost>0」 385그룹**만 본다. 나머지 617건은 비용 없는 그룹이라 원장 밖에 그대로다. **「전맹 해소」는 읽기 능력의 해소지 원장 편입 완료가 아니다.** (병행 세션도 독립적으로 같은 결론에 도달했다.)

### C. ★저쪽의 「설명 안 되는 두 번째 재시작」을 닫았다 — 그게 나였다
저쪽 트랙 기록이 `22:51:38Z 8001→8011`을 「미지의 주체·확인 안 됨」으로 남겼다. **내 `safe_deploy.sh ... --restart`다.** 파일 3개가 전부 CAS에서 「동일(이미 배포됨)」로 skip돼 전송이 0건이었고, `deploy_files()`는 `TO_SEND`가 비면 **매니페스트를 쓰기 전에 `return 0`** 한다(`scripts/safe_deploy.sh:251`). 그래서 backend 항목 없이 restart 항목만 남았다.
→ **「배포 기록 없는 재시작」은 미지의 침입이 아니라 CAS가 정상 작동한 흔적이다.** 동시에 두 세션이 같은 커밋을 60초 간격으로 밀었는데 뒤엣것이 안 덮은 **clobber 방지 실증**이다.

### D. ④의 축을 고쳤다 (판정은 안 고쳤다)
「일기 총계 불변」은 합격기준으로 쓸 수 없다 — 정기 레인이 총계를 올린다. 옳은 축으로 다시 세운 결과:
- 편입 창(22:57:00~22:58:59Z) 안 생성 일기 **0건**
- 편입 시각 22:57:48이 **일기 공백 구간 한가운데**: 직전 4391@22:37:41 → 직후 4392@23:40:05
- 편입 105행 중 `source != 'console_import'` **0건**
- 105행 검색어를 `target_id`로 가진 일기 **0건**
- `target_type='search_term'` 일기는 **역대 3건뿐**, 전부 편입 이전: `425 아이패드종이필름`·`4371 골프`·`4376 __배포검증_D-NAO-175__`
→ **진짜 표본 2건이 105건에 익사하지 않았다.** D-NAO-176이 경고하고 D-NAO-179가 문을 바꾼 이유가 그대로 실증됐다.

### E. 합격기준 ③을 주간 배치 안 기다리고 쟀다
`negative_kw_count`를 채우는 `bm_deep`은 **일요일 09:20 레인**인데 오늘은 월요일이다. 그래서 같은 함수(`get_restricted_keyword_count`)를 **prod 배포본으로 읽기 전용 직접 호출** — `bm_deep`과 동일 범위(`entity_type=adgroup`·`status!=deleted`·`WEB_SITE`) **526그룹**, 08-10 베이스라인 행수와 같은 분모. 결과 **723**(nonzero 67그룹, 실패 0). 베이스라인 12(nonzero 9).

## 5. ⚠️ 알아야 할 것

- **prod DB 실경로는 `/home/ubuntu/ohisell/backend/ohisell.db`**(1.8GB). 저장소 루트의 `/home/ubuntu/ohisell/ohisell.db`는 **4KB짜리 테이블 없는 죽은 파일**이다 — 루트를 물면 「no such table」로 조용히 실패한다. 이걸로 첫 조회를 한 번 날렸다.
- **prod SSH는 이번 세션엔 안 막혔다.** 앞 인계서 §5의 「auto-mode 분류기에 막힘」은 이번엔 재현 안 됨(필독 실측 판정: 유령).
- prod 스크립트 실행은 `/home/ubuntu/ohisell/backend/.venv/bin/python`. **`dotenv`를 fetcher import 전에** 로드해야 한다(교훈 #266).
- `detect_new_exclusions`는 **스케줄러 레인에 배선이 없다**(grep 0건). 크론이 아니라 `POST /api/naver/ad/search-term/executions/detect` 수동 호출로만 돈다. 병행 세션이 07:57:48에 localhost에서 쳤다.
- **CI는 여전히 결제 정지** — 빨강은 코드 신호가 아니다.
- 앞 인계서 미결 항목 **필독 실측 결과: 유효 6건 / 유령 1건**. 유효로 확인된 것 — `record_execution`의 `discovered` 죽은 인자(프로덕션 호출부 0건) · 미이스케이프 LIKE 2곳(`routers/orders.py:47-48` · `services/product_connection_map.py:117-118`) · 일기 action 표기 분열(`search_term_execution.py:38` `search_term_exclude` vs `naver_execution_harness.py:177` 등 `exclude_search_term`) · `test_vendor_item_axis` 1건 기존 실패 · #303 미배포(당시) · CI steps=0.

## 6. 남은 일 / 이월

- ~~**BM deep가 2026-08-16(일)에 안 돈 것으로 보인다** — 미조사.~~ → **✅같은 세션에서 조사 완료. 내 관측이 오독이었다(교훈 #297).** bm_deep은 **한 번도 일요일에 안 돈다** — `CronTrigger.from_crontab("20 9 * * 0")`에서 표준 crontab의 `0`(=일요일)을 APScheduler가 `day_of_week=0`(=**월요일**)로 읽는다. 실행 이력 전수가 **07-27·08-03·08-10·08-17 전부 월요일**이고 **놓친 실행 0건**이다. 오늘 09:23:58 정규 실행으로 스냅샷 `negative_kw_count` 합계가 **12 → 723**으로 자연 착지했다(합격기준 ③이 수동 측정 없이도 배치로 재현됨). 영향 크론 2개(`run_naver_bm_deep`·`sync_naver_keyword_volume`) 모두 「일요일」로 문서화돼 있고 실제로는 월요일 — 「일요일」 서술이 4개 파일 11곳. **미조치(고치면 잡이 하루 당겨진다) — Jino 판단 대기.**
- **617건은 여전히 원장 밖**이다(비용 없는 그룹). 편입 대상을 넓힐지는 별도 판단.
- 앞 인계서에서 그대로 넘어온 것: `record_execution(discovered)` 죽은 인자 정리 · API 호출 2배 라이브 부하 미관측 · 프로브 스크립트가 스크래치패드에만 있음 · wisdom 후보 27 → `hidden`(마감 **8/27**) · S8 「후보 10 처분」 신설 · **해석문이 8/13 08:35 이후 안 만들어진다**(미조사) · S6 성적표 판정 · S7 레버 개방 미정의 · 미이스케이프 LIKE 2곳 · 일기 action 표기 분열 · 품질지수 죽은 신호(`qi_grade=4` 91,172건).

## 7. Jino 대기

앞 인계서 §7 그대로 — 후보 50건 절단 여부(순손실 −1,009,853원/30일) · 콘솔 캡처(S5) 다음 그룹 = Z폴드8와이드 · Mac IP 대만 원복 · `node_modules` iCloud 밖 이전 · P4 괴리 감시 임계값 · 네이버 대행사 평가 후속 3건.

## 8. 상태·환경

- prod: `sellc.ohitech.co.kr` · pm2 **`ohisell-backend-8011`** · 백엔드 = `da3aa1bb` **배포 완료**
- main = `17fd5676` 이후 이 세션 커밋. PAO는 완전 정지(7캠페인 `optimizer='none'`)
- 원장 `naver_search_term_exclusion` = **153행**(excluded 149 · void 4), `source='console_import'` 150
- 테스트: `cd backend && python3 -m pytest -q` (기존 실패 1건: `test_vendor_item_axis::test_health_route_actually_returns_conservation`)

## 9. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_expsearch-deploy-baseline+crosscheck_20260817.md 와
.claude/memory/HANDOFF_expsearch-deployed-live_20260817.md 둘 다 읽고 이어서 작업해줘
```
