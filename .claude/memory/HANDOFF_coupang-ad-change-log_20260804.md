# 세션 인수인계: 쿠팡 광고 변경 이력 「수정 사항」 쿠팡판

> 저장일시: 2026-08-04 18:1x KST · main `ef4ea7a` 이후, prod 배포 완료 · **미푸시**
> 트랙: `docs/tracks/active/track_coupang-ad-change-log.md` (신설, D-CAC-1~5)
> 앞 작업: 로켓 1P 손익(`HANDOFF_rocket-sku-pnl+cost-source_20260804.md`) — **그 1순위는 아직 Jino 입력 대기**

## 1. 이 세션이 무엇을 했나

Jino: "Sellc에 우리가 광고 수정한 내역에 대해서 네이버 광고 수정사항 정리하는것처럼 정리할 수 있어?"

→ 쿠팡 광고(오픽스 3P + 오하이테크 1P)의 설정 변경을 sellc에서 시간순으로 보는 화면
`/coupang-ad-changes` 를 만들고 prod 배포. **원천이 둘**이다(정찰 중 두 번째를 발견).

## 2. 환경·명령

- prod 배포는 `scripts/safe_deploy.sh`만 (DB 변경 있으면 `--migrate`)
- 페처(Mac) 배포: `bash tools/install_local_runtime.sh`
- 테스트: `cd backend && python3 -m pytest tests/ -q`
- 라이브 회차 수동 실행:
  - 오하이테크 `~/.ohisell/venv/bin/python ~/.ohisell/tools/ohitech_ad_fetcher.py run`
  - 오픽스 `~/.ohisell/venv/bin/python ~/.ohisell/tools/ad_cost_browser_fetcher.py`
- ★**오픽스는 keycloak까지 만료되면 스스로 못 돌아온다** → `ad_cost_browser_fetcher.py login`
  창에서 Jino가 직접 로그인해야 한다(오하이테크는 Keychain 자동로그인이 있어 자가 복구).

## 3. 확정 결정 (D-CAC-1~5) — 상세·원문 인용은 트랙 파일

- **D-CAC-1** 대상 = 쿠팡 두 계정
- **D-CAC-2** 방식 = 자동 스냅샷 diff (정찰 선행)
- **D-CAC-3** ★On/Off·신규·삭제는 **전량**에서, 내용 diff는 **활성만**
  (Jino "On되어 있는 캠페인만 보면 되는거야"). 오하이테크 525개 중 활성 16개.
- **D-CAC-4** 원천 = `POST /marketing/tetris-api/campaigns` (두 계정 동일 엔드포인트)
- **D-CAC-5** ★**자기 크론을 두지 않는다** — xauth Akamai가 headless를 막아 창이 반드시 뜬다.
  2026-07-27에 이미 "버튼 누를 때만"으로 정리된 구조라 「광고비 갱신」 회차에 **얹는다**
  (창 뜨는 횟수 0회 증가). Jino "이거 매 시간 화면에 크롬이 뜨는거 아니야? 성가신데..."

## 4. ★★정찰에서 나온 큰 발견 3개

### ① 쿠팡이 변경 이력을 **직접 준다**
`POST /marketing/tetris-api/change-history/events-simple`
(광고센터 「변경 이력」 화면 `/marketing/change-history`가 쓰는 API)
- 응답: `[{campaignId, executionTime, executionId(UUID), changes:[{changeType, before, after, added, removed}]}]`
- **90일 소급 조회** 가능(1년은 500). `events`·`events-detail`은 404 — simple 하나뿐.
- changeType 4종: `VIID`(소재 개수) · `TROAS` · `BUDGET` · `CAMPAIGN_ONOFF`
- ★**정직하게: 이걸 모른 채 스냅샷 diff를 먼저 만들었다.** 겹치는 축은 쿠팡이 더 정확하다
  (전/후 값 + 실행 시각 + 소급). 그래서 두 원천을 합치고 **겹치면 쿠팡이 이긴다**.

### ② 키워드는 "어렵다"가 아니라 **대상이 없다**
활성 광고그룹 21개(오픽스 5 + 오하이테크 16) **전부 `keywordTargeting=AUTOMATIC` + `bidType=AUTO_BID`**.
사람이 키워드를 만지지 않는다 → 변경 이력에 키워드 유형이 0건인 게 당연. **영구 제외.**
(수동 키워드 캠페인을 새로 만들면 전제가 바뀐다.)

