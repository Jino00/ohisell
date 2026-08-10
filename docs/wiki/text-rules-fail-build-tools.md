---
pattern: 같은 규칙을 세 번 어겼으면 규칙이 아니라 도구가 필요하다
sources: [D-NAO-49, PR #217, 교훈 #147~#150, issue #247, 교훈 #201, CLAUDE.md 프로젝트 금지선]
enforcement: tool
enforcement_target: scripts/safe_deploy.sh · scripts/next_ids.sh · scripts/safe_merge.sh · scripts/install_hooks.sh
recurrence_tags: [rule-decay]
---

# 같은 규칙을 세 번 어겼으면 규칙이 아니라 도구가 필요하다

## 패턴

문서에 적힌 규칙은 **읽는 사람이 그 순간 기억할 때만** 작동한다.
병행 세션·컨텍스트 압축·급한 국면에서는 기억이 실패한다.
**3회 재발 = 텍스트 규칙의 사망 선고**이고, 그때부터는 구조가 막아야 한다.

## 이 repo의 계보 (전부 «세 번 실패 후 도구»)

| 도구 | 앞선 실패 | 막는 방식 |
|---|---|---|
| `safe_deploy.sh` (D-NAO-49) | 병행 세션이 서로의 배포를 덮음 — 문서 규칙이 **세 번 다 못 막음** | prod 파일이 내 브랜치 역사에 없으면 **배포 거부**(CAS) + 배포 락 |
| `--frontend` 스탬프 CAS | 프론트 clobber **3회**, 발견은 매번 **우연**(번들 해시 육안 대조) | `dist/.deploy-stamp`가 내 HEAD 조상이 아니면 거부. 도입 당일 4번째 차단 |
| `next_ids.sh` | D-NAO·교훈 번호 충돌 **3회**, "번호 부여 전에 fetch"가 HANDOFF에 **세 번 적히고 세 번 안 지켜짐** | origin/main과 내 브랜치 최댓값 중 큰 쪽 +1 출력 |
| `safe_merge.sh` | 빨간 CI 병합 · **체크 0건을 초록으로 오독** · CONFLICTING 병합(PR #231) | 거부 + `--force` 사용 시 자백 로그 |
| pre-commit 훅 (issue #247) | 공유 메인 폴더에서 남의 미추적 파일이 `git add -A`에 쓸림 (관측 2회) | main 아닌 브랜치 커밋 거부 + 새 파일 목록 표시 |

## 도구의 형태 — «차단»과 «거부+자백»을 구분한다

되돌릴 수 있는 사고(빨간 병합, 섞인 커밋)는 **revert가 되므로 하드 게이트 근거가 없다.**
그런 건 `--force` 탈출구를 두고 **자백 로그**를 남긴다. 하드 차단은 되돌릴 수 없을 때만.
(전역 CLAUDE.md §1 — 승인 지점은 계약 1회와 되돌릴 수 없는 액션 둘뿐.)

## 도구가 있어도 실패하는 자리

`next_ids.sh`는 정확히 답했는데 **부르는 순서**가 틀려 4번째 충돌이 났다(교훈 #201):
`next_ids.sh && cat >> LESSONS…`를 한 명령으로 묶어 출력을 읽기 전에 번호를 지어냈다.
→ [[read-external-values-before-writing]]

## 감사 질문

- 최근 4주에 **같은 태그로 2회 이상** 재발한 교훈이 있는가? → 있으면 도구화 백로그로.
- `enforcement: principle`인 항목이 재발했는가? → **principle은 최후수단이므로 승격 대상.**
