import axios from "axios";

/**
 * Единая точка входа во все запросы к бэкенду.
 * baseURL совпадает с тем, на чём крутится Flask (app.py -> port 5000).
 */
export const api = axios.create({
  baseURL: "http://localhost:5000/api",
  timeout: 15000,
  headers: {
    "Content-Type": "application/json",
  },
});

/**
 * Нормализуем любую ошибку axios в читаемый текст,
 * чтобы страницы могли просто показать err.message.
 */
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const message =
      error?.response?.data?.error ||
      error?.response?.data?.message ||
      error?.message ||
      "Неизвестная ошибка запроса";

    error.message = message;
    return Promise.reject(error);
  },
);

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
