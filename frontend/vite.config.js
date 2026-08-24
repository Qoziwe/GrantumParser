import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Базовый путь: на GitHub Pages сайт живёт в /GrantumParser/,
// локально и при сборке для Pages подхватывается автоматически.
// REPO_BASE нужен, если имя репозитория изменится.
const repoName = process.env.GITHUB_REPOSITORY?.split('/')[1] ?? 'GrantumParser'

export default defineConfig(({ mode }) => ({
  base: mode === 'development' ? '/' : `/${repoName}/`,
  plugins: [react()],
}))
