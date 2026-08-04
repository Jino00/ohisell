# 세션 인수인계: origin/main 병합 — 교훈·D-NAO 번호 충돌 해소

> 저장일시: 2026-08-04 23:xx KST · 트랙: 네이버 SA 광고 최적화 · 기록: 코드 변경 0(순수 정리)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 0. 한 줄 결론

`git merge origin/main`이 두 번 충돌했다 — 병행 세션(캠페인·그룹 `regTm` 신설 시각 부여,
D-NAO-148)이 main을 먼저 밀어 넣었고, 이 브랜치가 같은 시각 창에서 쓴 항목들과 번호가
겹쳤다. **main이 트렁크이므로 이 브랜치 쪽을 뒤로 재번호**했다(관례, 전례: LESSONS #113
"CAS는 합집합을 만들어라는 신호다"). 결정·교훈 본문은 한 글자도 안 바꿨다.

## 1. 처리 내역

### ① 교훈 번호 충돌 — 커밋 `ad1c890`
- 우리 브랜치 LESSONS #129~137(9건) ↔ main #129·#130(editTm 백필 금지선·복원 불가 추정 금지, 2건).
- **main 129·130은 그대로, 우리 것을 131~139로 +2 시프트.**
- 본문 내 자기참조(`#134`→`#136` 등)도 매핑대로 갱신. 크로스체크한 HANDOFF·트랙·claude-progress.txt
  중 실제 레슨 포인터였던 곳(관련 HANDOFF 2건)도 같이 갱신 — 나머지는 PR 번호이거나 128 이하라 무관.

### ② D-NAO 번호 충돌 — 이번 커밋
- 우리 브랜치 **D-NAO-148(원가 미상 매출을 손익 화면에 상설 표면화, 커밋 `88b0f42`)** ↔
  main의 **D-NAO-148(구조 신설·키워드 등록에 `regTm` 부여, 커밋 `57f8ece`)**.
- **main 148(regTm)은 그대로, 우리 148(원가 미상 표면화) → D-NAO-153으로 재번호.**
  D-NAO-149~152(매핑 소급 연결·원가 VAT축·회수비·손익 정합 종결·원가 미상 원인 분해)는
  main과 겹치지 않아 번호 그대로.
- ⚠️**커밋 메시지·해시는 고칠 수 없다.** `88b0f42`의 커밋 메시지는 이미 `D-NAO-146`으로 적혀
  있었고(1차 재부여 흔적), 트랙·HANDOFF 주석을 "→ 최종 D-NAO-153으로 재부여(커밋 메시지는
  그대로)"로 갱신했다.
- 갱신한 참조: `docs/tracks/active/track_naver-ad-optimization.md`(정의 1곳 + 다음 액션 목록
  2곳) · `.claude/memory/HANDOFF_monthly-fixed-cost+exchange-pnl_20260804.md`(정의·표·부채
  목록 등 6곳) · `.claude/memory/HANDOFF_relink-backfill+cost-axis-confirmed_20260804.md`
  (2곳) · `.claude/memory/LESSONS_LEARNED.md`(#136 제목의 D-NAO 태그 1곳) · `claude-progress.txt`
  (섹션 헤더 1곳). `docs/TRACKS.md`는 D-NAO-148/153 언급 자체가 없어 무변경.

## 2. 검증

```
grep -oE "D-NAO-1[0-9]{2}" docs/tracks/active/track_naver-ad-optimization.md | sort -u | tail -12
# ...D-NAO-148 D-NAO-149 D-NAO-150 D-NAO-151 D-NAO-152 D-NAO-153
grep -n "^\- \*\*D-NAO-1[4-5][0-9]" docs/tracks/active/track_naver-ad-optimization.md
# 149~153 각 1회씩만 정의, 148은 regTm(main) 정의 1회만 — 우리 원가 미상 항목은 153으로 이동됨
```

## 3. 다음 세션이 볼 것

- 두 병합 모두 `push`는 안 했다 — 브랜치 `claude/naver-display-ad-costs`에 로컬 커밋만 있음.
- 결정 내용 변경 없음. D-NAO-153(구 148, 원가 미상 표면화)의 실제 구현·라이브 합격 증거는
  `HANDOFF_monthly-fixed-cost+exchange-pnl_20260804.md` §2를 볼 것 — 이 파일은 번호 정리
  기록일 뿐 재검증은 안 했다.
