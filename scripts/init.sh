#!/bin/bash
# init.sh — 개발 서버 시작
# Claude가 이 파일을 읽어 개발 환경을 파악합니다.

set -e

echo "🚀 개발 서버 시작 중..."

# Backend
cd backend
python -m uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!
echo "✅ Backend → http://localhost:8000 (PID: $BACKEND_PID)"
cd ..

# Frontend
cd frontend
npm run dev &
FRONTEND_PID=$!
echo "✅ Frontend → http://localhost:5173 (PID: $FRONTEND_PID)"
cd ..

echo ""
echo "📖 API 문서: http://localhost:8000/docs"
echo "🛑 종료: kill $BACKEND_PID $FRONTEND_PID"

wait
