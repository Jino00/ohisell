# 트랙: 쿠팡 광고 설정 변경 내역 (「수정 사항」 쿠팡판)

> 생성 2026-08-04 · 상태: **구현·배포 완료 · 최종 합격 증거(실변경 1건) 대기**
> 대상: 오픽스(3P/RG, WING) + 오하이테크(1P 로켓, SUPPLIERHUB)
> 모델: 네이버 「수정 사항」 화면(D-NAO-135) — 날짜별 변경 이력 조회

## 왜 이 트랙이 생겼나

Jino: "Sellc에 우리가 광고 수정한 내역에 대해서 네이버 광고 수정사항 정리하는것처럼 정리할 수 있어?"

네이버 「수정 사항」이 서는 다리는 둘이다 — ①우리 실집행 로그(`naver_change_log`) ②설정 스냅샷 diff
+ 소재 `editTm` 앵커. **쿠팡은 둘 다 없다**: 우리가 쿠팡 광고를 쓰는 경로가 아예 없고(`services/coupang/`에
광고 쓰기 0건), 광고 테이블 3종은 성과(비용·노출·클릭·전환)만 담아 **diff를 뜰 설정 상태가 없다**.

★쿠팡은 **모든 변경이 외부다** — 네이버와 결정적으로 다른 점. 우리 실집행 로그가 원리적으로 안 생긴다.

## 확정 결정

### D-CAC-1 대상 = 쿠팡 두 계정 (2026-08-04)
오픽스(3P/RG) + 오하이테크(1P 로켓). Jino 선택: "쿠팡 오픽스 (3P/RG), 쿠팡 오하이테크 (1P 로켓)".
네이버 실집행분 분리 뷰는 **비선택**.

### D-CAC-2 방식 = 자동 스냅샷 diff (정찰 선행) (2026-08-04)
Jino 선택: "엔드포인트 정찰 먼저 (권장)". 수동 기록은 비선택 —
왜냐하면 네이버가 외부 감지를 넣은 이유가 정확히 "수동은 조용히 빈다"였다.

### D-CAC-3 ★On/Off 축과 내용 축을 가른다 (2026-08-04)
Jino 원문: **"On/off 부분이 있어. 그래서, 너는 새로 생긴 캠페인이 있는지, Off가 된 캠페인이
있는지를 파악하고 캠페인의 내용 변경에 대해서는 On되어 있는 캠페인만 보면 되는거야"**

| 축 | 범위 | 판정 |
|---|---|---|
| A. 존재·On/Off | **전량**(`isActive` 무필터) | 신규 생성 · Off 전환 · 삭제(목록에서 소멸) |
| B. 내용 변경 | **활성만**(`isActive:true`) | 캠페인·광고그룹 필드 diff |

이 규칙이 비용 문제를 푼다 — 오하이테크 캠페인이 **525개**인데 활성은 **16개**뿐이다(오픽스 16개 중 활성 5개).
서버 사이드 `isActive:true` 필터가 동작함을 실측 확인(`totalCount:16`, `hasNextPage:false` → 1콜).

★**받아들인 절충**: Off 상태에서 내용을 바꾸고 나중에 On하면 그 중간 변경은 안 잡힌다.
On되는 순간 값이 첫 스냅샷이 되므로 화면엔 **"신규 On(이전 값 없음)"**으로 표시한다 — 거짓 안심을 주지 않는다.

### D-CAC-4 원천 = `POST /marketing/tetris-api/campaigns` (실측, 2026-08-04)
두 계정 **동일 엔드포인트**(오픽스 `channel=3P` / 오하이테크 `channel=Retail`).
대시보드가 보내는 payload를 그대로 재생하고 `pagination.size`만 키우면 전량이 온다.

- 캠페인: `budget` `capType` `roasTarget` `targetType` `isActive` `isSuspended` `servingStatus`
  `learningStatus` `isAgencyManaged` `createdAt` **`updatedAt`** `version`
