# LESSONS_LEARNED.md — ohisell 프로젝트 학습 기록

## 1. Python 3.14 + SQLAlchemy 호환성 이슈

### 이슈
SQLAlchemy 2.0.40에서 `Mapped[str | None]` 사용 시 `TypeError: descriptor '__getitem__' requires a 'typing.Union' object` 에러 발생. Python 3.14의 typing 내부 변경으로 `Union.__getitem__` 동작이 달라짐.

### 해결
1. SQLAlchemy 2.0.48로 업그레이드
2. `from __future__ import annotations` 추가
3. `str | None` 대신 `Optional[str]` 사용

### 교훈
Python 3.14는 아직 최신이라 라이브러리 호환성 이슈가 있을 수 있음. SQLAlchemy는 반드시 2.0.48 이상 사용할 것. 새 Python 버전 사용 시 첫 마이그레이션에서 호환성을 바로 검증해야 함.

## N. 조망 원가 0 — "데이터 없음"이 아니라 "엉뚱한 원천" (2026-06-03, D-12)

### 🐛 이슈
종합조망 순이익에 원가가 거의 미반영(253옵션 중 201옵션 0원). "쿠팡 supplyPrice가 빈값이라 어쩔 수 없다"고 단정할 뻔함. 새 페이즈(P3/P5)를 더 쌓아도 이 구멍은 그대로였을 것.

### ✅ 해결
코딩 전 라이브 진단(읽기전용 SQL, 서버 DB)으로 원인을 사실로 확정 → 원가는 **이미 내부 product_master.cost_price에 792상품(89%) 있었고**, product_channel_mapping(coupang) 다리로 실거래 118옵션(66%)에 닿았다. 결합엔진이 coupang supply_price(0.6% 커버)만 보던 게 원인. 엔진 읽기측 조인을 내부 원가 다리로 바꿔 원가 0→468,313 반영(순이익 과대계상 교정). 신규 테이블·쿠팡호출 0.

### 📌 교훈
"데이터가 없다"고 단정하기 전에, **다른 원천에 이미 있는지 라이브로 진단**하라(원칙22). 특히 같은 시스템이 다른 경로(기존 회계엔진 profit_calculator)로 이미 그 데이터를 쓰고 있으면 거의 확실히 어딘가 있다. 진단 스크립트(diag_coverage.py·diag_bridge.py)로 "현재 원천 커버리지 vs 잠재 원천 커버리지"를 숫자로 비교하면 헛수고를 막는다. 부수: 확정 결정(D-10 saleAgentCommission 기준선)도 라이브에서 전제가 깨질 수 있으니(전부 0) 실데이터로 검증.

## N. 감사 "이상 0건"이 정상이 아니라 "비교 0건"일 수 있다 (2026-06-03, D-13)

### 🐛 이슈
P4 수수료 감사가 "anomaly 0"이라 잘 작동하는 줄 알았으나, 실제로는 기준선(saleAgentCommission)이 201옵션 전부 0이라 `registered<=0`에서 즉시 스킵 → **비교가 한 건도 안 일어남**. "0 이상"은 "전부 정상"이 아니라 "감사 부재"였다.

### ✅ 해결
기준선 데이터 실태를 라이브로 확인(전부 0). 기준선을 옵션 자기 정착 실측율(service_fee_ratio mode)로 교체(D-13). 검증 시 "정상 데이터→0" 뿐 아니라 **합성 이상 주입→플래그 1**을 반드시 확인해 감사가 실제로 비교하는지 증명. stats에 fee_options_checked(비교 시도 수)를 추가해 "비교 0건"과 "비교 후 0"을 구분 가능하게 함.

