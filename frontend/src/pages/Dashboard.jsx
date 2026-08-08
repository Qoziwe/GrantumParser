import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { fetchJobs, startParse } from "../api";

/**
 * Dashboard — главный экран запуска парсера.
 *
 * Что здесь происходит:
 *  - консольный инпут для ссылки + кнопка запуска (POST /parse через startParse);
 *  - лента последних задач с автоопросом /jobs каждые 5 секунд;
 *  - после успешного запуска — карточка-подтверждение с обратным отсчётом
 *    автоперехода на /logs/:id (переход можно отменить).
 *
 * Стили локальные, префикс gd- (grantum dashboard), цвета тянем из --gt-* каркаса.
 */

const STATUS_META = {
  pending: { label: "В очереди", dot: "gd-dot--pending" },
  running: { label: "Работает", dot: "gd-dot--running" },
  waiting_human: { label: "Ожидание", dot: "gd-dot--waiting" },
  completed: { label: "Готово", dot: "gd-dot--completed" },
  failed: { label: "Ошибка", dot: "gd-dot--failed" },
};

const PLACEHOLDER = "https://www.f6s.com/events";
const AUTO_JUMP_SECONDS = 3;
const POLL_MS = 5000;

function statusOf(job) {
  return STATUS_META[job?.status] || STATUS_META.pending;
}