- `groupList[]`에 **광고그룹이 중첩**: `bidType` `pricingType` `pricing` `roasTarget`
  `keywordTargeting` 자체 `updatedAt`·`version`
- **키워드 그레인은 미확인** — 캠페인을 열어 키워드 탭까지 눌러야 나온다(정찰 잔여).

## 라이브 증거 (2026-08-04)

**탐지력 증명 — 오하이테크 1P**
Jino가 캠페인 하나를 On/Off. 스냅샷을 뜨니 `[매.최] 메츨 싱`(id 104882010)의
`updatedAt = 2026-08-04T01:51:21Z` = **KST 10:51:21**, Jino 메시지 10:51:30 → **9초 차**.
같은 캠페인의 광고그룹 `updatedAt`은 06-26 그대로 → **캠페인 레벨 변경**임이 층으로 구분됨.

덤: 같은 조회에서 `[매.최] 폴드8_지문방지필름`(09:12:56) · `[매.최] 폴드8울트라_지문방지필름`(09:13:24)
— **내가 관여하지 않은 진짜 외부 변경 2건**이 함께 잡혔다(내 첫 오하이테크 접속은 10:55).

**오탐 없음 — 오픽스 3P**
27분간 3회 스냅샷(10:25 / 10:32 / 10:52) 필드 diff = 설정 변경 0건. 응답 전체에 `2026-08-04`
타임스탬프 0회 — 그 계정은 오늘 정말 아무도 안 만졌고, 기계는 그걸 정확히 "변경 없음"으로 냈다.

**★찾아낸 함정**: 유일하게 움직인 건 `spentBudget` 6,501.49 → 6,592.87(20분).
`averageTimeBudgetUtilRate`도 같은 성질이다. **성과 필드를 diff에서 빼지 않으면 매일 아침
"전 캠페인 수정됨"이 뜬다** — 네이버에서 소재 `editTm`이 상품 피드 재적용으로 전진해
`ad_edit`이 229:4로 오염됐던 것과 같은 종류의 함정.

## 도구

`tools/ad_settings_recon.py {ofix|ohitech}` (커밋 `4120ce3`) — 두 계정 공용 정찰기.
- ofix = storage_state 기동(WING) / ohitech = 기존 CDP Chrome(9224)에 attach(SUPPLIERHUB)
- ★ohitech 모드는 Chrome을 직접 띄우지 않는다 — 프로필 락·데몬 소유권을 안 건드리려고.
  선행: `python3 tools/ohitech_ad_fetcher.py chrome` 후 Jino 로그인.
- SSO 자동 재발급 내장(keycloak, 비번 없음). **headless는 keycloak이 막는다 — headful 필수.**

### D-CAC-5 자기 크론을 두지 않는다 — 「광고비 갱신」 회차에 얹는다 (2026-08-04)
Jino: "이거 매 시간 화면에 크롬이 뜨는거 아니야? 성가신데..."

`coupang_auth.py` 실측: **xauth Akamai가 headless를 Access Denied로 막는다** → 창이 반드시 뜬다.
그래서 2026-07-27에 이미 "창을 스스로 띄우지 않고 버튼 누를 때만 뜬다"로 정리된 구조다
(`ohitech_ad_fetcher.cmd_poll` docstring). 여기에 콜 몇 개를 얹으면 **창이 뜨는 횟수가 0회 늘어난다**.

주기가 느려도 손실이 거의 없다 — `updatedAt`이 캠페인·광고그룹 **양쪽에** 있어서 사흘 만에 떠도
발생 시각은 초 단위로 복원된다. 잃는 건 하나: 같은 필드를 두 스냅샷 사이에 두 번 바꾸면
중간값이 뭉친다(100,000→50,000→80,000이 "100,000→80,000"으로 보인다).

## 구현 (2026-08-04, main `fcbc683`·프론트 커밋, prod 배포 완료)

