import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'

// Базовый путь: на GitHub Pages сайт живёт в /GrantumParser/,
// локально и при сборке для Pages подхватывается автоматически.
// REPO_BASE нужен, если имя репозитория изменится.
const repoName = process.env.GITHUB_REPOSITORY?.split('/')[1] ?? 'GrantumParser'

// GitHub Pages отдаёт 404 на любой путь SPA (/logs, /results при F5).
// Лечится копией index.html в 404.html: Pages показывает её на несуществующих
// путях, приложение загружается и BrowserRouter сам подхватывает текущий URL.
function spaFallback() {
  return {
    name: 'spa-fallback-404',
    apply: 'build',
    closeBundle() {
      fs.copyFileSync(
        path.resolve(import.meta.dirname, 'dist/index.html'),
        path.resolve(import.meta.dirname, 'dist/404.html'),
      )
    },
  }
}

export default defineConfig(({ mode }) => ({
  base: mode === 'development' ? '/' : `/${repoName}/`,
  plugins: [react(), spaFallback()],
}))

