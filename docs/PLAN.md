# PLAN.md — 현재 Sprint 계획서
# Planner가 작성 → Generator가 실행 → Evaluator가 검증

## Sprint 정보
- Sprint ID: sprint-0
- 목표: 초기 세팅
- 시작일: 2026-04-01

## 이번 Sprint에서 만들 것 (What)
1. Frontend 초기 구조 (React + Vite + TypeScript + Tailwind)
2. Backend 초기 구조 (FastAPI + SQLite + Alembic)
3. scripts/init.sh — 개발 서버 동시 시작

## 왜 이 순서인가 (Why)
- 기반 구조 없이는 피처 개발 불가
- Sprint 0은 항상 환경 세팅 우선

## 하지 않을 것 (Out of Scope)
- 실제 비즈니스 로직 구현
- UI 디자인
- 배포 설정

## Sprint Contract (Generator ↔ Evaluator 합의)
- [ ] scripts/init.sh 실행 시 frontend + backend 동시 시작
- [ ] localhost:5173 → React 초기 화면 노출
- [ ] localhost:8000/docs → FastAPI Swagger UI 노출
- [ ] localhost:8000/health → {"status": "ok"} 응답
- [ ] 폴더 구조가 CLAUDE.md 명세와 일치
- [ ] 초기 git commit 완료

## 구현 힌트 (Generator용)
- How는 Generator가 판단. What/Why만 따를 것
- 막히면 추측하지 말고 공식 문서 확인 후 구현
- 확인 안 된 내용은 먼저 알릴 것
