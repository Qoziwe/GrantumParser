import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchProfiles, rescanProfile, deleteProfile } from "../api";

/**
 * Profiles — управление профилями сайтов.
 *
 * Таблица всех проанализированных сайтов с возможностью:
 * - просмотреть детали профиля;
 * - запустить принудительное пересканирование;
 * - удалить профиль (следующий запуск по этому домену создаст новый).
 *
 * Стили локальные, префикс gx- (grantum profiles), цвета из --gt-* каркаса.
 */

function formatDate(iso) {
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

function statusBadge(profile) {
  if (!profile.is_active) {
    if (profile.retry_not_before) {
      const now = new Date();
      const retryAt = new Date(profile.retry_not_before);
      if (retryAt > now) {
        return {
          label: "На кулдауне",
          className: "gx-badge gxbadge--cooldown",
        };
      }
    }
    return { label: "Сломан", className: "gx-badge gx-badge--broken" };
  }
  return { label: "Активен", className: "gx-badge gx-badge--active" };
}

export default function Profiles() {
  const navigate = useNavigate();
  const [profiles, setProfiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [rescanning, setRescanning] = useState(new Set());

  const loadProfiles = useCallback(async () => {
    try {
      setLoading(true);
      setError("");
      const data = await fetchProfiles();
      setProfiles(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || "Не удалось загрузить профили.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProfiles();
  }, [loadProfiles]);

  async function handleRescan(profileId) {
    if (rescanning.has(profileId)) return;

    setRescanning((prev) => new Set(prev).add(profileId));

    try {
      const result = await rescanProfile(profileId);
      // Переходим к логам задачи пересканирования
      if (result.job_id) {
        navigate(`/logs/${result.job_id}`);
      } else {
        loadProfiles();
      }
    } catch (err) {
      alert(err.message || "Не удалось запустить пересканирование.");
    } finally {
      setRescanning((prev) => {
        const next = new Set(prev);
        next.delete(profileId);
        return next;
      });
    }
  }

  async function handleDelete(profileId, domain, pathPrefix) {
    const confirmed = window.confirm(
      `Удалить профиль ${domain}${pathPrefix}?\n\n` +
        `Следующий запуск парсера по этому сайту снова запустит анализатор.`,
    );

    if (!confirmed) return;

    try {
      await deleteProfile(profileId);
      loadProfiles();
    } catch (err) {
      alert(err.message || "Не удалось удалить профиль.");
    }
  }

  return (
    <div className="gx-page">
      <style>{profilesCss}</style>

      <header className="gx-head">
        <p className="gx-kicker">
          <span className="gx-kicker-dot" aria-hidden="true" />
          profiles · управление сайтами
        </p>
        <h1 className="gx-title">
          Профили
          <span className="gx-title-accent">сайтов</span>
        </h1>
        <p className="gx-lead">
          Каждый профиль — это JSON-инструкция, как парсить конкретный сайт.
          Профили создаются автоматически при первом запуске и переиспользуются
          при повторных запусках. Если сайт изменил вёрстку, система сама
          пересканирует профиль.
        </p>
      </header>

      <section className="gx-toolbar">
        <button
          type="button"
          className="gx-refresh"
          onClick={loadProfiles}
          disabled={loading}
          title="Обновить список профилей"
        >
          ↻ обновить
        </button>
      </section>

      {error && (
        <div className="gx-error" role="alert">
          <span aria-hidden="true">!</span>
          {error}
        </div>
      )}

      {loading && profiles.length === 0 ? (
        <div className="gx-empty">
          <div className="gx-empty-mark" aria-hidden="true">
            ⟳
          </div>
          <p>Загрузка профилей…</p>
        </div>
      ) : profiles.length === 0 ? (
        <div className="gx-empty">
          <div className="gx-empty-mark" aria-hidden="true">
            ∅
          </div>
          <p>Профилей пока нет.</p>
          <span>
            Запусти парсер на вкладке «Запуск» — профили создадутся
            автоматически.
          </span>
        </div>
      ) : (
        <div className="gx-table-wrap">
          <table className="gx-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Домен</th>
                <th>Путь</th>
                <th>Версия</th>
                <th>Статус</th>
                <th>Ошибок</th>
                <th>Обновлено</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((profile) => {
                const badge = statusBadge(profile);
                const isRescanning = rescanning.has(profile.id);

                return (
                  <tr key={profile.id}>
                    <td className="gx-cell gx-cell-id">#{profile.id}</td>
                    <td className="gx-cell gx-cell-domain">{profile.domain}</td>
                    <td className="gx-cell gx-cell-path">
                      {profile.path_prefix}
                    </td>
                    <td className="gx-cell gx-cell-version">
                      v{profile.version}
                    </td>
                    <td className="gx-cell">
                      <span className={badge.className}>{badge.label}</span>
                    </td>
                    <td className="gx-cell gx-cell-fails">
                      {profile.fail_count || 0}
                    </td>
                    <td className="gx-cell gx-cell-date">
                      {formatDate(profile.updated_at)}
                    </td>
                    <td className="gx-cell gx-cell-actions">
                      <button
                        type="button"
                        className="gx-btn gx-btn-rescan"
                        onClick={() => handleRescan(profile.id)}
                        disabled={isRescanning}
                        title="Запустить принудительное пересканирование"
                      >
                        {isRescanning ? "⟳…" : "Пересканировать"}
                      </button>
                      <button
                        type="button"
                        className="gx-btn gx-btn-delete"
                        onClick={() =>
                          handleDelete(
                            profile.id,
                            profile.domain,
                            profile.path_prefix,
                          )
                        }
                        title="Удалить профиль"
                      >
                        Удалить
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const profilesCss = `
.gx-page {
  display: flex;
  flex-direction: column;
  gap: clamp(1.4rem, 3vw, 2.1rem);
  animation: gx-in 0.5s cubic-bezier(0.22, 1, 0.36, 1) both;
}
@keyframes gx-in {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: none; }
}
.gx-kicker {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
  margin: 0 0 0.85rem;
  font-size: 0.72rem;
  letter-spacing: 0.26em;
  text-transform: uppercase;
  color: var(--gt-ink-dim);
}
.gx-kicker-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--gt-teal);
  box-shadow: 0 0 12px var(--gt-teal);
}
.gx-title {
  margin: 0;
  font-family: var(--gt-display);
  font-weight: 700;
  font-size: clamp(2rem, 5.5vw, 3.3rem);
  line-height: 1.03;
  letter-spacing: -0.02em;
  color: var(--gt-ink);
}
.gx-title-accent {
  display: inline-block;
  margin-left: 0.35ch;
  color: var(--gt-amber);
}
.gx-lead {
  margin: 0.9rem 0 0;
  max-width: 60ch;
  color: var(--gt-ink-dim);
  font-size: clamp(0.96rem, 1.3vw, 1.08rem);
  line-height: 1.6;
}
.gx-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.8rem;
}
.gx-refresh {
  background: none;
  border: 1px solid var(--gt-line);
  border-radius: 0.6rem;
  color: var(--gt-ink-dim);
  padding: 0.4rem 0.8rem;
  cursor: pointer;
  font-size: 0.82rem;
  transition: color 0.2s ease, border-color 0.2s ease;
}
.gx-refresh:hover:not(:disabled) {
  color: var(--gt-ink);
  border-color: rgba(255, 255, 255, 0.22);
}
.gx-refresh:disabled {
  cursor: progress;
  opacity: 0.6;
}
.gx-error {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.7rem 0.9rem;
  border-radius: 0.6rem;
  background: rgba(255, 107, 107, 0.1);
  border: 1px solid rgba(255, 107, 107, 0.35);
  color: #ffb4b4;
  font-size: 0.92rem;
}
.gx-error span {
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
.gx-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 0.5rem;
  padding: 3rem 1.5rem;
  border: 1px dashed var(--gt-line);
  border-radius: 1rem;
  color: var(--gt-ink-dim);
}
.gx-empty-mark {
  font-family: var(--gt-display);
  font-size: 2.4rem;
  color: rgba(151, 163, 189, 0.4);
  margin-bottom: 0.3rem;
}
.gx-empty p {
  margin: 0;
  color: var(--gt-ink);
  font-weight: 600;
  font-size: 1.05rem;
}
.gx-empty span {
  font-size: 0.9rem;
  max-width: 42ch;
}
.gx-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--gt-line);
  border-radius: 0.9rem;
  background: rgba(255, 255, 255, 0.02);
}
.gx-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}
.gx-table thead {
  background: rgba(255, 255, 255, 0.04);
  border-bottom: 1px solid var(--gt-line);
}
.gx-table th {
  text-align: left;
  padding: 0.75rem 1rem;
  font-family: var(--gt-display);
  font-weight: 600;
  font-size: 0.78rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--gt-ink-dim);
  white-space: nowrap;
}
.gx-table tbody tr {
  border-bottom: 1px solid var(--gt-line);
  transition: background 0.2s ease;
}
.gx-table tbody tr:last-child {
  border-bottom: none;
}
.gx-table tbody tr:hover {
  background: rgba(240, 168, 80, 0.05);
}
.gx-cell {
  padding: 0.85rem 1rem;
  color: var(--gt-ink);
  vertical-align: middle;
}
.gx-cell-id {
  font-family: var(--gt-display);
  font-weight: 600;
  color: var(--gt-ink-dim);
  font-size: 0.85rem;
}
.gx-cell-domain {
  font-weight: 500;
}
.gx-cell-path {
  color: var(--gt-ink-dim);
  font-size: 0.85rem;
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
}
.gx-cell-version {
  font-family: var(--gt-display);
  font-weight: 600;
  color: var(--gt-teal);
}
.gx-cell-fails {
  font-family: var(--gt-display);
  font-weight: 600;
  color: var(--gt-ink-dim);
}
.gx-cell-date {
  color: var(--gt-ink-dim);
  font-size: 0.82rem;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.gx-cell-actions {
  display: flex;
  gap: 0.5rem;
  white-space: nowrap;
}
.gx-badge {
  display: inline-block;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  border: 1px solid transparent;
}
.gx-badge--active {
  color: var(--gt-teal);
  border-color: rgba(88, 214, 192, 0.4);
  background: rgba(88, 214, 192, 0.1);
}
.gx-badge--broken {
  color: #ff9a9a;
  border-color: rgba(255, 107, 107, 0.4);
  background: rgba(255, 107, 107, 0.1);
}
.gx-badge--cooldown {
  color: #e0c060;
  border-color: rgba(224, 192, 96, 0.4);
  background: rgba(224, 192, 96, 0.1);
}
.gx-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.85rem;
  border-radius: 0.6rem;
  cursor: pointer;
  font-size: 0.82rem;
  font-weight: 600;
  transition: transform 0.15s ease, background 0.2s ease, border-color 0.2s ease;
}
.gx-btn:disabled {
  cursor: progress;
  opacity: 0.6;
}
.gx-btn-rescan {
  border: 1px solid rgba(240, 168, 80, 0.4);
  background: rgba(240, 168, 80, 0.1);
  color: var(--gt-amber);
}
.gx-btn-rescan:hover:not(:disabled) {
  background: rgba(240, 168, 80, 0.18);
  border-color: rgba(240, 168, 80, 0.55);
  transform: translateY(-1px);
}
.gx-btn-delete {
  border: 1px solid var(--gt-line);
  background: transparent;
  color: var(--gt-ink-dim);
}
.gx-btn-delete:hover:not(:disabled) {
  color: #ff9a9a;
  border-color: rgba(255, 107, 107, 0.4);
  background: rgba(255, 107, 107, 0.08);
  transform: translateY(-1px);
}
@media (max-width: 768px) {
  .gx-table {
    font-size: 0.82rem;
  }
  .gx-table th,
  .gx-cell {
    padding: 0.6rem 0.7rem;
  }
  .gx-cell-actions {
    flex-direction: column;
    gap: 0.4rem;
  }
}
@media (prefers-reduced-motion: reduce) {
  .gx-page {
    animation: none;
  }
  .gx-btn {
    transition: none;
  }
}
`;
