# 세션 인수인계: 로켓 1P 상품별 손익 + 원가 정본을 sellc로

> 저장일시: 2026-08-04 09:40 KST · main `6b97a1c`(내 마지막 커밋) 기준, 전량 push·배포 완료
> 앞 HANDOFF: `HANDOFF_ohitech-ad-selfheal+option-adcost_20260803.md`
> 시작점: 그 HANDOFF §6 [1순위] "옵션 표에 상품명 붙이기"

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (main 고정)
- prod: `sellc.ohitech.co.kr` · **배포는 `scripts/safe_deploy.sh`만**(직접 scp 금지, CAS 가드)
  - 백엔드 `bash scripts/safe_deploy.sh backend/app/... --restart` / 마이그 동반 시 `--migrate`
  - 프론트 `bash scripts/safe_deploy.sh --frontend`
- 테스트: `cd backend && python3 -m pytest tests/ -q` (**4,531 passed**, ~2분) · 프론트 `npm run build`
- ★프론트 타입검사는 `npm run build`만 유효(`tsc --noEmit`은 0개 파일 검사)
- prod DB 조회: `ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && sqlite3 ohisell.db \"...\""`
- ★**`alembic/env.py`는 `DATABASE_URL`을 무시하고 `alembic.ini`의 `sqlalchemy.url`을 읽는다** — 환경변수로 임시 DB를 지정해도 로컬 dev DB가 올라간다(LESSONS #109).

## 2. 이번 세션 완료 목록

### ✅ 상품명 표시 (D-15, main `2e06e36`)
- 마이그 `d7c1a9e35f42`: `coupang_ad_option_daily.ad_product_name`·`conv_product_name`(nullable).
- 파서(`backend/app/routers/ad_costs.py`): XLSX [7]`광고집행 상품명`·[9]`광고전환매출발생 상품명` 적재. 컬럼은 **전체 어구로** 탐색("광고집행 상품명"은 "광고집행 옵션ID"와 접두가 같다). 빈 이름이 있는 이름을 덮지 않고 `'-'`는 이름이 아니다. 결과에 `option_named_rows`(0이면 헤더 변경 신호).
- **라이브**: 페처 1회 실행 → `option_named_rows: 11781`(전량), prod 11,781행, 화면 표시 확인.

### ✅ alembic 갈래 병합 (main `c6302c6`·`3c7a703`·`6fbdaaf`)
- prod head가 `rg9billed7c4e`(RG S9)인데 **그 파일이 main에 없었다**(미병합 브랜치에서 배포). main head는 `c4a7e2b91d63`(네이버 APPLY_TM). 같은 부모에서 두 갈래 → 그대로 얹으면 prod head 2개 → **모든 세션의 `alembic upgrade head`가 막힌다**.
- RG 세션의 머지 리비전 `mrg9b1c4a7e2` + 부모 파일 **2개만** main으로 가져와 재부모(Jino 승인).
- `models.py`는 CAS 거부 → prod 실배포본을 main 역사에 먼저 커밋 후 **세 세션 컬럼의 합집합**으로 배포.

### ✅ 상품(SKU)별 손익 (D-16, main `1fe315f`)
- **그레인을 옵션이 아니라 SKU로**: 매출(발주 라인)·원가는 상품번호 그레인, 광고비만 옵션 그레인인데 sku→option이 1:N(실측 최대 3, 기간 겹침) → 옵션 축은 안분(추정) 강제. SKU로 올리면 광고비 **더하기**만 남는다.
- 마이그 `e5b3f28a91c7`: `coupang_rocket_option_sku`(옵션ID↔상품번호 브리지) 신설 + **기존 관측 시드**(prod 244행/219 SKU). 적재 시 누적(`_observe_option_sku`), sku 빈 재수신은 기존 값을 안 지운다.
- 순이익은 **모르면 내지 않는다**(None + `profit_basis`).

### ✅ 원가 정본을 sellc로 (D-19, main `4693369`·`6b97a1c`) ★이번 세션의 핵심
- **우선순위: ①sellc 등록원가(`product_channel_mapping`) ②rocket 자동매핑(폴백) ③ignored=0 ④미상.**
- 계정 `net_profit`과 SKU 손익표가 **같은 해석기**(`_resolve_unit_cost`)를 쓴다.
- 출처를 행마다(`cost_source`)·커버리지마다(`ad_cost_sellc/auto`, `cost_by_source`) 남긴다.
- **라이브(2026-08-04 09:38 화면 확인)**: 커버리지 `상품 연결 98.1% · 매출 도달 95.1% · 순이익 도달 88.2% (sellc 80.2% · 자동추정 7.6%)`, 자동추정 원가에 `추정` 배지, 62922000 적자(−199,954원) 표시.

### ✅ 원가 매핑 사고 → 정정
- 어제 확정한 5건이 **자동 매칭 결과를 규칙으로 오독**한 산물이라 **전부 제거**. 상세는 §5.

## 3. 확정된 결정사항
- **D-15** 옵션 표 상품명 — XLSX가 실어 온 라벨을 적재 시점에 보존(1P Retail은 `coupang_product_item`에 없어 조인 불가).
- **D-16** 손익 그레인 = **SKU(상품번호)**. 브리지는 일급 테이블에 누적(판매분석 무료체험 08-20 종료 대비).
- **D-17 → 정정됨**(§5 참조).
- **D-18** 1P 원가의 96%가 이름 유사도 자동 매칭 위에 있었다 — 별도 감사 대상.
- **D-19** 원가 정본 = **sellc 등록원가**. 자동매핑은 폴백이고 출처를 표시한다.
- Jino 원문: "그래, 진행하자"(D-15) · "그래, 그 형태로 진행해"(D-16) · "원가 매핑 5건 확정해줘" · "잘못된 것도 있는데?" · "39017747 이것도 원가가 3400원이야" · "환율로 인해서 조금 바뀌었을 수 있어" · "원가는 sellc에 있는 원가를 사용하자" · "내가 직접 넣을게"(4건 연결)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|---|---|
| `backend/app/services/coupang/rocket_intelligence.py` | `_rocket_sku_pnl`(SKU 손익) · `_sellc_cost_by_product_number` · `_resolve_unit_cost`(원가 출처 우선순위) · `_rocket_cost`(계정 원가축) |
| `backend/app/services/coupang/rocket_promo_sync.py` | `_observe_option_sku` — 브리지 누적 보존 |
| `backend/app/routers/ad_costs.py` | XLSX 파서(상품명 적재·`options_only`·구조 가드) |
| `backend/app/models.py` | `CoupangRocketOptionSku`(브리지) · `CoupangAdOptionDaily.*_product_name` |
| `frontend/src/pages/CommandCenter.tsx` | 「💵 상품별 손익」표(출처 배지·커버리지 분해) · `pct1`(이미 %인 값 전용) |
| `backend/tests/test_rocket_sku_pnl.py` | 안분 부재·중복계상·원가 출처 우선순위 등 15건 |

## 5. 알려진 이슈 / 주의사항

### ★원가 매핑 사고 (반드시 읽을 것)
- 어제 "형제 확정 매핑이 만든 규칙을 따랐다"며 5건을 확정했으나, 그 선례들이 전부 `match_method='suggested'` + `note='auto score=0.56~0.78'` — **2026-06-17 23:25~23:31 6분 배치의 이름 유사도 자동 결과**였다. 자동 결과를 사람의 판단으로 오독하고 그 위에 쌓았다(LESSONS #117).
- 5건 전부 제거함. sellc가 3건을 자동으로 덮었고, 남은 4건은 아래 연결 대기.
- `match_method='manual'`도 신뢰 신호가 아니다 — ignored 22건이 같은 6분 배치다. **진짜 사람 결정은 `69411570` 하나뿐.**

### ★Jino가 직접 넣기로 한 옵션↔마스터 연결 4건 (미완료)
연결 키는 상품번호가 아니라 **옵션ID**. 화면: `/product-connection-map`

| 옵션ID | → 마스터 | 원가 | 상품 |
|---|---|---|---|
| `93373791456` | `OHI-0736` | 3,300 | 아이폰17프로 강화유리 2p+EZ툴 (형제가 0734·0735·0737, 연번 빈칸) |
| `95752961189` | `OHI-Z-LOWREF6-FOLD8-WIDE` | 6,090 | Z폴드8 무광택 |
| `95752961188` | `OHI-Z-LOWREF6-FOLD8-ULTRA` | 6,090 | Z폴드8울트라 무광택 |
| `95752961187` | `OHI-Z-LOWREF6-FLIP8` | 3,480 | Z플립8 무광택 |

- 폴드8/플립8은 형제(폴드7 `62922000`→`OHI-0472` 6,255 / 플립7 `62921998`→`OHI-0447` 3,480)가 **매트 6매 구성**(외부3+내부3)에 붙어 있다는 근거. 실제 판매가 2매입이면 `OHI-Z-PRIVACY-*`가 맞다 — **Jino 확인 사항.**
- 넣은 뒤 검증할 것: 원가 커버리지 76.14% → 96%대, 계정 순이익 변화가 새 원가와 원 단위 일치, 4개 상품 흑/적자.

### 기타
- **계정 순이익이 세션 중 크게 움직였다**: 38,230,542 → (매핑 확정) 32,805,918 → (롤백) 37,727,478 → (sellc 전환) **34,555,757**. 전부 원가 변화분과 원 단위 검산 완료 — 결함이 아니라 그동안 원가가 빠져 **순이익이 과대**했던 것의 정정.
- **★사업 신호**: `62922000`(폴드7 무광택)이 sellc 원가로 **적자**다(자동매핑 원가로는 흑자로 보였다). 원가 출처 하나가 흑자를 적자로 뒤집는다.
- 브리지 미연결 광고비 308,434원(190개 옵션) — 판매분석에 한 번도 안 잡힌 옵션들.
- 판매분석 "Basic 무료체험" **08-20 종료 예정**(D-CPP-5) — 브리지 신규 관측이 멈춘다. 이미 누적된 244건은 유지.
- **병행 세션이 같은 폴더에서 main에 커밋한다.** 이번 세션에 `LESSONS_LEARNED.md`·`models.py`·`claude-progress.txt` 충돌 3회, 번호 중복 2회(106~108, 76) 발생 — **양쪽 보존 + 재번호**로 처리. `git add -A` 금지, 내 파일만 스테이징할 것.
- codex 쿼터 리셋 `2026-08-09 16:16` — 이번 PR들 소급 교차 리뷰 대상.

## 6. 다음에 할 작업 (미완료)
- [ ] **[1순위] Jino의 4건 연결 후 검증** — §5 표 참조. 커버리지·순이익 검산.
- [ ] **자동매핑 잔여 12.8%(원가 2,165,476원) 정리** — sellc에 등록하면 자연 해소. 금액 큰 순으로 목록화.
- [ ] 브리지 없는 190개 옵션(광고비 308,434원) — 판매분석 미관측분 처리 방침.
- [ ] `39017749`·`39017751`·`39017754`는 sellc 연결이 있어 자동 해소됐는지 확인(어제 2,516 자동매핑이 문제였음).
- [ ] 0.02% 옵션합계 vs 계정총액 차이 원인 규명(이월).
- [ ] ①SSO 상시 실패 해소 — Chrome 상주 또는 `storage_state` 보존(이월).
- [ ] `scheduler_health` 제외 스트림 0으로 + WING1/2 red 판정(이월).
- [ ] codex 08-09 리셋 후 소급 교차 리뷰.

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_rocket-sku-pnl+cost-source_20260804.md 읽고 이어서 작업해줘
```
