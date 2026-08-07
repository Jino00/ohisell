import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // `_` 접두사는 "일부러 안 쓴다"는 표기다. 기본 설정은 이걸 안 봐줘서
      // 의도적으로 남긴 인자(`shipSub(_rgFf)` 등)가 error로 잡혔다.
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],

      // ── 부채 구간 (2026-08-07 CI 신설 시 실측) ────────────────────────────
      // 아래 넷은 **고칠 값어치가 있지만 이번에 고치지 않았다** — 테스트가 없는
      // 페이지의 동작을 바꾸는 리팩터라 CI 신설과 같은 커밋에 섞으면 안 된다.
      // error로 두면 CI가 첫날부터 빨간불이 되어 **무시당하는 검사**가 되므로
      // warn으로 낮추되, CI가 `--max-warnings`로 총량을 고정한다(늘어나면 실패).
      // ★숫자를 줄이는 방향으로만 움직여야 한다. 늘리려면 그건 새 부채다.
      //   no-explicit-any                     31건
      //   react-refresh/only-export-components 11건
      //   react-hooks/static-components         6건 (CommandCenter 446~454)
      //   react-hooks/set-state-in-effect       4건 (ProductForm·InventoryPage×2·Products)
      '@typescript-eslint/no-explicit-any': 'warn',
      'react-refresh/only-export-components': 'warn',
      'react-hooks/static-components': 'warn',
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
])
