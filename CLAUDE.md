# CLAUDE.md — ohisell
# 전역 ~/.claude/CLAUDE.md v2(계약 기반)와 합쳐져 적용된다. 구판: ~/.claude/archive/harness_v1_20260802/projects/

## 프로젝트 개요
- 이름: ohisell
- 목적: 오하이가 판매중인 오픈쇼핑몰에서의 실적 관리
- 상태: 운영 중 + 메가 프로젝트 진행
- 시작일: 2026-04-01

## ★ 진행 중 메가 프로젝트 (세션 시작 시 반드시 확인 — 기획 변형 금지)
- **단일 진입점: `docs/TRACKS.md`** — 활성 트랙 인덱스를 먼저 읽을 것.
- **현재 주력 트랙: `docs/tracks/active/track_naver-ad-optimization.md`** (네이버 SA 광고 최적화 — 우리판 MOP, 확정 결정 D-NAO-1~34 누적).
  - F0~F2+E1a 완료·prod 가동 중(크론 07:50/08:00/08:05/08:10). **현재 스프린트 = 실행 루프(X): `docs/PLAN_naver-ad-execution-loop.md` — 이 트랙을 건드리는 모든 세션은 그 문서 §0(방향 고정)을 먼저 읽고 §7 체크리스트로 현재 위치를 확인할 것**(D-NAO-34, 스프린트 끝날 때까지 유지. 배경 문서: ref 25 갭 분석·ref 26 논문 서베이).
  - 스프린트 X 진행 중 금지선: Phase 구조·개방 순서(제외키워드→정지재개→입찰) 임의 변경 금지, 예산 변경 개방은 스코프 밖, 위임 스위치는 Jino만. 설계=fable, 구현=Sonnet.
  - 이 트랙 작업은 main 기준 워크트리에서(2026-07-08 main 병합 완료 — 구 admiring-solomon 브랜치 고정 지침은 해제). 작업 전 `git branch --show-current` 확인 습관 유지.
- 이 트랙을 무시하거나 변형해서 진행하지 말 것. 변경은 Jino 승인 후 트랙 파일에 D-N으로 기록.
- (쿠팡 full-integration 트랙은 완료됨 — `docs/TRACKS.md` Completed 참조.)

## 기술 스택
- Frontend: React + Vite + TypeScript + Tailwind CSS
- Backend: FastAPI (Python 3.11+)
- DB: SQLite → PostgreSQL
- 기타: [추가 시 여기에]

## 폴더 구조
- CLAUDE.md: 이 파일
- claude-progress.txt: 세션 간 인계
- FEATURES.json: 전체 피처 목록
- docs/PLAN.md: 현재 Sprint 계획서
- docs/CONTEXT.md: 기술 결정 맥락
- docs/CHECKLIST.md: 작업 체크리스트
- scripts/init.sh: 개발 서버 시작

## 이 프로젝트만의 규칙 (금지선)

### ★prod 배포는 반드시 `scripts/safe_deploy.sh` — 직접 scp/rsync 금지 (D-NAO-49)
- 이유(2026-07-17 사고): 병행 세션 둘 다 "배포 → 나중에 PR" 순서라, 직접 scp는 **상대 세션이
  방금 배포한 코드를 구버전으로 덮는다**(qi 수집이 4분 만에 죽음). 문서 규칙은 세 번 다
  못 막았다 — 이 스크립트가 구조로 막는다.
- 동작: prod 파일의 현재 내용이 **내 브랜치 역사에 없는 버전이면 배포 거부**(CAS) +
  prod 측 배포 락 + 커밋 안 된 파일 거부 + 배포 매니페스트 기록.
- 백엔드: `scripts/safe_deploy.sh backend/app/... [--restart]` / 프론트: `--frontend`
- CAS 거부가 뜨면 = 다른 세션이 배포한 것. **덮지 말고** fetch·병합 후 재시도.

### ★DB 변경이 있으면 `--migrate` (마이그레이션 순서 가드, 2026-07-28)
- 이유(rocket-1p 리뷰 실증): 이 앱은 **부팅 시 인프로세스 마이그레이션을 하지 않는다**.
  `models.py`를 마이그레이션보다 먼저 배포하면 nullable 컬럼 추가라도 ORM이 엔티티를 통째로
  SELECT 하다 `OperationalError: no such column` → **그 테이블 ingest 경로가 통째로 침묵**한다
  (신규 필드만 죽는 게 아니다). 순서를 docstring/HANDOFF에 적는 방식은 이미 실패했으므로
  safe_deploy.sh가 구조로 강제한다.
- 강제 순서: **①마이그 파일 배포 → ②원격 `alembic upgrade head` → ③코드 배포 → ④재시작**
- `scripts/safe_deploy.sh backend/alembic/versions/xxx.py backend/app/models.py --migrate --restart`
- `--migrate` 없이 마이그레이션 대기 상태면 **코드 배포·재시작 거부**(수동 명령 안내 출력).
  커밋된 로컬 마이그 파일이 prod에 없는데 배포 목록에도 없으면 그것도 거부.
- 컬럼 삭제처럼 **구코드를 깨는** 마이그레이션은 순서가 반대 → `--migrate` 쓰지 말고 수동 조율.

## 스킬 라우팅 힌트 (선택 도구 — 자동 실행 의무 없음)

| 요청 유형 | 유용한 스킬 |
|---|---|
| 제품 아이디어·"만들 가치가 있나"·브레인스토밍 | `/office-hours` |
| 버그·에러·500·"왜 깨졌나" | `/investigate` |
| 배포·push·PR 생성 | `/ship` · `/land-and-deploy` |
| 사이트 QA·버그 찾기 | `/qa` |
| 코드 리뷰·diff 점검 | `/review` |
| 배포 후 문서 갱신 | `/document-release` |
| 주간 회고 | `/retro` |
| 디자인 시스템·브랜드 | `/design-consultation` |
| 비주얼 감사·디자인 폴리시 | `/design-review` |
| 아키텍처 리뷰 | `/plan-eng-review` |
| 코드 품질·헬스 체크 | `/health` |
