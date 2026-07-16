# PLAN — 키워드 시간별 축적 + 이중 루프 데이터 계층 (D-NAO-46 ②착수)

> 작성: 2026-07-17 (Fable 설계). 실측 근거: `docs/references/32_naver_sa_hh24_breakdown_recon_20260717.md`
> 트랙: `docs/tracks/active/track_naver-ad-optimization.md` D-NAO-46
> 구현: Sonnet TDD, codex 게이트(원칙19). 상태는 §7 체크리스트.

## §0 방향 고정 (이 스프린트가 무엇이고 무엇이 아닌가)

**이것이다**: D-NAO-46 이중 루프의 **데이터 계층(관찰 전용 수집)** — ①키워드/쇼핑그룹 grain 시간대별 imp/clk/cost/avgRnk 영구 축적(일 1회 hh24 스윕) ②캠페인 grain 시간별 스냅샷에 avgRnk 추가 ③qi(품질지수) 무상 수집. 전부 읽기 GET — 입찰·예산·상태에 손대지 않는다.

**이것이 아니다**: 시간당 실입찰 루프 개방(개방 순서 게이트 불변: 840 카나리 인과 검증 → 성적표 수주 신뢰 → 시간당 개방, D-NAO-46). trigger_watch 순위 이탈 트리거 활성화(축적 데이터로 베이스라인 잡은 뒤 별도 스프린트, §6). 파워링크 키워드 조합 변경(일~주 단위, 스코프 밖).

**왜 지금인가**: hh24 상세 데이터는 **네이버가 7일만 보존**(ref 32 §4) — 배포가 늦은 만큼 시간대별 순위 이력이 영구 소실된다. 이 데이터가 시간당 밴드 관제("순위 2.5~4 유지")의 유일한 원료다.

## §1 이중 루프 아키텍처 안에서의 위치 (D-NAO-46 설계 확정본)

```
[데이터 계층 — 이번 스프린트]
  naver_hourly_snapshot   캠페인 grain, 매시 :05, 당일 누적 (+avgRnk 추가)   ← 빠른 루프 원료
  naver_keyword_hourly    키워드/쇼핑그룹 grain, 일 1회 D-1 hh24 스윕, 영구    ← 학습·밴드 베이스라인 원료
  naver_entity.qi_grade   품질지수 1~7, entity_sync 일일 열거에 무상 편승      ← 품질 추세 원료

[빠른 루프(시간) — 순위·CPC·페이싱만, ROAS 금지(전환 간접 65~70%·~1일 정착)]
  현행: trigger_watch(:07, 페이싱+CPC 급등) + flight_loop(:15/2h, dry_run)
  다음 스프린트: 순위 이탈 트리거(캠페인 avgRnk 축적 후) → (게이트 통과 후) 핫셋 시간당 입찰 미세조정

[느린 루프(일) — 경제성]
  현행: 08:00 proposal_pipeline(밴드 선택·한계ROAS≥BEP) + 08:30 retro_scoring(성적표)
  naver_keyword_hourly가 유닛별 시간대 반응곡선·밴드 체류 이력을 공급
```

## §2 실측 근거 요약 (ref 32 — 설계를 결정한 사실들)

1. `/stats`는 id 단수만(복수 400) — 유닛당 1콜. 활성 스코프 1,736유닛(imp>0 키워드 1,452+쇼핑그룹 284).
2. `breakdown=hh24`+`timeIncrement=allDays`+단일일 `timeRange` = 1콜로 그 날의 시간대별 imp/clk/cost/avgRnk 곡선(실적 있는 시간대만, name="00시~01시" 라벨 파싱). **일 1,736콜·~6분 = 타당.**
3. hh24 상세는 **최근 7일만** — 캐치업은 D-6까지, 그 밖은 영구 소실.
4. breakdown은 조건 안 맞으면 **무언 무시**(200+집계 1행) — breakdowns 부재 시 실패로 처리해야 함.
5. avgRnk=0은 무의미(순위는 1부터) → NULL 저장.
6. qi: `/ncc/keywords` 응답 `nccQi.qiGrade` — entity_sync가 이미 매일 전 WEB_SITE 키워드 열거 → 추가 콜 0.

## §3 스키마 (Phase H0)

### 신규 테이블 `naver_keyword_hourly` (마이그레이션 1건)

grain: (ad_date, entity_id, hour). WEB_SITE=키워드(nkw-…), SHOPPING/BRAND_SEARCH=애드그룹(grp-…, ad_daily의 keyword_id='' sentinel 규약과 동일 축).

| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | Integer PK | |
| ad_date | Date, index | 대상 날짜(D-1 스윕) |
| hour | Integer | 0~23 (name 라벨 파싱) |
| entity_type | String(10) | keyword / adgroup |
| entity_id | String(50), index | nkw-… / grp-… |
| adgroup_id | String(50) default "" | keyword 행의 소속 그룹(ad_daily에서) |
| campaign_id | String(50), index | |
| campaign_type | String(20) | WEB_SITE/SHOPPING/BRAND_SEARCH |
| imp / clk / cost | Integer | 시간대 구간값(누적 아님 — hh24 breakdown 원본) |
| avg_rank | Numeric(6,2) nullable | avgRnk>0만, 0이면 NULL |
| synced_at | DateTime server_default now | ⚠️UTC(sqlite-server-default-now-is-utc) — 시간계산엔 미사용 |

UniqueConstraint(ad_date, entity_id, hour). 보존 365일 롤링(hourly_snapshot과 동일 상수 패턴).
용량: ~1,736유닛×실적시간대(평균 ~8) ≈ 1.4만행/일 ≈ 500만행/년 — SQLite 수용 범위.

### 기존 테이블 변경 (같은 마이그레이션)

- `naver_hourly_snapshot` + `avg_rank Numeric(6,2) nullable` — 캠페인 grain 당일 누적 순위.
- `naver_entity` + `qi_grade Integer nullable` — 품질지수 1~7.

## §4 SA/Harness 설계 (원칙18)

```
keyword_hourly_accrual (Harness, 신규 파일 keyword_hourly_sweep.py)
 ├─ SA: build_sweep_targets(db, sweep_date)      # naver_ad_daily에서 그 날 imp>0 유닛 도출(순수)
 ├─ SA: fetch_entity_hh24(entity_id, stat_date)  # fetcher 신규 — 1콜 hh24 곡선(파싱 포함)
 └─ 쓰기: sweep_keyword_hourly(db, sweep_date=D-1)  # 교체 upsert + 캐치업 + 365d 롤링
```

### fetcher 신규 `fetch_entity_hh24(entity_id, stat_date)` (naver_sa_ad_fetcher.py)

- GET `/stats` params: `id`(단수), `fields=["impCnt","clkCnt","salesAmt","avgRnk"]`, `timeRange={"since":d,"until":d}`(단일일), `timeIncrement=allDays`, `breakdown=hh24`.
- 반환 `[{hour, imp, clk, cost, avg_rank}, …]`. data 비면 `[]`(무실적 정상). **data는 있는데 breakdowns 키 부재/빈 배열이면 예외**(무언 무시 방어, ref 32 §4-④ — 단 imp>0인데 breakdowns만 없을 때).
- name 파싱: `^(\d{1,2})시` 정규식("0시~1시"/"00시~01시" 모두 허용), 실패 시 그 엔트리 skip+warn.
- avgRnk<=0 → avg_rank=None. 자격증명 없으면 기존 fetcher 규약대로 빈 반환.

### `sweep_keyword_hourly(db, *, sweep_date=None, fetch=None)` (쓰기 Harness)

1. sweep_date 기본 = kst_today()-1일.
2. **타깃 도출**: `naver_ad_daily`에서 `ad_date=sweep_date AND imp>0` → WEB_SITE는 keyword_id(nkw-…, entity_type='keyword'), SHOPPING/BRAND_SEARCH는 keyword_id='' sentinel 행의 adgroup_id(entity_type='adgroup'). ⚠️`adgroup_id='__backfill__'` sentinel 행 제외(campaign_backfill.BACKFILL_SENTINEL_ADGROUP — 2배 함정 규약).
3. 유닛별 fetch_entity_hh24 → (ad_date, entity_id) 기존 행 delete 후 insert(교체 멱등, hourly_snapshot 패턴). 유닛 단위 실패는 log+skip(카운트).
4. **캐치업**: sweep_date 외에 [D-6, D-2] 범위에서 "ad_daily imp>0 유닛인데 naver_keyword_hourly에 그 (ad_date, entity_id) 행이 0"인 유닛만 추가 스윕(유닛 단위 완전성 — 날짜 단위 아님). 전체 콜 상한 3,500/실행(가드, 초과분은 다음날).
5. 365일 롤링 삭제. 반환 `{date, targets, calls, rows, failed, catchup_calls}`.
6. fetch 파라미터는 테스트 주입용(원칙18-8).