### 📌 교훈
감사/검증 로직의 "이상 0건" 결과는 **비교가 실제로 일어났는지** 먼저 확인하라(원칙22). 0이 나오면 "정상 0"인지 "스킵 0"인지 구분하는 카운터(시도 수)를 둬라. 검증은 정상→음성뿐 아니라 **합성 양성 주입→양성**으로 탐지력을 증명해야 한다(원칙14). 확정 결정(D-N)의 전제도 라이브에서 깨질 수 있으니 기준선 데이터 실태부터 본다.

## 4. 네이버 클레임 쓰기 API 스펙 함정 — 철자·필드형이 직관과 다름

### 🐛 이슈
N7 wave2 반품 5종 구현 중, 네이버 API센터 실측 스펙이 직관과 어긋나는 지점이 있었다.
- 반품 보류 유형 enum에 `EXTRAFEEE`(E 3개)가 있다 — 오타처럼 보이지만 **원문 그대로**다. "고쳐서" EXTRAFEE로 보내면 거부될 위험.
- 반품 거부 사유 `rejectReturnReason`은 **자유 텍스트**인데, 취소 직접요청 `cancelReason`은 **enum**이다. 같은 "클레임 사유"라도 엔드포인트마다 enum/자유텍스트가 갈린다.
- 반품 수거 배송방법 `collectDeliveryMethod` enum은 발송용 `deliveryMethod`와 달리 RETURN_DELIVERY/RETURN_INDIVIDUAL/RETURN_MERCHANT/UNKNOWN을 포함 — 발송용 상수를 재사용하면 안 됨(별도 _VALID_COLLECT_DELIVERY_METHODS 신설).

### ✅ 해결
전부 API센터 스크린샷 실측만 사용(추측 금지). docs/references/14에 enum·철자 원문대로 기록(EXTRAFEEE 옆에 "★철자 원문대로" 주석). 수거 배송방법은 발송용과 별도 상수로 분리. 빌드/임포트/codex(P1·P2 0)/prod dry_run·검증400으로 확인.

### 📌 교훈
외부 API enum은 "오타로 보여도 고치지 말고" 실측 그대로 쓴다(원칙: 추측 금지). 비슷한 이름의 필드(cancelReason vs rejectReturnReason, deliveryMethod vs collectDeliveryMethod)라도 형(enum/자유텍스트)·허용값이 다를 수 있으니 **엔드포인트별로 스펙을 따로 확인**하고 상수를 재사용하지 마라. 스크린샷이 길어 잘리면(택배사 100+ 테이블 아래 송장/수량 필드) "이게 끝인가" 단정 말고 잘린 뒷부분을 반드시 확인한다.

## 5. 네이버 교환 쓰기 API — 반품과 닮았지만 필드명·제약이 미묘하게 다름

### 🐛 이슈
N7 wave3 교환 5종 구현 시 반품(wave2)과 구조가 거의 같아 그대로 복사하고 싶은 유혹이 있었으나, 실측 스펙이 미묘하게 달랐다.
- 경로가 `claim/exchange/*` (반품은 `claim/return/*`). 수거완료는 `claim/exchange/collect/approve`로 한 단계 더 깊다.
- 교환 보류 상세사유 필드명 = `holdbackExchangeDetailReason`, 추가비용 = `extraExchangeFeeAmount` (반품은 holdbackReturnDetailReason / extraReturnFeeAmount). holdbackClassType enum만 동일.
- 교환 재배송(dispatch)은 reDeliveryMethod/Company/TrackingNumber 3필드가 **전부 optional**(BODY는 required이나 개별 필드는 REQUIRED 배지 없음). N6 발송(dispatch)은 DELIVERY에 택배사+송장 필수였던 것과 대조.

### ✅ 해결
필드명은 실측대로 분리(Exchange 접미사). codex가 "재배송에 DELIVERY 시 택배사+송장 XOR 강제는 스펙에 없는 앱 제약"이라 지적 → 대화형 검증 후 합의 수용(원칙19): N6 dispatch와 다른 엔드포인트이고 실측상 전 필드 선택이므로 XOR 제거, 부분입력은 그대로 보내고 네이버가 검증. enum 상수(holdbackClassType, deliveryMethod)는 반품 것 재사용.

