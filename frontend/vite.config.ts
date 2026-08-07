/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // 기본 환경은 node(순수 로직 테스트가 대부분). 컴포넌트 테스트(.test.tsx)는 파일 상단의
  // `// @vitest-environment jsdom` 도크블록으로 개별 전환한다 — 전역 jsdom은 느리기만 하다.
  test: { environment: "node", include: ["src/**/*.test.ts", "src/**/*.test.tsx"] },
})
