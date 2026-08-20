# ref 84 — 밴드 × 상품 메타 «현재 단면» 교차 + 조인율 (D-NAO-212 · C10 · 북극성 M1 ④)

> 산출: 2026-08-21 06:59~07:01 KST · prod 라이브 · 창 = **단일 시점 단면**(폴링 1회차)
> 상위 정본: 계약 `docs/PLAN_naver-c10-product-meta.md` §5 ⓒ · 북극성 D-NAO-208

## 이 표가 «아닌» 것 (먼저 읽을 것)

- **연관 판정이 아니다.** A/B/C 어느 값도 매기지 않았다 — 홀드아웃 규율을 안 탔고, 이 자료는
  애초에 **한 시점의 단면**이라 재현·반증할 반쪽이 없다. 「밴드1에 SALE이 많다」류를 발견으로
  인용하면 안 된다.
- **금액을 배분하지 않았다**(D-NAO-194 fan-out 원칙 승계). 한 광고그룹에 상품이 여럿 붙으므로
  그룹 비용을 상품에 나누면 상한 성질이 깨진다. 이 표가 세는 것은 **(그룹,상품) 쌍 수**다.
- **시계열이 아니다.** 상품 도메인에 변경-피드가 없어(75건 전건 개봉 실측 2026-08-19) 소급이
  원리적으로 불가능하다. 변경 이력은 **폴링을 시작한 2026-08-21부터** 쌓인다.

## 조인율 (양방향 — 계약 ⓒ)

| 방향 | 분자/분모 | % |
|---|---|---|
| 광고 상품 중 메타 매칭 | 603 / 702 (distinct `mall_product_id`) | **85.9%** |
| 메타 중 광고 등재 | 603 / 1,213 (`channel_product_no`) | **49.7%** |
| (그룹,상품) 쌍 기준 | 1,552 / 1,761 | **88.1%** |

★**동일성은 여전히 [미상]이다.** 이 %는 커버리지이지 `channelProductNo ≡ mall_product_id`의
증명이 아니다 — 교집합은 인과가 아니다. 계약 §8 [미상] ③ 그대로 남는다.

★**미매칭 99개(광고엔 있는데 메타엔 없다)의 유력한 설명**: 무필터 응답의 `statusType` 분포에
**DELETE가 0건**이다(SALE 699·SUSPENSION 367·OUTOFSTOCK 81·CLOSE 65·UNADMISSION 1). 즉 삭제된
상품은 응답에 안 오는데 광고 원장에는 남아 있다. **다만 이건 정황이지 확인이 아니다** —
99개의 실제 상태를 개별 조회로 확인하지 않았다.

## 부수 확정 — 계약 §8 [미상] ⑤ 해소

`productStatusTypes` **무필터** 호출의 포함 범위 = SALE·SUSPENSION·OUTOFSTOCK·CLOSE·UNADMISSION
5종(1,213건). **DELETE·PROHIBITION은 이번 응답에 0건.** 「무필터면 전 상태가 온다」가 아니다.

## 커버리지 자백

- **전수 조사가 아니다.** 이 교차는 층2 36축 중 C10 **한 축**의 단면이고, 커머스 「호출가능·미적재」
  6축(C2 C3 C7 C8 C9 C11)과 층1 미배정 91 endpoint는 그대로 남는다.
- **WEB_SITE는 이 표에 원리적으로 없다** — `naver_adgroup_product`는 쇼핑 소재(`mallProductId`)에서만
  채워진다. 캠페인 유형별 매칭이 SHOPPING 한 줄뿐인 것은 결함이 아니라 구조다.
- 밴드 정본 CSV의 창은 **391일**이고 이 메타는 **오늘 한 시점**이다 — 두 자료의 창이 다르다.
- 리뷰 포인트 3필드는 1,213건 중 **74건**에만 실린다(응답 스키마가 항목마다 다르다는 것의 실측).

## 재현

```bash
ssh -o BatchMode=yes sellc.ohitech.co.kr \
  "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < extract_band_x_product_meta.sql > pairs_raw.txt
sed -n '/^adgroup_id|mall_product_id/,$p' pairs_raw.txt > pairs.psv
python3 build_cross.py pairs.psv ../63_band_decomposition/band_group_total.csv
```