### entity_sync qi 확장

- `get_keywords()`에 `"qi_grade": (k.get("nccQi") or {}).get("qiGrade")` 추가.
- collect_entities keyword 행에 qi_grade 포함, sync_entities upsert에 반영.
- **qi 변화 이력**: 기존 행 qi_grade와 다르고 둘 다 non-None이면 `NaverChangeLog(action='external_qi_change', before/after_value=등급, dry_run=True)` 기록 — `_log_external_status_change` 패턴 준용(외부 관찰 기록).

### 크론 (scheduler_service.py)

- `sweep_naver_keyword_hourly` **매일 09:10 KST**(08:00 일별수집이 ad_daily D-1을 채운 뒤). 표준 cron catch-up 목록에 포함(retro와 동일 취급 — 미발동 시 따라잡기, 스윕 자체의 7일 캐치업과 이중 안전).
- `snapshot_naver_ad_hourly`(:05 기존)는 코드만 변경(avgRnk 필드) — 크론 변경 없음.

## §5 경계·금지선

- 쓰기 API 0 (전부 GET). 입찰/예산/상태 불변. D-NAO-1 이익하한과 무관한 순수 관찰.
- naver_ad_daily 읽기 시 sentinel 규약(`__backfill__` 제외, 쇼핑 keyword_id='') 필수.
- `fetch_campaign_stats` `_STATS_FIELDS`에 avgRnk 추가 시 **기존 반환 키 불변**(avg_rank 키 추가만) — 기존 소비자(hourly_snapshot 외) 회귀 금지.
- 시간 계산은 kst_now()/kst_today()만(sqlite-server-default-now-is-utc).
- 스윕 실패가 크론 체인을 죽이지 않게 자체 예외 격리(기존 job 래퍼 패턴).

## §6 다음 스프린트 후보 (이번 스코프 밖, 기록만)

- trigger_watch 순위 이탈 트리거: naver_hourly_snapshot.avg_rank 2~3주 축적 후 캠페인별 베이스라인으로 활성화(trigger_watch.py 헤더의 보류 항목 해소).
- 핫셋(clk>0 ~226) 시간당 hh24 intraday 관제 + 시간당 입찰 미세조정 — **개방 게이트 통과 후**.
- 쇼핑 소재 qi(`/ncc/ads` nccQi) 수집, keywordstool 병행 확장.
- 레이트리밋 절대치 실측(스윕 운영 데이터로 자연 확보).

## §7 체크리스트 (구현 진행 기록)

- [x] H0: 마이그레이션(naver_keyword_hourly + avg_rank + qi_grade) + 모델
- [x] H0: fetch_entity_hh24 + _STATS_FIELDS avgRnk + get_keywords qi (TDD)
- [x] H1: sweep_keyword_hourly(타깃 도출·교체 upsert·캐치업·롤링) (TDD)
- [x] H1: hourly_snapshot avg_rank 저장 + entity_sync qi_grade+변화로그 (TDD)
- [x] H1: 크론 등록(09:10)+catch-up 목록
- [x] codex review PASS (2026-07-17 4R: 1R[P2] qi None 덮어쓰기 → 2R[P2]×2 빈 fetch 이력보존·grain을 keyword_id로 → 3R[P2] 캐치업 sweep_date 중복 제외 → 4R clean. 전부 RED 재현 후 수정)
- [ ] 라이브 합격(§8)
- [ ] PR + 트랙/progress 갱신

## §8 라이브 합격 시나리오 (원칙22 — 코드 존재·테스트 통과 ≠ 합격)

1. prod 마이그레이션 후 수동 1회: `sweep_keyword_hourly(sweep_date=직전일)` 완주 — targets≈수백~1,700, failed 소수, rows>0.
2. 정합 스팟체크: 임의 키워드 3개의 `SUM(imp) GROUP BY entity_id` == naver_ad_daily 같은 날 imp (hh24 구간값 합 = 일 합계).
3. avg_rank 채워짐(NULL 아님 비율 확인), 쇼핑 그룹 행 존재(entity_type='adgroup').
4. 다음 시각 :05 스냅샷에 naver_hourly_snapshot.avg_rank 값 유입.
5. 다음 entity_sync 실행 후 naver_entity.qi_grade 채워짐.
6. 09:10 크론 자연 발화(다음날 확인 — 09:30 예약 루틴이 겸사 확인 가능).