function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export default function Dashboard() {
  const navigate = useNavigate();

  const [url, setUrl] = useState("");
  const [iterations, setIterations] = useState(1);
  const [mode, setMode] = useState("fast");
  const [maxChildProfiles, setMaxChildProfiles] = useState(20);
  const [maxDetailPages, setMaxDetailPages] = useState(100);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [jobs, setJobs] = useState([]);

  // launched: { id, total } | null  — задача, которую только что создали
  const [launched, setLaunched] = useState(null);
  const [countdown, setCountdown] = useState(AUTO_JUMP_SECONDS);

  const inputRef = useRef(null);

  /** Подтянуть список задач. Тихо глотаем ошибку опроса, чтобы не мигать формой. */
  const loadJobs = useCallback(async () => {
    try {
      const data = await fetchJobs();
      setJobs(Array.isArray(data) ? data : []);
    } catch {
      /* опрос фоном — ошибку не показываем */
    }
  }, []);

  // Первичная загрузка + автоопрос, пока вкладка открыта.
  useEffect(() => {
    loadJobs();
    const timer = setInterval(loadJobs, POLL_MS);
    return () => clearInterval(timer);
  }, [loadJobs]);

  // Обратный отсчёт автоперехода после запуска.
  useEffect(() => {
    if (!launched) return;

    setCountdown(AUTO_JUMP_SECONDS);

    const tick = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          clearInterval(tick);
          navigate(`/logs/${launched.id}`);
          return 0;
        }
        return c - 1;
      });
    }, 1000);

    return () => clearInterval(tick);
  }, [launched, navigate]);

  async function handleLaunch(e) {
    e.preventDefault();
    setError("");

    const target = url.trim();
    if (!target) {
      setError("Вставьте ссылку на страницу.");
      inputRef.current?.focus();
      return;
    }

    setLoading(true);
    const pageCount = Number(iterations);
    if (!Number.isInteger(pageCount) || pageCount < 1 || pageCount > 100) {
      setError("Количество итераций должно быть целым числом от 1 до 100.");
      return;
    }

    const childLimit = Number(maxChildProfiles);
    if (!Number.isInteger(childLimit) || childLimit < 1) {
      setError("Лимит профилей должен быть целым числом от 1.");
      return;
    }

    const detailPageLimit = Number(maxDetailPages);
    if (!Number.isInteger(detailPageLimit) || detailPageLimit < 1) {
      setError("Лимит detail-страниц должен быть целым числом от 1.");
      return;
    }

    try {
      const job = await startParse(target, {
        iterations: pageCount,
        mode,
        maxChildProfiles: childLimit,
        maxDetailPages: detailPageLimit,
      });

      setLaunched({ id: job.id, total: job.total_found ?? 0 });
      setUrl("");
      loadJobs(); // сразу покажем новую задачу в ленте
    } catch (err) {
      setError(err.message || "Не удалось запустить парсер.");
    } finally {
      setLoading(false);
    }
  }

  const runningCount = jobs.filter((j) => j.status === "running").length;

  return (
    <div className="gd-page">
      <style>{dashboardCss}</style>

      {/* Шапка экрана: крупный дисплейный заголовок + живой счётчик активных задач. */}
      <header className="gd-head">
        <p className="gd-kicker">
          <span className="gd-kicker-dot" aria-hidden="true" />
          console · запуск парсера
        </p>
        <h1 className="gd-title">
          Запусти сбор
          <span className="gd-title-accent">F6S</span>в один клик.
        </h1>
        <p className="gd-lead">
          Вставь ссылку на листинг грантов, акселераторов или ивентов — бэкенд
          создаст задачу, поднимет headless-браузер под твоим аккаунтом и начнёт
          писать логи в реальном времени.
        </p>
      </header>

      {/* Консоль запуска. */}
      <form className="gd-console" onSubmit={handleLaunch}>
        <div className="gd-console-row">
          <span className="gd-prompt" aria-hidden="true">
            <span className="gd-prompt-mark">›</span>
            <span className="gd-prompt-url">url</span>
          </span>

          <input
            ref={inputRef}
            className="gd-input"
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            placeholder={PLACEHOLDER}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            aria-label="Ссылка на страницу"
          />

          <button
            className="gd-launch"
            type="submit"
            disabled={loading}
            aria-busy={loading}
          >
            {loading ? (
              <>
                <span className="gd-spinner" aria-hidden="true" />
                Запускаем…
              </>
            ) : (
              <>
                Запустить
                <span className="gd-launch-arrow" aria-hidden="true">
                  →
                </span>
              </>
            )}
          </button>
        </div>

        <div className="gd-options">
          <label className="gd-option-field">
            <span>Итерации</span>
            <input
              className="gd-number-input"
              type="number"
              min="1"
              max="100"
              step="1"
              value={iterations}
              onChange={(e) => setIterations(e.target.value)}
              aria-label="Количество итераций"
            />
          </label>

          {mode === "smart" && (
            <>
              <label className="gd-option-field">
                <span>Detail-страниц</span>
                <input
                  className="gd-number-input"
                  type="number"
                  min="1"
                  step="1"
                  value={maxDetailPages}
                  onChange={(e) => setMaxDetailPages(e.target.value)}
                  aria-label="Максимальное число detail-страниц"
                />
              </label>

              <label className="gd-option-field">
                <span>Лимит профилей</span>
                <input
                  className="gd-number-input"
                  type="number"
                  min="1"
                  step="1"
                  value={maxChildProfiles}
                  onChange={(e) => setMaxChildProfiles(e.target.value)}
                  aria-label="Лимит новых detail-профилей"
                />
              </label>
            </>
          )}

          <fieldset className="gd-mode-field">
            <legend>Режим</legend>
            <label className="gd-mode-choice">
              <input
                type="radio"
                name="parse-mode"
                value="fast"
                checked={mode === "fast"}
                onChange={(e) => setMode(e.target.value)}
              />
              Быстрый
            </label>
            <label className="gd-mode-choice">
              <input
                type="radio"
                name="parse-mode"
                value="smart"
                checked={mode === "smart"}
                onChange={(e) => setMode(e.target.value)}
              />
              Умный
            </label>
            <p className="gd-mode-help">
              {mode === "smart"
                ? "Переходит в карточки и запоминает структуры detail-страниц."
                : "Собирает только карточки листинга."}
            </p>
          </fieldset>
        </div>

        {error && (
          <p className="gd-error" role="alert">
            <span aria-hidden="true">!</span>
            {error}
          </p>
        )}
      </form>

      {/* Лента последних запусков. */}
      <section className="gd-feed" aria-label="Последние запуски">
        <div className="gd-feed-head">
          <h2 className="gd-feed-title">
            Последние запуски
            {runningCount > 0 && (
              <span className="gd-feed-live">
                <span className="gd-dot gd-dot--running" aria-hidden="true" />
                {runningCount} в работе
              </span>
            )}
          </h2>
          <button
            type="button"
            className="gd-refresh"
            onClick={loadJobs}
            title="Обновить сейчас"
          >
            ↻ обновить
          </button>
        </div>

        {jobs.length === 0 ? (
          <div className="gd-empty">
            Пока ни одного запуска. Вставь ссылку выше и нажми «Запустить».
          </div>
        ) : (
          <ul className="gd-list">
            {jobs.slice(0, 8).map((job) => {
              const meta = statusOf(job);
              return (
                <li key={job.id} className="gd-item">
                  <Link
                    to={`/logs/${job.id}`}
                    className="gd-item-main"
                    title="Открыть логи задачи"
                  >
                    <span className={`gd-dot ${meta.dot}`} aria-hidden="true" />
                    <span className="gd-item-id">#{job.id}</span>
                    <span className="gd-item-url" title={job.target_url}>
                      {hostOf(job.target_url)}
                      <span className="gd-item-path">{job.target_url}</span>
                    </span>
                  </Link>

                  <div className="gd-item-meta">
                    <span className={`gd-badge gd-badge--${job.status}`}>
                      {meta.label}
                    </span>
                    <span className="gd-item-count">
                      {job.total_found ?? 0} карточек
                    </span>
                    <span className="gd-item-time">
                      {formatTime(job.created_at)}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Карточка-подтверждение + автопереход. */}
      {launched && (
        <div className="gd-toast" role="status">
          <div className="gd-toast-body">
            <span className="gd-toast-check" aria-hidden="true">
              ✓
            </span>
            <div className="gd-toast-text">
              <strong>Задача #{launched.id} создана.</strong>
              <span>
                Переход к логам через{" "}
                <b className="gd-toast-count">{countdown}</b> с…
              </span>
            </div>
          </div>
          <div className="gd-toast-actions">
            <button
              type="button"
              className="gd-toast-go"
              onClick={() => navigate(`/logs/${launched.id}`)}
            >
              К логам
            </button>
            <button
              type="button"
              className="gd-toast-stay"
              onClick={() => setLaunched(null)}
            >
              Остаться
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const dashboardCss = `
.gd-page {
  display: flex;
  flex-direction: column;
  gap: clamp(1.75rem, 4vw, 2.75rem);
  animation: gd-in 0.5s cubic-bezier(0.22, 1, 0.36, 1) both;
}
@keyframes gd-in {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: none; }
}

.gd-kicker {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
  margin: 0 0 0.9rem;
  font-size: 0.72rem;
  letter-spacing: 0.26em;
  text-transform: uppercase;
  color: var(--gt-ink-dim);
}
.gd-kicker-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--gt-teal);
  box-shadow: 0 0 12px var(--gt-teal);
}

.gd-title {
  margin: 0;
  font-family: var(--gt-display);
  font-weight: 700;
  font-size: clamp(2.1rem, 6vw, 3.6rem);
  line-height: 1.02;
  letter-spacing: -0.02em;
  color: var(--gt-ink);
  max-width: 16ch;
}
.gd-title-accent {
  display: inline-block;
  margin: 0 0.35ch;
  padding: 0 0.18em;
  border-radius: 0.18em;
  color: #1a1206;
  background: linear-gradient(150deg, var(--gt-amber), #ffd79a);
  transform: rotate(-1.5deg);
}

.gd-lead {
  margin: 1rem 0 0;
  max-width: 56ch;
  color: var(--gt-ink-dim);
  font-size: clamp(0.98rem, 1.4vw, 1.1rem);
  line-height: 1.6;
}

/* ---- консоль запуска ---- */
.gd-console {
  background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.015));
  border: 1px solid var(--gt-line);
  border-radius: 1rem;
  padding: clamp(1rem, 2.5vw, 1.5rem);
  box-shadow: 0 24px 60px -34px rgba(0,0,0,0.8);
}
.gd-console-row {
  display: flex;
  align-items: stretch;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.gd-prompt {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0 0.85rem;
  border-radius: 0.7rem;
  background: rgba(240, 168, 80, 0.1);
  border: 1px solid rgba(240, 168, 80, 0.28);
  font-family: var(--gt-display);
  color: var(--gt-amber);
  user-select: none;
}
.gd-prompt-mark { font-size: 1.2rem; line-height: 1; }
.gd-prompt-url {
  font-size: 0.72rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  opacity: 0.85;
}

.gd-input {
  flex: 1 1 280px;
  min-width: 0;
  padding: 0.85rem 1rem;
  border-radius: 0.7rem;
  border: 1px solid var(--gt-line);
  background: rgba(10, 13, 22, 0.6);
  color: var(--gt-ink);
  font-family: var(--gt-body);
  font-size: 1rem;
  letter-spacing: 0.01em;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}
.gd-input::placeholder { color: rgba(151, 163, 189, 0.55); }
.gd-input:focus {
  outline: none;
  border-color: var(--gt-amber);
  background: rgba(10, 13, 22, 0.85);
  box-shadow: 0 0 0 4px rgba(240, 168, 80, 0.14);
}

.gd-options {
  display: flex;
  align-items: flex-start;
  gap: 1.25rem;
  margin-top: 1rem;
  flex-wrap: wrap;
}
.gd-option-field, .gd-mode-field {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  color: var(--gt-ink-dim);
  font-size: 0.82rem;
}
.gd-mode-field {
  flex-wrap: wrap;
  border: 0;
  padding: 0;
  margin: 0;
}
.gd-mode-field legend { padding: 0; margin-right: 0.25rem; }
.gd-number-input {
  width: 4.5rem;
  padding: 0.55rem 0.65rem;
  border: 1px solid var(--gt-line);
  border-radius: 0.55rem;
  background: rgba(0,0,0,0.2);
  color: var(--gt-ink);
}
.gd-mode-choice { display: inline-flex; align-items: center; gap: 0.3rem; }
.gd-mode-help { flex-basis: 100%; margin: 0.15rem 0 0; color: var(--gt-ink-dim); font-size: 0.75rem; }
.gd-launch {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.85rem 1.5rem;
  border: none;
  border-radius: 0.7rem;
  cursor: pointer;
  font-family: var(--gt-display);
  font-weight: 600;
  font-size: 1rem;
  color: #1a1206;
  background: linear-gradient(150deg, var(--gt-amber), #ffd79a);
  box-shadow: 0 10px 26px -10px rgba(240, 168, 80, 0.6);
  transition: transform 0.18s ease, box-shadow 0.18s ease, filter 0.18s ease;
}
.gd-launch:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 16px 34px -12px rgba(240, 168, 80, 0.7);
}
.gd-launch:active:not(:disabled) { transform: translateY(0); }
.gd-launch:disabled { cursor: progress; filter: saturate(0.7) brightness(0.95); }
.gd-launch-arrow { transition: transform 0.2s ease; }
.gd-launch:hover:not(:disabled) .gd-launch-arrow { transform: translateX(4px); }

.gd-spinner {
  width: 1rem;
  height: 1rem;
  border-radius: 50%;
  border: 2px solid rgba(26, 18, 6, 0.35);
  border-top-color: #1a1206;
  animation: gd-spin 0.7s linear infinite;
}
@keyframes gd-spin { to { transform: rotate(360deg); } }

.gd-error {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  margin: 0.9rem 0 0;
  padding: 0.7rem 0.9rem;
  border-radius: 0.6rem;
  background: rgba(255, 107, 107, 0.1);
  border: 1px solid rgba(255, 107, 107, 0.35);
  color: #ffb4b4;
  font-size: 0.92rem;
}
.gd-error span {
  display: grid;
  place-items: center;
  width: 1.15rem;
  height: 1.15rem;
  border-radius: 50%;
  background: rgba(255, 107, 107, 0.85);
  color: #2a0d0d;
  font-weight: 700;
  font-size: 0.8rem;
  flex: none;
}

/* ---- лента запусков ---- */
.gd-feed-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}
.gd-feed-title {
  display: flex;
  align-items: center;
  gap: 0.85rem;
  margin: 0;
  font-family: var(--gt-display);
  font-weight: 600;
  font-size: 1.15rem;
  letter-spacing: -0.01em;
}
.gd-feed-live {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  background: rgba(240, 168, 80, 0.12);
  border: 1px solid rgba(240, 168, 80, 0.3);
  color: var(--gt-amber);
  font-family: var(--gt-body);
  font-size: 0.74rem;
  font-weight: 500;
  letter-spacing: 0.04em;
}
.gd-refresh {
  background: none;
  border: 1px solid var(--gt-line);
  border-radius: 0.6rem;
  color: var(--gt-ink-dim);
  padding: 0.4rem 0.8rem;
  cursor: pointer;
  font-size: 0.82rem;
  transition: color 0.2s ease, border-color 0.2s ease;
}
.gd-refresh:hover { color: var(--gt-ink); border-color: rgba(255,255,255,0.22); }

.gd-empty {
  padding: 2rem 1.25rem;
  border: 1px dashed var(--gt-line);
  border-radius: 0.9rem;
  text-align: center;
  color: var(--gt-ink-dim);
  font-size: 0.95rem;
}

.gd-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
}
.gd-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  padding: 0.85rem 1rem;
  border: 1px solid var(--gt-line);
  border-radius: 0.8rem;
  background: rgba(255, 255, 255, 0.02);
  transition: border-color 0.2s ease, background 0.2s ease, transform 0.2s ease;
}
.gd-item:hover {
  border-color: rgba(240, 168, 80, 0.35);
  background: rgba(240, 168, 80, 0.05);
  transform: translateX(3px);
}
.gd-item-main {
  display: inline-flex;
  align-items: center;
  gap: 0.7rem;
  min-width: 0;
  text-decoration: none;
  color: inherit;
  flex: 1 1 240px;
}
.gd-item-id {
  font-family: var(--gt-display);
  font-weight: 600;
  color: var(--gt-ink-dim);
  font-size: 0.9rem;
  flex: none;
}
.gd-item-url {
  display: inline-flex;
  align-items: baseline;
  gap: 0.5rem;
  min-width: 0;
  font-weight: 500;
  color: var(--gt-ink);
}
.gd-item-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--gt-ink-dim);
  font-size: 0.82rem;
  font-weight: 400;
}
.gd-item-meta {
  display: inline-flex;
  align-items: center;
  gap: 0.9rem;
  flex-wrap: wrap;
}
.gd-item-count { color: var(--gt-ink-dim); font-size: 0.85rem; }
.gd-item-time {
  color: rgba(151, 163, 189, 0.7);
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
}

.gd-badge {
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  border: 1px solid transparent;
}
.gd-badge--pending   { color: var(--gt-ink-dim); border-color: var(--gt-line); }
.gd-badge--running   { color: var(--gt-amber); border-color: rgba(240,168,80,0.4); background: rgba(240,168,80,0.1); }
.gd-badge--completed { color: var(--gt-teal);  border-color: rgba(88,214,192,0.4);  background: rgba(88,214,192,0.1); }
.gd-badge--failed    { color: #ff9a9a; border-color: rgba(255,107,107,0.4); background: rgba(255,107,107,0.1); }
.gd-badge--waiting_human { color: #e0c060; border-color: rgba(224,192,96,0.4); background: rgba(224,192,96,0.1); }

/* точки-индикаторы статуса */
.gd-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex: none;
}
.gd-dot--pending   { background: var(--gt-ink-dim); opacity: 0.6; }
.gd-dot--running   { background: var(--gt-amber); box-shadow: 0 0 0 0 rgba(240,168,80,0.6); animation: gd-pulse 1.4s ease-out infinite; }
.gd-dot--completed { background: var(--gt-teal); }
.gd-dot--failed    { background: #ff6b6b; }
.gd-dot--waiting   { background: #e0c060; animation: gd-pulse 1.4s ease-out infinite; }
@keyframes gd-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(240,168,80,0.55); }
  70%  { box-shadow: 0 0 0 8px rgba(240,168,80,0); }
  100% { box-shadow: 0 0 0 0 rgba(240,168,80,0); }
}

/* ---- тост подтверждения ---- */
.gd-toast {
  position: fixed;
  left: 50%;
  bottom: 1.5rem;
  transform: translateX(-50%);
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 1.25rem;
  flex-wrap: wrap;
  width: min(640px, calc(100% - 2rem));
  padding: 0.9rem 1.1rem;
  border-radius: 0.9rem;
  border: 1px solid rgba(88, 214, 192, 0.35);
  background: rgba(18, 26, 38, 0.92);
  backdrop-filter: blur(10px);
  box-shadow: 0 24px 60px -24px rgba(0,0,0,0.85);
  animation: gd-toast-in 0.4s cubic-bezier(0.22, 1, 0.36, 1) both;
}
@keyframes gd-toast-in {
  from { opacity: 0; transform: translate(-50%, 18px); }
  to   { opacity: 1; transform: translate(-50%, 0); }
}
.gd-toast-body { display: flex; align-items: center; gap: 0.8rem; flex: 1 1 240px; }
.gd-toast-check {
  display: grid;
  place-items: center;
  width: 1.9rem;
  height: 1.9rem;
  border-radius: 50%;
  background: rgba(88, 214, 192, 0.18);
  border: 1px solid rgba(88, 214, 192, 0.5);
  color: var(--gt-teal);
  font-weight: 700;
  flex: none;
}
.gd-toast-text { display: flex; flex-direction: column; line-height: 1.3; }
.gd-toast-text strong { color: var(--gt-ink); font-weight: 600; }
.gd-toast-text span { color: var(--gt-ink-dim); font-size: 0.85rem; }
.gd-toast-count {
  color: var(--gt-amber);
  font-family: var(--gt-display);
  font-variant-numeric: tabular-nums;
}
.gd-toast-actions { display: inline-flex; gap: 0.5rem; }
.gd-toast-go,
.gd-toast-stay {
  border-radius: 0.6rem;
  padding: 0.5rem 0.9rem;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 600;
  transition: transform 0.15s ease, background 0.2s ease, border-color 0.2s ease;
}
.gd-toast-go {
  border: none;
  color: #06231f;
  background: linear-gradient(150deg, var(--gt-teal), #9af0e0);
}
.gd-toast-go:hover { transform: translateY(-1px); }
.gd-toast-stay {
  border: 1px solid var(--gt-line);
  background: transparent;
  color: var(--gt-ink-dim);
}
.gd-toast-stay:hover { color: var(--gt-ink); border-color: rgba(255,255,255,0.22); }

@media (max-width: 560px) {
  .gd-console-row { flex-direction: column; }
  .gd-launch { justify-content: center; }
  .gd-item { flex-direction: column; align-items: flex-start; }
}
@media (prefers-reduced-motion: reduce) {
  .gd-page, .gd-toast { animation: none; }
  .gd-dot--running { animation: none; }
  .gd-spinner { animation: none; }
}
`;