| 층 | 파일 |
|---|---|
| 마이그 | `c8d1a4f97b26` — `coupang_ad_entity_snapshot` · `coupang_ad_change_log` |
| 서비스 | `backend/app/services/coupang/ad_settings_diff.py` (A축/B축 분리·허용목록·idempotent) |
| 라우터 | `coupang_ops.py` — `POST /ad-settings/ingest`(토큰) · `GET /ad-changes`(KST 창) |
| 수집 | `tools/ad_settings_collect.py`(공용) → 페처 2종에 얹음 · 설치 목록에 공용 모듈 등록 |
| 화면 | `frontend/src/pages/CoupangAdChanges.tsx` — `/coupang-ad-changes` |
| 테스트 | 37건(`test_coupang_ad_settings_diff.py` 22 + `test_coupang_ad_changes_router.py` 15) |

**라이브 검증(2026-08-04 11:4x, prod)**
- 1회차: 전량 525건·활성 16건 수집 → `changes: 541`(캠페인 525 + 광고그룹 16의 created).
- ★그 541건이 **오늘 화면을 어지럽히지 않았다** — 쿠팡 `createdAt`에 귀속돼 진짜 생성일로 흩어졌다
  (오늘 0건, 최근 14일 12건). 감지일에 귀속했다면 525줄이 오늘 하루에 쏟아졌다.
- 2회차(2분 뒤): **`changes: 0`** — 실데이터 525건에서 오탐 없음.
- 화면: `/coupang-ad-changes` 30일 탭에 캠페인·광고그룹 짝이 KST 시각으로 정상 렌더.
- 허용목록 밖 22종은 전부 식별자·UI·파생(`adNodeId`·`id`·`depth`·`descriptor`·`groupId`…) — 놓친 설정 없음.

**오픽스 기준선(15:31, prod)** — 전량 16건·활성 5건 → `changes: 21`(캠페인 16 + 광고그룹 5).
화면 `오픽스` 필터 + 30일에 11건 정상 렌더(08-03 「사생활 지문방지필름」 신규 생성 등).
★**오픽스는 keycloak까지 만료되면 스스로 못 돌아온다** — 오하이테크는 Keychain 자동 로그인
(`_recover_session`)이 있어 회차 중 자가 복구했지만(11:46 실측), 오픽스는 그 경로가 없어
`ad_cost_browser_fetcher.py login`으로 Jino가 직접 로그인해야 했다(15:30). 같은 날 10:25엔
keycloak이 살아 있어 통과했으므로 만료 주기 문제다.

## ★★ 정찰 2차 (2026-08-04 17:2x) — 쿠팡이 **변경 이력 API를 직접 준다**

Jino "키워드, 소재 그레인까지 현실적으로 가능해? 안정적으로 구현할 수 있는지 실증먼저 해봐"

광고센터 메뉴에 **「변경 이력」**(`/marketing/change-history`)이 있었다. 그 화면이 부르는 API:

**`POST /marketing/tetris-api/change-history/events-simple`**
요청 `{"campaignIds":[...], "filter":{"executionTimeFrom":"20260506","executionTimeTo":"20260804"}}`
응답 `[{campaignId, executionTime(UTC), executionId(UUID), changes:[{changeType, before, after, added, removed, ruleType, campaignRule, budgetRollbackType}]}]`

**두 계정 모두 동작**(오픽스 WING / 오하이테크 SUPPLIERHUB, 같은 엔드포인트).
90일 실측: 오픽스 86건(05-26~08-03) · 오하이테크 108건(05-11~08-04).

| changeType | 90일 합계 | 내용 |
|---|---|---|
| `VIID` | 120 | **소재(광고 상품) 추가·제거** — `before/after/added/removed` **개수만** |
| `TROAS` | 35 | 목표 ROAS (270→230 식) |
| `BUDGET` | 28 | 일예산 (50,000→100,000 식) |
| `CAMPAIGN_ONOFF` | 21 | On/Off (true→false) |

