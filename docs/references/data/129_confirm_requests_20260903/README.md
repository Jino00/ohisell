# 129 — 확정 요청 본문 전건 (2026-09-03 · D-CPP-69 · D-CPP-70)

> **왜 이 폴더가 있나**: 완료 QA(2026-09-03 12:1x KST)가 정확히 짚었다 —
> *"실제 확정은 세션 스크래치패드의 `req273/*.json` 47개 파일을 curl로 개별 호출해 수행됐고,
> 이 실행 경로는 **git에서 재현·감사가 불가능**하다. 계약도 커밋 메시지도 이 gap을 스스로 신고하지 않았다."*
>
> 지적이 옳다. 계약 D-CPP-70 §6은 `scripts/confirm_purchased_b.py`와 회귀 테스트를 약속했는데,
> 그 뒤 Jino 지시(2026-09-03 11:26) *"코드짜지말고, 내가 그냥 알려줄께"*로 **코드 경로가 폐기**됐다.
> 그 결정 자체는 Jino의 것이지만, **「그러면 실행 흔적이 저장소에 안 남는다」는 대가를 내가 신고하지 않았다.**
> 이 폴더가 그 대가를 사후에 메운다 — 스크립트는 없지만 **보낸 것 전부**는 남는다.

## 무엇이 들어 있나

| 폴더 | 파일 | 무엇 | 결과 |
|---|---:|---|---|
| `slice1_74/` | 9 | 조립품 74 중 **59 SKU** 확정 요청 본문 (D-CPP-69) | 59/59 written · skipped 0 (11:35:22 KST) |
| `slice2_273/` | 47 | 매입 완제품·기타 **273 SKU** 확정 요청 본문 (D-CPP-70) | 273/273 written · skipped 0 (12:01:53 KST) |

각 파일은 `POST /api/cost/purchased-prices/confirm`에 **그대로 보낸 본문**이다:
`internal_skus` · `price` · `source_file` · `source_names`(SKU별 근거명) · `note`(Jino 판정 원문).

## 재현 방법

```
for f in <이 폴더>/slice2_273/*.json; do
  curl -s -X POST -H 'Content-Type: application/json' --data-binary @$f \
    http://127.0.0.1:8011/api/cost/purchased-prices/confirm
done
```

⚠️**그대로 돌리지 마라 — 멱등이 아니다.** `confirm_group`의 중복 제거는 «한 요청 안»에서만
작동하고 `cost_purchased_price.internal_sku`에 UNIQUE 제약이 없어, 재실행하면 **원장 행이 하나 더
쌓인다**(완료 QA가 코드로 확인). 계산에 쓰이는 값은 최신 1건이라 결과는 같지만 원장이 더러워진다.
재확정이 필요하면 기존 행을 먼저 읽어야 한다.

## 슬라이스 1이 9개뿐인 이유

조립품 74 중 **오픽스 15 SKU**는 이 경로가 아니라 픽 → 종 단가 채택 → 승인 → 컷오버로 처리됐다
(계산값 정본). 그 경로는 요청 본문이 아니라 엔드포인트 호출 4번이고 `docs/references/127_*.md` §2에 시각별로 남아 있다.