### ③ 소재는 **옵션ID 단위로 추적 가능**
`POST /marketing/tetris-api/{adGroupId}/ads` → `vendoritemid`(옵션ID)·`itemName`·`isActive`·
`pricingOverride`(소재별 입찰가)·`isDeleted`. `isDeleted=true`로 삭제분 별도 조회.
- 비용 실측: 오픽스 5그룹 543개 6콜 1.4초 / 오하이테크 16그룹 499개 17콜 14.6초 = **23콜 약 16초**
- ★조인율: 옵션ID 955개 중 **772건(81%)이 `coupang_ad_option_daily`**, **527건(55%)이
  `product_channel_mapping`** → 손익 축과 바로 이어진다.

## 5. ★★★함정: `hasNextPage`가 거짓말한다

같은 광고그룹(`204811906`)에서:

| 요청 | ads | pageInfo |
|---|---|---|
| `size=100` | **100** | `totalCount: 447, hasNextPage: **False**` |
| `size=500` | **447** | `totalCount: 447, hasNextPage: False` |

`hasNextPage`만 믿고 끊으면 **347개가 조용히 사라지고 로그에도 안 남는다.**
**`totalCount`가 권위값이다.** — 이 함정에 **내 정찰 스크립트가 직접 물려** 오픽스 소재를
543개가 아니라 196개로 과소 보고했었다. `ad_settings_collect._fetch_all_pages`는 교체 완료.

## 6. 구현 현황 (전부 prod 배포 완료)

| 층 | 파일 |
|---|---|
| 마이그 | `c8d1a4f97b26`(테이블 2종) · `d3f5b7a91c48`(source·external_id·detail_json) |
| 서비스 | `ad_settings_diff.py`(스냅샷 diff·A축/B축·허용목록) · `ad_change_history.py`(쿠팡 이벤트) |
| 라우터 | `coupang_ops.py` — `POST /ad-settings/ingest` · `GET /ad-changes` |
| 수집 | `tools/ad_settings_collect.py`(공용) → 페처 2종에 얹음 · 설치 목록 등록 |
| 화면 | `frontend/src/pages/CoupangAdChanges.tsx` → `/coupang-ad-changes` |
| 테스트 | **53건**(diff 22 · router 15 · history 16) |

**병합 규칙(핵심)**: `occurred_at`을 **초로 절삭**한다 — 쿠팡 `executionTime`은 초까지,
우리 `updatedAt`은 밀리초까지(01:51:21 vs 01:51:21.372)라 절삭 없이는 같은 사건이 영영 안 겹쳐
**두 줄로 뜬다**. 겹치면 쿠팡이 이긴다(스냅샷 행을 덮는다). 순서 무관(테스트로 고정).

## 7. 라이브 증거

- **11:47 1회차**(오하이테크): 전량 525·활성 16 → `changes 541`. ★그 541건이 쿠팡 `createdAt`에
  귀속돼 **오늘 화면을 어지럽히지 않았다**(오늘 0건). 감지일 귀속이었으면 525줄이 쏟아졌다.
- **11:49 2회차**: `changes 0` — 실데이터 525건에서 **오탐 0**.
- **15:31 오픽스**: 전량 16·활성 5 → `changes 21`. 화면 30일 탭 정상 렌더.
- **★18:00 백필**(오하이테크): 이벤트 108 → **113행**(한 이벤트 change 2개가 5건). 모르는 유형 0.
  90일 163건 = 소재 66 · 신규 50 · ROAS 24 · 예산 17 · 꺼짐 6.
- **★★합격기준 ① 통과**: 화면에
  `2026-08-04 10:51:21 · [매.최] 메츨 싱 · 일예산 1,500,000 → 70,000`
  (Jino가 그 시각에 실제로 바꾼 것). 아침 09:12·09:13 폴드8 예산 변경 2건도 함께.

## 8. 사고·가드 (둘 다 가드가 막았다)

