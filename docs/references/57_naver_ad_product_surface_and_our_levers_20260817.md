# ref 57 — 네이버 검색광고 상품 지도와 «우리가 쥔 손» 전수 대조 (2026-08-17)

> 발단: Jino 질문 — *"네이버 스마트스토어광고에 어떤 종류가 있는지 디테일하게 리뷰해줘. 내가 알기로 지금 우리가 가지고 있는 손은 쇼핑검색밖에 없고 그 기능도 우리가 모두 활용하고 있는건 아니잖아?"*
> 트랙: PAO — `docs/tracks/active/track_naver-ad-optimization.md`
> 성격: **사실 지도**다. 어느 레버를 열지에 대한 전략 판단은 담지 않는다([[no-ad-strategy-recommendations]] — 전략은 Jino 몫).

## 0. 출처와 그 한계 (먼저 읽을 것)

| 무엇 | 출처 | 상태 |
|---|---|---|
| 상품 유형·소재 유형·API 게이트 | 공식 스펙 JSON `naver/searchad-apidoc` gh-pages `assets/json/ncc-heroes-ncc.json` (275KB) | ✅ **직접 받아 파싱**(위임 결과를 재확인) |
| 에러코드 정의 | `naver/searchad-apidoc` master `NaverSA_API_Error_Code_MAP.md` | ✅ 직접 확인 |
| 우리 계정 구성·집행 이력 | prod `ohisell.db` 라이브 조회 | ✅ 실측 |
| 쇼핑 그룹 유형·자동입찰·확장검색 | 네이버 SA API 라이브(표본 12그룹, GET only) | ✅ 실측 |
| **지면(노출 위치)·과금 요율** | `saedu.naver.com` · `searchad.naver.com` · `gfa.naver.com` | ❌ **환경 정책 차단 — 확인 못 함** |

⚠️ 마지막 줄이 이 문서의 가장 큰 구멍이다. 「어디에 어떻게 노출되고 얼마를 받는가」는 **일반 지식으로 메우지 않았다**(§3 추정 금지). 조회일 2026-08-17 KST.

## 1. 전제 정정 — 파워링크는 «없는 손»이 아니다

질문의 전제 두 개가 실측과 갈린다.

| campaignTp | 통칭 | 캠페인 | 광고그룹 | 30일 광고비 | 30일 전환액 |
|---|---|---|---|---|---|
| `SHOPPING` | 쇼핑검색 | 31 (on 15) | 482 | **32,998,135원 (77%)** | 87,213,880원 |
| `WEB_SITE` | 파워링크 | 13 (on 8) | 526 | **9,861,586원 (23%)** | 27,314,560원 |
| `BRAND_SEARCH` | 브랜드검색 | 2 | 9 | **0원** (둘 다 off) | 0 |

- **계정 기준으로는 틀리다**: 파워링크가 월 986만원을 쓰고 있고 광고그룹 수는 오히려 쇼핑보다 많다(526 vs 482).
- **PAO 기준으로는 맞다**: `naver_campaign_settings`에 등록된 7캠페인 = 쇼핑 6 + 파워링크 1(`○ P. 아이패드 파워링크`, 현재 off). 전부 `optimizer='none'`(2026-07-30 전면 정지).
- 따라서 **파워링크 986만원은 PAO 바깥에서 굴러간다.** D-NAO-179의 제외키워드 711건 전맹이 정확히 이 영역이었던 것은 우연이 아니다 — 보는 대상이 아니었으니 보이지 않았다.

## 2. 상품 유형은 5종이다 (공식 enum 전체)

`Campaign.campaignTp` 원문:
```
['WEB_SITE', 'SHOPPING', 'BRAND_SEARCH', 'PLACE', 'POWER_CONTENTS']
```
→ **우리 보유 3 / 미보유 2**(`PLACE` 플레이스, `POWER_CONTENTS` 파워콘텐츠는 캠페인 자체가 없다).

