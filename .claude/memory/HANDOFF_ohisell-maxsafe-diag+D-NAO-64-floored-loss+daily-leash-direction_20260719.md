# 세션 인수인계: 맥세이프 손실 진단 + D-NAO-64(A) 지혈 배포 + 매출 분석 + 스윗스팟/일일고삐 방향 대화
> 저장일시: 2026-07-19 22:45 KST
> 새 대화 시작 시 이 파일 먼저 → 트랙 `docs/tracks/active/track_naver-ad-optimization.md`(D-NAO-59~64) → 직전 HANDOFF `.claude/worktrees/daily-rank-leash-profit-control-71b501/.claude/memory/HANDOFF_ohisell-03-maxsafe-onboard+RL4b-shopping-leash_20260719.md`
> ⚠️ 이 세션 후반은 **방향 대화(미승인 제안 포함)** — §6·§7을 정확히 구분해 읽을 것. 코드를 이어서 짜기 전에 Jino 승인 상태부터 확인.

## 1. 프로젝트 위치 및 환경
- 워크트리: `.claude/worktrees/video-content-summary-0e6c41` / 브랜치 `claude/maxsafe-loss-adgroup-diagnosis-4710fe` (main `5265528` 기준)
- **PR #63 오픈·미병합** → 현재 **main != prod**(prod가 commit `4582f5e`로 앞섬). 병합해야 main==prod 복원.
- prod: `sellc.ohitech.co.kr:/home/ubuntu/ohisell` — pm2 `ohisell-backend`(8001). 배포는 **`scripts/safe_deploy.sh`만**(CAS).
- prod read-only 검증: `ssh sellc.ohitech.co.kr 'cd /home/ubuntu/ohisell/backend && .venv/bin/python3 -'` ← **반드시 backend로 cd**(안 하면 상대경로 DATABASE_URL이 빈 DB를 봄 — failure memory 기록됨). 실 DB=`/home/ubuntu/ohisell/backend/ohisell.db`. `kst_today`는 `app.utils.kst`(date_utils 아님).
- 테스트: `cd backend && python3 -m pytest -q` → **2227 passed**(+7 이 세션).
- **모델 라우팅(Jino 지시, 이 세션)**: 구조 잡기=Fable, 하위작업 설계·구현=Opus, 단순 업무=Sonnet — 서브에이전트 model 오버라이드로 자동 배분, 옵션 선택은 Claude 추천안으로 자동 진행, 끝까지 자동 handoff.

## 2. 이번 세션 완료 목록

### ✅ A. 맥세이프 손실 광고그룹 심층 진단 (라이브 실측)
- 캠페인 `cmp-a001-02-000000010769985`(SHOPPING·ours·auto). 활성 광고그룹 3개 전부 상품 `13563480014`(거울 카드지갑, BEP 1.6902).
- **범인 = `grp-a001-02-000000070109616`(맥세이프_MO, 모바일)**: 최근 7일(07-12~18) **비용 268,172원·전환 2·매출 50,700 → naverROAS 0.19·실질(×1.21 보정) 0.23~0.07 수준, 캠페인 지출의 91%**.
- 07-19 12:53 레인이 형제는 처리(PC 70108611 무전환→pause 제안 1192 approved / 컨텐츠 70109620 bid_down 500→430 제안 1185)했으나 **MO만 제안 0건**.
- **사각지대 2겹**: ① `shopping_pause_candidates`의 `conv_amt==0` 게이트 — MO는 전환 있어 배제 ② bid_down은 `_step_down_bid(50)=70≥50`이라 skip(하한 70). 저ROAS+전환 약간+입찰 바닥 교집합.
- **★데이터 정합성 별건(미해결)**: entity 그룹입찰=50인데 실측 CPC 824(MO)·851(PC) → **실효 입찰이 소재(상품)-레벨에 있는 정황**(= 그룹입찰 레버가 지출을 못 조름). 라이브 API 403(간헐 레이트리밋)으로 이 세션에서 확정 못함. **= 근본수정 B의 대상.**
- 부수: 유일 흑자그룹 69089475는 07-13 네이버에서 삭제됨. 삭제그룹 2건(69087677/69089452) bid_down 제안이 매일 404 fail 노이즈(1181·1182).