- ★**소급 조회가 된다** — 90일 과거를 지금 당장 받는다. 우리 스냅샷 diff는 원리적으로 배포 이후만 가능하다.
- ★**`executionId`가 UUID** — 멱등 키가 공짜로 있다.
- 기간 상한: 90일 OK, 1년은 500(`Cannot read properties of null`). 정확한 상한 미측정.
- `events`·`event`·`events-detail`은 **404** — `events-simple` 하나뿐이다.
- 한 이벤트의 `changes`는 1~2개(103:5).

**★합격 증거의 원료가 여기 있었다**: Jino의 08-04 변경이 `BUDGET 1,500,000 → 70,000`
(`2026-08-04T01:51:21Z` = KST 10:51:21, `[매.최] 메츨 싱`)로 그대로 들어 있다. 우리 스냅샷은
`updatedAt`으로 **시각만** 알았는데 쿠팡은 **전→후 값**을 준다.

### 키워드 — 추적할 대상이 존재하지 않는다
prod 스냅샷 실측: **활성 광고그룹 21개(오픽스 5 + 오하이테크 16) 전부 `keywordTargeting=AUTOMATIC`
+ `bidType=AUTO_BID`.** 사람이 키워드를 추가·삭제·입찰하지 않는다 → 변경 이력에 키워드 changeType이
0건인 게 당연하다. **"구현이 어렵다"가 아니라 "관리 대상이 없다".**
(단 비활성 캠페인의 광고그룹은 안 봤고, 수동 키워드 캠페인을 새로 만들면 전제가 바뀐다.)

### 두 원천은 상호보완적이다 — 내 스냅샷 diff가 이걸 대체하지 않는다
| | change-history API | 스냅샷 diff(현 구현) |
|---|---|---|
| 예산·목표ROAS·On/Off | ✅ 전/후 + 정확한 시각 + **90일 소급** | ✅ 배포 이후만 |
| **소재(상품) 추가·제거** | ✅ **개수만**(어떤 상품인지는 없음) | ❌ |
| 신규 캠페인·삭제 | 미확인 | ✅ |
| 이름·기타 캠페인 설정 13종 | ❌ | ✅ |
| 광고그룹 15개 필드 | ❌ | ✅ |
| 키워드 | 대상 없음 | 대상 없음 |

★정직하게: **이 API를 모른 채 스냅샷 diff를 먼저 만들었다.** 겹치는 축(예산·ROAS·On/Off)은
쿠팡 쪽이 더 정확하다(전/후 값 + 소급). 네이버 「수정 사항」이 두 원천을 합치듯
(`naver_change_log ∪ naver_agency_op`) 여기도 합치는 게 맞다 — 어느 한쪽만으로는 반쪽이다.

## ★★ 정찰 3차 (2026-08-04 17:4x) — 옵션ID 단위 소재 추적이 **된다**

Jino "광고 캠페인에 어떤 옵션ID가 추가됐는지 볼 수 있는 방법은 전혀없는거야?"

**`POST /marketing/tetris-api/{adGroupId}/ads`**
요청 `{"isDeleted":false,"pagination":{"page":0,"size":500},"sortedBy":"ID","isSortDesc":true}`
응답 `{ads:[{adNodeId, vendoritemid, itemName, isActive, isSuspended, isDeleted,
           pricingOverride, servingStatus, type}], pageInfo:{totalCount, hasNextPage}}`

- ★**`vendoritemid` = 옵션ID**가 그대로 온다 → 스냅샷 diff하면 **어떤 옵션이 붙고 빠졌는지** 정확히 안다.
  `events-simple`의 `VIID`는 개수만 주지만, 이 목록과 합치면 "옵션 95838755133 외 19개 추가"로 쓸 수 있다.
- ★`pricingOverride` — **소재별 입찰가 오버라이드**도 온다(현재 오픽스 0건이지만 축은 존재).
- ★`isDeleted=true`로 **삭제된 소재도 따로 조회**된다(AI스마트광고 42건).
- **비용 실측(오픽스)**: 활성 캠페인 5 · 광고그룹 5 · 소재 196개 → **콜 6회 · 1.0초**.

