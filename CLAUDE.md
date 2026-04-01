# CLAUDE.md — 프로젝트 설정
# 프로젝트명: ohisell
# 이 파일은 전역 CLAUDE.md와 합쳐져 적용됩니다.

## 프로젝트 개요
- 이름: ohisell
- 목적: 오하이가 판매중인 오픈쇼핑몰에서의 실적 관리
- 상태: 계획 중
- 시작일: 2026-04-01

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
