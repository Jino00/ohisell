# 세션 인수인계: 1p-ad-gap-refill + nca-split

> 저장일시: 2026-08-06 저녁 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
  (루트 폴더는 **main 고정** — 브랜치 작업은 `.claude/worktrees/`)
- 이 세션 워크트리: `.claude/worktrees/cost-bridge-measure` · 브랜치 `claude/ref47-cost-bridge-measure`
- prod: `https://sellc.ohitech.co.kr` · 서버 `sellc.ohitech.co.kr:/home/ubuntu/ohisell`
- 백엔드 테스트: `cd backend && python3 -m pytest tests/...`
- 환경변수: `backend/.env`(`DATABASE_URL` 등) — 값은 저장소 밖
- **이 세션은 prod 배포를 하지 않았다** — 데이터 적재는 소급 도구로 DB에 직접 upsert했고
  코드 배포는 없었다(백엔드/프론트 코드 변경 자체가 없음, 도구 스크립트만).

## 2. 이번 세션 완료 목록
- ✅ **2026-03-17~06-17 1P 광고비 결손 64,349,348원 발견·적재** (D-CPP-16). D-CPP-14/D-21이
  메운 2025-07-24~2026-03-16 결손 뒤에 또 다른 결손 93일이 있었다. 소급 도구의
  `GAP_END="2026-03-16"`과 "라이브는 2026-05-18~"이라는 전제 사이의 구멍. 그 구간 Billboard
  판매방식 100% Retail(3P 0%) + NCA도 2026-04·05·06 전부 100% Retail이라 3P가 얹히지 않아
  `report/SALES` ALL 전액으로 93일 적재. 2026-05-28~06-17은 행은 있는데 값이 거의 0이라 결손
  카운터가 "있다"고 세어 63일간 아무도 못 봤다.
- ✅ **비-PA=NCA 정체 규명 + Retail분 4,199,736원 가산** (D-CPP-17). `reportType='nca'` 보고서의
  「판매방식」 컬럼을 실측하면 열 달 전부 NCA 합계가 원천 비-PA와 0.09% 안에서 일치. Retail 비중은
  달마다 뒤집혀(2025-07 0%~2026-01 0%, 중간에 94.5%·100%·53.7% 등) 비례배분 불가 → 실측으로만
  가른다. 소급 구간 Retail분 4,199,736원 + 전환매출 8,245,140원 같이 가산(축 불일치로 RoAS
  인위적 저하 방지).
- ✅ **ref 46 기술 오류 정정**: "Billboard ReportType enum은 pa·da 둘뿐"은 틀렸다. enum 오류
  메시지가 유효값을 알려준다 — "DA","NCA","PA","da","nca". NCA도 보고서로 받힌다.
- ✅ **옵션ID별 광고비 401일 전수 소급** (D-CPP-18). 2025-07-01~2026-08-05 401일 전부, 빠진 날
  0. 178,956행(Retail 131,935 / 3P 47,021). `options_only=True` 경로라 계정 총액 머니 테이블에
  영향 없음.
