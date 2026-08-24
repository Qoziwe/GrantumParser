import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchItems, fetchJobs, deleteJob, deleteAllJobs } from "../api";

/**
 * Results — «сейф» проекта: все спаршенные карточки.
 *
 * Два слоя фильтрации работают вместе:
 *  - чип задачи  -> серверный фильтр GET /items?job_id=... (fetchItems);
 *  - строка поиска -> клиентский фильтр по title / url / raw_text.
 *
 * Раскрытие строк — аккордеон на Set (можно открыть несколько и сравнить).
 * raw_text показываем с сохранёнными переносами и прокруткой внутри,
 * чтобы длинный текст не рвал вёрстку. Рядом — копирование в буфер.
 *
 * Бонус под суть задачи (сбор данных): выгрузка текущей выборки в CSV
 * (с BOM, чтобы Excel не ломал кириллицу).
 *
 * УДАЛЕНИЕ — на уровне ЗАДАЧ (Job), а не карточек:
 *  - выбрано «Все»        -> кнопка «удалить все задачи» (каскад: задачи+карточки+логи);
 *  - выбрана задача #N    -> кнопка «удалить задачу #N»  (только она, каскадом).
 * Обе с подтверждением. Авто-обновления НЕТ: таблица не дёргается, данные
 * тянутся по кнопке «обновить» (прогресс идущего парсера — на вкладке логов).
 *
 * Стили локальные, префикс gr- (grantum results), цвета из --gt-* каркаса.
 */

const STATUS_DOT = {
  pending: "gr-dot--pending",
  running: "gr-dot--running",
  completed: "gr-dot--completed",
  failed: "gr-dot--failed",
};

// Приглушённая палитра для цветных полос по job_id (детерминированно).
const ROW_HUES = [
  "#f0a850", // amber
  "#58d6c0", // teal
  "#7aa2ff", // soft blue
  "#c79bf0", // soft violet
  "#ff8f7a", // soft coral
  "#a7d977", // soft lime
];

