# 00.아이폰_17 쇼핑검색 소재(ad) ID — SA API 추출 (2026-07-12)

> 출처: 네이버 검색광고 API `GET /ncc/ads?nccAdgroupId=<adgroup>` (우리 naver_sa_ad_fetcher._get 재사용, CUSTOMER 1313769).
> 용도: MOP 목표입찰(target-bidding) 폼의 "소재 ID" 입력값. UI 로그인 불필요.

| 애드그룹 | 입찰(추정) | 소재 상태 | nccAdId (SHOPPING_PRODUCT_AD, inspect=APPROVED) |
|---|---|---|---|
| 01. 강화유리 (grp-...617822) | 50원 | **ELIGIBLE** | nad-a001-02-000000415739855 / 856 / 857 / 858 |
| 02. 사생활 (grp-...617868) | 50원 | **ELIGIBLE** | nad-a001-02-000000415739890 / 891 / 892 / 893 |
| 03. 6H 사생활 (grp-...091017) | 500원 | **AD_ABNORMAL_INTERLOCK(소재 연동 상태 비정상)** | nad-a001-02-000000419880022 / 023 / 024 / 025 |

## ★핵심 발견 (원칙22)
- **03.6H사생활이 07-11~12 노출 0**이던 정확한 원인 = **소재 4개 `status=PAUSED, statusReason=AD_ABNORMAL_INTERLOCK`(=화면 "소재 연동 상태 비정상")**. ★**정지(끈 것)가 아님**: ON/OFF 토글 ON·`userLock=false`·`inspectStatus=APPROVED`이나 소재↔쇼핑상품 **연동이 비정상**이라 `enable=false`로 미노출. 토글로는 안 풀림 — 소재 연동 정상화 필요(상품/소재 영역). ⚠️처음에 `status`만 보고 "PAUSED=정지"로 오보고→`statusReason`으로 정정(2026-07-12).
- 01·02는 소재 ELIGIBLE·APPROVED = 정상 노출 가능 상태. (01은 실제 178노출/rank3.8, 02는 3노출.)
- 각 애드그룹에 소재가 4개씩(상품 4개). MOP Basic 목표입찰은 **소재 1개만** 등록 가능 → 어느 소재를 걸지 선택 필요.

## 소재별 노출 (SA /stats, 07-11~12) — hero 소재 특정
- 01.강화유리: **739856 = imp 132(주력)**, 739855=21, 739857=25, 739858=0 (합 178).
- 02.사생활: 739890=3, 나머지 0 (합 3).
- → **목표입찰 최적 후보 = nad-a001-02-000000415739856**(01.강화유리, 132노출·rank~4·0클릭). 목표=Avg Rank 상향으로 상위노출→클릭 유도 검토.

## ★03 연동 비정상 근본원인 (referenceData 01↔03 실측 비교, 2026-07-12)
- 결정적 차이: **03 소재 4개 = `referenceData.DEL_FLAG=1`·`DEL_TM=2025-09-24`**(연결 몰상품이 삭제됨) vs 01 = `DEL_FLAG=0`(상품 살아있음).
- 즉 03.6H사생활 소재가 가리키는 **스마트스토어 상품(몰상품 12449593659 등)이 2025-09-24에 삭제** → 광고 소재가 죽은 상품을 계속 참조 → `statusReason=AD_ABNORMAL_INTERLOCK`·`enable=false`. 03 소재 editTm도 2025-09-24 이후 방치.
- **고침**: 삭제된 스마트스토어 상품 재등록 또는 소재를 살아있는 상품으로 재연동/재생성(상품/소재 영역, Jino). 광고 토글로는 불가.
- 검증 방법(재사용): `_get("/ncc/ads",{nccAdgroupId})` → `status`+`statusReason`+`enable`+`userLock`+`referenceData.DEL_FLAG` 함께 확인(status 단독 신뢰 금지).