- ⏸️ **원가 브리지(ref 47)는 측정만 하고 보류** — 커버리지 68.03%, confirmed 184건 중 183건이
  이름 유사도 자동 매핑(교훈 #117이 경고한 패턴), 결정적 브리지와 매출의 55.1%에서 불일치.
  Jino 지시로 원가표 재업로드 대기 상태로 착수 안 함.
- ✅ 트랙 파일 `docs/tracks/active/track_coupang-promo-pnl.md`에 D-CPP-16~18 기록.

## 3. 확정된 결정사항
- **D-CPP-16**: 2026-03-17~06-17 결손 64,349,348원을 `report/SALES` ALL 전액으로 적재한다 —
  이 구간은 Billboard·NCA 둘 다 100% Retail이라 3P가 안 얹힌다는 실측 근거가 있다.
- **D-CPP-17**: 비-PA는 NCA(`reportType='nca'`)와 동일 개념이고, 판매방식(Retail/3P)은
  월별 비례배분하지 않고 「판매방식」 컬럼 실측으로만 가른다. 광고비를 올릴 땐 같은 축의
  전환매출도 같이 올린다(D-CPP-2: 소비자가/회계매출 축 분리 취지 유지).
- **D-CPP-18**: 옵션ID별 광고비는 `options_only=True`로 소급해 계정 총액(머니) 테이블과
  분리 유지한다 — 옵션 분해가 계정 합계를 흔들면 안 된다.
- **원가 브리지는 이번 세션 스코프가 아니다** — Jino의 원가표 재업로드가 선행 조건.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `tools/ohitech_ad_option_backfill.py` | 옵션ID별 광고비 월별 소급 도구(`options_only=True`) |
| `tools/ohitech_nca_report.py` | NCA(비-PA) Billboard 보고서 다운로드·측정 |
| `tools/ohitech_nca_daily.py` | NCA 일별 집계·가산 도구 |
| `docs/references/46_rocket_1p_ad_cost_gap_20260805.md` | 1P 광고비 결손 조사 기록. §5-1④에 NCA enum 정정 포함 |
| `docs/references/47_rocket_1p_cost_bridge_measurement_20260806.md` | 원가 브리지 측정 결과(보류 상태) |
| `docs/tracks/active/track_coupang-promo-pnl.md` | 이 트랙의 단일 진실 원천. D-CPP-16~18 |

## 5. 알려진 이슈 / 주의사항
- ①`ohitech_ad_option_report.py`의 `gql()`이 fetch 실패를 빈 응답으로 받아 "캠페인 0개"로
  출력한다 — **세션 죽음이 데이터 없음으로 둔갑**한다. 이번 세션에서 9개월 구간이 조용히
  건너뛰어진 적이 있었다(재실행으로 회복). 재발 방지를 위해 실패와 "진짜 0개"를 구분하는
  가드가 필요.
- ②NCA 보고서를 한 달 통으로 요청하면 72MB가 오는데 zip이 아니다 — base64 경로가 큰 파일을
  못 견딘다. 주 단위로 쪼개서 받아야 안전하다(이번 세션은 쪼개서 우회).
- 원가 브리지(ref 47)는 confirmed 184건 중 183건이 이름 유사도 자동 매핑이라 신뢰도가 낮다
  (교훈 #117: 자동 매핑 금지 패턴과 동일 계열의 위험).
- 오픽스 RG 배선(계약 합격기준① 유일 미충족, 라이브 −13,869,712원)은 Jino가 오하이테크 완료
  후로 명시적으로 미뤘다 — 이번 세션 스코프 밖.

## 6. 다음에 할 일
- [ ] (a) **원가 브리지 전환**(ref 47) — Jino 원가표 재업로드 대기. 현재 커버리지 68.03%,
      confirmed 184건 중 183건이 이름 유사도 자동 매핑, 결정적 브리지와 매출의 55.1%에서
      불일치 상태라 그대로 쓸 수 없다.
- [ ] (b) **오픽스 RG 배선**(계약 합격기준① 유일 미충족, 라이브 −13,869,712원) — Jino가
      오하이테크 완료 후로 지시. 갈림길 2개 Jino 답변 대기(RG를 1P처럼 별도 leaf로 뺄지 vs
      오픽스 채널 행에 합칠지 / 정산비용 귀속을 일할 배분할지 현행 유지할지).
- [ ] (c) `ohitech_ad_option_report.py` 조용한 실패 수정 — `gql()`이 fetch 실패를 빈 응답으로
      삼키지 않도록 예외를 명시적으로 올리게 고칠 것.
- [ ] (d) `ohitech_nca_report.py` 큰 파일 다운로드 수정 — 월 단위 요청 시 72MB 비-zip 응답을
      못 받는 문제. 주 단위 분할을 도구 자체에 내장할지 판단.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_1p-ad-gap-refill+nca-split_20260806.md 읽고 이어서 작업해줘