function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function shortDate(iso) {
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

/** Детерминированный цвет полосы/метки по id задачи. */
function hueFor(jobId) {
  const n = Number(jobId) || 0;
  return ROW_HUES[n % ROW_HUES.length];
}

function statusDot(status) {
  return STATUS_DOT[status] || "gr-dot--pending";
}

/** Превью текста в свёрнутой строке (первая непустая строка, обрезанная). */
function previewOf(text) {
  if (!text) return "";
  const line = text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .find(Boolean);
  if (!line) return "";
  return line.length > 96 ? line.slice(0, 96) + "…" : line;
}

/** Корректное CSV-поле: всегда в кавычках, внутренние " удваиваем. */
function csvCell(value) {
  const s = String(value ?? "").replace(/"/g, '""');
  return `"${s}"`;
}

function buildCsv(rows) {
  const header = [
    "id", "job_id", "title", "url", "structuring_status",
    "category", "organizer", "deadline", "funding_amount",
    "location", "description", "tags", "raw_text", "created_at",
  ];
  const lines = [header.map(csvCell).join(",")];
  for (const r of rows) {
    const sd = r.structured_data || {};
    lines.push(
      [
        r.id, r.job_id, sd.title || r.title, r.url, r.structuring_status || "skipped",
        sd.category || "", sd.organizer || "", sd.deadline || "",
        sd.funding_amount || "", sd.location || "",
        sd.description || "", (sd.tags || []).join("; "),
        r.raw_text, r.created_at,
      ]
        .map(csvCell)
        .join(","),
    );
  }
  // BOM — чтобы Excel открыл кириллицу без кракозябр.
  return "" + lines.join("\r\n");
}

function downloadCsv(rows, scopeLabel) {
  if (!rows.length) return;
  const blob = new Blob([buildCsv(rows)], {
    type: "text/csv;charset=utf-8",
  });
  const stamp = new Date().toISOString().slice(0, 10);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `grantum-items-${scopeLabel}-${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function Results() {
  const [jobs, setJobs] = useState([]);
  const [items, setItems] = useState([]); // уже отфильтровано сервером по задаче
  const [selectedJob, setSelectedJob] = useState(null); // null = «Все»
  const [query, setQuery] = useState("");

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false); // идёт удаление задач
  const [error, setError] = useState("");

  const [openIds, setOpenIds] = useState(() => new Set());
  const [copiedId, setCopiedId] = useState(null);

  /** Список задач (для чипов). Тихо на ошибке. */
  const loadJobs = useCallback(async () => {
    try {
      const data = await fetchJobs();
      setJobs(Array.isArray(data) ? data : []);
    } catch {
      /* фон */
    }
  }, []);

  /** Карточки: с фильтром по задаче или все. */
  const loadItems = useCallback(async (jobId, { isRefresh = false } = {}) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    try {
      const data = await fetchItems(jobId ?? undefined);
      setItems(Array.isArray(data) ? data : []);
      setOpenIds(new Set()); // сброс раскрытий при смене выборки
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // Первичная загрузка. Авто-поллинга намеренно нет: таблица не дёргается,
  // данные обновляются только кнопкой «обновить».
  useEffect(() => {
    loadJobs();
    loadItems(selectedJob);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pickJob(jobId) {
    setSelectedJob(jobId);
    setQuery("");
    setError("");
    loadItems(jobId);
  }

  function refresh() {
    setError("");
    loadJobs();
    loadItems(selectedJob, { isRefresh: true });
  }

  function toggleOpen(id) {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function copyText(item) {
    const payload = item.raw_text || item.title || "";
    try {
      await navigator.clipboard.writeText(payload);
    } catch {
      /* fallback: старый способ */
      const ta = document.createElement("textarea");
      ta.value = payload;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        /* noop */
      }
      ta.remove();
    }
    setCopiedId(item.id);
    setTimeout(() => {
      setCopiedId((cur) => (cur === item.id ? null : cur));
    }, 1400);
  }

  /**
   * Удалить ВСЕ задачи каскадом (задачи + их карточки + их логи).
   * Доступно, когда выбран чип «Все».
   */
  async function handleDeleteAllJobs() {
    if (!jobs.length || busy) return;
    const ok = window.confirm(
      "Удалить ВСЕ задачи и все их карточки и логи?\n\n" +
        "Это необратимо: база запусков и результатов очистится полностью.",
    );
    if (!ok) return;
    setBusy(true);
    setError("");
    try {
      await deleteAllJobs();
      setSelectedJob(null);
      setQuery("");
      setItems([]);
      setJobs([]);
      setOpenIds(new Set());
      await loadJobs(); // подтянет актуальное (после удаления — пусто)
    } catch {
      setError("Не удалось удалить задачи. Проверь, что бэкенд запущен.");
    } finally {
      setBusy(false);
    }
  }

  /**
   * Удалить ОДНУ выбранную задачу каскадом (её карточки + её логи).
   * Доступно, когда выбран чип конкретной задачи.
   */
  async function handleDeleteJob() {
    if (selectedJob == null || busy) return;
    const job = jobs.find((j) => j.id === selectedJob);
    const cnt = job?.total_found ?? 0;
    const ok = window.confirm(
      `Удалить задачу #${selectedJob} и все её карточки (${cnt} шт) и логи?\n\n` +
        "Это необратимо.",
    );
    if (!ok) return;
    setBusy(true);
    setError("");
    try {
      await deleteJob(selectedJob);
      // удалили именно выбранную -> сбрасываем выбор и показываем «Все»
      setSelectedJob(null);
      setQuery("");
      setOpenIds(new Set());
      await loadJobs();
      await loadItems(null);
    } catch {
      setError("Не удалось удалить задачу. Проверь, что бэкенд запущен.");
    } finally {
      setBusy(false);
    }
  }

  // Клиентский поиск поверх серверной выборки.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) => {
      let hay =
        `${it.title || ""} ${it.url || ""} ${it.raw_text || ""}`.toLowerCase();
      // Поиск по полям structured_data
      if (it.structured_data) {
        const sd = it.structured_data;
        hay += ` ${sd.title || ""} ${sd.description || ""} ${sd.organizer || ""}`;
        hay += ` ${sd.category || ""} ${sd.location || ""} ${sd.eligibility || ""}`;
        hay += ` ${(sd.tags || []).join(" ")} ${(sd.industry || []).join(" ")}`;
        hay = hay.toLowerCase();
      }
      return hay.includes(q);
    });
  }, [items, query]);

  const totalAll = useMemo(
    () => jobs.reduce((sum, j) => sum + (j.total_found || 0), 0),
    [jobs],
  );

  const scopeLabel = selectedJob == null ? "all" : `job${selectedJob}`;
  const nothingAtAll = !loading && items.length === 0 && selectedJob == null;
  const nothingByFilter = !loading && items.length > 0 && filtered.length === 0;
  const emptyByJob = !loading && items.length === 0 && selectedJob != null;

  return (
    <div className="gr-page">
      <style>{resultsCss}</style>

      <header className="gr-head">
        <p className="gr-kicker">
          <span className="gr-kicker-dot" aria-hidden="true" />
          archive · собрано с F6S
        </p>
        <h1 className="gr-title">
          Всё, что нашёл
          <span className="gr-title-accent">парсер.</span>
        </h1>
        <p className="gr-lead">
          Здесь оседают карточки грантов, акселераторов и ивентов после каждого
          запуска. Фильтруй по задаче, ищи по тексту, выгружай выборку в CSV или
          удаляй целые запуски — данные готовы ехать дальше без ручной
          копипасты.
        </p>
      </header>

      {/* Панель управления. */}
      <div className="gr-toolbar">
        <div className="gr-chips" role="tablist" aria-label="Фильтр по задаче">
          <button
            type="button"
            role="tab"
            aria-selected={selectedJob == null}
            className={"gr-chip" + (selectedJob == null ? " is-active" : "")}
            onClick={() => pickJob(null)}
          >
            <span className="gr-chip-all" aria-hidden="true">
              ∑
            </span>
            Все
            <span className="gr-chip-num">{totalAll}</span>
          </button>

          {jobs.slice(0, 12).map((job) => {
            const active = job.id === selectedJob;
            return (
              <button
                key={job.id}
                type="button"
                role="tab"
                aria-selected={active}
                className={"gr-chip" + (active ? " is-active" : "")}
                onClick={() => pickJob(job.id)}
                style={{ "--gr-hue": hueFor(job.id) }}
                title={job.target_url}
              >
                <span
                  className={`gr-dot ${statusDot(job.status)}`}
                  aria-hidden="true"
                />
                <span className="gr-chip-id">#{job.id}</span>
                <span className="gr-chip-num">{job.total_found ?? 0}</span>
              </button>
            );
          })}
        </div>

        <div className="gr-tools">
          <label className="gr-search">
            <svg
              viewBox="0 0 24 24"
              className="gr-search-ico"
              aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <line x1="16.5" y1="16.5" x2="21" y2="21" />
            </svg>
            <input
              type="search"
              placeholder="поиск по названию, ссылке, тексту…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Поиск по карточкам"
            />
            {query && (
              <button
                type="button"
                className="gr-search-clear"
                onClick={() => setQuery("")}
                aria-label="Очистить поиск"
              >
                ×
              </button>
            )}
          </label>

          <button
            type="button"
            className="gr-btn gr-btn--ghost"
            onClick={() => downloadCsv(filtered, scopeLabel)}
            disabled={filtered.length === 0}
            title={
              filtered.length ? "Выгрузить выборку в CSV" : "Нечего выгружать"
            }
          >
            <span aria-hidden="true">↓</span> CSV
          </button>

          <button
            type="button"
            className="gr-btn gr-btn--ghost"
            onClick={refresh}
            title="Обновить список"
          >
            <span
              className={"gr-ico" + (refreshing ? " is-spin" : "")}
              aria-hidden="true"
            >
              ↻
            </span>
            обновить
          </button>

          {/* Деструктивная кнопка зависит от того, что выбрано:
              «Все» -> удалить все задачи; задача #N -> удалить эту задачу. */}
          {selectedJob == null ? (
            <button
              type="button"
              className="gr-btn gr-btn--danger"
              onClick={handleDeleteAllJobs}
              disabled={jobs.length === 0 || busy}
              title={
                jobs.length
                  ? "Удалить все задачи вместе с их карточками и логами"
                  : "Нет задач — удалять нечего"
              }
            >
              <span aria-hidden="true">🗑</span>
              {busy ? "удаление…" : "удалить все задачи"}
            </button>
          ) : (
            <button
              type="button"
              className="gr-btn gr-btn--danger"
              onClick={handleDeleteJob}
              disabled={busy}
              title={`Удалить задачу #${selectedJob} со всеми её карточками и логами`}
            >
              <span aria-hidden="true">🗑</span>
              {busy ? "удаление…" : `удалить задачу #${selectedJob}`}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="gr-error" role="alert">
          {error}
        </div>
      )}

      {/* Лента счётчиков. */}
      <div className="gr-stats">
        <span className="gr-stat">
          <b>{filtered.length}</b> показано
        </span>
        <span className="gr-stat-sep" aria-hidden="true" />
        <span className="gr-stat">
          <b>{items.length}</b> загружено
        </span>
        <span className="gr-stat-sep" aria-hidden="true" />
        <span className="gr-stat">
          <b>{jobs.length}</b> задач
        </span>
        {query && (
          <span className="gr-stat gr-stat--hint">по запросу «{query}»</span>
        )}
      </div>

      {/* Таблица-список. */}
      <div className="gr-table-wrap">
        {loading ? (
          <div className="gr-skeleton" aria-hidden="true">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                className="gr-sk-row"
                key={i}
                style={{ animationDelay: `${i * 70}ms` }}
              >
                <span className="gr-sk gr-sk--n" />
                <span className="gr-sk gr-sk--bar" />
                <span className="gr-sk gr-sk--title" />
                <span className="gr-sk gr-sk--badge" />
              </div>
            ))}
          </div>
        ) : nothingAtAll ? (
          <div className="gr-empty">
            <div className="gr-empty-mark" aria-hidden="true">
              ∅
            </div>
            <p>Пока ни одной карточки.</p>
            <span>
              Запусти парсер на вкладке «Запуск» — собранные гранты и ивенты
              появятся здесь автоматически.
            </span>
          </div>
        ) : emptyByJob ? (
          <div className="gr-empty">
            <div className="gr-empty-mark" aria-hidden="true">
              ⌗
            </div>
            <p>В задаче #{selectedJob} ничего не сохранилось.</p>
            <span>
              Парсер мог не найти карточек на странице или не пройти внутрь них.
              Проверь логи этого запуска.
            </span>
          </div>
        ) : nothingByFilter ? (
          <div className="gr-empty">
            <div className="gr-empty-mark" aria-hidden="true">
              ⌕
            </div>
            <p>По запросу «{query}» ничего не нашлось.</p>
            <button
              type="button"
              className="gr-empty-reset"
              onClick={() => setQuery("")}
            >
              Сбросить поиск
            </button>
          </div>
        ) : (
          <div
            className="gr-table"
            role="table"
            aria-label="Спаршенные карточки"
          >
            <div className="gr-thead" role="row">
              <span className="gr-th gr-th--n" role="columnheader">
                №
              </span>
              <span className="gr-th gr-th--main" role="columnheader">
                Название · источник
              </span>
              <span className="gr-th gr-th--status" role="columnheader">
                AI
              </span>
              <span className="gr-th gr-th--job" role="columnheader">
                Задача
              </span>
              <span className="gr-th gr-th--date" role="columnheader">
                Когда
              </span>
              <span
                className="gr-th gr-th--go"
                role="columnheader"
                aria-label="Ссылка на источник"
              />
            </div>

            <div className="gr-tbody">
              {filtered.map((item, i) => {
                const open = openIds.has(item.id);
                const hue = hueFor(item.job_id);
                const prev = previewOf(item.raw_text);
                const sd = item.structured_data;
                const sStatus = item.structuring_status || "skipped";
                return (
                  <div
                    key={item.id}
                    className="gr-rowblock"
                    style={{
                      "--gr-hue": hue,
                      animationDelay: `${Math.min(i, 14) * 35}ms`,
                    }}
                  >
                    <button
                      type="button"
                      className={"gr-row" + (open ? " is-open" : "")}
                      onClick={() => toggleOpen(item.id)}
                      aria-expanded={open}
                      role="row"
                    >
                      <span className="gr-cell gr-cell--n" role="cell">
                        {String(i + 1).padStart(2, "0")}
                      </span>

                      <span className="gr-cell gr-cell--main" role="cell">
                        <span className="gr-item-title">
                          {(sd && sd.title) || item.title || "Без названия"}
                          {sd && sd.category && (
                            <span className="gr-cat-badge">{sd.category}</span>
                          )}
                        </span>
                        <span className="gr-item-sub">
                          <span className="gr-item-host">
                            {hostOf(item.url) || "—"}
                          </span>
                          {sd && sd.organizer && (
                            <>
                              <span className="gr-item-sep" aria-hidden="true">
                                ·
                              </span>
                              <span className="gr-item-org">{sd.organizer}</span>
                            </>
                          )}
                          {!sd && prev && (
                            <>
                              <span className="gr-item-sep" aria-hidden="true">
                                ·
                              </span>
                              <span className="gr-item-prev">{prev}</span>
                            </>
                          )}
                        </span>
                      </span>

                      <span className="gr-cell gr-cell--status" role="cell">
                        <span
                          className={`gr-struct-badge gr-struct-badge--${sStatus}`}
                          title={
                            sStatus === "success" ? "AI-структурировано" :
                            sStatus === "pending" ? "Ожидает обработки" :
                            sStatus === "failed" ? `Ошибка: ${item.structuring_error || "неизвестная"}` :
                            "Без структурирования"
                          }
                        >
                          {sStatus === "success" ? "🧠" :
                           sStatus === "pending" ? "⏳" :
                           sStatus === "failed" ? "⚠" : "📋"}
                        </span>
                      </span>

                      <span className="gr-cell gr-cell--job" role="cell">
                        <span className="gr-job-badge">#{item.job_id}</span>
                      </span>

                      <span className="gr-cell gr-cell--date" role="cell">
                        {shortDate(item.created_at)}
                      </span>

                      <span className="gr-cell gr-cell--go" role="cell">
                        <a
                          className="gr-ext"
                          href={item.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="Открыть источник"
                          onClick={(e) => e.stopPropagation()}
                          aria-label="Открыть источник в новой вкладке"
                        >
                          ↗
                        </a>
                        <span
                          className={"gr-caret" + (open ? " is-open" : "")}
                          aria-hidden="true"
                        >
                          ⌄
                        </span>
                      </span>
                    </button>

                    {/* Раскрывающаяся панель */}
                    <div className={"gr-expand" + (open ? " is-open" : "")}>
                      <div className="gr-expand-inner">
                        <div className="gr-raw">
                          <div className="gr-raw-head">
                            {!sd && <span className="gr-raw-cap">raw_text</span>}
                            <button
                              type="button"
                              className={
                                "gr-copy" +
                                (copiedId === item.id ? " is-done" : "")
                              }
                              onClick={(e) => {
                                e.stopPropagation();
                                copyText(item);
                              }}
                            >
                              {copiedId === item.id
                                ? "✓ скопировано"
                                : "копировать"}
                            </button>
                          </div>

                          {sd ? (
                            <div className="gr-structured">
                              {sd.description && (
                                <p className="gr-sd-desc">{sd.description}</p>
                              )}

                              <div className="gr-sd-grid">
                                {sd.category && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Категория</span>
                                    <span className="gr-sd-value gr-sd-cat">{sd.category}</span>
                                  </div>
                                )}
                                {sd.organizer && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Организатор</span>
                                    <span className="gr-sd-value">{sd.organizer}</span>
                                  </div>
                                )}
                                {sd.location && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Место</span>
                                    <span className="gr-sd-value">{sd.location}</span>
                                  </div>
                                )}
                                {sd.country && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Страна</span>
                                    <span className="gr-sd-value">{sd.country}</span>
                                  </div>
                                )}
                                {sd.deadline && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Дедлайн</span>
                                    <span className="gr-sd-value gr-sd-deadline">{sd.deadline}</span>
                                  </div>
                                )}
                                {sd.start_date && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Начало</span>
                                    <span className="gr-sd-value">{sd.start_date}</span>
                                  </div>
                                )}
                                {sd.end_date && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Окончание</span>
                                    <span className="gr-sd-value">{sd.end_date}</span>
                                  </div>
                                )}
                                {sd.funding_amount && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Финансирование</span>
                                    <span className="gr-sd-value gr-sd-funding">
                                      {sd.funding_amount}{sd.currency ? ` ${sd.currency}` : ""}
                                    </span>
                                  </div>
                                )}
                                {sd.stage && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Стадия</span>
                                    <span className="gr-sd-value">{sd.stage}</span>
                                  </div>
                                )}
                                {sd.language && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Язык</span>
                                    <span className="gr-sd-value">{sd.language}</span>
                                  </div>
                                )}
                                {sd.is_free != null && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Бесплатно</span>
                                    <span className="gr-sd-value">{sd.is_free ? "Да ✓" : "Нет"}</span>
                                  </div>
                                )}
                                {sd.confidence_score != null && (
                                  <div className="gr-sd-field">
                                    <span className="gr-sd-label">Уверенность AI</span>
                                    <span className="gr-sd-value">
                                      {Math.round(sd.confidence_score * 100)}%
                                    </span>
                                  </div>
                                )}
                              </div>

                              {sd.eligibility && (
                                <div className="gr-sd-block">
                                  <span className="gr-sd-label">Кто может участвовать</span>
                                  <p className="gr-sd-text">{sd.eligibility}</p>
                                </div>
                              )}

                              {sd.requirements && sd.requirements.length > 0 && (
                                <div className="gr-sd-block">
                                  <span className="gr-sd-label">Требования</span>
                                  <ul className="gr-sd-list">
                                    {sd.requirements.map((r, ri) => (
                                      <li key={ri}>{r}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {sd.benefits && sd.benefits.length > 0 && (
                                <div className="gr-sd-block">
                                  <span className="gr-sd-label">Преимущества</span>
                                  <ul className="gr-sd-list">
                                    {sd.benefits.map((b, bi) => (
                                      <li key={bi}>{b}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {sd.full_description && (
                                <div className="gr-sd-block">
                                  <span className="gr-sd-label">Полное описание</span>
                                  <p className="gr-sd-text gr-sd-full">{sd.full_description}</p>
                                </div>
                              )}

                              {sd.tags && sd.tags.length > 0 && (
                                <div className="gr-sd-tags">
                                  {sd.tags.map((tag, ti) => (
                                    <span key={ti} className="gr-sd-tag">{tag}</span>
                                  ))}
                                </div>
                              )}

                              {sd.industry && sd.industry.length > 0 && (
                                <div className="gr-sd-tags">
                                  {sd.industry.map((ind, ii) => (
                                    <span key={ii} className="gr-sd-tag gr-sd-tag--ind">{ind}</span>
                                  ))}
                                </div>
                              )}

                              {sd.application_url && (
                                <a
                                  className="gr-sd-apply"
                                  href={sd.application_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  Подать заявку →
                                </a>
                              )}
                            </div>
                          ) : (
                            <pre className="gr-raw-body">
                              {item.raw_text || "—"}
                            </pre>
                          )}

                          {item.structuring_error && (
                            <div className="gr-sd-error">
                              ⚠ {item.structuring_error}
                            </div>
                          )}

                          <a
                            className="gr-raw-url"
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {item.url}
                          </a>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const resultsCss = `
.gr-page {
  display: flex;
  flex-direction: column;
  gap: clamp(1.3rem, 3vw, 2rem);
  animation: gr-in 0.5s cubic-bezier(0.22, 1, 0.36, 1) both;
}
@keyframes gr-in {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: none; }
}

.gr-kicker {
  display: inline-flex; align-items: center; gap: 0.55rem;
  margin: 0 0 0.85rem;
  font-size: 0.72rem; letter-spacing: 0.26em; text-transform: uppercase;
  color: var(--gt-ink-dim);
}
.gr-kicker-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--gt-amber); box-shadow: 0 0 12px rgba(240,168,80,0.6);
}
.gr-title {
  margin: 0;
  font-family: var(--gt-display); font-weight: 700;
  font-size: clamp(2rem, 5.5vw, 3.3rem); line-height: 1.03; letter-spacing: -0.02em;
  color: var(--gt-ink);
}
.gr-title-accent { display: inline-block; margin-left: 0.35ch; color: var(--gt-teal); }
.gr-lead {
  margin: 0.9rem 0 0; max-width: 62ch;
  color: var(--gt-ink-dim);
  font-size: clamp(0.96rem, 1.3vw, 1.08rem); line-height: 1.6;
}

/* ---- toolbar ---- */
.gr-toolbar {
  display: flex; flex-direction: column; gap: 0.9rem;
  padding: clamp(0.9rem, 2vw, 1.25rem);
  border: 1px solid var(--gt-line); border-radius: 1rem;
  background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.015));
}
.gr-chips { display: flex; flex-wrap: wrap; gap: 0.45rem; }
.gr-chip {
  display: inline-flex; align-items: center; gap: 0.45rem;
  padding: 0.42rem 0.75rem; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--gt-line); background: rgba(255,255,255,0.02);
  color: var(--gt-ink-dim); font-size: 0.84rem;
  transition: border-color 0.2s ease, background 0.2s ease, color 0.2s ease, transform 0.15s ease;
}
.gr-chip:hover { color: var(--gt-ink); border-color: rgba(255,255,255,0.22); transform: translateY(-1px); }
.gr-chip.is-active {
  color: var(--gt-ink);
  border-color: var(--gr-hue, var(--gt-amber));
  background: color-mix(in srgb, var(--gr-hue, var(--gt-amber)) 14%, transparent);
}
.gr-chip-all { font-family: var(--gt-display); font-weight: 700; color: var(--gt-amber); }
.gr-chip-id { font-family: var(--gt-display); font-weight: 600; }
.gr-chip-num {
  font-size: 0.72rem; font-variant-numeric: tabular-nums;
  padding: 0.05rem 0.4rem; border-radius: 999px;
  background: rgba(255,255,255,0.06); color: var(--gt-ink-dim);
}
.gr-chip.is-active .gr-chip-num { background: rgba(255,255,255,0.12); color: var(--gt-ink); }

.gr-tools { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.gr-search {
  position: relative; display: inline-flex; align-items: center;
  flex: 1 1 260px; min-width: 200px;
  border: 1px solid var(--gt-line); border-radius: 0.7rem;
  background: rgba(10,13,22,0.6);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.gr-search:focus-within {
  border-color: var(--gt-amber);
  box-shadow: 0 0 0 4px rgba(240,168,80,0.14);
}
.gr-search-ico {
  width: 1.05rem; height: 1.05rem; margin-left: 0.7rem; flex: none;
  fill: none; stroke: var(--gt-ink-dim); stroke-width: 2; stroke-linecap: round;
}
.gr-search input {
  flex: 1; min-width: 0; border: none; background: transparent;
  color: var(--gt-ink); padding: 0.7rem 0.6rem; font-size: 0.92rem;
  font-family: var(--gt-body);
}
.gr-search input:focus { outline: none; }
.gr-search input::placeholder { color: rgba(151,163,189,0.55); }
.gr-search-clear {
  border: none; background: transparent; color: var(--gt-ink-dim);
  cursor: pointer; font-size: 1.2rem; line-height: 1; padding: 0 0.7rem;
}
.gr-search-clear:hover { color: var(--gt-ink); }

.gr-btn {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.6rem 0.95rem; border-radius: 0.7rem; cursor: pointer;
  font-size: 0.86rem; font-weight: 600; font-family: var(--gt-body);
  transition: transform 0.15s ease, border-color 0.2s ease, color 0.2s ease, background 0.2s ease;
}
.gr-btn--ghost {
  border: 1px solid var(--gt-line); background: rgba(255,255,255,0.02);
  color: var(--gt-ink-dim);
}
.gr-btn--ghost:hover:not(:disabled) { color: var(--gt-ink); border-color: rgba(255,255,255,0.24); transform: translateY(-1px); }
.gr-btn--danger {
  border: 1px solid color-mix(in srgb, #ff6b6b 42%, var(--gt-line));
  background: rgba(255,107,107,0.06); color: #ff9a9a;
}
.gr-btn--danger:hover:not(:disabled) { color: #fff; border-color: #ff6b6b; background: rgba(255,107,107,0.16); transform: translateY(-1px); }
.gr-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.gr-ico { display: inline-block; }
.gr-ico.is-spin { animation: gr-spin 0.7s linear infinite; }
@keyframes gr-spin { to { transform: rotate(360deg); } }

/* ---- плашка ошибки ---- */
.gr-error {
  padding: 0.6rem 0.95rem; border-radius: 0.7rem;
  border: 1px solid color-mix(in srgb, #ff6b6b 45%, var(--gt-line));
  background: rgba(255,107,107,0.1); color: #ffb4b4; font-size: 0.86rem;
}

/* ---- лента счётчиков ---- */
.gr-stats { display: flex; align-items: center; gap: 0.8rem; flex-wrap: wrap; color: var(--gt-ink-dim); font-size: 0.84rem; }
.gr-stats b { color: var(--gt-ink); font-family: var(--gt-display); font-weight: 600; font-variant-numeric: tabular-nums; margin-right: 0.3rem; }
.gr-stat-sep { width: 4px; height: 4px; border-radius: 50%; background: rgba(151,163,189,0.4); }
.gr-stat--hint { color: var(--gt-amber); }

/* ---- таблица ---- */
.gr-table-wrap {
  border: 1px solid var(--gt-line); border-radius: 1rem; overflow: hidden;
  background: rgba(255,255,255,0.015);
  box-shadow: 0 24px 60px -40px rgba(0,0,0,0.85);
}
.gr-thead {
  display: grid;
  grid-template-columns: 3rem 1fr 3rem 5.5rem 7.5rem 4.5rem;
  gap: 0.75rem; align-items: center;
  padding: 0.7rem 1rem;
  background: rgba(255,255,255,0.03);
  border-bottom: 1px solid var(--gt-line);
  position: sticky; top: 0; z-index: 2;
  backdrop-filter: blur(6px);
}
.gr-th {
  font-size: 0.68rem; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--gt-ink-dim); font-weight: 600;
}
.gr-th--n, .gr-th--go { text-align: center; }

.gr-tbody { display: flex; flex-direction: column; }

.gr-rowblock {
  border-bottom: 1px solid var(--gt-line);
  animation: gr-row-in 0.45s cubic-bezier(0.22, 1, 0.36, 1) both;
}
.gr-rowblock:last-child { border-bottom: none; }
@keyframes gr-row-in {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: none; }
}

.gr-row {
  display: grid;
  grid-template-columns: 3rem 1fr 3rem 5.5rem 7.5rem 4.5rem;
  gap: 0.75rem; align-items: center;
  width: 100%; text-align: left; cursor: pointer;
  padding: 0.85rem 1rem 0.85rem calc(1rem - 3px);
  background: transparent; border: none; color: inherit;
  border-left: 3px solid var(--gr-hue, transparent);
  transition: background 0.18s ease, padding-left 0.18s ease;
}
.gr-row:hover { background: rgba(255,255,255,0.03); padding-left: 1rem; }
.gr-row.is-open { background: color-mix(in srgb, var(--gr-hue) 7%, transparent); }

.gr-cell--n { text-align: center; font-family: var(--gt-display); font-weight: 600; color: var(--gt-ink-dim); font-variant-numeric: tabular-nums; }
.gr-cell--main { min-width: 0; display: flex; flex-direction: column; gap: 0.2rem; }
.gr-item-title {
  font-weight: 600; color: var(--gt-ink); font-size: 0.98rem; line-height: 1.3;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.gr-item-sub { display: flex; align-items: baseline; gap: 0.45rem; min-width: 0; }
.gr-item-host {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 0.76rem; color: var(--gr-hue, var(--gt-amber)); flex: none;
}
.gr-item-sep { color: rgba(151,163,189,0.4); flex: none; }
.gr-item-prev {
  color: var(--gt-ink-dim); font-size: 0.8rem;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.gr-cell--job { text-align: center; }
.gr-job-badge {
  display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px;
  font-family: var(--gt-display); font-weight: 600; font-size: 0.76rem;
  color: var(--gr-hue, var(--gt-amber));
  border: 1px solid color-mix(in srgb, var(--gr-hue, var(--gt-amber)) 45%, transparent);
  background: color-mix(in srgb, var(--gr-hue, var(--gt-amber)) 12%, transparent);
}
.gr-cell--date { color: var(--gt-ink-dim); font-size: 0.8rem; font-variant-numeric: tabular-nums; }
.gr-cell--go { display: inline-flex; align-items: center; justify-content: center; gap: 0.4rem; }
.gr-ext {
  display: grid; place-items: center; width: 1.9rem; height: 1.9rem;
  border-radius: 0.5rem; text-decoration: none; color: var(--gt-ink-dim);
  border: 1px solid var(--gt-line); font-size: 0.95rem;
  transition: color 0.2s ease, border-color 0.2s ease, transform 0.2s ease, background 0.2s ease;
}
.gr-ext:hover { color: var(--gt-ink); border-color: rgba(255,255,255,0.3); background: rgba(255,255,255,0.05); transform: translate(1px, -1px); }
.gr-caret { color: var(--gt-ink-dim); transition: transform 0.25s ease; font-size: 1.1rem; line-height: 1; }
.gr-caret.is-open { transform: rotate(180deg); color: var(--gr-hue, var(--gt-amber)); }

/* раскрытие */
.gr-expand {
  display: grid; grid-template-rows: 0fr;
  transition: grid-template-rows 0.3s cubic-bezier(0.22, 1, 0.36, 1);
}
.gr-expand.is-open { grid-template-rows: 1fr; }
.gr-expand-inner { overflow: hidden; }
.gr-raw {
  margin: 0 1rem 1rem calc(1rem);
  padding: 0.9rem 1rem;
  border-radius: 0.7rem;
  border: 1px solid var(--gt-line);
  border-left: 3px solid var(--gr-hue, var(--gt-amber));
  background: #070a12;
}
.gr-raw-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.6rem; }
.gr-raw-cap {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 0.7rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--gt-ink-dim);
}
.gr-copy {
  border: 1px solid var(--gt-line); background: rgba(255,255,255,0.03);
  color: var(--gt-ink-dim); border-radius: 0.5rem; padding: 0.3rem 0.7rem;
  cursor: pointer; font-size: 0.76rem; font-weight: 600;
  transition: color 0.2s ease, border-color 0.2s ease, background 0.2s ease;
}
.gr-copy:hover { color: var(--gt-ink); border-color: rgba(255,255,255,0.24); }
.gr-copy.is-done { color: var(--gt-teal); border-color: rgba(88,214,192,0.5); background: rgba(88,214,192,0.1); }
.gr-raw-body {
  margin: 0; white-space: pre-wrap; word-break: break-word;
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 0.82rem; line-height: 1.6; color: var(--gt-ink);
  max-height: 280px; overflow-y: auto;
  scrollbar-width: thin; scrollbar-color: rgba(240,168,80,0.4) transparent;
}
.gr-raw-body::-webkit-scrollbar { width: 8px; }
.gr-raw-body::-webkit-scrollbar-thumb { background: rgba(240,168,80,0.35); border-radius: 999px; }
.gr-raw-url {
  display: inline-block; margin-top: 0.7rem;
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 0.76rem; color: var(--gt-amber); text-decoration: none;
  word-break: break-all; border-bottom: 1px dashed rgba(240,168,80,0.4);
}
.gr-raw-url:hover { color: #ffd79a; }

/* скелетоны */
.gr-skeleton { padding: 0.5rem 0; }
.gr-sk-row {
  display: grid; grid-template-columns: 3rem 1fr 5.5rem 7.5rem;
  gap: 0.75rem; align-items: center; padding: 0.95rem 1rem;
  border-bottom: 1px solid var(--gt-line);
  animation: gr-sk-fade 1.1s ease-in-out infinite alternate;
}
@keyframes gr-sk-fade { from { opacity: 0.45; } to { opacity: 0.85; } }
.gr-sk { display: block; height: 0.8rem; border-radius: 0.4rem; background: rgba(255,255,255,0.07); }
.gr-sk--n { width: 1.4rem; }
.gr-sk--bar { width: 40%; height: 0.6rem; }
.gr-sk--title { width: 70%; }
.gr-sk--badge { width: 2.4rem; height: 1.1rem; border-radius: 999px; }

/* пусто */
.gr-empty {
  display: flex; flex-direction: column; align-items: center; text-align: center;
  gap: 0.5rem; padding: 3rem 1.5rem; color: var(--gt-ink-dim);
}
.gr-empty-mark { font-family: var(--gt-display); font-size: 2.4rem; color: rgba(151,163,189,0.4); margin-bottom: 0.3rem; }
.gr-empty p { margin: 0; color: var(--gt-ink); font-weight: 600; font-size: 1.05rem; }
.gr-empty span { font-size: 0.9rem; max-width: 44ch; }
.gr-empty-reset {
  margin-top: 0.4rem; border: 1px solid rgba(240,168,80,0.4); background: rgba(240,168,80,0.1);
  color: var(--gt-amber); border-radius: 0.6rem; padding: 0.5rem 1rem; cursor: pointer;
  font-size: 0.85rem; font-weight: 600; transition: background 0.2s ease;
}
.gr-empty-reset:hover { background: rgba(240,168,80,0.18); }

/* точки статуса в чипах */
.gr-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.gr-dot--pending   { background: var(--gt-ink-dim); opacity: 0.6; }
.gr-dot--running   { background: var(--gt-amber); animation: gr-pulse 1.4s ease-out infinite; }
.gr-dot--completed { background: var(--gt-teal); }
.gr-dot--failed    { background: #ff6b6b; }
@keyframes gr-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(240,168,80,0.55); }
  70%  { box-shadow: 0 0 0 8px rgba(240,168,80,0); }
  100% { box-shadow: 0 0 0 0 rgba(240,168,80,0); }
}

@media (max-width: 720px) {
  .gr-thead { display: none; }
  .gr-row {
    grid-template-columns: 2.2rem 1fr auto;
    grid-template-areas:
      "n main go"
      "n job go";
    row-gap: 0.35rem; column-gap: 0.6rem;
  }
  .gr-cell--n { grid-area: n; }
  .gr-cell--main { grid-area: main; }
  .gr-cell--status { display: none; }
  .gr-cell--job { grid-area: job; text-align: left; }
  .gr-cell--date { display: none; }
  .gr-cell--go { grid-area: go; }
  .gr-item-prev { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .gr-page, .gr-rowblock, .gr-sk-row { animation: none; }
  .gr-dot--running, .gr-ico.is-spin { animation: none; }
  .gr-expand { transition: none; }
}

/* ---- category badge в строке ---- */
.gr-cat-badge {
  display: inline-block; margin-left: 0.5rem;
  padding: 0.1rem 0.45rem; border-radius: 0.35rem;
  font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; vertical-align: middle;
  color: var(--gt-teal); background: rgba(88,214,192,0.12);
  border: 1px solid rgba(88,214,192,0.3);
}
.gr-item-org {
  color: var(--gt-ink-dim); font-size: 0.8rem;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* ---- structuring status ---- */
.gr-cell--status { text-align: center; }
.gr-struct-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 1.8rem; height: 1.8rem; border-radius: 0.45rem;
  font-size: 0.85rem; line-height: 1;
  transition: transform 0.15s ease;
}
.gr-struct-badge:hover { transform: scale(1.15); }
.gr-struct-badge--success { background: rgba(88,214,192,0.12); border: 1px solid rgba(88,214,192,0.3); }
.gr-struct-badge--pending { background: rgba(240,168,80,0.1); border: 1px solid rgba(240,168,80,0.3); }
.gr-struct-badge--failed  { background: rgba(255,107,107,0.1); border: 1px solid rgba(255,107,107,0.3); }
.gr-struct-badge--skipped { background: rgba(255,255,255,0.03); border: 1px solid var(--gt-line); }



/* ---- structured data display ---- */
.gr-structured {
  display: flex; flex-direction: column; gap: 0.8rem;
}
.gr-sd-desc {
  margin: 0; color: var(--gt-ink); font-size: 0.92rem;
  line-height: 1.55; border-left: 2px solid var(--gt-teal);
  padding-left: 0.75rem;
}
.gr-sd-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 0.55rem;
}
.gr-sd-field {
  display: flex; flex-direction: column; gap: 0.15rem;
  padding: 0.5rem 0.65rem; border-radius: 0.5rem;
  background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
}
.gr-sd-label {
  font-size: 0.65rem; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--gt-ink-dim); font-weight: 600;
}
.gr-sd-value {
  color: var(--gt-ink); font-size: 0.88rem; font-weight: 500;
  word-break: break-word;
}
.gr-sd-cat { color: var(--gt-teal); font-weight: 700; text-transform: uppercase; font-size: 0.8rem; }
.gr-sd-deadline { color: var(--gt-amber); font-weight: 600; }
.gr-sd-funding { color: #7aff7a; font-weight: 700; font-family: var(--gt-display); }

.gr-sd-block {
  display: flex; flex-direction: column; gap: 0.3rem;
}
.gr-sd-text {
  margin: 0; color: var(--gt-ink); font-size: 0.86rem; line-height: 1.55;
  max-height: 200px; overflow-y: auto;
  scrollbar-width: thin; scrollbar-color: rgba(240,168,80,0.4) transparent;
}
.gr-sd-full { font-size: 0.82rem; color: rgba(220,225,240,0.85); }
.gr-sd-list {
  margin: 0.2rem 0 0; padding-left: 1.2rem;
  color: var(--gt-ink); font-size: 0.84rem; line-height: 1.6;
}
.gr-sd-list li::marker { color: var(--gt-teal); }

.gr-sd-tags {
  display: flex; flex-wrap: wrap; gap: 0.35rem;
}
.gr-sd-tag {
  display: inline-block; padding: 0.2rem 0.55rem;
  border-radius: 999px; font-size: 0.72rem; font-weight: 600;
  color: var(--gt-amber); background: rgba(240,168,80,0.1);
  border: 1px solid rgba(240,168,80,0.3);
}
.gr-sd-tag--ind {
  color: #7aa2ff; background: rgba(122,162,255,0.1);
  border-color: rgba(122,162,255,0.3);
}

.gr-sd-apply {
  display: inline-flex; align-items: center; gap: 0.3rem;
  align-self: flex-start;
  padding: 0.45rem 0.9rem; border-radius: 0.55rem;
  font-size: 0.82rem; font-weight: 700; text-decoration: none;
  color: #0a0d16; background: var(--gt-teal);
  transition: transform 0.15s ease, box-shadow 0.2s ease;
}
.gr-sd-apply:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(88,214,192,0.35);
}

.gr-sd-error {
  margin-top: 0.4rem; padding: 0.4rem 0.6rem;
  border-radius: 0.4rem; font-size: 0.76rem;
  color: #ffb4b4; background: rgba(255,107,107,0.08);
  border: 1px solid rgba(255,107,107,0.25);
}
`;
