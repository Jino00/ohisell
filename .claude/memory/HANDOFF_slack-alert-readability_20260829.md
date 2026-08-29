# HANDOFF — Slack 광고 알림 가독성 수리 (2026-08-29, Opus)

> 목표이름: **광고알림 가독성 목표** · 세션 `424f54ed` · 저장소 Ohiselling
> 착지: 커밋 `582222ce` → PR [#571](https://github.com/Jino00/ohisell/pull/571) → 머지 `66dda262`

## 0. 목적 (Jino 원문 — 발명하지 말 것)

2026-08-29 16:26, Jino가 Slack 스크린샷과 함께: *"이렇게 오는 광고 메시지는 무슨의미인지 전혀 알 수가 없어. 알아볼 수 있게 메시지를 조절해줘"*

실물:
```
OhiSell_naver  오후 4:07
네이버 SA 제안 1건 생성
- trigger_pacing: 1건
```

## 1. 그 메시지의 뜻과 원인

**뜻**: 매시 :05에 도는 감시 루프(`trigger_watch.find_pacing_anomalies`)가 「어떤 캠페인이 오늘 예산을 예상보다 2배 빠르게(과속) 또는 정오 이후 절반 이하로 느리게(저속) 쓰고 있다」를 감지한 **정보성** 알림. 실행 매핑 자체가 없어 승인 대상이 아니고 하루 뒤 자동 만료된다.

**원인은 한 곳**: `slack_notifier._build_summary`가 유형별 **건수만** 세고, 호출부가 이미 쥐고 있던 캠페인·금액·배수를 통째로 버렸다.

★**같은 병을 이미 앓았고 약도 있었다** — CTR 경보가 ID·내부코드 투성이라 해석 불가였던 사고(2026-07-28, Jino 발의) 뒤 만든 것이 `alert_humanizer`(D-NAO-103)인데, **Slack 발송 경로에만 연결이 안 돼 있었다.** 「고쳤다」가 «그 표면»에서만 참이었던 것.

## 2. 한 일

| 파일 | 무엇 |
|---|---|
| `slack_notifier.py` | 사람이 읽는 본문(제목 / 승인 대기 절 / 참고용 절 / 마무리 안내). 유형 22종 한국어 라벨 + 미등록 유형 원문 폴백. 이름 없으면 ID 폴백 |
| `trigger_watch.py` | 경보 숫자를 사람 문장으로(`_pacing_human`·`_cpc_human`) + `alert_humanizer.entity_names`로 캠페인 이름 주입 |
| `proposal_pipeline.py` | 08:00 묶음도 같은 요약기를 쓰므로 이름을 붙여 넘김 |

**전 → 후**
```
네이버 SA 제안 1건 생성          👀 네이버 SA · 예산 소진이 너무 느림 (1건)
- trigger_pacing: 1건
                                  • 03. 아이폰_강화유리
                                    16시 기준 1.2만 원 씀 / 하루예산 10.0만 원
                                    이 시각이면 6.6만 원쯤 나갔을 자리 (기대의 0.2배)
                                    → 이대로면 오늘 예산을 다 못 씁니다.

                                 참고용 알림입니다 — 자동으로 바뀌는 건 없습니다.
                                 입찰·예산 조정은 08:00 정기 검토가 판단합니다.
```

## 3. ★다음 사람이 밟지 말아야 할 함정

- ⚠️**`trigger_pacing`의 `rationale`은 고치면 안 된다.** `retro_pacing_scorer._PATTERN`이 그 문자열을 정규식으로 파싱해 사후 채점한다. 「읽기 좋게 하겠다」고 손대면 채점이 조용히 죽는다. **기계용 문장(rationale)과 사람용 문장(Slack)을 갈라 둔 것이 이 수리의 핵심**이고, 회귀 가드는 `test_saved_rationale_still_parses_for_retro_scoring`.
- ⚠️**통지용 dict와 DB용 dict를 섞지 마라.** `NaverProposal(created_at=now, **p)`라 모델에 없는 키가 들어가면 저장이 깨진다. `_slack_payload`가 사본을 만든다.
- ⚠️**`slack_notifier`에서 `proposal_writer`를 import하면 순환이다**(`proposal_writer`→`trigger_watch`→`slack_notifier`). `_INFORMATIONAL_TYPES` 값 복제는 의도된 회피고, 드리프트는 `test_informational_set_matches_proposal_writer`가 잡는다.

## 4. 적대 리뷰

- **1R FAIL (P1 1건)** — 08:00 경로의 표면 배선이 테스트로 고정돼 있지 않았다. 이름 주입을 통째로 지워도 **113건이 전부 초록**이었고 Slack 본문은 조용히 `— cmp1`로 회귀했다. 변이 9종 중 2종 생존, 둘 다 그 자리.
  → 해소: `test_run_daily_posts_campaign_name_to_slack_not_raw_ids`. `run_daily`를 통째로 태워 `requests.post`의 `json["text"]`를 직접 잰다. 2R에서 리뷰어가 그 2종을 재주입해 둘 다 잡히는 것 확인.
- **P2 채택 4**: 같은 캠페인 중복 나열 접기 · 단일 묶음 제목 중복 · 극단 저속의 「기대의 0.0배」 뭉갬 · 사람 문장 빌더 없는 유형에서 통지가 사라지며 `reason:"no_proposals"`로 **원인을 오보**하던 경로.
- **P2 기각 1**: 키워드 단위 표시(합격기준이 「어느 캠페인」이라 범위 밖 → 이월).
- **2R PASS · P1 = 0.**

## 5. 완료 QA (별도 기 · Sonnet high · 읽기 전용) — 대조 2건

```
판정(앵커 §합격): 달성 — 6항목 전부 라이브 증거로 충족
판정(Jino 지시 원문): 부분달성 — 렌더된 본문 자체는 지시를 충족하나,
  실제 Slack 채널 도착·Jino 육안 확인은 배포가 없어 미검증.
  지금 손에 있는 증거는 webhook requests.post 페이로드까지다.
```

**미검증으로 남은 것**: 이 문구가 실제 Jino의 Slack에 도착하는 것은 **아직 아무도 안 봤다.** VM 배포(cron rsync → pm2)가 돌아야 하고, 진짜 페이싱 이탈이 나야 뜬다. 실제 발송으로 앞당기려면 외부 발송이라 Jino 승인이 필요하다(이 세션에서 물었으나 답 없이 종료).

## 6. 이월 (고치지 말고 적은 것)

- 실행형 제안(bid_up·negative_keyword 등)은 **키워드 단위**인데 Slack은 캠페인 이름까지만 — 「어느 키워드인지」가 없다. → 소관: `docs/tracks/active/track_naver-ad-optimization.md`
- 사람 문장 빌더가 있는 유형은 pacing·CPC **2종뿐**. 나머지 20종은 유형 라벨 + 대상 이름까지만 간다(숫자 상세 없음). → 같은 트랙
- ⚠️**iCloud 본 폴더 `AI Program/Ohiselling`의 로컬 `main`이 `origin/main`보다 800 커밋 뒤처져 있다.** 그 폴더의 체인 등록부 `pao-논의.jsonl`은 **n=49(8/25)에서 멈춰** 있고 실제는 **n=68(8/29 15:4x 종결)**이다.
  ★이 세션은 그 stale 사본 때문에 착수 시 「n=49가 `end_kst: null` = 살아 있다」로 **오판**했다. Jino 확인과 `git show origin/main:...`으로 정정. **생존 판정을 로컬 파일로만 하면 틀린다 — `origin/main`의 등록부를 봐야 한다.**

## 7. 테스트

- 관련 4파일 **118 passed** · naver 전량 **3,760 passed / 0 failed**
- CI 3/3 통과 — **backend py3.10(VM 버전)** · backend py3.14 · frontend
- 변이 주입 누적 13종(표면 3종 포함), 최종 전부 잡힘 · 전건 원복 확인

## 8. 다음 후보

1. VM 배포 후 실제 Slack 채널에서 새 본문 1건 육안 확인 → 「Jino 지시 원문」 판정을 부분달성 → 달성으로 올릴 수 있다.
2. 이월 2건(키워드 단위 표시 · 나머지 20종 상세)
3. iCloud 본 폴더 `main` 정리(800 커밋 뒤처짐)
