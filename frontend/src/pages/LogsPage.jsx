import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { fetchJobLogs, fetchJobs } from "../api";
import { useVisibleInterval } from "../hooks/useVisibleInterval";

/**
 * LogsPage — наблюдение за задачей в реальном времени.
 *
 * Логика опроса (по документации + чуть аккуратнее):
 *  - /jobs тянем каждые 5с всегда — чтобы чипы и статус-бар были живыми;
 *  - /jobs/:id/logs тянем каждые 3с, пока задача не в терминальном статусе;
 *  - при переходе в completed/failed делаем один финальный подтяг логов
 *    (эффект пересоздаётся по смене status) и останавливаем интервал.
 *
 * Выбор задачи:
 *  - /logs/:jobId  -> фиксируемся на задаче из URL;
 *  - /logs         -> берём самую свежую задачу из /jobs;
 *  - клик по чипу  -> navigate(/logs/:id), URL и выбор синхронны.
 *
 * Стили локальные, префикс gl- (grantum logs), цвета из --gt-* каркаса.
 */

const STATUS_META = {
  pending: { label: "В очереди", dot: "gl-dot--pending" },
  running: { label: "Работает", dot: "gl-dot--running" },
  waiting_human: { label: "Ожидание", dot: "gl-dot--waiting" },
  completed: { label: "Готово", dot: "gl-dot--completed" },
  failed: { label: "Ошибка", dot: "gl-dot--failed" },
};

const TERMINAL = new Set(["completed", "failed"]);
const JOBS_POLL_MS = 5000;
const LOGS_POLL_MS = 3000;
const SCROLL_GUARD_PX = 40;

// Парсер пишет именно так: "Успешно обработана карточка {n}/{m}".
const PROGRESS_RE = /обработана карточка\s+(\d+)\s*\/\s*(\d+)/;

function statusOf(job) {
  return STATUS_META[job?.status] || STATUS_META.pending;
}

function clock(iso) {
  if (!iso) return "--:--:--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function startedAt(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function levelClass(level) {
  const v = (level || "").toUpperCase();
  if (v === "WARNING" || v === "WARN") return "gl-lvl--warn";
  if (v === "ERROR" || v === "ERR") return "gl-lvl--err";
  return "gl-lvl--info";
}

/**
 * Прогресс читаем прямо из логов — это единственный честный источник.
 * known=true только когда в логах реально есть строка прогресса,
 * чтобы не рисовать бар выдуманными цифрами на этапе «ищу карточки…».
 */
function computeProgress(logs, job) {
  let done = 0;
  let total = 0;
  let known = false;

  for (let i = logs.length - 1; i >= 0; i -= 1) {
    const m = (logs[i]?.message || "").match(PROGRESS_RE);
    if (m) {
      done = Number(m[1]);
      total = Number(m[2]);
      known = true;
      break;
    }
  }

  if (known) {
    total = Math.max(total, job?.total_found || 0);
  }

  const percent =
    total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return { done, total, percent, known };
}