### 📌 교훈
"비슷한 API"일수록 **복붙 후 필드명·경로·필수여부를 1:1로 재대조**하라. 같은 개념(보류 상세사유)도 클레임 종류마다 필드명이 다르다(Return/Exchange). 한 엔드포인트(N6 발송)에서 필수였던 조건이 유사 엔드포인트(교환 재배송)에선 선택일 수 있으니 이전 구현의 제약을 그대로 이식하지 마라 — 스펙에 없는 제약은 유효 요청을 막는다. codex가 "스펙에 없는 앱 제약"을 지적하면 추측금지 원칙상 대체로 수용이 맞다.

## 6. 네이버 N8 상품 쓰기 — prod 실측으로 범위를 줄이는 게 안전 (옵션 미사용·가격 묶임)

### 🐛 이슈
N8 "재고·가격·수정·등록 전부" 요청을 그대로 다 구현하려다, API센터 스펙과 prod 데이터를 보고 두 가지를 발견.
- "상품 옵션 재고 변경"(option-stock) API는 이름과 달리 `salePrice`가 REQUIRED라 재고만 바꿔도 가격을 함께 보내야 함 → read-modify-write 안 하면 가격 손실 위험. 게다가 옵션별(optionCombinations/optionStandards) 페이로드 필요.
- prod 상품 1,202개 실측: 오하이는 원상품(origin_product_no)당 재고 1개, 변종은 group_product_no로 묶인 **별도 원상품**(= 옵션 미사용). 옵션별 재고 API가 애초에 불필요.
- 반면 "판매 상태 변경"(change-status)은 가격을 전혀 안 받고 원상품 단위 재고/상태(SALE/OUTOFSTOCK/SUSPENSION)를 처리 → 품절/재입고/판매중지를 가격 위험 0으로 커버.

### ✅ 해결
Jino와 대화로 범위를 2단계 축소(D-11): 수정·등록 제외(라이브 상품 손상 위험) → 옵션재고도 제외(미사용+가격위험) → **change-status만** 구현. codex 1차 P1×2/P2×3 전부 합의 수정(OUTOFSTOCK→재고0 강제, SALE→재고≥1, 클라 enum allowlist, 전이규칙 위반까지 막는 NAVER_STATUS_TRANSITIONS, 타입 union) → 2차 pass. prod dry_run 7케이스 라이브 통과.

### 📌 교훈
"전부 구현해줘" 요청이라도 **API 스펙 실측 + 실데이터 프로브로 진짜 필요/위험을 먼저 확인**하면 범위가 줄어든다(원칙22). 특히 외부 쓰기 API는 이름(옵션 재고 변경)과 실제 동작(가격까지 필수)이 다를 수 있으니 필수 필드를 꼭 본다. 같은 목적(재고 관리)을 더 안전한 엔드포인트(가격 안 받는 change-status)로 달성할 수 있으면 그쪽을 택한다. 전이 규칙이 있는 상태머신 API는 현재상태별 유효 전이만 UI에 노출해 무효 요청(네이버 400)을 사전 차단한다.

## 7. E1a expert_desk(T1~T9) — SQLAlchemy 스크립트 함정 + 프론트 라이브 프리뷰 안전 절차

