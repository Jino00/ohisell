# 세션 인수인계: 인계 목록 전수 실측 + 쿠팡 광고비·로켓1P 정산 신선도 감시 편입 — D-CPP-39

> 저장 2026-08-12 00:2x KST · 트랙: 쿠팡 손익 정합 (`docs/tracks/active/track_coupang-promo-pnl.md`)
>
> ⚠️ **정정(2026-08-12 00:3x)**: 이 파일의 초안은 문서 갱신 서브에이전트가 썼고, 서두에
> 「이 세션은 문서 전담이며 D-CPP-39는 전 세션 작업」이라 적혀 있었다. **사실이 아니다** —
> 그건 그 서브에이전트 자신의 시야였다. **이 세션이 전 과정을 했다**: 인계목록 실측 →
> QA 재실행 → 그 QA에서 발견 → 규칙 2건 구현 → prod 배포 → 적대 리뷰 → PR #285 생성·병합 →
> 문서 기록. 전 세션(`HANDOFF_ohitech-3p-qa+ad-basis_20260811.md`)은 D-CPP-38까지다.
>
> **이 계약은 전부 종결이다(§6 미완 0건).** 아래 §5 「별건」은 이 계약과 무관하게 남아 있는
> 이월 항목이다(고치라는 지시가 아니라 존재 사실 기록 — 교훈 #256).
>
> ★**다음 세션의 작업은 Jino가 지정했다(2026-08-12 00:29)**: *"오픽스 2P, 3P를 오하이테크 3P
> 처럼 진행하자"*. **2P = RG(로켓그로스)**다(`docs/PLAN_S7_net_profit_flip.md:15` 「2P(RG)
> 광고비」 — 추정 아니라 repo 용례). 즉 **오픽스(COUPANG_WING1)의 RG + 3P**를 오하이테크 3P와
> 같은 수준으로 정합·검증하는 것. 착수 전 계약 1장 + Jino 승인 필요(중형+). §6-다음 참조.

---

## 1. 환경

- 작업 위치: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
  (루트, `main` 브랜치 — 이 세션은 워크트리를 쓰지 않았다. 문서만 바꾸는 세션이라 공유 메인
  폴더 pre-commit 훅의 「main 위 커밋에 새 파일」경고만 해당, 브랜치 전환은 없음)
- prod: `ssh sellc.ohitech.co.kr` · 활성 백엔드 포트 `8001`(pid `3737723`, 블루-그린은
  8011↔8001을 번갈아 씀 — `ss -ltnp | grep 800`으로 확인)
- 헬스: `curl http://127.0.0.1:8001/api/scheduler/health`(prod 로컬), 외부에서는 403(§5)
- D-CPP 번호는 `docs/tracks/active/track_coupang-promo-pnl.md`에서 최댓값(**D-CPP-38**)을
  세고 +1 — `scripts/next_ids.sh`는 D-NAO만 준다(스크립트 한계, 이번에 재확인).

---

## 2. 이번 세션 완료 목록

### (a) 인계 목록 전수 실측 — §6 8건 중 4건이 유령
전 HANDOFF(`HANDOFF_ohitech-3p-qa+ad-basis_20260811.md`) §6의 8건을 라이브로 하나씩 쟀다.
진짜 남은 일은 **0건**이었다(상세는 교훈 #256, 이미 그 HANDOFF와 LESSONS_LEARNED에 기록돼
있어 이번엔 재기록하지 않음 — 요약만 §3에 남긴다).

### (b) QA 재실행 — 합격기준 6개 전부 PASS
prod 403이라 `ssh -L 9999` 터널 + prod dist 로컬 서빙으로 무대를 재구성(교훈 #250 절차,
§5-3에 재현 절차 기록). 창: 2026-07-12~08-10, 계정 오하이테크(COUPANG_WING2).
- A 3P 매출 vs 쿠팡 정본: 867,810 == 867,810, 차 0.0000%
- B D-CPP-38 광고 실토: WING2 `applies=false·total=None` / WING1 `applies=true·5,602,012`
- C 계정 등가성: revenue·total_fee·cost·net_profit·revenue_3p·revenue_rg·ad_spend
  7지표 전부 차 0.00
- D 순이익 사슬 잔차 3구간 전부 0 → `net_profit` 464,950.09
- E 3P 원가 커버리지 100%(22/22), 매출 있는 옵션 25개 중 미연결 0건
- F 화면 실렌더: 계정별로 라벨·값(`0원`이 아니라 `—`)·각주 세 곳이 갈림(D-CPP-38 의도대로)