### ★★★ 함정: `hasNextPage`가 거짓말한다 (조용한 절단)
같은 광고그룹(204811906)에 대해:

| 요청 | 받은 ads | pageInfo |
|---|---|---|
| `size=100, page=0` | **100** | `totalCount: 447, hasNextPage: **False**` |
| `size=100, page=1` | 100 | `totalCount: 447, hasNextPage: False` |
| `size=500, page=0` | **447** | `totalCount: 447, hasNextPage: False` |

`hasNextPage`만 믿고 루프를 끊으면 **347개가 조용히 사라진다**. 로그에도 안 남는다.
**`totalCount`가 권위값이다** — 받은 수가 totalCount에 못 미치면 계속 받아야 한다.

⚠️**이 함정이 현 구현에도 잠재해 있다**: `tools/ad_settings_collect.py::_fetch_all_pages`가
`hasNextPage`만 본다. campaigns 엔드포인트에선 우연히 정상 동작했지만(오하이테크 525건 수신 =
totalCount 525 일치, 라이브 확인) 같은 코드를 `/ads`에 쓰면 즉시 절단된다.
→ 다음 슬라이스에서 **totalCount 기준으로 교체**하고, 못 채우면 경고를 남긴다(조용한 절단 금지).

## 미결

- [ ] **최종 합격 증거**: Jino가 광고센터에서 실제로 한 번 바꾸면 전/후 값이 뜨는지.
      (10:51 변경은 11:47 기준선보다 **앞서** 흡수됐다 — 새 변경이 필요하다.)
- [ ] 키워드 그레인 정찰 (캠페인 → 키워드 탭) — 1차 스코프 밖
- [ ] 오하이테크 SSO 상시 실패 — keycloak 세션까지 만료돼 Jino 수동 로그인이 필요했다.
      (기존 인계의 "①SSO 상시 실패 해소" 미결과 같은 건. 단 페처 자체는 Keychain 자동 로그인으로 복구됨)
- [ ] codex PR 경계 리뷰 (쿼터 리셋 08-09)

## 계약 초안 (승인 대기)

① **목표** — 쿠팡 광고 설정 변경(신규·On/Off·내용)을 sellc에서 시간순으로 본다.
   **안 하는 것**: 쿠팡 광고 자동 집행(쓰기) · 네이버 화면 변경 · 경보/알림 · 변경의 성과 귀속 분석 · 키워드 그레인(1차 제외).

② **판단기준**
- 항상 스냅샷 diff로 잡는다 — 쿠팡은 우리 실집행 로그가 원리적으로 안 생긴다(모든 변경이 외부).
- 항상 `updatedAt`을 발생 시각으로 쓰고, 없으면 **감지 시각임을 행에 표시**한다 —
  네이버에서 감지일로 귀속했다가 07-30 변경을 08-03으로 잡은 실사고가 있었다.
- 항상 **성과 필드는 diff 대상에서 뺀다**(`spentBudget`·`averageTimeBudgetUtilRate` 등) — 위 함정.
- 변경 **주체를 단정하지 않는다** — `isAgencyManaged`는 현재 관리 주체지 변경 주체가 아니다.
- On/Off는 전량, 내용은 활성만(D-CAC-3).

③ **금지선** — 쿠팡 광고에 **쓰기 요청 금지**(읽기 전용). DB는 마이그레이션으로만.
   배포는 `scripts/safe_deploy.sh --migrate`.

④ **합격기준(라이브)** — prod에 스냅샷 2회 이상 적재된 뒤, Jino가 광고센터에서 실제로 한 번 바꾸면
   다음 스냅샷에서 그 변경이 화면에 뜨고 **전/후 값과 발생 시각이 함께** 보인다.
   신규 캠페인 1건과 Off 전환 1건도 각각 행으로 뜬다.

⑤ **예산** — 반나절.