`Adgroup.adgroupType` 원문:
```
['WEB_SITE', 'SHOPPING', 'INFORMATION', 'PRODUCT', 'BRAND_SEARCH', 'PLACE', 'CATALOG']
```

`Ad.type` 원문(소재 15종):
```
TEXT_45 · SHOPPING_PRODUCT_AD · CONTENTS_AD_INFORMATION · CONTENTS_AD_PRODUCT ·
BRAND_SEARCH_AD · PLACE_AD · CATALOG_AD · SHOPPING_BRAND_AD · LOCAL_AD ·
SHOPPING_BRAND_IMAGE_THUMBNAIL_AD · SHOPPING_BRAND_IMAGE_BANNER_AD ·
BRAND_SEARCH_NEW_AD · RSA_AD · MEDICAL_AD · DOOH_AD
```
→ 우리가 쓰는 것은 `SHOPPING_PRODUCT_AD`와 파워링크 텍스트 소재뿐. **카탈로그형(`CATALOG_AD`)·쇼핑 브랜드형(`SHOPPING_BRAND_*` 3종)·반응형 검색광고(`RSA_AD`)는 미사용.**

**라이브 표본 실측(쇼핑 그룹 12개 무작위, GET only)**:
- `adgroupType` = **SHOPPING 12/12** → 우리 쇼핑은 전부 «쇼핑몰 상품형»이다(카탈로그형 아님).
- `autobidStrategy.isAutobidActive` = **False 12/12** → 시스템 자동입찰 **전부 꺼짐**(100% 수동 입찰).
- `useExpSearch` = **True 12/12** → 확장검색 전부 켜짐.

※ `autobidStrategy` 필드 설명 원문: *"광고 그룹의 자동 입찰 전략입니다.. (쇼핑 검색 캠페인의 **상품몰 유형만 유효**합니다.)"* — 즉 우리 그룹이 바로 그 유효 대상인데 쓰지 않고 있다.

## 3. 쇼핑검색과 파워링크는 레버 구조가 근본부터 다르다

| | 쇼핑검색 | 파워링크 |
|---|---|---|
| 키워드 엔티티(`naver_entity`) | **0개** | **91,172개 (전부)** |
| 30일 비용 grain | 전액 `keyword_id` 없음 | 키워드별 3,999,818원 + 그 외 5,861,768원 |
| 입찰 단위 | 그룹 입찰 / 소재 개별 입찰 | 키워드 입찰 |
| 우리 입찰 하한 가드 | 50원 | 70원 |
| 제외키워드 API | **불가** | **가능** |
| 콘텐츠 네트워크 입찰 | **불가**(스펙 명시) | 가능 |

쇼핑검색은 **키워드를 등록하는 상품이 아니다**(상품 소재가 검색어에 자동 매칭). 그래서 쇼핑에서 제어 가능한 축은 넷뿐이다: ①그룹/소재 입찰 ②소재 on/off ③제외(**콘솔 수동만**) ④예산.

**소재 입찰 실태**(`naver_adgroup_product` 전수): 소재 1,761개 중 **588개(221그룹)가 소재 개별 입찰**(`use_group_bid_amt=0`), 1,173개(36그룹)는 그룹 입찰을 따름. 소재 잠금(`ad_user_lock=1`) 317개.
→ D-NAO-164의 「03은 PAO 그룹 입찰인데 실효는 소재였다」가 이 구조에서 나온다.

## 4. 유형별 API 게이트 — 스펙 원문

| 기능 | 게이트 | 원문 |
|---|---|---|
| 제외키워드 (GET/POST/DELETE 3개 전부) | **WEB_SITE 전용** | *"This feature is only available for adgroups of website campaign types."* |
| 〃 거부 시 에러 | code 3728 | `3728 \| 키워드 확장 노출 제외키워드를 지원하지 않는 캠페인 유형입니다.` |
| 콘텐츠 네트워크 입찰(`contentsNetworkBidAmt`) | 쇼핑 제외 | *"This field isn't use to Adgroup of Shopping campaign type."* |
| 자동입찰(`autobidStrategy`) | 쇼핑 상품몰형 전용 | *"(쇼핑 검색 캠페인의 상품몰 유형만 유효합니다.)"* |
| 타겟팅 `AD_TAG` | 쇼핑 하위 카탈로그 소재 전용 | *"AD_TAG is applied to catalog creatives under shopping campaigns, and an error occurs when registering for other creatives."* |
| 광고그룹 생성 | BRAND_SEARCH는 `media`+`templateId` 필수 / POWER_CONTENTS는 `contentsType` 필수 | `adgroupAttrJson` 설명 |