### 🐛 이슈
- 독립 파이썬 스크립트에서 `from app.database import Base, engine; Base.metadata.create_all(engine)`만 실행하면 **테이블이 0개** 생성된다. `app.models`를 명시적으로 import하지 않으면 SQLAlchemy declarative 등록(모델 클래스가 `Base`에 자신을 등록하는 부수효과)이 일어나지 않아 `Base.metadata`가 비어 있다. pytest에서는 항상 `from app.models import X, Y`를 같이 import해서 이 함정을 못 느꼈다.
- 프론트 시각 검증을 위해 `app.main:app`(전체 앱)을 그대로 띄우면 `lifespan`이 `start_scheduler()`를 호출해 real APScheduler가 진짜 크론(외부 API 크레덴셜 사용)까지 등록한다 — 스크래치 DB로 프론트만 확인하려던 의도와 달리 실제 외부 호출 위험을 안게 된다.
- `mcp__Claude_Preview__preview_start`용 `.claude/launch.json`은 **작업 대상 워크트리가 아니라 이 세션 자체의 워크트리**(`.claude/worktrees/<session>/.claude/launch.json`)에 있어야 인식된다 — 코드가 있는 워크트리에 만들면 "launch.json 없음" 에러가 난다.

### ✅ 해결
- 스크립트에서 `Base.metadata.create_all` 전에 반드시 `from app import models  # noqa: F401`을 먼저 import.
- 프론트 프리뷰는 필요한 라우터만 마운트한 미니 FastAPI 앱(`app.include_router(naver_ad.router)`만, `app.main`의 `lifespan`/스케줄러 없이)을 임시로 만들어 사용 — 스크래치 DB에 대해서도 외부 API 부작용이 전혀 없게 격리.
- launch.json은 `runtimeExecutable`을 다른 워크트리 경로의 wrapper 스크립트나 `bash -c "cd '<target>' && ..."`로 지정해, 세션 워크트리에 두고 실제 코드는 작업 워크트리를 가리키게 한다.
- prod 사본 검증 시 대상 테이블(NaverProposal)이 비어 있으면 "생성 파이프라인 자체가 prod에 배포된 적 없다"는 신호일 수 있다(추정 금지 — 코드 버그로 오판하지 않고 먼저 배포 이력을 재확인) — 이럴 땐 상위 파이프라인(proposal_pipeline)을 같은 사본에서 먼저 돌려 실제 데이터를 만든 뒤 하위 기능(expert_desk)을 검증한다(89K 재검증 때와 동일 원칙).

### 📌 교훈
독립 스크립트로 SQLAlchemy 모델을 다룰 때는 **모델 모듈을 명시적으로 import했는지 항상 확인**한다(pytest의 암묵적 import에 기대지 마라). 스크래치 DB로 프론트를 검증할 때도 백엔드 진입점을 있는 그대로 쓰지 말고 **필요한 라우터만 마운트한 최소 앱**을 만들어 외부 부작용 표면을 원천 차단한다. `preview_start`의 `launch.json`은 세션 워크트리 기준이라는 것을 기억한다. prod 사본 검증에서 "데이터가 없다"는 관찰은 버그가 아니라 **배포 상태에 대한 실측 정보**일 수 있으니 코드를 의심하기 전에 그 가능성부터 확인한다.

## 8. 워크트리에만 사는 기록물은 조용히 소실된다 — 인덱스에서 지우기 전 원본 실존 확인

### 🐛 이슈
MEMORY.md 압축 작업(2026-07-17) 중, 인덱스 항목 `HANDOFF_ohisell-mop-live-observation_20260711`의 **원본 파일이 어디에도 없는 것**을 발견. 인덱스는 워크트리 `naver-ad-x1b-sprint-a42eb5`에 있다고 기록했고 그 워크트리는 멀쩡히 존재하는데 파일만 없었다(동명의 git 브랜치 ref만 잔존).
- 그 세션의 유일한 기록이 **인덱스 줄 자체**였다. "상세는 원본에 있으니 인덱스는 줄이면 된다"는 전제로 지웠다면 MOP 3계층 요금제·과거 ROAS 실측(1.80/2.31/1.67)·준실험 +8.6%가 영구 소멸했을 것.
- 배경: Jino 지시(2026-07-11)로 인계 기록은 프로젝트 로컬(워크트리 `.claude/memory/`)에 쓴다. 그 결과 고유 149개 HANDOFF가 946개 경로에 흩어져 있고(iCloud ` 2.md` 중복본 포함), 워크트리가 정리되면 사본이 통째로 사라질 수 있다.
- 같은 구조적 취약점: D-NAO-47(설계 스펙 전문)이 미머지 브랜치 `claude/video-content-summary-0e6c41`에만 존재 → 그 브랜치가 버려지면 함께 소멸. main 기준 트랙의 최대 번호는 46이었다.

