import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, NavLink, Routes, Route } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import LogsPage from "./pages/LogsPage";
import Results from "./pages/Results";
import Profiles from "./pages/Profiles";
import Catalog from "./pages/Catalog";
import Login from "./pages/Login";
import { api, logout } from "./api";

/**
 * Каркас админки Grantum.
 *
 * - BrowserRouter даёт контекст роутера всему дереву.
 * - NavLink сам помечает активную ссылку (className получает { isActive }).
 * - Роут "/" end, чтобы не матчился вместе с /logs и /results.
 * - /logs/:jobId — опциональный параметр для прямых ссылок на задачу.
 *
 * Аутентификация: пока /jobs не ответил успешно, весь контент скрыт
 * за экраном логина. Событие gt:unauthorized (401 из api.js) снова
 * показывает логин — например, после истечения сессии.
 */
export default function App() {
  const [authed, setAuthed] = useState(false); // null = проверяется
  const [checking, setChecking] = useState(true);

  const checkAuth = useCallback(async () => {
    setChecking(true);
    try {
      await api.get("/jobs");
      setAuthed(true);
    } catch {
      setAuthed(false);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    checkAuth();
    const onUnauthorized = () => setAuthed(false);
    window.addEventListener("gt:unauthorized", onUnauthorized);
    return () => window.removeEventListener("gt:unauthorized", onUnauthorized);
  }, [checkAuth]);

  async function handleLogout() {
    try {
      await logout();
    } catch {
      /* сессия уже мертва — не страшно */
    }
    setAuthed(false);
  }

  if (!authed) {
    if (checking) return null; // короткая проверка сессии без мигания
    return <Login onSuccess={() => setAuthed(true)} />;
  }

  return (
    <BrowserRouter>
      <style>{shellCss}</style>

      {/* Амбиентный фон: два медленно дрейфующих цветовых пятна поверх базы. */}
      <div className="gt-ambient" aria-hidden="true" />

      <div className="gt-shell">
        <header className="gt-topbar">
          <NavLink to="/" end className="gt-brand">
            <span className="gt-brand-mark" aria-hidden="true">
              G
            </span>
            <span className="gt-brand-text">
              <span className="gt-brand-name">Grantum</span>
              <span className="gt-brand-sub">parser console</span>
            </span>
          </NavLink>

          <nav className="gt-nav" aria-label="Основная навигация">
            <NavLink
              to="/"
              end
              className={({ isActive }) =>
                "gt-nav-link" + (isActive ? " is-active" : "")
              }
            >
              <span className="gt-nav-index">01</span>
              Запуск
            </NavLink>
            <NavLink
              to="/logs"
              className={({ isActive }) =>
                "gt-nav-link" + (isActive ? " is-active" : "")
              }
            >
              <span className="gt-nav-index">02</span>
              Логи
            </NavLink>
            <NavLink
              to="/results"
              className={({ isActive }) =>
                "gt-nav-link" + (isActive ? " is-active" : "")
              }
            >
              <span className="gt-nav-index">03</span>
              Результаты
            </NavLink>
            <NavLink
              to="/catalog"
              className={({ isActive }) =>
                "gt-nav-link" + (isActive ? " is-active" : "")
              }
            >
              <span className="gt-nav-index">04</span>
              Каталог
            </NavLink>
            <NavLink
              to="/profiles"
              className={({ isActive }) =>
                "gt-nav-link" + (isActive ? " is-active" : "")
              }
            >
              <span className="gt-nav-index">05</span>
              Профили
            </NavLink>

            <button
              type="button"
              className="gt-logout"
              onClick={handleLogout}
              title="Завершить сессию"
            >
              выход
            </button>
          </nav>
        </header>

        <main className="gt-content">
          <Routes>
            <Route path="/" end element={<Dashboard />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/logs/:jobId" element={<LogsPage />} />
            <Route path="/results" element={<Results />} />
            <Route path="/catalog" element={<Catalog />} />
            <Route path="/profiles" element={<Profiles />} />
            <Route path="*" element={<Dashboard />} />
          </Routes>
        </main>

        <footer className="gt-foot">
          <span>Grantum · F6S parser</span>
          <span className="gt-foot-dot" aria-hidden="true" />
          <span>MVP build</span>
        </footer>
      </div>
    </BrowserRouter>
  );
}

/**
 * Локальная визуальная система каркаса.
 * Префикс gt- защищает классы от столкновений со стилями страниц.
 * Перебивает дефолтный Vite CSS (центрирование body / #root) за счёт
 * более позднего порядка в DOM при равной специфичности.
 */
const shellCss = `
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

:root {
  --gt-bg: #0f1320;
  --gt-bg-soft: #141a2b;
  --gt-line: rgba(255, 255, 255, 0.08);
  --gt-ink: #e8ecf6;
  --gt-ink-dim: #97a3bd;
  --gt-amber: #f0a850;
  --gt-teal: #58d6c0;
  --gt-display: 'Space Grotesk', system-ui, sans-serif;
  --gt-body: 'IBM Plex Sans', system-ui, sans-serif;
}

* { box-sizing: border-box; }

html, body, #root {
  margin: 0;
  padding: 0;
  width: 100%;
  max-width: none;
  text-align: left;
}

body {
  background: var(--gt-bg);
  color: var(--gt-ink);
  font-family: var(--gt-body);
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}

.gt-ambient {
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  overflow: hidden;
  background:
    radial-gradient(120% 80% at 50% -10%, rgba(240, 168, 80, 0.10), transparent 60%),
    var(--gt-bg);
}
.gt-ambient::before,
.gt-ambient::after {
  content: "";
  position: absolute;
  width: 60vmax;
  height: 60vmax;
  border-radius: 50%;
  filter: blur(90px);
  opacity: 0.5;
}
.gt-ambient::before {
  top: -20vmax;
  left: -10vmax;
  background: radial-gradient(circle, rgba(240, 168, 80, 0.22), transparent 70%);
  animation: gt-drift-a 22s ease-in-out infinite alternate;
}
.gt-ambient::after {
  bottom: -25vmax;
  right: -15vmax;
  background: radial-gradient(circle, rgba(88, 214, 192, 0.18), transparent 70%);
  animation: gt-drift-b 26s ease-in-out infinite alternate;
}
@keyframes gt-drift-a {
  from { transform: translate3d(0, 0, 0) scale(1); }
  to   { transform: translate3d(8vmax, 6vmax, 0) scale(1.15); }
}
@keyframes gt-drift-b {
  from { transform: translate3d(0, 0, 0) scale(1.1); }
  to   { transform: translate3d(-7vmax, -5vmax, 0) scale(1); }
}

.gt-shell {
  position: relative;
  z-index: 1;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.gt-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1.5rem;
  padding: 1.25rem clamp(1.25rem, 4vw, 3rem);
  border-bottom: 1px solid var(--gt-line);
  backdrop-filter: blur(6px);
  position: sticky;
  top: 0;
  z-index: 5;
  background: rgba(15, 19, 32, 0.55);
}

.gt-brand {
  display: inline-flex;
  align-items: center;
  gap: 0.85rem;
  text-decoration: none;
  color: inherit;
}
.gt-brand-mark {
  display: grid;
  place-items: center;
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 0.7rem;
  font-family: var(--gt-display);
  font-weight: 700;
  font-size: 1.25rem;
  color: #1a1206;
  background: linear-gradient(150deg, var(--gt-amber), #ffd79a);
  box-shadow: 0 6px 20px rgba(240, 168, 80, 0.25);
  transition: transform 0.25s ease;
}
.gt-brand:hover .gt-brand-mark { transform: rotate(-6deg) scale(1.05); }
.gt-brand-text { display: flex; flex-direction: column; line-height: 1.05; }
.gt-brand-name {
  font-family: var(--gt-display);
  font-weight: 600;
  font-size: 1.2rem;
  letter-spacing: -0.01em;
}
.gt-brand-sub {
  font-size: 0.68rem;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--gt-ink-dim);
}

.gt-nav { display: flex; align-items: center; gap: clamp(0.5rem, 2vw, 1.75rem); }
.gt-nav-link {
  position: relative;
  display: inline-flex;
  align-items: baseline;
  gap: 0.45rem;
  padding: 0.4rem 0.1rem;
  text-decoration: none;
  color: var(--gt-ink-dim);
  font-weight: 500;
  font-size: 0.95rem;
  letter-spacing: 0.01em;
  transition: color 0.2s ease;
}
.gt-nav-index {
  font-family: var(--gt-display);
  font-size: 0.62rem;
  letter-spacing: 0.1em;
  color: rgba(151, 163, 189, 0.55);
  transition: color 0.2s ease;
}
.gt-nav-link::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: -2px;
  height: 2px;
  border-radius: 2px;
  background: var(--gt-amber);
  transform: scaleX(0);
  transform-origin: left;
  transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}
.gt-nav-link:hover { color: var(--gt-ink); }
.gt-nav-link:hover::after { transform: scaleX(0.5); }
.gt-nav-link.is-active { color: var(--gt-ink); }
.gt-nav-link.is-active .gt-nav-index { color: var(--gt-amber); }
.gt-nav-link.is-active::after { transform: scaleX(1); }

.gt-logout {
  background: none; border: 1px solid var(--gt-line); border-radius: 999px;
  padding: 0.35rem 0.9rem; cursor: pointer;
  color: var(--gt-ink-dim); font: inherit; font-size: 0.8rem;
  letter-spacing: 0.05em; transition: all 0.18s ease;
}
.gt-logout:hover {
  color: #ff8f7a; border-color: rgba(255,143,122,0.5);
}

.gt-content {
  flex: 1;
  width: 100%;
  max-width: 1180px;
  margin: 0 auto;
  padding: clamp(1.75rem, 4vw, 3.25rem) clamp(1.25rem, 4vw, 3rem) 4rem;
}

.gt-foot {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 1.25rem clamp(1.25rem, 4vw, 3rem);
  border-top: 1px solid var(--gt-line);
  color: var(--gt-ink-dim);
  font-size: 0.78rem;
  letter-spacing: 0.04em;
}
.gt-foot-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--gt-teal);
  box-shadow: 0 0 10px var(--gt-teal);
}

@media (prefers-reduced-motion: reduce) {
  .gt-ambient::before, .gt-ambient::after { animation: none; }
  .gt-brand-mark, .gt-nav-link::after { transition: none; }
}

@media (max-width: 620px) {
  .gt-topbar { flex-direction: column; align-items: flex-start; gap: 1rem; }
  .gt-nav { width: 100%; justify-content: space-between; }
}
`;