## 5. 우리가 부르는 API vs 안 부르는 API

**부르는 것**: `/ncc/campaigns` · `/ncc/adgroups` · `/ncc/ads` · `/ncc/keywords` · `/ncc/adgroups/{id}/restricted-keywords` · `/stat-reports` · `/keywordstool` · `/estimate/average-position-bid/id` · `/estimate/performance-bulk`

**스펙에 있는데 코드가 한 번도 안 부르는 것**(백엔드 전수 grep 0건):

| 미사용 엔드포인트/필드 | 무엇 |
|---|---|
| `/ncc/ad-extensions` | 확장소재 |
| `/ncc/targets`, `/ncc/criterion*` | 타겟팅(지역·요일시간·기기·성별·연령 — dictionary type `SD/RL/RP/DV/AG/GN/AD/CA`) |
| `/ncc/shared-budgets` | 공유 예산 |
| `/ncc/labels`, `/ncc/label-refs` | 라벨 |
| `/ncc/product-groups` | 상품 그룹 |
| `/ncc/time-contracts` | 브랜드검색 정액 계약(`contractAmt`/`paymentAmt`) |
| `/ncc/brand-new/contracts` | 신제품검색 경매 계약(`biddingRound`/`biddingStatus`) |
| `/master-reports` | 엔티티 스냅샷 리포트(item 30종) |
| ~~`SHOPPINGKEYWORD_DETAIL` · `SHOPPINGKEYWORD_CONVERSION_DETAIL`~~ | **정정(2026-08-17 라이브 실측)**: 미사용이 **아니다**. `/stat-reports` 라이브 조회 결과 우리 계정에 `EXPKEYWORD 3 · SHOPPINGKEYWORD_DETAIL 3 · SHOPPINGKEYWORD_CONVERSION_DETAIL 3 · AD 3 · AD_CONVERSION 3` = 15건이 이미 있고, `naver_sa_ad_fetcher.create_stat_report(report_tp, …)`가 타입을 **인자로 받아** 만든다. 최초 판정은 `grep`이 경로 문자열만 보고 `reportTp` 값을 안 본 탓이다 — 조사 방법이 만든 거짓 음성. |
| `autobidStrategy` **쓰기** | 읽어서 「ML 자동입찰 중이면 손대지 않는다」 가드로만 씀 |
| `useExpSearch` **쓰기** | `extended_search`로 읽기만 함(BM Phase 3) |

## 6. 배선된 레버 5종과 «실제로 쥔» 횟수

코드에 배선된 실행 액션(`naver_execution_harness._ACTION_BY_PROPOSAL_TYPE`): `update_bid` · `set_user_lock`(정지/재개) · `update_budget` · `exclude_search_term` · `add_negative_keyword`.

`naver_change_log`에서 `dry_run=0` 전 기간 실측:

| 액션 | 횟수 | 기간 |
|---|---|---|
| `update_bid` | **425** | 2026-07-17 ~ **07-30** |
| `set_user_lock` | 21 | 07-19 ~ 08-10 |
| `update_budget`(+페이싱 4) | 14 | 07-23 ~ 07-30 |
| `exclude_search_term` | **1** | 07-22 |
| `add_negative_keyword` | **0** | — |
| ─ 참고: `external_bid_change` | **443** | 07-22 ~ **08-14** |
| ─ 참고: `external_keyword_added` | 167 | 07-22 ~ 08-11 |