### ✅ 해결
지우기 전 **전 워크트리 대상 실존 확인을 선행**(`find . -name "HANDOFF_<name>.md"`)하고, 고아로 판명된 1건은 내용 전량을 토픽 파일 `mop-competitor-benchmark.md`로 이전 + "원본 소실·이 파일이 유일 사본"이라고 파일 안에 명시. 인덱스 줄도 "파일 있음"처럼 읽히지 않게 고쳐 씀. 방침 자체는 `ohisell-session-record-policy.md`로 기록.

### 📌 교훈
**인덱스에서 항목을 지우는 것은 "원본이 있다"는 가정에 전적으로 의존하는 행위다 — 그 가정을 매번 검증하라.** 압축·정리 작업의 기본 순서는 ①원본 실존 확인 → ②없으면 내용을 살릴 곳부터 만들기 → ③그 다음에 축약. 순서를 바꾸면 정리가 곧 소실이다. 파일이 여러 워크트리에 흩어지는 프로젝트에서는 "없다"고 단정하기 전에 **워크트리 전체를 검색**한다(한 워크트리에 없는 게 정상). 그리고 **미머지 브랜치에만 있는 기록은 아직 안전하지 않다** — main에 안착해야 durable하다.

## 9. 인덱스가 본문을 머금으면 읽기 한도를 위협한다 — 인덱스는 인덱스로 유지

### 🐛 이슈
MEMORY.md가 **30.6KB까지 비대**해져 24.4KB 읽기 한도를 초과, 매 세션 자동 로드되는 파일이 잘려 읽힐 위험에 도달. 원인은 규칙 위반의 누적 — 메모리 시스템 규칙은 "one line per memory, never put memory content there"인데, 실제로는 HANDOFF 항목마다 수백~수천 자 본문이 들어가 있었다.
- 세션마다 "이건 중요하니 인덱스에 남기자"가 한 줄씩 쌓인 결과. 각 세션 입장에선 합리적이었다.
- 부작용: 같은 사실(MOP 요금제·bidYn·advertiserId)이 6~7개 항목에 중복 서술 → 정정이 일어나도 일부만 고쳐져 **인덱스 안에서 서로 모순**(예: "웜업 미탈출" vs "bidYn=N")이 생김.

### ✅ 해결
30.6KB → 14.1KB(64줄). HANDOFF 항목은 "제목 — 한 줄 훅"으로 축약하고, 여러 항목에 흩어져 중복되던 살아있는 사실을 **토픽 파일로 승격**(`mop-competitor-benchmark`·`naver-ad-profit-spot-bands`·`naver-ad-data-cadence`·`naver-ad-budget-control-policy`·`naver-ad-claimed-vs-wired-gaps`). 최신 3~4건만 훅을 충실히 유지(살아있는 컨텍스트).

### 📌 교훈
**인덱스에 본문을 쓰고 싶어지면 그건 토픽 파일이 필요하다는 신호다.** 사실은 한 곳(토픽 파일)에만 두고 인덱스·HANDOFF는 가리키기만 해야 정정이 한 번에 끝난다 — 같은 사실을 N군데 쓰면 정정이 N군데 필요하고, 반드시 일부가 누락돼 기억이 스스로 모순된다. 인덱스 항목은 "이 세션이 뭘 했는지" 한 줄 + 관련 토픽 링크로 족하다. 상세는 이미 HANDOFF에 있으니 중복이다. 주기적으로 크기를 점검하고(한도 대비), 넘기 전에 압축한다 — 단, 압축 순서는 교훈 8을 따른다.
