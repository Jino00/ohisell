# PLAN — 로켓배송 매출 수동 입력

> Sprint: 4B-rocket-manual-revenue
> 작성: 2026-05-17 (Opus 계획)
> 목적: 쿠팡 로켓배송(위탁) 채널의 일별 전체 매출을 수동 입력하여 대시보드에 반영

## 1. 배경 / Why

- 로켓배송은 위탁판매라 API로 주문을 못 가져옴 (현재 주문 0건)
- 쿠팡 광고센터에 매출 엑셀 다운로드 메뉴 없음 → 일별 화면 숫자를 수동 입력해야 함
- 광고비는 이미 XLSX 업로드(AdCost)로 자동 적재 중 → **추가로 필요한 건 "전체 매출액" 1개뿐**
- 확정 규칙: 로켓배송은 **순이익 계산 제외, 매출만 표시** (원가/수수료 데이터 없음)

## 2. 확정 스펙

| 항목 | 내용 |
|------|------|
| 입력 | 날짜 + 전체 매출액 (1필드) |
| 저장 | 신규 `manual_revenue` 테이블, (channel_id, revenue_date) 유니크 → 재입력 시 덮어쓰기 |
| 표시 | 대시보드 매출에 합산, 광고비는 기존 AdCost대로, 순이익 "—" |
| VAT | 미차감, 입력값 그대로 표시 |
| UI | Settings 페이지에 입력 폼 + 입력 내역 표(수정/삭제) |

## 3. 아키텍처 (Agent / Harness / SA)

```
Agent: 로켓배송 매출 관리 (Settings 페이지 메뉴)
│
├── Harness H1 — 매출 입력/조회 흐름 (Router → SA)
│     ├── SA-1 upsert_manual_revenue(db, channel_id, date, amount, memo)  [쓰기·멱등]
│     ├── SA-2 list_manual_revenue(db, channel_id, from, to)              [읽기·목록]
│     └── SA-3 delete_manual_revenue(db, channel_id, date)                [삭제]
│
└── Harness H2 — 대시보드 집계 병합 (기존 trend/breakdown 확장)
      ├── SA-4 get_daily_manual_revenue(db, from, to, channel_id=None)    [읽기·일별 집계]
      └── 기존 calculate_daily_trend / calculate_channel_summary
            → H2가 SA-4 출력을 Order 기반 포인트에 "매출-only 라인"으로 주입
              (revenue += 입력값, cost/commission/vat/net_profit 불변)
```

원칙 6 준수: SA는 서로 모름. H2(profit_calculator)가 SA-4 출력을 기존 집계에 주입하는 허브.

## 4. 구현 단위 (Sub-Agent 먼저 → Harness → Agent)

### Phase 1 — DB / Model
- `models.py`: `ManualRevenue` 클래스 추가 (channel_id FK, revenue_date, gross_revenue, memo, created_at, updated_at, Unique(channel_id, revenue_date))
- alembic 신규 마이그레이션 (down_revision = `a1c24f0b9d31`)

### Phase 2 — SA (services)
- 신규 `app/services/manual_revenue_service.py`: SA-1/2/3/4 (순수 함수, 단일 책임)

### Phase 3 — Harness 통합
- `profit_calculator.py`: `calculate_daily_trend` + `calculate_channel_summary`에 SA-4 출력 병합 (매출-only, 순이익 미반영)

### Phase 4 — Router (Agent backend)
- 신규 `app/routers/manual_revenue.py`: `POST` / `GET` / `DELETE`
- `main.py`에 라우터 등록

### Phase 5 — Frontend (Agent UI)
- `Settings.tsx`: "로켓배송 매출 입력" 섹션 (날짜+금액 폼, 내역 표, 수정/삭제)

### Phase 6 — 검증
- 입력 → 대시보드 매출 반영 실측 확인
- `/codex review` PASS

## 5. 완료 기준

- 날짜+금액 입력 → 저장 → 같은 날 재입력 시 덮어쓰기 동작
- 대시보드 trend/channel-breakdown에 로켓배송 매출 표시, 순이익 "—"
- 광고비는 기존 AdCost 그대로 (중복/누락 없음)
- 입력 내역 수정/삭제 동작
- codex review PASS

## 6. 범위 밖 (이번 Sprint 제외)

- 이미지/OCR 자동 인식 (수동 입력으로 확정)
- 로켓배송 원가/수수료 계산 (데이터 없음, 영구 제외)
- 다른 채널 수동 매출 입력 (테이블은 범용이나 UI는 로켓배송만)
