# CLAUDE.md — 프로젝트 설정
# 프로젝트명: ohisell
# 이 파일은 전역 CLAUDE.md와 합쳐져 적용됩니다.

## 프로젝트 개요
- 이름: ohisell
- 목적: 오하이가 판매중인 오픈쇼핑몰에서의 실적 관리
- 상태: 운영 중 + 메가 프로젝트 진행
- 시작일: 2026-04-01

## ★ 진행 중 메가 프로젝트 (세션 시작 시 반드시 확인 — 기획 변형 금지)
- **트랙: `docs/tracks/active/track_coupang-full-integration.md`** ← 단일 진실 원천
- 쿠팡 Open API 100개 엔드포인트(읽기+쓰기 전부) 연결 → 종합 조망(Command Center).
- 핵심 불변 결정: ① 백엔드 우선→프론트 ② 시스템은 사실/지표 정리만(전략 추천 금지) ③ 광고비는 XLSX 업로드(공식 API 없음) ④ 아키텍처 clients/coupang/*(SA)→services/coupang/*(Harness)→routers/pages.
- 이 트랙을 무시하거나 변형해서 진행하지 말 것. 변경은 Jino 승인 후 트랙 파일에 D-N으로 기록.

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

## 이 프로젝트만의 규칙
(없음 — 전역 CLAUDE.md 규칙 따름)

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
