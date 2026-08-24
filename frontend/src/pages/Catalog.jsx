import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchItems } from "../api";

/**
 * Catalog — единая страница всех найденных конкурсов/грантов
 * с супер-подробной фильтрацией и поиском.
 *
 * Данные: GET /items (все карточки), фильтрация полностью клиентская —
 * объёмы MVP (сотни карточек) это позволяют, ответ мгновенный.
 *
 * Фильтры:
 *  - полнотекстовый поиск (название, описание, организатор, теги, текст);
 *  - категория (grant / accelerator / hackathon / ...);
 *  - страна/регион (Global, Russia, Europe, ...);
 *  - стадия стартапа (idea / mvp / early / growth / any);
 *  - индустрия (AI/ML, FinTech, ...);
 *  - язык (RU / EN / ...);
 *  - теги (динамический список из данных, мультивыбор);
 *  - только бесплатные;
 *  - только с дедлайном в будущем;
 *  - сортировка: новые / дедлайн / финансирование / название.
 *
 * Стили локальные, префикс gc- (grantum catalog), цвета из --gt-* каркаса.
 */

const CATEGORIES = [
  "grant", "accelerator", "hackathon", "competition", "fellowship",
  "event", "course", "incubator", "scholarship", "other",
];

const CATEGORY_LABELS = {
  grant: "Грант",
  accelerator: "Акселератор",
  hackathon: "Хакатон",
  competition: "Конкурс",
  fellowship: "Стипендия/Fellowship",
  event: "Ивент",
  course: "Курс",
  incubator: "Инкубатор",
  scholarship: "Scholarship",
  other: "Другое",
};

const COUNTRIES = [
  "Global", "Europe", "CIS", "MENA", "LATAM", "APAC",
  "Russia", "USA", "UK", "Germany", "France", "China", "India", "UAE",
  "Kazakhstan", "Belarus", "Uzbekistan", "Israel", "Singapore", "Canada",
  "Australia", "Other",
];

const STAGES = ["idea", "mvp", "early", "growth", "any"];

const STAGE_LABELS = {
  idea: "Idea",
  mvp: "MVP",
  early: "Early",
  growth: "Growth",
  any: "Любая",
};

const LANGUAGES = ["RU", "EN", "ES", "FR", "DE", "ZH", "AR"];

const SORTS = [
  { value: "new", label: "Сначала новые" },
  { value: "deadline", label: "По дедлайну" },
  { value: "funding", label: "По финансированию" },
  { value: "title", label: "По названию" },
];

/** Пытаемся вытащить дату из текстового поля дедлайна. */
function parseDeadline(text) {
  if (!text) return null;
  const s = String(text).trim();
  // ISO или близкие форматы: 2024-05-01, 01.05.2024, 1 May 2024 и т.п.
  const iso = Date.parse(s);
  if (!Number.isNaN(iso)) return new Date(iso);
  const dmy = s.match(/(\d{1,2})[./](\d{1,2})[./](\d{2,4})/);
  if (dmy) {
    const year = dmy[3].length === 2 ? `20${dmy[3]}` : dmy[3];
    return new Date(+year, +dmy[2] - 1, +dmy[1]);
  }
  return null;
}

/** Числовое значение финансирования для сортировки ("$50,000" -> 50000). */
function fundingNumber(sd) {
  if (!sd) return -1;
  const m = `${sd.funding_amount || ""}`.replace(",", ".").match(
    /(\d+(?:\.\d+)?)/,
  );
  return m ? parseFloat(m[1]) : -1;
}