### ✅ B. D-NAO-64(A) 단기 지혈 — 구현·리뷰·배포·라이브 검증 (commit `4582f5e`, prod 가동 중)
- Jino 승인 원문: **"A로 단기 지혈 먼저, 트랙에 기록해줘"**.
- 구현(TDD 전 단계 RED→GREEN):
  - `backend/app/services/naver_ad/account_diagnosis.py` `shopping_pause_candidates`: `bep_roas`·`correction_factor` 옵션 인자 + **floored_loss 경로**(전환>0 ∧ 보정ROAS<BEP ∧ at-floor(`_step_down_bid(bid)≥bid`) ∧ cost≥stop_loss) + 전 후보에 `reason` 필드(`zero_conv`/`floored_loss`). bep 미주입=휴면(하위호환).
  - `backend/app/services/naver_ad/diagnosis.py` `build_diagnosis`: account BEP·보정계수 주입.
  - `backend/app/services/naver_ad/proposal_writer.py` `_terminal_pause`: 전환 유무로 사유문 분기(전환 있는데 '무전환' 쓰던 거짓 수정).
  - 테스트 +7: `tests/test_naver_ad_diagnosis.py`(floored 5종+배선 1) · `tests/test_naver_proposal_writer.py`(정직 사유문 1).
- **독립 적대적 리뷰 GATE PASS(P1·P2 0)**: 이중제안 방어(at-floor는 `_bid_proposal`이 skip=상호배타)·over-fire 방어·refactor 등가성·non-ours 무노출(build() ours 필터 :758). P3 2건: ①account-avg BEP 사용은 shopping_group_bep와 동일 계승(상품 BEP 전환 시 두 보드 동시 변경 필요) ②stale-bid 타이밍=option B 별건 + **`shopping_resume_candidates`가 status=='on' 필터라 pause된 그룹의 자동 복귀 경로 별도 확인 권고**.
- safe_deploy CAS 3파일 통과·재시작 healthy. **라이브 실증(prod read-only)**: 보드 37건(zero_conv 27+floored_loss 10), account_bep 1.637·보정계수 1.2076. **ours에서 새로 pause 나갈 floored_loss = MO 정확히 1건**(나머지 9는 non-ours→필터). **순효과 = 07-20 08:50 일 레인에 MO 터미널 pause 1건 추가**(정밀 지혈·MO 쿨다운 없음=발동 확실).
- 트랙 D-NAO-64 기록 완료(진단 전문+결정+구현+미결). `claude-progress.txt` 갱신. failure memory 1건(prod verify cwd 함정).

### ✅ C. 매출 추이 분석 (Jino "매출이 안 느는 것 같은데 지표로" 요청)
- **★분석 함정: `naver_ad_daily`는 `__backfill__` 센티널 행과 광고그룹 상세 행이 같은 날짜 공존 → 그냥 SUM하면 2배**. dedup 규칙 = (날짜,캠페인)별 센티널 있으면 센티널만, 없으면 상세 합.
- 주간(dedup, naverROAS): 03강화 W2 150.5만/5.60 → W4 48.5만/3.91(**MOP 운영 하 -42%**) · 04지문 W3 17.6만/3.39 → W4 11.3만/1.82(ours, -35%, 단 주당 전환 13건=노이즈 수준·07-17 bid_up 실험일 매출0 포함) · 맥세이프 W4 32.7만 비용/0.58(출혈) · 기타(MOP/수동) W3 1,596만 → W4 1,083만(**-32%**).
- **결론(Jino에 보고)**: 매출 감소는 사실이나 **계정 전체 동반 하락(-32~42%, MOP 운영분이 더 큼)=시장 요인 지배적**. ours 고유 문제는 "성장 레버 미가동"(07-12~18 실집행 = 04 입찰 5건뿐, 07-19에 19건은 온보딩+지혈). 볼륨 확장 기계(RL1~5·CD5)는 라이브 0~2일차.
- ours 커버리지 = 계정 매출의 ~6.8%(W4 79만/1,162만) — 단 **Jino 지시로 커버리지 논의는 종료: "우리가 운영하는 캠페인만 보면 된다"**.

