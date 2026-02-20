import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],

      // ── Project-specific rules aligned with Guidelines.md ──────

      // Catch unused variables but allow underscore-prefixed ones
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],

      // Allow empty object types (common in component props extending base)
      '@typescript-eslint/no-empty-object-type': 'off',
    },
  },
  {
    files: ['src/app/lib/api/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                '**/lib/rlm-api',
                '**/lib/rlm-api/*',
                './rlm-api',
                './rlm-api/*',
                '../rlm-api',
                '../rlm-api/*',
                '../../lib/rlm-api',
                '../../lib/rlm-api/*',
              ],
              message:
                'lib/api must not import from lib/rlm-api. Keep legacy/fallback surfaces isolated.',
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/app/lib/rlm-api/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                '**/lib/api',
                '**/lib/api/*',
                './api',
                './api/*',
                '../api',
                '../api/*',
                '../../lib/api',
                '../../lib/api/*',
              ],
              message:
                'lib/rlm-api must not import from lib/api. Keep core backend contracts isolated.',
            },
          ],
        },
      ],
    },
  },
);