`external_*`은 **우리가 한 것이 아니라 관측한 것**이다(대행사·MOP). 즉 **2026-07-30 PAO 전면 정지 이후 이 계정을 실제로 움직이는 손은 우리가 아니라 대행사다.**

## 7. ★ 스펙은 5주 전부터 우리 리포 안에 있었다

`docs/references/data/ncc-heroes-ncc.json` — **2026-07-10 커밋 `4fa3fe2e`(ref 27 작업)로 들어온 공식 스펙 파일**이다. 그 안에:
- `EXP_SEARCH` **6회** 등장
- `"only available for adgroups of website campaign types"` **3회** 등장

즉 **2026-08-11~16에 라이브 프로브를 돌려 확정한 두 사실(「쇼핑 차단은 캠페인 유형 게이트」·「제외 타입이 둘」)이 5주 넘게 리포 안 파일에 적혀 있었다.**

다만 **같은 파일 안에서 요청 스펙과 응답 스펙이 갈라져 있다**:

```
GET 파라미터  type: enum = ['KEYWORD_PLUS_RESTRICT']              ← 하나뿐
             desc  = "기본값은 'KEYWORD_PLUS_RESTRICT' 입니다."
데이터 모델   AdgroupRestrictKwd.type         enum = ['KEYWORD_PLUS_RESTRICT', 'EXP_SEARCH']
             NccAdgroupRestrictKwdResponse.type enum = ['KEYWORD_PLUS_RESTRICT', 'EXP_SEARCH']
```

**읽는 쪽 스펙만 보면 타입은 하나로 보이고, 게다가 그게 기본값이라 생략해도 한 타입만 온다.** D-NAO-179의 「723건 중 12건(1.7%)」 전맹이 이 비대칭에서 나왔다 — 교훈 #293(「한 값만 물으면 API는 「없다」고 200으로 대답한다」)의 문서적 뿌리가 여기다.

**집행 지점 후보(미구현)**: 새 SA 엔드포인트를 배선할 때 `docs/references/data/ncc-heroes-ncc.json`에서 **요청 파라미터 enum과 응답 모델 enum을 둘 다** 열어 대조한다. 한쪽만 보면 이 결함이 재발한다.

## 7-b. 후속 (2026-08-17 같은 날)

§5의 「미사용」 목록을 **라이브 GET으로 전수 호출**한 결과는 **ref 58**에 있다. 요약하면 이 문서의 §5는 「코드가 안 부른다」는 사실로는 맞지만, **「계정에 그 기능이 없다」는 뜻이 전혀 아니었다** — 타겟팅 1,271행·확장소재 413개·브랜드검색 계약 35건이 이미 걸려 있었다. 그리고 위 정정처럼 §5 목록 자체에 거짓 음성이 하나 있었다.

## 8. 확인 못 한 것 (명시)

1. **지면(노출 위치) 전부** — 마케팅 사이트 접근 차단.
2. **과금 요율·단가** — 입력 제약(예: 입찰가 70~100000)은 확인했으나 요율이 아니다.
3. **쇼핑 3분류(상품형/카탈로그형/브랜드형) ↔ 코드값 공식 매핑표** — 코드는 흩어져 확인되나 1:1 매핑 문장 없음.
4. `LOCAL_AD` · `MEDICAL_AD` · `DOOH_AD`가 속한 `campaignTp` — enum엔 있으나 소속 명시 문장 없음.
5. 신제품검색(`BrandNewContract`)이 속한 `campaignTp` — 명시 없음.
6. 클릭초이스플러스 단종 여부 — 캠페인 삭제 API 설명에 "뉴 클릭초이스" 레거시 문구 1건만 잔존, 현재 enum엔 없음.
7. GFA가 검색광고와 별도 시스템인지의 1차 확인 — 사이트 차단. (단 이 API 스펙에 GFA 리소스가 **전혀 없다**는 것은 전수 확인.)
8. `SHOPPINGKEYWORD_DETAIL`의 필드 레벨 스펙 — enum 존재만 확인.
