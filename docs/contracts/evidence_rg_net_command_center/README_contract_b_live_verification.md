# 계약 ⓑ 라이브 검증 — 종합조망 RG 매출 == 대시보드 RG 매출 (D-CPP-49)

> 관측: **2026-08-22 17:43~17:45 KST** · prod `https://sellc.ohitech.co.kr` · 배포 커밋 `05855acf`
> 세션 `d8e6ddbb` (사슬 「쿠팡-손익정합」 n=3) · 트랙 `docs/tracks/active/track_coupang-rg-net-ledger.md`

## 합격기준 원문
> `- [ ] ⓑ 같은 창에서 종합조망 RG 매출 == 대시보드 RG 매출(둘 다 net)`

## 관측 (창 2026-08-05 ~ 08-20)

| 계정 | 종합조망 `revenue_rg` | 대시보드 RG 행 | 일치 | gross였다면 | 옵션축 |
|---|---|---|---|---|---|
| WING1 오픽스 | **3,152,860** | **3,152,860** | ✅ | 3,651,030 | 16/16 complete |
| WING2 오하이테크 | **30,000** | **30,000** | ✅ | 53,900 | 16/16 complete |

**「gross였다면」 열이 이 작업의 전부다** — 종전 종합조망은 저 값을 매출로 쓰고 있었고, 대시보드는
왼쪽 값을 쓰고 있었다. 같은 화면이 RG 매출을 두 값으로 말했다.

### 교차검산 — 회사 소계
- 개인회사 오픽스: 종합조망 `revenue` **3,271,660** == 대시보드 회사 행 **3,271,660**
  (= RG 3,152,860 + 3P 118,800). 행에서도 소계에서도 두 엔진이 같은 말을 한다.
- 주식회사 오하이테크는 회사 행이 다르다(695,800 vs 51,937,600) — **정상이다.** 대시보드 회사
  행은 로켓배송 1P(48,965,400)와 자사몰(2,268,900)을 포함하는데, 종합조망은 옵션 grain이라
  1P(PO grain)를 안 담는다(별도 블록). ⓑ가 묻는 것은 **RG 행**이고 그건 위 표에서 일치한다.
  ⚠️다만 그 창의 3P가 종합조망 665,800 vs 대시보드 673,300으로 **7,500원 차이**가 있다 —
  RG와 무관한 선행 사항이고 이번 계약 범위 밖이라 **고치지 않고 적는다**(이월).

## 이관한 감시 신호가 살아 있는가
축을 net으로 옮기면 RG 드리프트는 «같은 축끼리의 비교»가 되어 0이 된다 — 그 0은 「정합」이 아니라
「같은 숫자를 두 번 읽었다」다. ref 18이 재던 신호는 이름이 다른 칸으로 옮겼고, 라이브에서 값이 나온다:

```
rg_same_axis          : True
ours.revenue_rg       : 3152860        drift.pct_rg       : 0        ← 같은 축(0이 정상)
ours.revenue_rg_gross : 3651030.00     drift.pct_rg_gross : +15.80%  ← 진짜 수집 간극
```

## 재현 명령 (읽기 전용)
```bash
AUTH=$(cat ~/.ohisell_prod_auth)
curl -s -u "$AUTH" "https://sellc.ohitech.co.kr/api/overview/command-center?from=2026-08-05&to=2026-08-20&account=COUPANG_WING1" \
  | python3 -c "import json,sys; s=json.load(sys.stdin)['account']['summary']; print(s['revenue_rg'], s['revenue_rg_gross'], s['rg_option_axis_days'])"
curl -s -u "$AUTH" "https://sellc.ohitech.co.kr/api/dashboard/channel-breakdown?date_from=2026-08-05&date_to=2026-08-20" \
  | python3 -c "import json,sys; print([r['revenue'] for r in json.load(sys.stdin) if '오픽스 · 쿠팡 로켓그로스' in r['label']])"
```

## 이 증거가 답하지 못하는 것 (자백)
- **브라우저 실렌더 스크린샷은 없다.** HTTP 응답까지만 확인했다. 프론트는 이 값들을 렌더하는
  테스트 11종(`rgNetAxisSurface.test.tsx`)으로 덮여 있으나, 실제 픽셀은 Jino가 화면에서 본다.
- 창을 08-05~08-20 하나만 봤다(계약 ⓐ와 같은 창). 다른 창·다른 커버리지 조건은 미관측.

## 원자료
같은 디렉터리의 `command-center_wing1_*.json` · `command-center_wing2_*.json` ·
`channel-breakdown_*.json` · `revenue-reconcile_wing1_*.json` (전부 prod 원 응답 그대로).