★순이익이 전 세션 오전 QA의 467,399.18 → 464,950.09로 **2,449.09원 감소**했는데 이는
회귀가 아니라 검증이다: 원가 2,694원(EZ툴→`OHI-0225`, §2-a에서 이미 매핑돼 있던 값) −
매입세액공제 244.91원(2,694 × 10/110 = 244.909…) = 2,449.09원. 원가 1건 연결이 순이익과
부가세 양쪽으로 정확히 전파된 것을 재확인했을 뿐, 이 세션에서 새로 연결한 것은 없다.

RG 드리프트 +31.72%(우리 gross 40,700 vs 쿠팡 net 30,900, 차 9,800)는 버그 아님 — 판매분석
RFM 축 08-09가 0원인 것과 D-11의 알려진 gross-vs-net 잔차. 화면이 「취소분」이라 정확히
실토한다(교훈 #255 참조).

### (c) D-CPP-39 — 신선도 감시 편입 (코드는 전 세션 작업, 이 세션은 트랙 기록만)
`docs/tracks/active/track_coupang-promo-pnl.md`에 D-CPP-39로 기록. 요지:
- `DATA_FRESHNESS_RULES`에 `ad_cost`(max_age_days 3.0)·`rocket_settlement`(10.0) 추가
- 임계는 라이브 간격 분포 실측(추정 아님) — rocket_settlement: 90일 `issue_date` 간격
  1일×47·2일×7·3일×4·7일×2(관측 최대 7) → 10.0 = 관측최대7 + 여유3
- 검증: 전체 5410 passed · 변이 3/3 KILLED · 적대 리뷰 1R PASS(P1=0)
- **라이브 재확인(이 세션에서 직접 curl, 2026-08-12 00:22 KST)**:
  `data_stale`에 `{"name":"coupang_ad_cost_sales","account_key":"COUPANG_WING1","state":"stale",
  "age_days":3.0155,"max_age_days":3.0}` 실제로 잡힘. `rocket_1p_settlement`는 아직 나이
  10일 미만이라 조용(설계대로). 배포는 `--restart-legacy` 1회(pid `3711063`→`3737723`,
  ss 출력으로 재확인).
- PR #285, `--force` 병합(CI 전면 정지 — §5-1).

전체 4개 문서(트랙·LESSONS_LEARNED·claude-progress.txt·이 HANDOFF)를 명시적 `git add`로
커밋·push했다(`git add -A` 금지 — 공유 메인 폴더).

---

## 3. 확정된 결정사항 (번복 금지)

- **D-CPP-39**: `ad_cost`(3.0일)·`rocket_settlement`(10.0일) 신선도 규칙 신설. 임계 근거는
  라이브 분포 실측이지 추정이 아니다 — 재조정하려면 같은 방식(간격 분포 재측정)으로만.
- **D-CPP-39**: 두 규칙의 `account_key`는 표시용, 필터는 vendor_id(`_SALES_KEY`,
  `ROCKET_1P_VENDOR_ID` 재사용)로 건다. 기존 4개 규칙(account_key가 실제 필터)과 역할이
  다르다는 것을 코드 주석에 명시(적대 리뷰 P2 채택).
- **교훈 #256 재확인**: HANDOFF §6의 이전 8건 중 4건은 유령이었다 — 재작업 대상 아님.

---

## 4. 핵심 파일

| 파일 | 역할 |
|---|---|
| `backend/app/services/scheduler_health.py` | `DATA_FRESHNESS_RULES` — D-CPP-39가 2건 추가한 곳(코드는 전 세션에 완료, 이 세션은 미변경) |
| `backend/tests/test_ad_cost_rocket_settlement_freshness.py` | D-CPP-39 테스트(전 세션 작성) |
| `docs/tracks/active/track_coupang-promo-pnl.md` | D-CPP-39 결정 기록(이 세션 추가) |
| `.claude/memory/LESSONS_LEARNED.md` | 교훈 #257·#258(이 세션 추가) |
| `claude-progress.txt` | 이 세션 요약 항목(맨 위) |

---

## 5. 알려진 이슈 / 주의사항

### 5-1. CI 전면 정지 — Jino 조치 필요할 수 있음
GitHub Actions 러너가 리포 전체에서 할당되지 않는다(`gh run list --branch main`으로 확인,
최근 5건 전부 `completed failure`, 문서만 바꾼 커밋도 포함). job에 스텝 자체가 없다 —
결제 문제로 정지된 것으로 추정(전 세션 HANDOFF에서도 같은 관측). **CI가 빨간불이어도
그게 이 브랜치의 결함이라는 뜻이 아니다** — INCONCLUSIVE이지 FAIL이 아니다(교훈 #123).
`safe_merge.sh`가 정상 작동하려면 이 문제가 먼저 풀려야 한다. 결제 확인은 Jino 몫.

### 5-2. 403 미해소 — Mac IP 회전(출장 중), 복귀 시 자연 해소 예상
Mac IP가 하루에도 여러 번 바뀐다(전 세션 기록: 203.239.246.21→115.23.234.145→
116.84.110.196→125.227.60.87 등). nginx 데몬 경로 IP 허용목록 예외는 **Jino 결정 사항**
(보안 설정, 모델이 손대지 않음) — 서버에 파일만 올려 두고 적용 대기 상태다. 이 때문에
쿠팡 광고비 등 일부 수집이 지연되고 있고, **그게 정상 동작으로 D-CPP-39의 새 배너가 지금
울리고 있다**(`coupang_ad_cost_sales` age 3.0일, §2-c). 출장에서 복귀해 IP가 고정되면
기존 허용목록으로 자연 해소되고 정체분은 catch-up될 것으로 예상 — **확인 필요, 근거 없음**
(과거 유사 사례에 근거한 예상일 뿐, 이번에 실측된 사실 아님).

### 5-3. 디스크 86.3%(여유 13.2GB) — 이 세션에서 직접 실측(2026-08-12 00:22 KST)
prod 헬스 응답에서 직접 확인: `disk_low` 배너가 이미 `state=low`로 켜져 있다(`used_percent
86.34`, `warn_percent 85.0`, `free_bytes` 약 13.2GB). 08-10 저녁 83%로 정리했던 것이 이후
재상승했다(전 세션 HANDOFF에서도 87%로 관측). 임계(85%)를 살짝 넘긴 상태 — 즉시 위험은
아니나(전 사고는 ENOSPC, 즉 100% 근접) 관찰 대상. 원인 조사·정리는 이번 계약 범위 밖.

### 5-4. QA 무대 재현 절차 (다음 세션이 재사용, 교훈 #250)
이번 세션 scratchpad의 서빙 스크립트는 세션 종료와 함께 사라지므로 **다음 세션엔 없다** —
재작성 필요(약 40줄). 절차:
1. `ssh -f -N -L 9999:127.0.0.1:8001 sellc.ohitech.co.kr`(활성 포트는 `ss -ltnp | grep 800`으로
   먼저 확인 — 블루그린이라 8011/8001을 오간다)
2. `rsync -az sellc.ohitech.co.kr:/home/ubuntu/ohisell/frontend/dist/ <로컬 디렉토리>/`
3. 로컬 정적 서버 + `/api/*`를 9999로 프록시하는 파이썬 스크립트(`http.server` 서브클래스 +
   `urllib.request`로 프록시, 약 40줄)
4. 브라우저로 `http://127.0.0.1:<local-port>/<대상 화면 경로>`

nginx를 거치지 않으므로 **캐시 헤더·IP 허용목록 층은 이 절차로 검증되지 않는다** — 그 층은
별도 확인이 필요하다.

---

## 6. 다음에 할 작업

**(없음 — 이 계약은 완전히 종결됐다.)** D-CPP-39 코드·배포·리뷰·라이브 확인·문서화 전부 완료,
QA 6개 합격기준 전부 PASS, 인계 목록 §6은 실측 결과 0건이었다. 다음 세션이 이 HANDOFF에서
바로 이어받을 «미완 작업»은 없다.

### 별건(이번 계약과 무관 — 트랙 백로그, 착수 여부는 별도 판단)
아래는 전 세션(`HANDOFF_ohitech-3p-qa+ad-basis_20260811.md` §5-1c)이 「이번 범위 밖」으로
이월한 D-CPP-38 P2 항목들이다. 이 세션에서 손대지 않았고, 진행 상황도 갱신되지 않았다 —
그대로 존재만 재기록한다.
- 전체 합산 뷰(`account=None`)의 광고비 표시 정합 — D-CPP-38 이월 1
- 광고센터 행 0건을 「0원」으로 단정하는 신선도 축 문제 — D-CPP-38 이월 2
- `option_only` GMV를 보존식 판정·배너에 편입 — D-CPP-36 이월(아직 미착수)
- `check_failed`의 `reason`이 배너엔 `impact`만 나가고 API body에만 있는 비대칭

### 확인 필요 — 근거 없음
- 403이 Jino 복귀(IP 고정) 시 자연 해소될 것이라는 예상(§5-2) — 과거 패턴 기반 추정이지
  이번에 라이브로 확인된 사실이 아니다.

---

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_health-freshness-coverage_20260812.md 읽어줘. D-CPP-39는 완전히
종결된 계약이라 §6에 이어받을 작업이 없다 — 새 요청이 있으면 그것부터 시작하고, 없으면
§5 CI 정지(Jino 조치 필요할 수 있음)와 디스크 86.3%만 참고로 확인해줘.
```