### ✅ D. HANDOFF 저장(이 파일)

## 3. 확정된 결정사항 (이 세션)
- **D-NAO-64(A)**: 스톱로스 바닥그룹 저ROAS 지혈 — 위 §2-B. 트랙 기록 완료.
- **커버리지 스코프**: ours 4개 캠페인 내에서만 승부(이관 논의 불요) — Jino 원문 "커버리지 관련해서 우리가 운영하는 광고캠페인만 보면 되".
- **예산 자동증액 방향 동의**: Jino 원문 "예산의 경우 만약 우리가 목표로 한 RoAS가 달성되는 경우 예산을 자동으로 늘리는 로직도 좋다고 생각해" — 스프린트 X 금지선("예산 변경 개방은 스코프 밖")의 해제 방향 표명. 정식 스코프는 미확정(§6).
- **내외부 자료 병행**: "최고RoAS와 최대 매출 달성 방법을 내부+외부 자료에서 찾아야" — ref 26 TOP5 + 신규 딥리서치 예정.
- **모델 라우팅**: Fable(구조)/Opus(하위)/Sonnet(단순), 옵션은 Claude 추천안 자동 진행(§1).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-ad-optimization.md` | D-NAO-59~64 전문(먼저 읽기) |
| `backend/app/services/naver_ad/account_diagnosis.py` | `shopping_pause_candidates` floored_loss 경로(:528~) |
| `backend/app/services/naver_ad/diagnosis.py` | build_diagnosis BEP 주입(:105~) |
| `backend/app/services/naver_ad/proposal_writer.py` | `_terminal_pause` 정직 사유문·`_stop_loss_proposal`·`_step_down_bid`·build() ours 필터(:758) |
| `backend/app/services/naver_ad/auto_operator.py` | 일 레인 08:50·시간당 :20·RL3 장중 고삐 |
| `claude-progress.txt` | 최신 상태 요약 |

## 5. 알려진 이슈 / 주의사항
- **★main != prod**: PR #63 병합 필요(병합하면 복원).
- **★07-20 08:50 관측 3건**: ① MO 터미널 pause 실집행(D-NAO-64 첫 라이브 발동, rationale "저ROAS 바닥그룹" 문구) ② 03 광고그룹 3개(59832280/344/206) RL4b 자동 재정지 ③ RL 자연발동(장중 고삐 bid_down·탐침, 09:03/:20 포함).
- **MO pause 발동은 기본값=발동**(Jino에게 "막으려면 말해달라" 했고 응답 없음 = 발동 예정. 단 §6 일일고삐 방향이 확정되면 이 pause는 "레버 불능 예외 ②"로 재분류되고, B 완성 시 고삐 체제로 복귀시켜야 함).
- **naver_ad_daily 2배 함정**: 캠페인 합계 낼 때 반드시 §2-C dedup 규칙(센티널 우선). 이번 세션에서 실제로 한 번 틀렸다 잡음.
- **보정계수 현재 라이브 값 = ×1.2076**(naver_ad_daily ROAS에 곱함, 0.19→0.23). "÷2.6"은 네이버 콘솔 숫자 기준의 과거 메모 — 혼용 주의.
- **API 403 간헐**(레이트리밋): `_get_adgroup`류 반복 호출 시. 이 세션에서 소재-레벨 입찰 확정을 막음.
- 스프린트 X 금지선 중 "예산 변경 개방 스코프 밖"은 Jino가 방향 전환 표명(§3) — 정식 개방은 새 D-N 기록과 함께.
- codex 소급 리뷰 07-23(RL1~5·RL4b·D-NAO-64 커밋 전부).

## 6. 다음에 할 작업 (미완료 — ★승인 상태 주의)

### ★제안됨·Jino 최종 승인 대기 (이 세션 마지막 대화 — "일단 저장" 지시로 멈춤)
Jino의 MOP41 오전 철학 재확인 + "왜 또 스탑로스?" 지적(원문: *"내가 광고 담당자라면 광고를 끄기 전에 다양한 시도를 해볼꺼다, 스탑로스를 daily로 잘라보는건 어떨까? 만약 오늘 성과가 안좋아서 계속 loss가 생긴다면 쭉 낮추다가 다음날 다시 우리가 지향하는 밴드 순위에서 다시 시작하는거지. 성과가 좋아서 매출이 잘 난다고 하면 쭉 순위를 올리면서 위로 올리고, 그 순위는 다음날이어도 일부로 낮추지는 않고."* + *"우리가 운영하는 MOP프로그램 전체에 이게 적용되었으면 좋겠는데"* + sellc 캠페인별 loss 정책 버튼 제안) → Claude가 제안한 구조(**미승인**):
- **loss 대응 기본값 = 고삐-일일리셋(Jino 방식) ours 전 캠페인**: 장중 하향→바닥 대기(정지 아님)→자정 리셋→익일 밴드 재시작·승자 관성. pause는 예외만(①ML 자동입찰 ②입찰-지출 연결 끊김=MO형, B 완성 시 해제).
- 스프린트 순서 제안: **DL(스톱로스 daily 절체+바닥 대기+pause 예외화) → B(소재-레벨 입찰 인식·제어) → sellc UI(캠페인별 loss 정책 스위치, 기본값=고삐) → L2(예산 자동증액) → L3(인벤토리 확장)**.
- 인벤토리 확장(L3) 방법 제시됨: (a) 검색어 채굴→키워드 승격(P_Test, naver_search_term_daily+keywordstool) (b) 미광고 상품 투입(쇼핑, 자연판매 실적 있는 상품 우선) (c) 지면/디바이스 확장.
- 예산 자동증액(L2) 설계 골격 제시됨: 보정ROAS≥목표 ∧ 소진율≥~90% 며칠 연속 → +15~20% 스텝, 증액 후 한계ROAS<BEP면 자동 되돌림, 기존 예산 가드레일 유지.
- **다음 세션 첫 행동: Jino에게 이 구조 승인 여부 확인 → 승인 시 트랙에 D-NAO-65로 기록(위 Jino 원문 인용 포함) 후 DL부터 자동 진행**(모델 라우팅 §1).

### 승인 불요·예정된 것
- [ ] 07-20 08:50 관측 3건(§5) — 원칙22: 발동 전 "됐다" 금지.
- [ ] PR #63 병합 → main==prod 복원.
- [ ] 외부 딥리서치 1회: "ROAS 제약 하 매출 최대화 — 예산 탄력 운영+인벤토리 확장 자동화"(ref 26 TOP5 보강).
- [ ] 소재-레벨 입찰 실확인(API 403 재시도) — B 설계의 선행 실측.
- [ ] codex 소급 리뷰 07-23.
- [ ] (P3 후속) `shopping_resume_candidates` status 필터로 pause 그룹 복귀 경로 확인 · account-avg vs 상품 BEP 두 보드 동시 전환 검토.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/worktrees/video-content-summary-0e6c41/.claude/memory/HANDOFF_ohisell-maxsafe-diag+D-NAO-64-floored-loss+daily-leash-direction_20260719.md 읽고 이어서 작업해줘. 우선 §6의 일일고삐 구조 제안 승인 여부를 나(Jino)에게 확인하고, 07-20 08:50 결과(MO pause·03 재정지) 관측해줘.`
