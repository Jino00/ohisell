# ref 91 — 자동운영 «기반»의 정의: 내부 프레임 ↔ 외부 표준 대조 (2026-08-22)

> 세션 `cd7d21df` · 체인 「PAO 논의 32」 · **읽기 전용 조사 — 코드·배포·prod 쓰기 0건**
> ✅**ref 번호 91 확정** — 2026-08-22 12:2x KST `ls docs/references/ | tail -3` 실행, 최댓값이 **90**(`90_scorer_objective_function_mismatch_20260821.md`)임을 확인했다.
> (초판은 「잠정 — 확인하지 못했다」라 적었다. 확인된 뒤 그 캐벗을 지우지 않으면 그게 바로 이 세션이 종일 갚은 「문서에 남은 낡은 주장」이 된다 — 교훈 #345.)

---

## §0. 한 줄 결론 · 그리고 커버리지 자백

**북극성 사다리(M0→M6)는 방향·순서가 옳고 M5까지 밟으면 자동운영 기반의 대부분이 선다. 그러나 사다리를 «전부 합격하고도» 「브레이크만 강한, 자기 성과를 모르는 자동운영」이 나올 수 있는 구멍이 남는다** — 내부 판단(Fable)이 셋을 짚었고, 외부 표준 대조가 그중 둘을 독립으로 확증하면서 **넷을 더 얹었다.**

**이 문서는 「전수 조사 완료」가 아니다.** 내부 판단은 1차 자료 4종(ref 82·90·65 + 트랙 헤더)만 읽었고 **코드를 새로 열지 않았다.** 외부 조사는 웹 3렌즈이고 **1차 출처 확보에 실패한 항목이 여럿 있다**(§3-4 등급표). 라이브 실측은 §6 한 건뿐이다.

**이 문서는 처분을 지정하지 않는다.** M3·M4 계약에 무엇을 명문화할지는 Jino 몫이다(§1 승인 지점 ①). §7은 «점검표 초안»이지 확정 항목이 아니다.

---

## §1. 발단

Jino 질문 2개 (2026-08-22):
1. *"fable로 생각했을때 위의 층으로 진행하면 PAO광고의 광고를 자동운영할때 기반은 갖춰진다고 봐? 아니면 더 필요한 기본이 있을까?"* (08:09)
2. *"이 판단에 균형감을 갖추기 위해서 외부 시각도 가져오는건 어때?"* (08:23)

설계: ①Fable 1기에 구조 검증(저장소 1차 자료만) ②**Fable의 결론을 보여주지 않은 채** 외부 렌즈 3기를 독립으로 돌림 ③코디네이터가 겹쳐 봄.
★**결론을 주면 확인 편향이 생겨 검증이 아니라 추인이 된다** — 그래서 블라인드로 돌렸다. 수렴한 항목은 그만큼 신뢰도가 높다.

---

## §2. 내부 프레임 — 「자동운영의 기반」 6범주 (Fable 판단)

자동운영 = **사람이 화면을 보지 않는 시간에도 기계가 돈을 움직이는 상태.**

| # | 범주 | 왜 기반인가 | 북극성 L0~L4 대응 |
|---|---|---|---|
| ① | **감각** — 상태를 본다 | 못 보는 축은 기계가 「문제없음」으로 오독한다 | L0+L1 그대로 |
| ② | **가치 기준** — 목적함수의 통화(총이익 절대액)로 성패를 잰다 | 자가 틀리면 기계는 틀린 방향을 «학습된 확신»으로 민다 | ★**층이 없다** |
| ③ | **손과 안전** — 레버 + 가드레일·킬스위치·되돌리기 | 사람과 달리 «초당 오류 반복»이 가능하다 | L3 그대로 |
| ④ | **귀속** — 누가 바꿨고 무엇이 효과인가 | 귀속 없는 학습은 남의 조치를 자기 효과로 배운다 | ★층이 없다 |
| ⑤ | **학습 루프** — 행위→채점→지혜→제안 | 「매시간 개선」의 실체 | L4 그대로 |
| ⑥ | **자기 감시** — 파이프 자신의 침묵 실패를 알아챈다 | 사람이 안 보는 순간 죽은 신호는 돈으로 직결 | ★어느 층에도 없다 |

★**판단**: L0~L4는 ①③⑤를 정확히 담지만 **②④⑥은 «층»이 아니어서 마일스톤 담당이 생기지 않았다.**
②는 D-NAO-222→223으로 **사후에** M3에 박혔다(층 부재가 낳은 사고의 사후 수리) · ④는 M4의 «선행 결정»으로만 · ⑥은 어디에도 없다.

### 2-1. 사다리가 채우는가 (Fable 판정: **조건부 예**)

| 범주 | 채우나 | 왜 / 잔여 |
|---|---|---|
| ① 감각 | 대체로 | 잔여: qi_grade 죽은 신호 · 쇼핑 검색량 원리적 불가 · 기기별 지출 원장 부재 — 셋 다 M 항목 아님 |
| ② 가치 기준 | 채움(D-NAO-223 이후) | 단 라이브 증거가 M4에 걸리는 순환 |
| ③ 손·안전 | **반쪽** | 브레이크 완비. **액셀이 열리는지는 어느 M도 판정하지 않는다** |
| ④ 귀속 | **반쪽** | 분리 «합의»는 있는데 «위반 감지»가 쇼핑에서 사각 |
| ⑤ 학습 루프 | 채움 | 단 액셀이 막히면 M5는 원리적으로 미도달 |
| ⑥ 자기 감시 | **안 채움** | 담당 없음 |

### 2-2. Fable이 짚은 구멍 (중요도 순, 요지만 — 전문은 세션 트랜스크립트)

- **F1. 액셀의 개통** ★최중요 — `update_bid` 425건 중 194건(45.6%) 미집행, 차단이 네이버(20건)가 아니라 **우리 가드레일 174건(89.7%)**, 그중 「BEP 미달 증액 금지」 61건이 D-NAO-59가 잡으라는 바로 그 구간. ⇒ **M4 합격기준(실집행 diary 행 + 위반 0 + 되돌림 0, 1주)은 「하향 1건만 하고 조용히 있는 카나리」도 통과시킨다.**
- **F2. 카나리의 «성과» 판정자 부재** — M4는 안전 판정, M5는 학습 루프 판정. **카나리 창 전체의 총이익 기여를 합산해 「해가 없었나」를 판정하는 마일스톤이 없다.**
- **F3. 쇼핑 grain 외부변경 사각** — 외부변경 4,937건 중 98.9%가 키워드 축인데 쇼핑엔 키워드가 없다(비용의 92.5%가 쇼핑). ⇒ 「되돌림 0 관측」이 **«관측 불가»를 «0건»으로 오독**할 수 있다(교훈 #123 패턴).
- **F4. 상품 BEP 624그룹(73%) 미확보** — 담당은 다른 트랙(product-connection-map)인데 **의존 게이트로 등재돼 있지 않다.** ★새 M 없이 우회 가능: M4 카나리 선정을 「상품BEP 확보 230그룹 우선」으로.
- **F5. 자기 감시층 부재** — qi_grade · CD3 상시-0 · 소비처 0 크론 5개 · `success` 로그 밑 절단.
- **F6(보조). 광고 밖 수요 대조축** — 스마트스토어 대조축 결정이 사다리에 안 매달려 있다.

### 2-3. Fable의 순서 의견
- **M4의 위치(뒤에서 세 번째)는 옳다** — 근거가 가설이 아니라 실측(D-NAO-85)이고 M0~M3이 전부 재가동 불요라 순서 비용도 없다. **유지 권고.**
- ★**M3↔M4 순환**: 새 채점식의 라이브 증거는 새 실집행을 요구하는데 재개가 M4다. **M3 계약이 「합격 = 배선 + 소급/표본 증거까지, 첫 라이브 채점은 M4 창의 관측 항목」이라는 경계를 명문으로 안 가지면 M3 자체가 종결 불가 M이 된다.**
- **소유권 분리 «협의»는 지금 병렬 가능** — 코드가 아니라 달력 시간이 걸리는 유일한 게이트.
- **F1(액셀)은 M4 «착수 전»** — 재개 후 발견하면 카나리 1주 관측이 통째로 무효 데이터가 된다.

### 2-4. 과잉 (Fable) — **없다**
M6만이 기반이 아닌 고도화인데 이미 맨 뒤 + 진입 조건 4개라 아무것도 막지 않는다.
★단 **「기반 갖춰짐」은 7/7이 아니라 M5 통과 시점**이다 — 이 구분이 없으면 「기반이 아직」과 「트랙이 아직」이 섞여 읽힌다.

---

## §3. 외부 3렌즈 — 무엇을 가져왔나

렌즈 셋을 **Fable 결론 미제공** 상태로 독립 실행했다.
① 광고 플랫폼 자동입찰 실무 ② 자동화 운영 안전(SRE/MLOps/금융규제) ③ 실험설계·증분 측정·탐색

### 3-1. 수렴 — 외부가 Fable과 **같은 곳**을 짚었다 (신뢰도 상승 3건)

| Fable | 외부 확증 | 등급·출처 |
|---|---|---|
| **F1 액셀 미개통** | 밴딧·추천 문헌의 표준 실패 모드. **OPE는 로그 정책이 «한 번도 시도 안 한 행동»을 원리적으로 평가 못 한다**(support deficiency). Google은 **Smart Bidding Exploration**을 공식 기능으로 판다 | [1차] https://support.google.com/google-ads/answer/16294686 · 콜드스타트: [논문] https://arxiv.org/html/2502.01867 |
| **F2 카나리 성과 판정자 부재** | SRE Workbook: *"Terminating a canary deployment after receiving just a handful of queries doesn't provide a useful signal for systems characterized by diverse queries."* | [1차] https://sre.google/workbook/canarying-releases/ |
| **F5 자기 감시** | ML Test Score **Monitor 1**이 우리 qi_grade 사고를 그대로 서술: 업스트림이 조용히 바뀌면 *"without necessarily producing values that are strange enough to trigger other monitoring"* ★그리고 **Kayenta 기본값이 「데이터 없음 = 실패 아님」**이라 `mustHaveData`를 명시로 켜야 한다 | [1차 논문] https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/aad9f93b86b7addfea4c419b9100c6cdd26cacea.pdf · [1차] https://spinnaker.io/docs/guides/user/canary/judge/ |

★**Kayenta의 NODATA 기본 통과는 우리 「절단됐는데 로그는 `success`」(교훈 #319 계열)와 구조가 같은 함정**이다 — 다른 도메인에서 같은 실패 모양이 표준 문서에 적혀 있다는 것이 이 대조의 수확이다.

### 3-2. 외부에만 있던 것 — Fable이 못 짚은 새 축 (중요도 순)

**E1. ★★대조군 분리가 계획에 아예 없다 — 그런데 대조군이 이미 공짜로 있다**
「카나리 → 1주 → 확장」은 정책 vs 정책이 아니라 **정책 vs 과거(pre-post)**다. Google 저자군(Vaver & Koehler 계열):
> *"it is generally not possible to determine the incremental impact of advertising by merely observing such data across time."*
> [1차] https://www.unofficialgoogledatascience.com/2016/06/estimating-causal-effects-using-geo.html · 원논문 https://research.google.com/pubs/archive/38355.pdf

외부 정석은 **동시병행 분할** — 일부는 대행사 유지, 일부는 자동 운영. ★**우리는 대행사가 나머지를 계속 운영하므로 대조군을 새로 만들 필요가 없다.**
⇒ Jino 원 질문 *"우리가 대행사보다 잘 운영할 수 있는 준비가 되었냐"*(2026-08-21 23:26)에 **정면으로 답하는 유일한 설계**이고, ref 90 §6이 「창 비교는 교락으로 못 잰다」고 닫은 벽을 **우회**한다.

**E2. ★★관측 창 1주는 표준의 1/6이다**
Google Smart Bidding Exploration [1차]: **"test for 6+ weeks"** + *"it might take 1-2 additional weeks for Exploration to fully ramp"* · **"avoid testing Smart Bidding Exploration in brand new campaigns"** · 램프업 구간은 *"exclude that time period from performance evaluation"*.
Google Lift study [1차, https://support.google.com/google-ads/answer/16627074]: 최소 7일 · **전환 3,000건** 권고 — **우리 저클릭 쇼핑 구조에선 원리적으로 미달**.
우리 D-1 지연·D+1~7 정착을 더하면 창은 더 길어져야 한다.

**E3. ★★네이버 네이티브 자동입찰과 충돌할 수 있다 (제3의 주체)**
네이버 공식 API 문서 [1차, https://naver.github.io/searchad-apidoc/]:
- 파워링크 그룹 = `ML`, 쇼핑 상품형 = `MAXCONV`, 미사용 = `NONE`
- **자동입찰을 켜고 수동 입찰가를 넣으면 에러**
- 자동입찰 사용 시 **일 예산 필수**

우리 엔진은 수동 입찰가를 쓴다. ⇒ 대행사가 어느 그룹에 자동입찰을 켜뒀다면 **우리 쓰기가 실패하거나, 우리가 켜면 그쪽 설정을 깬다.** 「대행사 vs 우리」가 아니라 **3주체**일 수 있다.
★**이 축은 §6에서 라이브로 실측했다.**

**E4. 표본 부족 시 «자기 정지» 문턱**
Microsoft tROAS [1차, https://learn.microsoft.com/en-us/advertising/campaign-management-service/targetroasbiddingscheme?view=bingads-13]:
> *"If your campaign falls below 30 conversions or has zero revenue over any 30-day period, Target ROAS will stop optimizing your bids."*

플랫폼조차 표본이 마르면 **스스로 멈춘다.** 우리 엔진에 그 장치가 있는지, 저볼륨 쇼핑에서 어떻게 도는지 미정 [확인 안 됨].

**E5. 킬스위치를 «당겨봤는가»**
FINRA 15-09 [1차 규제문서, https://www.finra.org/sites/default/files/notice_doc_file_ref/Notice_Regulatory_15-09.pdf]는 킬스위치 **존재**가 아니라 *"periodic testing... regarding automated or manual kill switch parameters"*를 감독 항목으로 묻는다.
ML Test Score **Infra 7** [1차]: 롤백은 *"an emergency procedure, operators should practice doing it normally, when not in emergency conditions."*
⇒ 우리는 안전장치가 **있다**만 있고 **드릴(연습 발동)**이 없다.

**E6. 「총이익」 값 자체의 정확도·전달 검증**
Google [1차, https://support.google.com/google-ads/answer/15099424]: 값 기반 입찰 채택 전 *"wait for 1-2 conversion cycles for your campaign to receive conversion values at a similar rate before adopting."*
★**F4(상품 BEP 230/854)를 한 단계 격상시킨다** — 「미판정 그룹이 많다」가 아니라 **「입력값이 근사인 채로 최적화를 돌린다」**이다.

**E7. 정지 23일 = stale 신호** — ML Test Score **Monitor 4**. 재가동 판정에 「얼마나 오래된 신호에 기반하나」가 없다.

**E8. 「못 푼다」에 정식 이름이 있다 — positivity/overlap violation**
> *"theoretical (structural) violation of positivity assumption occurs when a subpopulation... have zero probability of receiving at least one of the treatments, so that even if we let the sample size go to infinity, we would still never observe all treatment values"*

⇒ D-NAO-183의 *"표본을 더 모아도 안 풀린다"*가 결론이 아니라 **계산으로 확인 가능한 판정**이 된다(사전등록 겹침 구간의 covariate balance).
그리고 DiD·synthetic control의 한계도 명문화돼 있다: DiD는 *"identification of the ATT fails if pre-treatment covariates do not adequately account for all relevant confounding information"* [https://arxiv.org/pdf/2201.01194] · synthetic control은 잔여 교란이 **분리 가능(separable)**할 때만 작동 [https://arxiv.org/pdf/2312.00955].
★**CUPED는 교란 해소 도구가 아니다** — 무작위 배정이 이미 성립한 실험의 분산 축소용이다. 관측 데이터엔 전제가 안 맞는다.

**E9(보조). 변경 빈도·폭 규칙(학습 리셋)** — 플랫폼들은 «큰 변경»이 학습을 리셋한다는 개념을 갖는다. 우리 재가동 후 파라미터 조정 규칙이 없다. ⚠️Meta 수치(예산 20%·구매 100건)는 **1차 확보 실패 — 인용 금지**.

### 3-3. ★ 균형 — 외부는 **반대 방향 위험**도 말한다

Fable은 「D-NAO-85가 실측이니 M4를 앞당기지 마라」고 했다. 외부 문헌은 **정지 자체의 비용**을 말한다.

> 광고 입찰 콜드스타트: 탐색이 부족하면 *"bidding strategies become overly conservative, leading to slow traffic activation and delayed signal accumulation"* [https://arxiv.org/html/2502.01867]
> 피드백 루프: *"feedback is observable only for items the historical policy chose to expose... amplifying popularity bias while suppressing long-tail items"* [ACM CIKM 2020, https://dl.acm.org/doi/10.1145/3340531.3412152]
> 사회적 학습 이론: *"learning failures are the typical case for any fixed episode length"* [https://arxiv.org/html/2602.05835]

⇒ **우리는 23일째 정지 중이고 그동안 support가 좁아지고 있다.** 「더 배우고 켠다」는 우리 순서는 외부 실무(전환량 문턱만 넘으면 켜고 탐색하며 배운다)보다 **보수적**이고, **그 보수성 자체에 값이 붙는다.**

★두 위험이 다 참이라면 답은 「더 기다리기」도 「그냥 켜기」도 아니라 **«대조군을 두고 작게 켜기»**다 — 그게 E1이 정석이라 부르는 설계다.

### 3-4. 과잉 검사 — 결과: **과잉 없음, 오히려 반대**

Fable이 「기반」이라 한 것 중 **외부가 전제로 안 보는 항목은 하나도 없었다.** 외부는 오히려 더 요구한다(대조군·6주 창·값 정확도·킬스위치 드릴).
단 하나 결이 다른 지점: **외부 플랫폼은 L1 연관 지식을 다 채우고 켜라고 하지 않는다.** 전환량 문턱만 보고 켠 뒤 탐색으로 배운다 — §3-3의 긴장이 그것이다.

### 3-5. 출처 등급 경고 (인용 전 반드시 볼 것)
- **1차**: Google Ads 공식 도움말 · 네이버 공식 API 문서(`naver/searchad-apidoc`) · Google SRE Book/Workbook · ML Test Score 논문(Breck et al. 2017) · FINRA 15-09 · SEC 15c3-5 · Spinnaker/Kayenta 문서.
- **참고(2차) — 인용 금지 또는 등급 병기**: Meta 학습 단계 수치(예산 20%·구매 100건, **1차 원문 확보 실패**) · 「대행사·인하우스·자동화 3자 권한 분리」 플랫폼 공식 가이드는 **못 찾음(판정불능)** · Vaver & Koehler 원논문은 PDF 추출 실패로 저자군 블로그 경유 인용.
- **기준일 미상**: Google Lift study의 Bayesian 전환은 *"in 2025"*까지만 나온다. 조사일 = **2026-08-22 KST**.

---

## §6. 라이브 실측 1건 — 네이버 네이티브 자동입찰 (E3 검증)

**2026-08-22 11:5x KST · prod 읽기 전용.** 재현 명령은 §9.

### 6-1. 관측
우리 원장 세 곳(`naver_entity`·`naver_entity_snapshot`·`naver_adgroup_target_current`)에 자동입찰 관련 컬럼 **전건 0개**. 그러나 `naver_change_log`의 외부변경 **전체 페이로드**에는 실려 있다 — 키 이름은 **`autobidStrategy`(객체) · `systemBiddingType` · `aiAdsOptIn`**.

| 항목 | 값 |
|---|---|
| adgroup 전체 페이로드를 «한 번이라도» 가진 그룹 | **31** — **전건 `campaign_type='SHOPPING'`** |
| 그중 «최신 행»이 전체 페이로드인 그룹 | **24** |
| `autobidStrategy.isAutobidActive` | **24/24 = `false`** |
| `autobidStrategy.autobidBidGoal` | **24/24 = `NONE`** |
| `systemBiddingType` | **24/24 = `NONE`** |
| `aiAdsOptIn` | **24/24 = `1`** ← ★정체 미확인 |
| 최신 관측일 | **2026-07-30** (조사일 기준 23일 전) |
| 전체 광고그룹 수 | **1,017** → 관측 커버리지 **24/1,017 = 2.4%** |

### 6-2. 판정
- ✅ **충돌 위험은 관측된 범위에서 0** — 24그룹 전건 네이티브 자동입찰 OFF.
- ⚠️ **그러나 분모가 2.4%이고 최신 관측이 07-30이다.** 그 뒤 대행사가 켰는지는 **모른다**. 「0건」이 아니라 **「거의 안 봤다」**이다(교훈 #123 구분).
- ★★**진짜 발견은 관측 사각 자체다**: **상시 원장에 이 축이 없다.** change_log에 «우연히» 실린 것이고, 그것도 SHOPPING 31그룹뿐이다. ⇒ **대행사가 내일 자동입찰을 켜도 우리는 구조적으로 모른다.** 네이버 공식 문서상 자동입찰 ON이면 수동 입찰가 설정이 **에러**이므로, 그 상태에서 우리 엔진의 쓰기는 실패하거나 상대 설정을 깬다.
- ★**F3(쇼핑 외부변경 사각)의 «새 축»이다** — Fable은 키워드/그룹 grain으로만 봤는데, 여기 **그룹 «설정» 축**이 하나 더 있었다.
- ★**추가 API 콜 0으로 닫힐 가능성** — 그 페이로드는 우리가 이미 받는 응답에 실려 온다. `/ncc/targets` 폐기분(D-NAO-201)과 **같은 모양**이다. ⚠️단 **어느 호출이 그걸 가져오는지는 코드 확인 필요** — 작성 시점에 저장소 접근 불가라 미확인.

### 6-3. 이 관측이 만든 [미상]
1. **`aiAdsOptIn = 1`의 정체** — 「AI 광고 옵트인」이 무엇을 켜는지, 우리 입찰과 상호작용하는지 **확인 안 됨**.
2. **07-30 이후 현재 상태** — 라이브 API 재조회로만 확정 가능(코드 접근 필요).
3. **WEB_SITE·BRAND_SEARCH 그룹의 자동입찰 상태** — 관측 31건이 전건 SHOPPING이라 **다른 유형은 표본 0**.

---

## §7. M3·M4 계약 §4 점검표 «초안» — 처분 아님

> 아래는 **선택지 나열**이다. 채택·기각·문언은 전부 Jino 몫이고, 계약이므로 §1 승인 지점 ①이다.
> ★**새 마일스톤을 만들지 않는다** — 전부 M3·M4 계약의 합격기준·선행 조사로 흡수 가능한 크기다(전역 §1 「게이트를 새로 세우지 않는다」).

### 7-1. M3 계약(지혜 성적표 + 채점기 교정)에 넣을 후보

| # | 항목 | 근거 | 성격 |
|---|---|---|---|
| M3-a | **D-NAO-223 명문화** — 채점기 교정을 §4 합격기준의 별도 항목으로. 「M3에 포함」이 아니라 「§4에 문장으로」 | D-NAO-223 · 교훈 #343 재발 방지 | **합격기준** |
| M3-b | **M3↔M4 순환의 경계 문언** — 「M3 합격 = 배선 + 소급/표본 증거까지, 새 식의 첫 라이브 채점은 M4 창의 관측 항목」 | Fable §4 · 없으면 M3이 종결 불가 M | **합격기준(경계 문언)** |
| M3-c | **첫 확인 항목**: 「`dry_run=False` 행이 07-30 이후 0건」이 실제로 참인가 (D-NAO-223 전제의 미실측 절반) | D-NAO-223 각주 | **선행 조사**(읽기 전용) |
| M3-d | **§8 미결**: 기존 150건 `outcome`의 소급 재채점 여부. ★`success` 2행(id 974·975)은 `outcome IS NULL` 필터 때문에 **소급 안 하면 영원히 새 식의 사정권 밖** | ref 90 §8-A · 교훈 #274 | **미결 항목**(§8에 적고 Jino 결정) |
| M3-e | **값 정확도 게이트(E6)** — 성적표가 소비하는 「총이익」 값의 grain·지연·커버리지를 명시. 상품BEP 230/854가 분모에 어떻게 반영되는지 | E6 · F4 | **합격기준** |

> ★**표기 정정(2026-08-22 13:0x, 완료 QA 지적)**: 초판 M3 표는 「항목·근거」 2열뿐이라 M4 표(3열)와 달리
> **성격 열이 없었다.** 앵커 합격기준 ⓓ가 「항목마다 근거와 성격이 붙어 있다」를 요구했는데 M3 5항목 전부가
> 미표기였다 — QA가 잡아 이 개정에서 채웠다. **같은 문서 안에서 두 표의 형식이 갈린 것**이 결함의 모양이다.

### 7-2. M4 계약(L3 재개)에 넣을 후보

| # | 항목 | 근거 | 성격 |
|---|---|---|---|
| M4-a | ★**액셀 대칭 검사** — 합격기준에 «상향·증액 계열 실집행 ≥N건 + 새 식 채점 기록»을 명문화. 없으면 「하향 1건만 한 카나리」가 통과한다 | F1 · 북극성 §7 | 합격기준 |
| M4-b | ★**대조군 분할** — 카나리를 «시간순 스위치»가 아니라 «동시병행 셀»로. 대행사 유지군 ↔ 자동 운영군. **대조군은 이미 존재한다** | E1 | 설계 |
| M4-c | ★**관측 창 재검토** — 1주는 외부 표준(6주+램프업)의 1/6. 우리 D+1~7 정착을 감안한 창 길이를 계약에 못박을 것 | E2 | 합격기준 |
| M4-d | ★**네이티브 자동입찰 실태 확정 + 상시 적재** — 카나리 대상 그룹의 `autobidStrategy`·`systemBiddingType`·`aiAdsOptIn`를 **선행 조사**로 확정하고, 가능하면 추가 콜 0으로 원장에 적재 | E3 · §6 | 선행 조사 |
| M4-e | **카나리 성과 롤업** — 창 전체의 Σ 총이익 기여(새 식 기준) 판정. 「안전」과 별개 | F2 | 합격기준 |
| M4-f | **카나리 선정 조건: 상품BEP 확보 230그룹 우선** — 새 M 없이 F4 구멍을 밟지 않고 지나간다 | F4 | 선정 조건 |
| M4-g | **NODATA ≠ 성공** — 관측 항목마다 「이번 창에 이 지표에 데이터가 몇 건 들어왔는가」를 판정 조건에 포함 | 3-1 Kayenta · F5 | 합격기준 |
| M4-h | **신호 신선도 sentinel** — 자동 판정이 소비하는 원장 N개의 최신 타임스탬프 표 1장. 가장 싼 형태의 ⑥ 자기 감시 | F5 · E7 | 관측 항목 |
| M4-i | **킬스위치 드릴** — 재개 전 실제로 한 번 당겨보고 기록 | E5 | 선행 절차 |
| M4-j | **표본 부족 시 자기 정지 문턱** — 우리 엔진에 있는지 확인하고, 없으면 넣을지 결정 | E4 | 선행 조사 |
| M4-k | **쇼핑 grain 외부변경 커버리지 실측** — 그룹·소재·타겟 diff가 실제로 어느 수준 작동하는지(읽기 전용 1건이면 갈린다) | F3 · §6 | 선행 조사 |

### 7-3. 사다리 밖에 남는 것 (담당 지목만)
- **F6 광고 밖 수요 대조축**(스마트스토어) — Jino 결정 대기. M4 계약 «전»으로 당길지가 선택지.
- **E8 positivity 판정** — D-NAO-183을 「검증 가능한 판정」으로 바꾸는 읽기 전용 조사. 어느 M에도 안 걸려 있다.
- **품질지수 죽은 신호(qi_grade)** · **쇼핑 검색량 원리적 불가** · **기기별 지출 원장 부재** — ① 감각의 잔여 구멍, M 항목 아님.

---

## §8. [미상] · 못 본 곳

1. **Fable은 코드를 새로 열지 않았다** — F1의 「액셀 차단」이 **현재 파라미터에서도 같은지 미재현**(07-30 창 관측 + 08-10·11 가드레일 파라미터 변경 2건 이후). ★**가장 싼 다음 수가 이것이다.**
2. **F3의 「쇼핑 감지 사각」은 분포(98.9% 키워드)로부터의 추론** — 대행사가 실제로 키워드만 만졌을 가능성 배제 못 함. 읽기 전용 조사 1건이면 갈린다.
3. **`budget_pacing`(유일한 가동 항목)과 `optimizer='none'` 게이트의 관계 [미상]** — 「액셀이 막혀 있다」의 한 귀퉁이가 여기 걸린다.
4. **승격 지혜 1건(07-27)의 실체 미확인** — M3 합격기준 「승격 지혜 ≥1건에 성적 행」이 그 1건으로 성립 가능한지.
5. **§6의 [미상] 3건** — `aiAdsOptIn` 정체 · 07-30 이후 현재 상태 · WEB_SITE/BRAND_SEARCH 표본 0.
6. **6범주 분해 자체가 Fable의 판단이다** — 다른 분해도 가능하다. 다만 ②를 층으로 안 세운 결과가 D-NAO-222의 늦은 발견이었다는 사실이 이 분해를 지지한다.
7. **외부 조사의 1차 확보 실패분** — §3-5 참조.
8. **M2-z가 아직 안 돌았다** — M2 잔여의 실상은 M2-z 판정이 정하고, 그 결과가 M2→M3 경계를 바꿀 수 있다.

---

## §9. 증거 재현 명령

⚠️**ssh 키 경로 주의**: 기본 `~/.ssh/config`의 `IdentityFile`이 iCloud 경로(`~/Library/Mobile Documents/...`)를 가리킨다. **iCloud 접근이 막히면 ssh도 같이 죽는다.** iCloud 밖 사본이 `~/.ssh/oracle_cloud_legacy`에 있다(2026-08-22 실사용 확인).

```bash
K="$HOME/.ssh/oracle_cloud_legacy"; H="ubuntu@sellc.ohitech.co.kr"
DB="/home/ubuntu/ohisell/backend/ohisell.db"

# ① 우리 원장 세 곳에 자동입찰 컬럼이 «없다»는 것 (기대: 전부 0)
for t in naver_entity naver_entity_snapshot naver_adgroup_target_current; do
  printf "%-32s " "$t"
  ssh -i "$K" -o IdentitiesOnly=yes "$H" "sqlite3 -readonly $DB \"SELECT COUNT(*) FROM pragma_table_info('$t') WHERE lower(name) LIKE '%autobid%' OR lower(name) LIKE '%bidding%' OR lower(name) LIKE '%aiads%';\""
done

# ② change_log 페이로드의 자동입찰 3필드 분포 (기대: isAutobidActive 전건 0)
ssh -i "$K" -o IdentitiesOnly=yes "$H" "sqlite3 -readonly $DB \"
WITH latest AS (
  SELECT entity_id, MAX(changed_at) mx FROM naver_change_log
  WHERE entity_type='adgroup' AND after_value LIKE '{%' GROUP BY entity_id
), s AS (
  SELECT json_extract(c.after_value,'\\\$.autobidStrategy.isAutobidActive') active,
         json_extract(c.after_value,'\\\$.autobidStrategy.autobidBidGoal')  goal,
         json_extract(c.after_value,'\\\$.systemBiddingType')               systype,
         json_extract(c.after_value,'\\\$.aiAdsOptIn')                      aiads,
         date(c.changed_at) d
  FROM naver_change_log c JOIN latest l ON l.entity_id=c.entity_id AND l.mx=c.changed_at
  WHERE c.entity_type='adgroup'
)
SELECT COALESCE(active,'(필드없음)'), COALESCE(goal,'-'), COALESCE(systype,'-'),
       COALESCE(aiads,'-'), COUNT(*), MIN(d), MAX(d) FROM s GROUP BY 1,2,3,4 ORDER BY 5 DESC;\""

# ③ 관측 커버리지 (기대: 31그룹 전건 SHOPPING / 전체 1,017)
ssh -i "$K" -o IdentitiesOnly=yes "$H" "sqlite3 -readonly $DB \"
WITH latest AS (SELECT entity_id, MAX(changed_at) mx FROM naver_change_log
  WHERE entity_type='adgroup' AND after_value LIKE '%autobidStrategy%' GROUP BY entity_id)
SELECT COALESCE(e.campaign_type,'(미상)'), COUNT(*) FROM naver_change_log c
JOIN latest l ON l.entity_id=c.entity_id AND l.mx=c.changed_at
LEFT JOIN naver_entity e ON e.entity_type='adgroup' AND e.entity_id=c.entity_id
GROUP BY 1 ORDER BY 2 DESC;\""
ssh -i "$K" -o IdentitiesOnly=yes "$H" "sqlite3 -readonly $DB \"SELECT COUNT(*) FROM naver_entity WHERE entity_type='adgroup';\""

# ④ adgroup 페이로드의 전체 키 목록 (autobidStrategy·systemBiddingType·aiAdsOptIn 실재 확인)
ssh -i "$K" -o IdentitiesOnly=yes "$H" "sqlite3 -readonly $DB \"
SELECT DISTINCT key FROM naver_change_log, json_each(naver_change_log.after_value)
WHERE entity_type='adgroup' AND after_value LIKE '%autobidStrategy%' ORDER BY key;\""
```

**외부 출처 재확인**: §3-1·§3-2의 URL을 그대로 열 것. ★**참고(2차) 등급 항목은 §3-5의 경고를 먼저 읽고 인용할 것.**

---

## §10. 이 문서가 하지 않은 것
- **처분·우선순위 확정** — §7은 초안이고 채택은 Jino 몫이다.
- **코드 수정·설계 문서** — 읽기 전용 조사다.
- **「우리 vs 대행사」 숫자 생산** — ref 90 §6의 이유로 여전히 의도적 미생산이다. E1(대조군 분할)은 그 숫자를 «만드는 방법»의 제안이지 숫자가 아니다.
- **북극성 §3 수치 갱신** — 별건(같은 세션의 계기판 산출물 참조).