1. **alembic 헤드 분기 3회째** — 병행 세션 `e7a2c5b90d84`(월 고정비)와 내 `d3f5b7a91c48`이
   같은 부모에서 갈라져 prod 헤드 2개 → 배포 안전 중단. **Jino 승인 후**(17:56 "그래")
   상대 파일을 main으로 가져오고(prod 배포본과 **바이트 동일** 확인) 내 것을 재부모.
   내 리비전은 그 시점에 **미적용**이라 적용된 이력은 안 건드렸다.
2. **`models.py` CAS 차단** — 병행 세션 배포본이 내 역사에 없었다. 덮었으면 「월 고정비」가
   죽었다(7/17 qi 수집 clobber와 동일 패턴). prod 실배포본을 main에 편입 → 그 위에 내 컬럼
   재적용 → **합집합**으로 배포.

## 9. 남은 작업

### 완료 (18:0x~18:11)
- [x] **오픽스 90일 백필** — 이벤트 86 → 91행
- [x] 화면 소재 라벨 3종 + 옵션ID 펼치기 + `source`/`detail` 노출
- [x] **소재 옵션ID 수집·diff** — `ad_creative_diff.py` 신설, 테스트 15건(변이 테스트 포함).
      두 계정 소재 기준선 **1,042건**(오픽스 543 · 오하이테크 499), `baseline:True`로 증감 행 0.
      ★쿠팡 `ads_changed` 행에 **개수가 정확히 맞을 때만** 옵션 목록을 얹는다(enrich).
      어긋나면 우리 행을 따로 내고 시각을 '감지'로 표시.
- [x] 전체 회귀 **4,635 passed** (이월했던 wing 6건도 해소됨 — 병행 세션이 고친 듯)

### 라이브 현황 (18:11 기준, 90일 254건)
`ads_changed` 120 · `field_change` 63 · `created` 50 · `turned_off` 19 · `turned_on` 2
원천: 쿠팡 204 · 스냅샷 50

### ★NCA 축 추가 (18:32~18:33) — Jino가 화면으로 잡아낸 누락
같은 엔드포인트인데 `goalType`별로 따로 물어야 한다. **오픽스 SALES 16 + NCA 4 = 20**,
**오하이테크 SALES 525 + NCA 48 = 573**. 오하이테크 활성이 16 → **19**로 늘었다(NCA 3개 가동 중).
- 내 오판: `goalType: null`을 "필터 해제"로 착각(실은 기본값 SALES). 값 16종을 실제로 훑어
  SALES·NCA만 유효함을 확인. `GOAL_TYPES` 상수 + `_fetch_by_goals()`.
- 변경 이력·소재는 campaignId만 받으므로 **코드 변경 0으로** NCA까지 덮인다(실측 신규 12건).
- 07-24 09:43에 NCA 4개를 연달아 끈 이력이 소급으로 들어왔다 — "비-PA가 7-24에 끊긴" 이유가
  이력으로 설명된다.

### 확정 결정 추가
- **D-CAC-6** 변경 주체 = **Jino 단일**(쿠팡은 대행사 없음) → 주체 축 불필요.
- **D-CAC-7** 미확인 축(「인지도 상승」 goalType·미지의 changeType·디스플레이 개시)은
  **Jino가 운영 시점에 알려준다**. 추측으로 미리 넣지 않는다.

### 남은 것
- [ ] **합격기준 ② 미증명** — 소재 변경이 **실제로 한 번 더 일어나야** 옵션ID 목록이 붙는다.
      지금 `ads_changed` 120건은 전부 "옵션 목록 없음(개수만 기록됨)"이다(소급 불가 축이라 정상).
      화면이 그 사실을 문구로 말한다 — 빈칸으로 두면 "소재가 안 바뀐 것"으로 읽히기 때문.
- [ ] codex PR 경계 리뷰 (쿼터 리셋 **08-09**)
- [ ] 미푸시 커밋 push + PR (Jino 요청 시)

## 10. 이월 (스코프 밖)

- `tests/test_wing_poll_fetch_error_report.py` **6건 실패** — 내 변경 이전부터 깨져 있다
  (내 파일 stash 후 재현 확인). RG 회차 종료 보고 계약 관련, 병행 세션 영역.

## 11. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_coupang-ad-change-log_20260804.md 읽고 §9 남은 작업 이어서
```