function toggleSet(set, value) {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

export default function Catalog() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  // Состояние фильтров
  const [query, setQuery] = useState("");
  const [cats, setCats] = useState(() => new Set());
  const [countries, setCountries] = useState(() => new Set());
  const [stages, setStages] = useState(() => new Set());
  const [industries, setIndustries] = useState(() => new Set());
  const [languages, setLanguages] = useState(() => new Set());
  const [tags, setTags] = useState(() => new Set());
  const [freeOnly, setFreeOnly] = useState(false);
  const [activeDeadlineOnly, setActiveDeadlineOnly] = useState(false);
  const [sort, setSort] = useState("new");

  /** Все теги из структурированных данных с частотой. */
  const allTags = useMemo(() => {
    const freq = new Map();
    for (const it of items) {
      const t = it.structured_data?.tags;
      if (!Array.isArray(t)) continue;
      for (const tag of t) {
        const k = String(tag).trim();
        if (!k) continue;
        freq.set(k, (freq.get(k) || 0) + 1);
      }
    }
    return [...freq.entries()].sort((a, b) => b[1] - a[1]).map(([t]) => t);
  }, [items]);

  /** Все индустрии, реально встречающиеся в данных. */
  const allIndustries = useMemo(() => {
    const set = new Set();
    for (const it of items) {
      const ind = it.structured_data?.industry;
      if (Array.isArray(ind)) ind.forEach((i) => i && set.add(i));
    }
    return [...set].sort();
  }, [items]);

  /** Основная фильтрация + сортировка. */
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const now = new Date();

    let out = items.filter((it) => {
      const sd = it.structured_data;

      if (!sd) return false; // каталог показывает только AI-структурированные

      if (cats.size && !cats.has(sd.category)) return false;
      if (countries.size && !countries.has(sd.country)) return false;
      if (stages.size && !stages.has(sd.stage || "any")) return false;
      if (languages.size && !languages.has(sd.language)) return false;
      if (freeOnly && sd.is_free !== true) return false;

      if (industries.size) {
        const ind = Array.isArray(sd.industry) ? sd.industry : [];
        if (!ind.some((i) => industries.has(i))) return false;
      }

      if (tags.size) {
        const t = Array.isArray(sd.tags) ? sd.tags : [];
        if (!t.some((x) => tags.has(String(x).trim()))) return false;
      }

      if (activeDeadlineOnly) {
        const dl = parseDeadline(sd.deadline);
        if (!dl || dl < now) return false;
      }

      if (q) {
        const hay = [
          sd.title, it.title, sd.description, sd.full_description,
          sd.organizer, sd.location, sd.country, sd.eligibility,
          sd.deadline, sd.funding_amount,
          ...(Array.isArray(sd.tags) ? sd.tags : []),
          ...(Array.isArray(sd.industry) ? sd.industry : []),
          it.raw_text || "",
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }

      return true;
    });

    out = out.sort((a, b) => {
      switch (sort) {
        case "deadline": {
          const da = parseDeadline(a.structured_data?.deadline);
          const db = parseDeadline(b.structured_data?.deadline);
          if (!da && !db) return 0;
          if (!da) return 1; // без дедлайна — вниз
          if (!db) return -1;
          return da - db;
        }
        case "funding":
          return fundingNumber(b.structured_data) -
            fundingNumber(a.structured_data);
        case "title": {
          const ta = (a.structured_data?.title || a.title || "").toLowerCase();
          const tb = (b.structured_data?.title || b.title || "").toLowerCase();
          return ta.localeCompare(tb, "ru");
        }
        case "new":
        default:
          return new Date(b.created_at || 0) - new Date(a.created_at || 0);
      }
    });

    return out;
  }, [
    items, query, cats, countries, stages, industries, languages,
    tags, freeOnly, activeDeadlineOnly, sort,
  ]);

  const loadItems = useCallback(async ({ isRefresh = false } = {}) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    try {
      const data = await fetchItems();
      setItems(Array.isArray(data) ? data : []);
    } catch {
      setError("Не удалось загрузить данные. Проверь, что бэкенд запущен.");
      setItems([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadItems();
  }, [loadItems]);

  const activeFilters =
    cats.size + countries.size + stages.size + industries.size +
    languages.size + tags.size + (freeOnly ? 1 : 0) +
    (activeDeadlineOnly ? 1 : 0) + (query.trim() ? 1 : 0);

  function resetAll() {
    setQuery("");
    setCats(new Set());
    setCountries(new Set());
    setStages(new Set());
    setIndustries(new Set());
    setLanguages(new Set());
    setTags(new Set());
    setFreeOnly(false);
    setActiveDeadlineOnly(false);
    setSort("new");
  }

  /** Рендер группы чипов-чекбоксов. */
  function chipGroup(label, values, selected, setter, format = (v) => v) {
    return (
      <div className="gc-group" key={label}>
        <span className="gc-group-label">{label}</span>
        <div className="gc-chips">
          {values.map((v) => {
            const on = selected.has(v);
            return (
              <button
                key={v}
                type="button"
                className={"gc-chip" + (on ? " is-on" : "")}
                aria-pressed={on}
                onClick={() => setter(toggleSet(selected, v))}
              >
                {format(v)}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  const structuredCount = useMemo(
    () => items.filter((it) => it.structured_data).length,
    [items],
  );

  return (
    <div className="gc-page">
      <style>{catalogCss}</style>

      <header className="gc-head">
        <p className="gc-kicker">
          <span className="gc-kicker-dot" aria-hidden="true" />
          catalog · все возможности в одном месте
        </p>
        <h1 className="gc-title">
          Каталог <span className="gc-title-accent">конкурсов.</span>
        </h1>
        <p className="gc-lead">
          Все гранты, акселераторы, хакатоны и конкурсы, собранные парсером и
          размеченные нейросетью. Комбинируй фильтры, чтобы найти своё.
        </p>
      </header>

      {/* Поиск + сортировка */}
      <div className="gc-toolbar">
        <label className="gc-search">
          <svg viewBox="0 0 24 24" className="gc-search-ico" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <line x1="16.5" y1="16.5" x2="21" y2="21" />
          </svg>
          <input
            type="search"
            placeholder="поиск: название, описание, организатор, тег…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Поиск по каталогу"
          />
          {query && (
            <button
              type="button"
              className="gc-search-clear"
              onClick={() => setQuery("")}
              aria-label="Очистить поиск"
            >
              ×
            </button>
          )}
        </label>

        <label className="gc-sort">
          <span>сортировка</span>
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </label>

        <label className={"gc-toggle" + (freeOnly ? " is-on" : "")}>
          <input
            type="checkbox"
            checked={freeOnly}
            onChange={(e) => setFreeOnly(e.target.checked)}
          />
          бесплатно
        </label>

        <label className={"gc-toggle" + (activeDeadlineOnly ? " is-on" : "")}>
          <input
            type="checkbox"
            checked={activeDeadlineOnly}
            onChange={(e) => setActiveDeadlineOnly(e.target.checked)}
          />
          приём открыт
        </label>
      </div>

      {/* Расширенные фильтры */}
      <div className="gc-filters">
        <div className="gc-filters-head">
          <span className="gc-filters-title">Фильтры</span>
          <span className="gc-filters-count">
            {activeFilters > 0 && `активно: ${activeFilters}`}
          </span>
          {activeFilters > 0 && (
            <button type="button" className="gc-reset" onClick={resetAll}>
              сбросить всё ×
            </button>
          )}
        </div>

        {chipGroup("Категория", CATEGORIES, cats, setCats,
          (c) => CATEGORY_LABELS[c] || c)}

        {chipGroup("Страна / регион", COUNTRIES, countries, setCountries)}

        {chipGroup("Стадия", STAGES, stages, setStages,
          (s) => STAGE_LABELS[s] || s)}

        {allIndustries.length > 0 &&
          chipGroup("Индустрия", allIndustries, industries, setIndustries)}

        {chipGroup("Язык", LANGUAGES, languages, setLanguages)}

        {allTags.length > 0 && (
          <div className="gc-group">
            <span className="gc-group-label">
              Теги ({allTags.length})
            </span>
            <div className="gc-chips gc-chips--tags">
              {allTags.slice(0, 60).map((tag) => {
                const on = tags.has(tag);
                return (
                  <button
                    key={tag}
                    type="button"
                    className={
                      "gc-chip gc-chip--tag" + (on ? " is-on" : "")
                    }
                    aria-pressed={on}
                    onClick={() => setTags(toggleSet(tags, tag))}
                  >
                    #{tag}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {error && (
        <div className="gc-error" role="alert">{error}</div>
      )}

      {/* Счётчики */}
      <div className="gc-stats">
        <span className="gc-stat"><b>{filtered.length}</b> найдено</span>
        <span className="gc-stat-sep" aria-hidden="true" />
        <span className="gc-stat">
          <b>{structuredCount}</b> размечено AI
        </span>
        <span className="gc-stat-sep" aria-hidden="true" />
        <span className="gc-stat"><b>{items.length}</b> всего карточек</span>
        {query && (
          <span className="gc-stat gc-stat--hint">по запросу «{query}»</span>
        )}
        <button
          type="button"
          className="gc-refresh"
          onClick={() => loadItems({ isRefresh: true })}
          title="Обновить каталог"
        >
          <span
            className={"gc-refresh-ico" + (refreshing ? " is-spin" : "")}
            aria-hidden="true"
          >
            ↻
          </span>
          обновить
        </button>
      </div>

      {/* Сетка карточек */}
      <div className="gc-grid-wrap">
        {loading ? (
          <div className="gc-skeleton" aria-hidden="true">
            {Array.from({ length: 6 }).map((_, i) => (
              <div className="gc-sk-card" key={i}
                style={{ animationDelay: `${i * 70}ms` }}>
                <span className="gc-sk gc-sk--badge" />
                <span className="gc-sk gc-sk--title" />
                <span className="gc-sk gc-sk--line" />
                <span className="gc-sk gc-sk--line gc-sk--short" />
              </div>
            ))}
          </div>
        ) : !loading && structuredCount === 0 ? (
          <div className="gc-empty">
            <div className="gc-empty-mark" aria-hidden="true">∅</div>
            <p>Пока нет размеченных карточек.</p>
            <span>
              Запусти парсер в умном режиме на вкладке «Запуск» — после
              AI-структурирования конкурсы появятся здесь автоматически.
            </span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="gc-empty">
            <div className="gc-empty-mark" aria-hidden="true">⌕</div>
            <p>Ничего не подходит под фильтры.</p>
            <button type="button" className="gc-empty-reset" onClick={resetAll}>
              Сбросить фильтры
            </button>
          </div>
        ) : (
          <div className="gc-grid">
            {filtered.map((it, i) => {
              const sd = it.structured_data;
              const dl = parseDeadline(sd.deadline);
              const dlActive = dl && dl >= new Date();
              return (
                <article
                  className="gc-card"
                  key={it.id}
                  style={{ animationDelay: `${Math.min(i, 14) * 35}ms` }}
                >
                  <div className="gc-card-top">
                    {sd.category && (
                      <span className={`gc-badge gc-badge--${sd.category}`}>
                        {CATEGORY_LABELS[sd.category] || sd.category}
                      </span>
                    )}
                    {dlActive && (
                      <span className="gc-live" title="Приём заявок открыт">
                        ● open
                      </span>
                    )}
                  </div>

                  <h2 className="gc-card-title">
                    {(sd.title || it.title || "Без названия").slice(0, 140)}
                  </h2>

                  {sd.description && (
                    <p className="gc-card-desc">
                      {sd.description.slice(0, 180)}
                      {sd.description.length > 180 ? "…" : ""}
                    </p>
                  )}

                  <div className="gc-card-meta">
                    {sd.organizer && (
                      <span className="gc-meta" title="Организатор">
                        ⌗ {sd.organizer.slice(0, 40)}
                      </span>
                    )}
                    {sd.country && (
                      <span className="gc-meta" title="Страна/регион">
                        ⌖ {sd.country}
                      </span>
                    )}
                    {sd.location && (
                      <span className="gc-meta" title="Место">
                        ⌖ {sd.location.slice(0, 30)}
                      </span>
                    )}
                    {sd.funding_amount && (
                      <span className="gc-meta gc-meta--money" title="Финансирование">
                        ✦ {sd.funding_amount}
                        {sd.currency ? ` ${sd.currency}` : ""}
                      </span>
                    )}
                    {sd.is_free === true && (
                      <span className="gc-meta" title="Бесплатное участие">
                        ✓ free
                      </span>
                    )}
                    {sd.deadline && (
                      <span
                        className={
                          "gc-meta gc-meta--deadline" +
                          (dlActive ? " is-open" : "")
                        }
                        title="Дедлайн"
                      >
                        ⏱ {sd.deadline.slice(0, 30)}
                      </span>
                    )}
                    {sd.language && (
                      <span className="gc-meta" title="Язык">{sd.language}</span>
                    )}
                  </div>

                  {(sd.tags?.length > 0 || sd.industry?.length > 0) && (
                    <div className="gc-card-tags">
                      {(sd.industry || []).slice(0, 3).map((ind) => (
                        <span key={ind} className="gc-tag gc-tag--ind">{ind}</span>
                      ))}
                      {(sd.tags || []).slice(0, 5).map((t) => (
                        <span key={t} className="gc-tag">#{String(t)}</span>
                      ))}
                    </div>
                  )}

                  <div className="gc-card-foot">
                    <a
                      className="gc-card-link"
                      href={sd.application_url || it.url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Подробнее ↗
                    </a>
                    {sd.confidence_score != null && (
                      <span
                        className="gc-conf"
                        title={`Уверенность AI: ${Math.round(sd.confidence_score * 100)}%`}
                      >
                        {Math.round(sd.confidence_score * 100)}%
                      </span>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

const catalogCss = `
.gc-page { display: flex; flex-direction: column; gap: 1.25rem; }

/* Шапка */
.gc-head { display: flex; flex-direction: column; gap: 0.5rem; }
.gc-kicker {
  display: inline-flex; align-items: center; gap: 0.5rem;
  margin: 0; font-size: 0.72rem; letter-spacing: 0.22em;
  text-transform: uppercase; color: var(--gt-ink-dim);
}
.gc-kicker-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--gt-teal); box-shadow: 0 0 10px var(--gt-teal);
}
.gc-title {
  margin: 0; font-family: var(--gt-display); font-weight: 700;
  font-size: clamp(1.8rem, 4vw, 2.6rem); letter-spacing: -0.02em;
}
.gc-title-accent { color: var(--gt-amber); }
.gc-lead { margin: 0; max-width: 62ch; color: var(--gt-ink-dim); line-height: 1.55; font-size: 0.95rem; }

/* Тулбар поиска */
.gc-toolbar {
  display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem;
  padding: 0.85rem 1rem;
  background: var(--gt-bg-soft);
  border: 1px solid var(--gt-line); border-radius: 0.9rem;
}
.gc-search {
  flex: 1 1 260px; display: flex; align-items: center; gap: 0.55rem;
  padding: 0.5rem 0.75rem;
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--gt-line); border-radius: 0.6rem;
  transition: border-color 0.2s ease;
}
.gc-search:focus-within { border-color: var(--gt-teal); }
.gc-search-ico {
  width: 16px; height: 16px; fill: none; stroke: var(--gt-ink-dim);
  stroke-width: 2; flex-shrink: 0;
}
.gc-search input {
  flex: 1; min-width: 0; background: none; border: none; outline: none;
  color: var(--gt-ink); font: inherit; font-size: 0.92rem;
}
.gc-search input::placeholder { color: rgba(151,163,189,0.6); }
.gc-search-clear {
  background: none; border: none; color: var(--gt-ink-dim);
  font-size: 1.1rem; cursor: pointer; line-height: 1; padding: 0 0.15rem;
}
.gc-search-clear:hover { color: var(--gt-ink); }

.gc-sort {
  display: inline-flex; align-items: center; gap: 0.45rem;
  font-size: 0.78rem; color: var(--gt-ink-dim); letter-spacing: 0.05em;
}
.gc-sort select {
  background: rgba(255,255,255,0.04); color: var(--gt-ink);
  border: 1px solid var(--gt-line); border-radius: 0.5rem;
  padding: 0.42rem 0.55rem; font: inherit; font-size: 0.85rem;
  cursor: pointer; outline: none;
}
.gc-sort select:focus { border-color: var(--gt-teal); }
.gc-sort option { background: var(--gt-bg-soft); }

.gc-toggle {
  display: inline-flex; align-items: center; gap: 0.45rem;
  padding: 0.42rem 0.75rem; cursor: pointer; user-select: none;
  border: 1px solid var(--gt-line); border-radius: 999px;
  font-size: 0.82rem; color: var(--gt-ink-dim);
  transition: all 0.18s ease;
}
.gc-toggle:hover { color: var(--gt-ink); }
.gc-toggle.is-on {
  color: #1a1206; background: var(--gt-teal);
  border-color: var(--gt-teal); font-weight: 600;
}
.gc-toggle input { display: none; }

/* Панель фильтров */
.gc-filters {
  display: flex; flex-direction: column; gap: 0.9rem;
  padding: 1rem 1.1rem 1.1rem;
  background: var(--gt-bg-soft);
  border: 1px solid var(--gt-line); border-radius: 0.9rem;
}
.gc-filters-head { display: flex; align-items: baseline; gap: 0.9rem; }
.gc-filters-title {
  font-family: var(--gt-display); font-weight: 600; font-size: 0.95rem;
  letter-spacing: 0.02em;
}
.gc-filters-count {
  font-size: 0.78rem; color: var(--gt-amber); letter-spacing: 0.05em;
}
.gc-reset {
  margin-left: auto; background: none; border: none; cursor: pointer;
  color: var(--gt-ink-dim); font: inherit; font-size: 0.8rem;
  letter-spacing: 0.03em; text-decoration: underline dotted;
}
.gc-reset:hover { color: #ff8f7a; }

.gc-group { display: flex; flex-direction: column; gap: 0.45rem; }
.gc-group-label {
  font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.16em;
  color: var(--gt-ink-dim);
}
.gc-chips { display: flex; flex-wrap: wrap; gap: 0.35rem; }
.gc-chip {
  padding: 0.32rem 0.7rem; cursor: pointer; user-select: none;
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--gt-line); border-radius: 999px;
  color: var(--gt-ink-dim); font: inherit; font-size: 0.8rem;
  transition: all 0.15s ease;
}
.gc-chip:hover { color: var(--gt-ink); border-color: rgba(255,255,255,0.22); }
.gc-chip.is-on {
  background: var(--gt-amber); color: #1a1206;
  border-color: var(--gt-amber); font-weight: 600;
}
.gc-chips--tags { max-height: 130px; overflow-y: auto; padding-right: 0.25rem; }
.gc-chip--tag.is-on {
  background: var(--gt-teal); border-color: var(--gt-teal);
}

/* Статистика */
.gc-stats {
  display: flex; align-items: center; flex-wrap: wrap; gap: 0.8rem;
  font-size: 0.82rem; color: var(--gt-ink-dim);
}
.gc-stat b {
  color: var(--gt-ink); font-family: var(--gt-display); font-size: 0.95rem;
}
.gc-stat-sep {
  width: 4px; height: 4px; border-radius: 50%;
  background: rgba(151,163,189,0.4);
}
.gc-stat--hint { color: var(--gt-amber); }
.gc-refresh {
  margin-left: auto; display: inline-flex; align-items: center; gap: 0.4rem;
  background: none; border: 1px solid var(--gt-line); border-radius: 999px;
  padding: 0.35rem 0.8rem; cursor: pointer;
  color: var(--gt-ink-dim); font: inherit; font-size: 0.8rem;
  transition: all 0.18s ease;
}
.gc-refresh:hover { color: var(--gt-ink); border-color: rgba(255,255,255,0.22); }
.gc-refresh-ico { display: inline-block; }
.gc-refresh-ico.is-spin { animation: gc-spin 0.9s linear infinite; }
@keyframes gc-spin { to { transform: rotate(360deg); } }

/* Ошибка */
.gc-error {
  padding: 0.8rem 1rem; border-radius: 0.7rem;
  background: rgba(255,143,122,0.08);
  border: 1px solid rgba(255,143,122,0.35);
  color: #ff8f7a; font-size: 0.88rem;
}

/* Сетка карточек */
.gc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
  gap: 1rem;
}
.gc-card {
  display: flex; flex-direction: column; gap: 0.65rem;
  padding: 1.1rem 1.15rem;
  background: var(--gt-bg-soft);
  border: 1px solid var(--gt-line); border-radius: 1rem;
  animation: gc-in 0.4s cubic-bezier(0.22, 1, 0.36, 1) both;
  transition: transform 0.2s ease, border-color 0.2s ease;
}
@keyframes gc-in {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: translateY(0); }
}
.gc-card:hover { transform: translateY(-3px); border-color: rgba(240,168,80,0.4); }

.gc-card-top {
  display: flex; align-items: center; justify-content: space-between; gap: 0.5rem;
}
.gc-badge {
  padding: 0.22rem 0.6rem; border-radius: 999px;
  font-size: 0.68rem; font-weight: 600; letter-spacing: 0.08em;
  text-transform: uppercase;
}
.gc-badge--grant { background: rgba(240,168,80,0.15); color: var(--gt-amber); }
.gc-badge--accelerator { background: rgba(88,214,192,0.14); color: var(--gt-teal); }
.gc-badge--hackathon { background: rgba(122,162,255,0.14); color: #7aa2ff; }
.gc-badge--competition { background: rgba(199,155,240,0.14); color: #c79bf0; }
.gc-badge--fellowship { background: rgba(167,217,119,0.14); color: #a7d977; }
.gc-badge--event { background: rgba(255,143,122,0.14); color: #ff8f7a; }
.gc-badge--course { background: rgba(97,218,231,0.13); color: #61dae7; }
.gc-badge--incubator { background: rgba(240,200,120,0.13); color: #f0c878; }
.gc-badge--scholarship { background: rgba(240,168,80,0.12); color: #ffc37a; }
.gc-badge--other { background: rgba(151,163,189,0.14); color: var(--gt-ink-dim); }
.gc-live {
  font-size: 0.68rem; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--gt-teal);
}

.gc-card-title {
  margin: 0; font-family: var(--gt-display); font-weight: 600;
  font-size: 1.05rem; line-height: 1.3; letter-spacing: -0.01em;
}
.gc-card-desc {
  margin: 0; font-size: 0.85rem; line-height: 1.5;
  color: var(--gt-ink-dim);
}

.gc-card-meta {
  display: flex; flex-wrap: wrap; gap: 0.3rem 0.85rem;
  font-size: 0.78rem; color: var(--gt-ink-dim);
}
.gc-meta { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.gc-meta--money { color: var(--gt-amber); font-weight: 600; }
.gc-meta--deadline.is-open { color: var(--gt-teal); }

.gc-card-tags { display: flex; flex-wrap: wrap; gap: 0.3rem; }
.gc-tag {
  padding: 0.14rem 0.5rem; border-radius: 999px;
  background: rgba(255,255,255,0.05); border: 1px solid var(--gt-line);
  font-size: 0.7rem; color: var(--gt-ink-dim);
}
.gc-tag--ind {
  background: rgba(88,214,192,0.09);
  color: var(--gt-teal);
  border-color: rgba(88,214,192,0.25);
}

.gc-card-foot {
  display: flex; align-items: center; justify-content: space-between;
  margin-top: auto; padding-top: 0.5rem;
  border-top: 1px dashed var(--gt-line);
}
.gc-card-link {
  color: var(--gt-amber); text-decoration: none; font-size: 0.85rem;
  font-weight: 600; letter-spacing: 0.02em;
}
.gc-card-link:hover { text-decoration: underline; }
.gc-conf {
  font-size: 0.72rem; color: rgba(151,163,189,0.6);
  border: 1px solid var(--gt-line); border-radius: 999px;
  padding: 0.12rem 0.5rem;
}

/* Скелетон */
.gc-skeleton {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
  gap: 1rem;
}
.gc-sk-card {
  display: flex; flex-direction: column; gap: 0.7rem;
  height: 220px; padding: 1.1rem;
  background: var(--gt-bg-soft);
  border: 1px solid var(--gt-line); border-radius: 1rem;
  animation: gc-pulse 1.2s ease-in-out infinite;
}
.gc-sk {
  display: block; border-radius: 0.4rem;
  background: rgba(255,255,255,0.06);
}
.gc-sk--badge { width: 90px; height: 16px; }
.gc-sk--title { width: 85%; height: 20px; }
.gc-sk--line { width: 100%; height: 12px; }
.gc-sk--short { width: 55%; }
@keyframes gc-pulse {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}

/* Пустые состояния */
.gc-empty {
  display: flex; flex-direction: column; align-items: center; gap: 0.6rem;
  padding: 3.5rem 1.5rem; text-align: center;
  border: 1px dashed var(--gt-line); border-radius: 1rem;
  color: var(--gt-ink-dim);
}
.gc-empty-mark {
  font-family: var(--gt-display); font-size: 2.2rem; color: rgba(240,168,80,0.5);
}
.gc-empty p { margin: 0; color: var(--gt-ink); font-weight: 500; }
.gc-empty span { max-width: 46ch; font-size: 0.85rem; line-height: 1.5; }
.gc-empty-reset {
  margin-top: 0.4rem; cursor: pointer;
  background: none; border: 1px solid var(--gt-line); border-radius: 999px;
  padding: 0.45rem 1.1rem; color: var(--gt-amber);
  font: inherit; font-size: 0.85rem; font-weight: 600;
  transition: all 0.18s ease;
}
.gc-empty-reset:hover { border-color: var(--gt-amber); }

@media (max-width: 620px) {
  .gc-grid, .gc-skeleton { grid-template-columns: 1fr; }
  .gc-refresh { margin-left: 0; }
}
`;
