#!/bin/bash
# init.sh — ohisell 개발 서버 시작
# 프로젝트 루트에서 실행: bash scripts/init.sh

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "🚀 ohisell 개발 서버 시작 중..."

# Backend (venv 활성화 후 실행)
cd backend
source .venv/bin/activate
alembic upgrade head 2>/dev/null
python -m app.seed 2>/dev/null
python -m uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!
echo "✅ Backend → http://localhost:8000 (PID: $BACKEND_PID)"
cd "$PROJECT_ROOT"

# Frontend
cd frontend
npm run dev &
FRONTEND_PID=$!
echo "✅ Frontend → http://localhost:5173 (PID: $FRONTEND_PID)"
cd "$PROJECT_ROOT"

echo ""
echo "📖 API 문서: http://localhost:8000/docs"
echo "🛑 종료: kill $BACKEND_PID $FRONTEND_PID"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
