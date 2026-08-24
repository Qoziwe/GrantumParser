import { useState } from "react";
import { login } from "../api";

/**
 * Login — полноэкранный шлюз доступа к консоли.
 *
 * Показывается, когда бэкенд отвечает 401 на любой API-запрос
 * (событие gt:unauthorized из api.js) или при первичной загрузке.
 * После успешного входа вызывает onSuccess -> приложение загружается.
 *
 * Стили локальные, префикс gl2- (gl- занят LogsPage).
 */
export default function Login({ onSuccess }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [shake, setShake] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError("");
    try {
      await login(password);
      setPassword("");
      onSuccess?.();
    } catch (err) {
      setError(err.message || "Не удалось войти.");
      setShake(true);
      setTimeout(() => setShake(false), 500);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gl2-gate">
      <style>{loginCss}</style>

      <form
        className={"gl2-card" + (shake ? " is-shake" : "")}
        onSubmit={handleSubmit}
        autoComplete="on"
      >
        <div className="gl2-mark" aria-hidden="true">G</div>

        <h1 className="gl2-title">Grantum Console</h1>
        <p className="gl2-sub">Доступ только по паролю</p>

        <label className="gl2-field">
          <span className="gl2-label">Пароль</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••••"
            autoFocus
            autoComplete="current-password"
            disabled={busy}
            maxLength={1024}
          />
        </label>

        {error && (
          <div className="gl2-error" role="alert">{error}</div>
        )}

        <button type="submit" className="gl2-btn" disabled={busy || !password}>
          {busy ? "проверяю…" : "войти →"}
        </button>
      </form>

      <p className="gl2-foot">
        grantum · parser console · protected area
      </p>
    </div>
  );
}

const loginCss = `
.gl2-gate {
  position: fixed; inset: 0; z-index: 50;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 1.5rem;
  background:
    radial-gradient(120% 80% at 50% -10%, rgba(240,168,80,0.10), transparent 60%),
    var(--gt-bg);
}
.gl2-card {
  display: flex; flex-direction: column; gap: 1rem;
  width: min(92vw, 380px);
  padding: 2rem 1.75rem 1.75rem;
  background: var(--gt-bg-soft);
  border: 1px solid var(--gt-line); border-radius: 1.25rem;
  box-shadow: 0 30px 80px rgba(0,0,0,0.45);
}
.gl2-card.is-shake { animation: gl2-shake 0.45s ease; }
@keyframes gl2-shake {
  20% { transform: translateX(-9px); }
  40% { transform: translateX(8px); }
  60% { transform: translateX(-5px); }
  80% { transform: translateX(4px); }
}

.gl2-mark {
  align-self: center;
  display: grid; place-items: center;
  width: 3.2rem; height: 3.2rem; border-radius: 0.9rem;
  font-family: var(--gt-display); font-weight: 700; font-size: 1.6rem;
  color: #1a1206;
  background: linear-gradient(150deg, var(--gt-amber), #ffd79a);
  box-shadow: 0 8px 26px rgba(240,168,80,0.3);
}
.gl2-title {
  margin: 0.4rem 0 0; text-align: center;
  font-family: var(--gt-display); font-weight: 600;
  font-size: 1.35rem; letter-spacing: -0.01em; color: var(--gt-ink);
}
.gl2-sub {
  margin: 0; text-align: center;
  font-size: 0.78rem; letter-spacing: 0.18em; text-transform: uppercase;
  color: var(--gt-ink-dim);
}

.gl2-field { display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.5rem; }
.gl2-label {
  font-size: 0.68rem; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--gt-ink-dim);
}
.gl2-field input {
  padding: 0.7rem 0.85rem;
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--gt-line); border-radius: 0.6rem;
  color: var(--gt-ink); font: inherit; font-size: 1rem;
  outline: none; transition: border-color 0.2s ease;
}
.gl2-field input:focus { border-color: var(--gt-teal); }
.gl2-field input::placeholder { color: rgba(151,163,189,0.45); }

.gl2-error {
  padding: 0.55rem 0.8rem; border-radius: 0.55rem;
  background: rgba(255,143,122,0.08);
  border: 1px solid rgba(255,143,122,0.35);
  color: #ff8f7a; font-size: 0.84rem;
}

.gl2-btn {
  margin-top: 0.4rem; padding: 0.7rem 1rem;
  border: none; border-radius: 0.6rem; cursor: pointer;
  background: linear-gradient(150deg, var(--gt-amber), #ffd79a);
  color: #1a1206; font: inherit; font-weight: 700; font-size: 0.95rem;
  letter-spacing: 0.03em;
  transition: transform 0.15s ease, filter 0.15s ease;
}
.gl2-btn:hover:not(:disabled) { transform: translateY(-1px); filter: brightness(1.06); }
.gl2-btn:disabled { opacity: 0.45; cursor: not-allowed; }

.gl2-foot {
  margin: 0; font-size: 0.7rem; letter-spacing: 0.22em;
  text-transform: uppercase; color: rgba(151,163,189,0.45);
}
`;