export default function LogsPage() {
  const { jobId } = useParams();
  const navigate = useNavigate();

  const [jobs, setJobs] = useState([]);
  const [logs, setLogs] = useState([]);
  // id последнего полученного лога: поллинг догружает только новые записи
  // вместо полного списка каждые 3 секунды.
  const lastLogIdRef = useRef(0);
  const [selectedId, setSelectedId] = useState(() =>
    jobId ? Number(jobId) : null,
  );

  const termRef = useRef(null);
  const userScrolledRef = useRef(false);
  const [showJump, setShowJump] = useState(false);

  // Синхронизация выбора с URL при навигации между /logs/:id.
  useEffect(() => {
    setSelectedId(jobId ? Number(jobId) : null);
  }, [jobId]);

  const effectiveId = useMemo(() => {
    if (selectedId != null) return selectedId;
    return jobs[0]?.id ?? null;
  }, [selectedId, jobs]);

  const activeJob = useMemo(
    () => jobs.find((j) => j.id === effectiveId) || null,
    [jobs, effectiveId],
  );

  const status = activeJob?.status || null;
  const isTerminal = !!status && TERMINAL.has(status);
  const meta = statusOf(activeJob);
  const progress = useMemo(
    () => computeProgress(logs, activeJob),
    [logs, activeJob],
  );

  /** Тихо подтянуть список задач (фон). */
  const loadJobs = useCallback(async () => {
    try {
      const data = await fetchJobs();
      setJobs(Array.isArray(data) ? data : []);
    } catch {
      /* фон — ошибку не показываем */
    }
  }, []);

  /** Подтянуть логи выбранной задачи. Первый вызов — полный, далее — только новые записи. */
  const loadLogs = useCallback(async (id) => {
    if (id == null) return;
    try {
      const data = await fetchJobLogs(id);
      const list = Array.isArray(data) ? data : [];
      lastLogIdRef.current =
        list.length > 0 ? list[list.length - 1].id : 0;
      setLogs(list);
    } catch {
      /* следующий тик перезапросит */
    }
  }, []);

  /** Инкрементальный поллинг: запрашиваем только записи после последнего id. */
  const pollLogs = useCallback(async (id) => {
    if (id == null) return;
    try {
      const fresh = await fetchJobLogs(id, {
        afterId: lastLogIdRef.current || undefined,
      });
      const incoming = Array.isArray(fresh) ? fresh : [];
      if (!incoming.length) return;
      setLogs((prev) => {
        const seen = new Set(prev.map((l) => l.id));
        const deduped = incoming.filter((l) => !seen.has(l.id));
        if (!deduped.length) return prev;
        return [...prev, ...deduped];
      });
      lastLogIdRef.current = Math.max(
        lastLogIdRef.current,
        incoming[incoming.length - 1].id,
      );
    } catch {
      /* следующий тик перезапросит */
    }
  }, []);

  // Опрос /jobs — всегда (пауза, когда вкладка скрыта).
  useVisibleInterval(loadJobs, JOBS_POLL_MS);

  // Сброс окна при смене задачи (до того, как придут новые логи).
  useEffect(() => {
    setLogs([]);
    lastLogIdRef.current = 0;
    userScrolledRef.current = false;
    setShowJump(false);
  }, [effectiveId]);

  // Полная загрузка логов при выборе задачи.
  useEffect(() => {
    if (effectiveId == null) return;
    loadLogs(effectiveId);
  }, [effectiveId, loadLogs]);

  // Инкрементальный поллинг (только новые записи), пока задача не завершена;
  // при скрытой вкладке интервал ставится на паузу.
  useVisibleInterval(
    useCallback(() => {
      if (effectiveId != null && !isTerminal) pollLogs(effectiveId);
    }, [effectiveId, isTerminal, pollLogs]),
    LOGS_POLL_MS,
  );

  // Финальный инкрементальный подтяг при переходе в терминальный статус.
  useEffect(() => {
    if (effectiveId == null || !isTerminal) return;
    pollLogs(effectiveId);
  }, [effectiveId, isTerminal, pollLogs]);

  // Автопрокрутка вниз, если пользователь не читает историю выше.
  useEffect(() => {
    const el = termRef.current;
    if (!el) return;
    if (!userScrolledRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [logs]);

  function handleTermScroll() {
    const el = termRef.current;
    if (!el) return;
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_GUARD_PX;
    userScrolledRef.current = !atBottom;
    setShowJump(!atBottom);
  }

  function jumpToBottom() {
    const el = termRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    userScrolledRef.current = false;
    setShowJump(false);
  }

  function pickJob(id) {
    navigate(`/logs/${id}`);
  }

  return (
    <div className="gl-page">
      <style>{logsCss}</style>

      <header className="gl-head">
        <p className="gl-kicker">
          <span
            className={`gl-kicker-dot ${isTerminal ? "is-idle" : ""}`}
            aria-hidden="true"
          />
          observability · {isTerminal ? "поток остановлен" : "живой поток"}
        </p>
        <h1 className="gl-title">
          Смотри парсер
          <span className="gl-title-accent">изнутри.</span>
        </h1>
        <p className="gl-lead">
          Каждая строка ниже приходит прямо из базы по мере работы
          headless-браузера: переходы, найденные карточки, ошибки. Окно ведёт
          себя как настоящий терминальный tail — скроллит само, пока ты не полез
          читать вверх.
        </p>
      </header>

      {/* Переключатель задач. */}
      <section className="gl-picker" aria-label="Задачи">
        <div className="gl-picker-head">
          <h2 className="gl-picker-title">Задачи</h2>
          <button
            type="button"
            className="gl-refresh"
            onClick={loadJobs}
            title="Обновить список задач"
          >
            ↻ обновить
          </button>
        </div>

        {jobs.length === 0 ? (
          <p className="gl-picker-empty">
            Задач пока нет — запусти парсер на вкладке «Запуск».
          </p>
        ) : (
          <div className="gl-chips" role="tablist">
            {jobs.slice(0, 14).map((job) => {
              const m = statusOf(job);
              const active = job.id === effectiveId;
              return (
                <button
                  key={job.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className={"gl-chip" + (active ? " is-active" : "")}
                  onClick={() => pickJob(job.id)}
                  title={job.target_url}
                >
                  <span className={`gl-dot ${m.dot}`} aria-hidden="true" />
                  <span className="gl-chip-id">#{job.id}</span>
                  <span className="gl-chip-status">{m.label}</span>
                </button>
              );
            })}
          </div>
        )}
      </section>

      {activeJob ? (
        <>
          {/* Статус-бар задачи. */}
          <div className={`gl-statusbar gl-statusbar--${status}`}>
            <div className="gl-status-left">
              <span
                className={`gl-dot gl-dot--big ${meta.dot}`}
                aria-hidden="true"
              />
              <div className="gl-status-text">
                <span className="gl-status-label">{meta.label}</span>
                <span className="gl-status-id">задача #{activeJob.id}</span>
              </div>
            </div>

            {status === "waiting_human" && (
              <div className="gl-waiting-banner" role="alert">
                <span className="gl-waiting-icon" aria-hidden="true">
                  ⏸
                </span>
                <div className="gl-waiting-text">
                  <strong>Задача ждёт действия человека</strong>
                  <span>
                    {activeJob.block_reason === "auth_required"
                      ? "Требуется авторизация в окне Chrome"
                      : "Требуется решение капчи или обход блока"}
                  </span>
                </div>
              </div>
            )}

            <div className="gl-status-right">
              <div className="gl-stat">
                <span className="gl-stat-num">
                  {activeJob.total_found ?? 0}
                </span>
                <span className="gl-stat-cap">карточек</span>
              </div>
              <div className="gl-stat">
                <span className="gl-stat-num gl-stat-num--mono">
                  {startedAt(activeJob.created_at)}
                </span>
                <span className="gl-stat-cap">старт</span>
              </div>
              <span
                className={"gl-poll " + (isTerminal ? "is-paused" : "is-live")}
              >
                <span className="gl-poll-dot" aria-hidden="true" />
                {isTerminal ? "PAUSED" : "LIVE"}
              </span>
            </div>
          </div>

          {/* Прогресс — только когда парсер реально рапортует о карточках. */}
          <div className="gl-progress-wrap">
            <div className="gl-progress-meta">
              <span className="gl-progress-cap">
                {progress.known
                  ? `обработано ${progress.done} из ${progress.total}`
                  : status === "running"
                    ? "сканирую страницу…"
                    : "прогресс"}
              </span>
              {progress.known && (
                <span className="gl-progress-pct">{progress.percent}%</span>
              )}
            </div>
            <div
              className="gl-progress-track"
              role="progressbar"
              aria-valuenow={progress.known ? progress.percent : 0}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="gl-progress-fill"
                style={{
                  width: progress.known ? `${progress.percent}%` : "0%",
                }}
              />
              {!progress.known && status === "running" && (
                <div className="gl-progress-indet" aria-hidden="true" />
              )}
            </div>
          </div>

          {/* Терминал логов. */}
          <div className="gl-term">
            <div className="gl-term-bar">
              <div className="gl-term-lights" aria-hidden="true">
                <span className="gl-light gl-light--r" />
                <span className="gl-light gl-light--y" />
                <span className="gl-light gl-light--g" />
              </div>
              <span className="gl-term-name">job-#{activeJob.id}.log</span>
              <button
                type="button"
                className="gl-term-reload"
                onClick={() => loadLogs(effectiveId)}
                title="Подтянуть логи сейчас"
              >
                ↻
              </button>
            </div>

            <div
              className="gl-term-body"
              ref={termRef}
              onScroll={handleTermScroll}
              role="log"
              aria-live="off"
              aria-label={`Логи задачи ${activeJob.id}`}
            >
              {logs.length === 0 ? (
                <div className="gl-wait">
                  <span className="gl-wait-text">ожидание первых логов</span>
                  <span className="gl-wait-dots" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                </div>
              ) : (
                <>
                  {logs.map((log) => (
                    <div
                      key={log.id}
                      className={`gl-line gl-line--${(log.level || "info").toLowerCase()}`}
                    >
                      <span className="gl-ts">{clock(log.created_at)}</span>
                      <span className={`gl-lvl ${levelClass(log.level)}`}>
                        {(log.level || "INFO").toUpperCase().padEnd(7)}
                      </span>
                      <span className="gl-msg">{log.message}</span>
                    </div>
                  ))}

                  {!isTerminal && (
                    <span className="gl-cursor" aria-hidden="true" />
                  )}
                </>
              )}
            </div>

            {showJump && (
              <button
                type="button"
                className="gl-jump"
                onClick={jumpToBottom}
                aria-label="Прокрутить к новым логам"
              >
                ↓ вниз
              </button>
            )}
          </div>
        </>
      ) : (
        <div className="gl-empty">
          <div className="gl-empty-mark" aria-hidden="true">
            ∅
          </div>
          <p>Нет задачи для просмотра.</p>
          <span>
            Запусти парсер на вкладке «Запуск» — логи появятся здесь сами.
          </span>
        </div>
      )}
    </div>
  );
}

const logsCss = `
.gl-page {
  display: flex;
  flex-direction: column;
  gap: clamp(1.4rem, 3vw, 2.1rem);
  animation: gl-in 0.5s cubic-bezier(0.22, 1, 0.36, 1) both;
}
@keyframes gl-in {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: none; }
}

.gl-kicker {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
  margin: 0 0 0.85rem;
  font-size: 0.72rem;
  letter-spacing: 0.26em;
  text-transform: uppercase;
  color: var(--gt-ink-dim);
}
.gl-kicker-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--gt-teal);
  box-shadow: 0 0 12px var(--gt-teal);
  animation: gl-blink 1.6s ease-in-out infinite;
}
.gl-kicker-dot.is-idle {
  background: var(--gt-ink-dim);
  box-shadow: none;
  animation: none;
  opacity: 0.6;
}
@keyframes gl-blink { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }

.gl-title {
  margin: 0;
  font-family: var(--gt-display);
  font-weight: 700;
  font-size: clamp(2rem, 5.5vw, 3.3rem);
  line-height: 1.03;
  letter-spacing: -0.02em;
  color: var(--gt-ink);
}
.gl-title-accent {
  display: inline-block;
  margin-left: 0.35ch;
  color: var(--gt-amber);
}
.gl-lead {
  margin: 0.9rem 0 0;
  max-width: 60ch;
  color: var(--gt-ink-dim);
  font-size: clamp(0.96rem, 1.3vw, 1.08rem);
  line-height: 1.6;
}

/* ---- переключатель задач ---- */
.gl-picker-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 1rem; margin-bottom: 0.8rem;
}
.gl-picker-title {
  margin: 0;
  font-family: var(--gt-display);
  font-weight: 600; font-size: 1.05rem; letter-spacing: -0.01em;
}
.gl-refresh {
  background: none; border: 1px solid var(--gt-line); border-radius: 0.6rem;
  color: var(--gt-ink-dim); padding: 0.4rem 0.8rem; cursor: pointer;
  font-size: 0.82rem; transition: color 0.2s ease, border-color 0.2s ease;
}
.gl-refresh:hover { color: var(--gt-ink); border-color: rgba(255,255,255,0.22); }

.gl-picker-empty {
  margin: 0; padding: 1.1rem 1.25rem;
  border: 1px dashed var(--gt-line); border-radius: 0.8rem;
  color: var(--gt-ink-dim); font-size: 0.92rem;
}

.gl-chips { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.gl-chip {
  display: inline-flex; align-items: center; gap: 0.5rem;
  padding: 0.45rem 0.8rem; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--gt-line); background: rgba(255,255,255,0.02);
  color: var(--gt-ink-dim); font-size: 0.84rem;
  transition: border-color 0.2s ease, background 0.2s ease, color 0.2s ease, transform 0.15s ease;
}
.gl-chip:hover { color: var(--gt-ink); border-color: rgba(255,255,255,0.22); transform: translateY(-1px); }
.gl-chip.is-active {
  color: var(--gt-amber);
  border-color: rgba(240,168,80,0.5);
  background: rgba(240,168,80,0.1);
}
.gl-chip-id { font-family: var(--gt-display); font-weight: 600; }
.gl-chip-status { font-size: 0.76rem; opacity: 0.85; }

/* точки статуса */
.gl-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.gl-dot--big { width: 12px; height: 12px; }
.gl-dot--pending   { background: var(--gt-ink-dim); opacity: 0.6; }
.gl-dot--running   { background: var(--gt-amber); animation: gl-pulse 1.4s ease-out infinite; }
.gl-dot--completed { background: var(--gt-teal); }
.gl-dot--failed    { background: #ff6b6b; }
.gl-dot--waiting   { background: #e0c060; animation: gl-pulse 1.4s ease-out infinite; }
@keyframes gl-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(240,168,80,0.55); }
  70%  { box-shadow: 0 0 0 8px rgba(240,168,80,0); }
  100% { box-shadow: 0 0 0 0 rgba(240,168,80,0); }
}

/* ---- статус-бар ---- */
.gl-statusbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 1.25rem; flex-wrap: wrap;
  padding: 1rem 1.25rem; border-radius: 1rem;
  border: 1px solid var(--gt-line);
  background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.015));
}
.gl-statusbar--running   { border-color: rgba(240,168,80,0.3); }
.gl-statusbar--completed { border-color: rgba(88,214,192,0.3); }
.gl-statusbar--failed    { border-color: rgba(255,107,107,0.3); }
.gl-statusbar--waiting_human { border-color: rgba(224,192,96,0.3); }
.gl-status-left { display: inline-flex; align-items: center; gap: 0.85rem; }
.gl-status-text { display: flex; flex-direction: column; line-height: 1.15; }
.gl-status-label {
  font-family: var(--gt-display); font-weight: 600; font-size: 1.2rem;
  color: var(--gt-ink); letter-spacing: -0.01em;
}
.gl-status-id { font-size: 0.78rem; color: var(--gt-ink-dim); }
.gl-waiting-banner {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 0.7rem 0.9rem;
  border-radius: 0.7rem;
  background: rgba(224, 192, 96, 0.12);
  border: 1px solid rgba(224, 192, 96, 0.35);
  margin-top: 0.8rem;
  animation: gl-wait-pulse 2s ease-in-out infinite;
}
@keyframes gl-wait-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(224, 192, 96, 0.3); }
  50% { box-shadow: 0 0 0 6px rgba(224, 192, 96, 0); }
}
.gl-waiting-icon {
  font-size: 1.4rem;
  color: #e0c060;
  flex: none;
}
.gl-waiting-text {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}
.gl-waiting-text strong {
  color: #e0c060;
  font-weight: 600;
  font-size: 0.95rem;
}
.gl-waiting-text span {
  color: var(--gt-ink-dim);
  font-size: 0.82rem;
}
.gl-status-right { display: inline-flex; align-items: center; gap: 1.4rem; flex-wrap: wrap; }
.gl-stat { display: flex; flex-direction: column; line-height: 1.1; }
.gl-stat-num { font-family: var(--gt-display); font-weight: 600; font-size: 1.15rem; color: var(--gt-ink); }
.gl-stat-num--mono { font-variant-numeric: tabular-nums; font-size: 1rem; }
.gl-stat-cap { font-size: 0.68rem; letter-spacing: 0.16em; text-transform: uppercase; color: var(--gt-ink-dim); }
.gl-poll {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.25rem 0.65rem; border-radius: 999px;
  font-size: 0.7rem; font-weight: 600; letter-spacing: 0.14em;
}
.gl-poll.is-live { color: var(--gt-teal); background: rgba(88,214,192,0.12); border: 1px solid rgba(88,214,192,0.35); }
.gl-poll.is-paused { color: var(--gt-ink-dim); background: rgba(255,255,255,0.04); border: 1px solid var(--gt-line); }
.gl-poll-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.gl-poll.is-live .gl-poll-dot { animation: gl-blink 1.2s ease-in-out infinite; }

/* ---- прогресс ---- */
.gl-progress-wrap { display: flex; flex-direction: column; gap: 0.5rem; }
.gl-progress-meta { display: flex; align-items: baseline; justify-content: space-between; }
.gl-progress-cap { font-size: 0.82rem; color: var(--gt-ink-dim); letter-spacing: 0.02em; }
.gl-progress-pct { font-family: var(--gt-display); font-weight: 600; color: var(--gt-amber); font-variant-numeric: tabular-nums; }
.gl-progress-track {
  position: relative; height: 8px; border-radius: 999px; overflow: hidden;
  background: rgba(255,255,255,0.06); border: 1px solid var(--gt-line);
}
.gl-progress-fill {
  height: 100%; border-radius: 999px;
  background: linear-gradient(90deg, var(--gt-amber), var(--gt-teal));
  transition: width 0.45s cubic-bezier(0.22, 1, 0.36, 1);
}
.gl-progress-indet {
  position: absolute; top: 0; bottom: 0; width: 35%;
  background: linear-gradient(90deg, transparent, rgba(240,168,80,0.55), transparent);
  animation: gl-indet 1.3s ease-in-out infinite;
}
@keyframes gl-indet { 0% { left: -35%; } 100% { left: 100%; } }

/* ---- терминал ---- */
.gl-term {
  position: relative;
  border-radius: 0.9rem; overflow: hidden;
  border: 1px solid var(--gt-line);
  background: #070a12;
  box-shadow: 0 30px 70px -40px rgba(0,0,0,0.9), inset 0 1px 0 rgba(255,255,255,0.03);
}
.gl-term-bar {
  display: flex; align-items: center; gap: 0.7rem;
  padding: 0.6rem 0.9rem;
  background: rgba(255,255,255,0.03);
  border-bottom: 1px solid var(--gt-line);
}
.gl-term-lights { display: inline-flex; gap: 0.4rem; }
.gl-light { width: 11px; height: 11px; border-radius: 50%; opacity: 0.85; }
.gl-light--r { background: #ff6159; }
.gl-light--y { background: #ffbd2e; }
.gl-light--g { background: #28c840; }
.gl-term-name {
  flex: 1; text-align: center;
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 0.78rem; color: var(--gt-ink-dim); letter-spacing: 0.04em;
}
.gl-term-reload {
  background: none; border: none; color: var(--gt-ink-dim); cursor: pointer;
  font-size: 1rem; line-height: 1; padding: 0.1rem 0.3rem; border-radius: 0.4rem;
  transition: color 0.2s ease, background 0.2s ease;
}
.gl-term-reload:hover { color: var(--gt-ink); background: rgba(255,255,255,0.06); }

.gl-term-body {
  padding: 0.9rem 1rem 1.1rem;
  height: min(58vh, 560px);
  overflow-y: auto;
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 0.84rem; line-height: 1.55;
  scrollbar-width: thin;
  scrollbar-color: rgba(240,168,80,0.4) transparent;
}
.gl-term-body::-webkit-scrollbar { width: 9px; }
.gl-term-body::-webkit-scrollbar-thumb { background: rgba(240,168,80,0.35); border-radius: 999px; }

.gl-line {
  display: flex; gap: 0.7rem; align-items: baseline;
  padding: 0.08rem 0 0.08rem 0.6rem;
  border-left: 2px solid transparent;
  border-radius: 0 0.25rem 0.25rem 0;
}
.gl-line:hover { background: rgba(255,255,255,0.025); }
.gl-line--warning { border-left-color: rgba(240,168,80,0.5); }
.gl-line--error   { border-left-color: rgba(255,107,107,0.6); background: rgba(255,107,107,0.05); }

.gl-ts { color: rgba(151,163,189,0.55); flex: none; font-variant-numeric: tabular-nums; }
.gl-lvl { flex: none; font-weight: 600; letter-spacing: 0.04em; white-space: pre; }
.gl-lvl--info { color: var(--gt-teal); }
.gl-lvl--warn { color: var(--gt-amber); }
.gl-lvl--err  { color: #ff8585; }
.gl-msg { color: var(--gt-ink); word-break: break-word; }

.gl-cursor {
  display: inline-block; width: 8px; height: 1.05em; margin-left: 0.2rem;
  vertical-align: text-bottom; background: var(--gt-amber);
  animation: gl-caret 1s steps(1) infinite;
}
@keyframes gl-caret { 0%,50% { opacity: 1; } 50.01%,100% { opacity: 0; } }

/* ожидание логов */
.gl-wait {
  display: flex; align-items: center; gap: 0.6rem;
  color: var(--gt-ink-dim); padding: 0.4rem 0.2rem;
}
.gl-wait-text { font-size: 0.85rem; }
.gl-wait-dots { display: inline-flex; gap: 0.25rem; }
.gl-wait-dots i {
  width: 5px; height: 5px; border-radius: 50%; background: var(--gt-amber);
  animation: gl-wait 1.2s ease-in-out infinite;
}
.gl-wait-dots i:nth-child(2) { animation-delay: 0.2s; }
.gl-wait-dots i:nth-child(3) { animation-delay: 0.4s; }
@keyframes gl-wait { 0%,80%,100% { opacity: 0.2; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }

/* кнопка «вниз» */
.gl-jump {
  position: absolute; right: 1rem; bottom: 1rem;
  display: inline-flex; align-items: center; gap: 0.3rem;
  padding: 0.45rem 0.8rem; border-radius: 999px; cursor: pointer;
  border: 1px solid rgba(240,168,80,0.4); background: rgba(18,26,38,0.92);
  backdrop-filter: blur(6px); color: var(--gt-amber);
  font-size: 0.8rem; font-weight: 600;
  box-shadow: 0 10px 26px -12px rgba(0,0,0,0.8);
  animation: gl-jump-in 0.25s ease both;
}
.gl-jump:hover { background: rgba(240,168,80,0.16); }
@keyframes gl-jump-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }

/* пустое состояние */
.gl-empty {
  display: flex; flex-direction: column; align-items: center; text-align: center;
  gap: 0.5rem; padding: 3rem 1.5rem;
  border: 1px dashed var(--gt-line); border-radius: 1rem;
  color: var(--gt-ink-dim);
}
.gl-empty-mark {
  font-family: var(--gt-display); font-size: 2.4rem; color: rgba(151,163,189,0.4);
  margin-bottom: 0.3rem;
}
.gl-empty p { margin: 0; color: var(--gt-ink); font-weight: 600; font-size: 1.05rem; }
.gl-empty span { font-size: 0.9rem; max-width: 42ch; }

@media (max-width: 600px) {
  .gl-statusbar { flex-direction: column; align-items: flex-start; }
  .gl-status-right { width: 100%; justify-content: space-between; }
  .gl-term-body { font-size: 0.78rem; }
  .gl-line { gap: 0.45rem; }
}
@media (prefers-reduced-motion: reduce) {
  .gl-page { animation: none; }
  .gl-kicker-dot, .gl-dot--running, .gl-poll.is-live .gl-poll-dot,
  .gl-cursor, .gl-wait-dots i, .gl-progress-indet { animation: none; }
  .gl-progress-fill { transition: none; }
}
`;
