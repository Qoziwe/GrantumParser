import axios from "axios";

/**
 * Единая точка входа во все запросы к бэкенду.
 * baseURL совпадает с тем, на чём крутится Flask (app.py -> port 5000).
 *
 * Аутентификация: сессионные куки (HttpOnly). Каждый мутирующий запрос
 * обязан нести заголовок X-CSRF-Token со значением из куки gt_csrf
 * (двойной submit) — интерцептор ниже делает это автоматически.
 */
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "http://localhost:5000/api",
  timeout: 15000,
  withCredentials: true, // отправлять/принимать куки сессии
  headers: {
    "Content-Type": "application/json",
  },
});

/** Читает значение куки по имени. */
function readCookie(name) {
  const match = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${name}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : null;
}

// Подставляем CSRF-токен во все небезопасные запросы.
api.interceptors.request.use((config) => {
  if (!["get", "head", "options"].includes(config.method)) {
    const token = readCookie("gt_csrf");
    if (token) config.headers["X-CSRF-Token"] = token;
  }
  return config;
});

/**
 * Нормализуем любую ошибку axios в читаемый текст.
 * 401 — сессии нет/истекла: показываем экран логина (глобальное событие).
 */
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    const message =
      error?.response?.data?.error ||
      error?.response?.data?.message ||
      error?.message ||
      "Неизвестная ошибка запроса";

    error.message = message;

    if (status === 401 && !`${error.config?.url || ""}`.includes("/auth/")) {
      window.dispatchEvent(new CustomEvent("gt:unauthorized"));
    }

    return Promise.reject(error);
  },
);

/** POST /auth/login — вход по паролю. Куки ставит сервер. */
export async function login(password) {
  const { data } = await api.post("/auth/login", { password });
  return data;
}

/** POST /auth/logout — выход, сервер удаляет сессию. */
export async function logout() {
  const { data } = await api.post("/auth/logout");
  return data;
}

/** POST /auth/password — смена пароля (инвалидирует все сессии). */
export async function changePassword(oldPassword, newPassword) {
  const { data } = await api.post("/auth/password", {
    old_password: oldPassword,
    new_password: newPassword,
  });
  return data;
}

/** POST /parse — создать задачу и запустить парсер. Возвращает объект Job. */
export async function startParse(url, options = {}) {
  const { data } = await api.post("/parse", {
    url,
    iterations: options.iterations ?? 1,
    mode: options.mode ?? "fast",
    maxChildProfiles: options.maxChildProfiles ?? 20,
    maxDetailPages: options.maxDetailPages ?? 100,
  });
  return data;
}

/** GET /jobs — список всех запусков, новые сверху. */
export async function fetchJobs() {
  const { data } = await api.get("/jobs");
  return data;
}

/** GET /jobs/:id/logs — логи одной задачи, по порядку появления. */
export async function fetchJobLogs(jobId) {
  const { data } = await api.get(`/jobs/${jobId}/logs`);
  return data;
}

/** GET /items — спаршенные карточки. jobId необязателен (фильтр по задаче). */
export async function fetchItems(jobId) {
  const params = jobId ? { job_id: jobId } : {};
  const { data } = await api.get("/items", { params });
  return data;
}

// frontend/src/api.js
export const deleteJob = (id) => api.delete(`/jobs/${id}`).then((r) => r.data);

export const deleteAllJobs = () => api.delete("/jobs").then((r) => r.data);

// ============================================================
// Profiles API
// ============================================================

/** GET /profiles — список всех профилей сайтов. */
export async function fetchProfiles() {
  const { data } = await api.get("/profiles");
  return data;
}

/** POST /profiles/:id/rescan — принудительное пересканирование профиля. */
export async function rescanProfile(profileId) {
  const { data } = await api.post(`/profiles/${profileId}/rescan`);
  return data;
}

/** DELETE /profiles/:id — удаление профиля. */
export async function deleteProfile(profileId) {
  const { data } = await api.delete(`/profiles/${profileId}`);
  return data;
}

export default api;
