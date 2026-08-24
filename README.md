# Grantum Parser — Universal Edition

Универсальный парсер на базе LLM для автоматического сбора и структурирования информации о грантах, акселераторах, стартап-программах и мероприятиях. 

Проект эволюционировал из узкоспециализированного скрипта в **автономный движок**, который умеет анализировать незнакомые сайты, писать для них инструкции по парсингу, обходить блокировки с помощью человека (human-in-the-loop) и асинхронно структурировать собранные данные в строгий JSON.

---

## 🌟 Ключевые возможности

1. **Автоматический анализ сайтов (Zero-shot scraping)**
   При подаче нового URL парсер (через модуль `analyzer`) скачивает DOM, выделяет повторяющиеся блоки и отправляет их в LLM. Модель возвращает JSON-инструкцию (селекторы, стратегии пагинации), которая сохраняется как `SiteProfile`.
2. **Два режима работы: Fast и Smart**
   * **Fast:** Собирает только карточки с листинга (поверхностный сбор).
   * **Smart:** Переходит внутрь каждой карточки (detail page). Если структура detail-страницы неизвестна, динамически создает `ChildProfile` через LLM и кэширует его для следующих карточек.
3. **AI-Структурирование (Structurer)**
   Фоновый воркер асинхронно берет "сырые" тексты собранных карточек и прогоняет их через LLM батчами. На выходе получается строгий JSON со стандартизированными полями (категория, стадия, индустрия, локация, дедлайны и т.д.).
4. **Human-in-the-loop (Telegram + noVNC)**
   Если парсер сталкивается с капчей или окном авторизации, задача переходит в статус `WAITING_HUMAN`. В Telegram отправляется уведомление со ссылкой на виртуальный экран (noVNC). Человек решает капчу, и парсер автоматически продолжает работу.
5. **Пул LLM-ключей (Key Rotation)**
   Встроенный менеджер API-ключей автоматически балансирует нагрузку, обходит минутные (RPM/TPM) и дневные лимиты провайдера, а также временно блокирует ключи при ошибках 429/401.
6. **Встроенная безопасность (Auth)**
   Доступ к API и фронтенду защищен паролем. Реализованы сессии (HttpOnly куки), защита от брутфорса, CSRF-токены и rate-лимиты.

---

## 🏗 Архитектура проекта

Проект разделен на две основные части, общающиеся по REST API. Браузер вынесен в отдельный изолированный процесс, к которому бекенд подключается по протоколу CDP (Chrome DevTools Protocol).

```text
[ Frontend (React/Vite) ] <--- REST API ---> [ Backend (Flask/SQLite) ]
                                                      |
                                                (CDP :9222)
                                                      v
[ Telegram (Уведомления) ] <--- [ Xvfb (Виртуальный дисплей) + Chromium ]
                                                      |
[ noVNC (Удаленный доступ)] <-------------------------+
```

---

## 📂 Структура Backend

Бекенд написан на Python (Flask + SQLAlchemy) и использует Playwright для управления браузером.

### База данных (SQLite / `models.py`)
- **SiteProfile**: Инструкция для парсинга листинга (домен, префикс пути, JSON-схема, статус активности).
- **ChildProfile**: Инструкция для detail-страниц, привязанная к родительскому `SiteProfile`.
- **Job**: Задача парсинга (URL, статус, лимиты, режим, время).
- **ParsedItem**: Результат парсинга. Хранит сырой текст (`raw_text`) и структурированный JSON (`structured_data`), а также статус структурирования.
- **Log**: Логи выполнения задачи для отображения в реальном времени на фронтенде.

### Основные модули
- `app.py`: Инициализация Flask, настройка CORS, конфигурация куки (SameSite, Secure) и регистрация REST API эндпоинтов.
- `parser.py`: Ядро исполнения. Подключается к Chromium, выполняет навигацию, применяет инструкции из `SiteProfile`/`ChildProfile`, обрабатывает пагинацию и отлавливает капчи.
- `analyzer.py`: Модуль первичного анализа. Очищает DOM от мусора (`script`, `style`), применяет эвристику для поиска карточек и просит LLM сгенерировать JSON-инструкцию.
- `structurer.py`: Фоновый поток `StructuringWorker`. Берет `ParsedItem` со статусом `pending`, батчами отправляет их в LLM для приведения к единому стандарту и сохраняет результат.
- `llm_client.py` & `llm_key_pool.py`: Клиент для OpenAI-совместимых API с поддержкой ротации пула ключей, отслеживанием токенов и обработкой Rate Limits.
- `auth.py`: Модуль безопасности. Защита от брутфорса, управление сессиями (HttpOnly Cookies) и CSRF-токенами.
- `notifier.py`: Интеграция с Telegram. Агрегирует уведомления о блокировках и отправляет ссылку на виртуальный экран.

---

## 💻 Структура Frontend

Фронтенд — это Single Page Application (SPA) на React и Vite.
- **`src/api.js`**: Единый Axios-клиент. Автоматически перехватывает 401 ошибки и подставляет CSRF-токены. Берет базовый URL из переменной `VITE_API_URL`.
- **`src/pages/Dashboard.jsx`**: Запуск парсера, настройка лимитов, выбор режима (Fast/Smart).
- **`src/pages/Catalog.jsx`**: Мощный клиентский фильтр и поиск по всем собранным и структурированным карточкам (поиск по индустриям, локациям, дедлайнам).
- **`src/pages/Profiles.jsx`**: Управление созданными AI профилями сайтов (просмотр, удаление, принудительный рескан).
- **`src/pages/LogsPage.jsx`**: Просмотр логов парсера в реальном времени (polling). Если задача ждет человека — выводит баннер.

---

## 🚀 Развертывание на сервере (Ubuntu / Debian)

Данная инструкция описывает установку на стандартный Linux-сервер (VPS). Для реализации "виртуального экрана" (чтобы решать капчи прямо в браузере сервера по ссылке) мы используем связку Xvfb + x11vnc + noVNC.

### 1. Подготовка системы и установка зависимостей
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git curl nginx
sudo apt install -y chromium-browser xvfb x11vnc novnc websockify
```
*(Примечание: на Ubuntu `chromium-browser` может ставиться через snap. Для серверов предпочтительнее использовать чистый Debian, где Chromium ставится через apt).*

### 2. Клонирование и настройка Backend
```bash
git clone https://github.com/YOUR_USER/GrantumParser.git /opt/GrantumParser
cd /opt/GrantumParser/backend

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt gunicorn
```

Создайте конфигурационный файл `.env`:
```bash
cp .env.example .env
nano .env
```
Обязательные параметры:
```env
SERVER_LOCATION=vps
CDP_URL=http://127.0.0.1:9222
PUBLIC_BROWSER_URL=https://browser.yourdomain.com/vnc.html
LLM_API_KEYS=sk-key1,sk-key2
AUTH_PASSWORD=your_secure_password
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
CORS_ORIGINS=https://your-frontend-domain.com
```

### 3. Настройка виртуального экрана (Browser + noVNC)
Задайте пароль для VNC, чтобы никто посторонний не мог управлять браузером:
```bash
x11vnc -storepasswd YOUR_VNC_PASSWORD /etc/x11vnc.pass
```

Создайте скрипт запуска браузера `/opt/start-browser.sh`:
```bash
#!/bin/bash
export DISPLAY=:99
# Очистка старых процессов
pkill -f "Xvfb :99" ; pkill -f "remote-debugging-port=9222"
pkill x11vnc ; pkill websockify
sleep 2

# Виртуальный экран
Xvfb :99 -screen 0 1280x800x24 &
sleep 1

# Запуск Chromium с открытым портом отладки (CDP)
chromium \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-profile \
  --no-first-run \
  --no-default-browser-check \
  --disable-session-crashed-bubble \
  about:blank &

sleep 4

# VNC сервер с паролем
x11vnc -display :99 -forever -rfbauth /etc/x11vnc.pass -quiet -rfbport 5900 &
# Web-клиент noVNC
exec websockify --web=/usr/share/novnc/ 6080 localhost:5900
```
Сделайте скрипт исполняемым: `chmod +x /opt/start-browser.sh`

### 4. Создание Systemd сервисов

Создайте сервис для браузера `/etc/systemd/system/grantum-browser.service`:
```ini
[Unit]
Description=Grantum Virtual Browser and noVNC
After=network.target

[Service]
User=root
ExecStart=/opt/start-browser.sh
Restart=always

[Install]
WantedBy=multi-user.target
```

Создайте сервис для бекенда `/etc/systemd/system/grantum-backend.service`:
```ini
[Unit]
Description=Grantum Parser Backend (Gunicorn)
After=network.target

[Service]
User=root
WorkingDirectory=/opt/GrantumParser/backend
Environment="PATH=/opt/GrantumParser/backend/venv/bin"
# Важно: 1 worker обязателен для корректной работы пула ключей в памяти!
ExecStart=/opt/GrantumParser/backend/venv/bin/gunicorn --workers 1 --threads 8 --worker-class gthread --bind 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Запустите сервисы:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now grantum-browser grantum-backend
```

### 5. Настройка Nginx (Реверс-прокси)
Настройте Nginx для проксирования запросов к бекенду и noVNC. 
- API: `api.yourdomain.com` -> `http://127.0.0.1:5000`
- Browser: `browser.yourdomain.com` -> `http://127.0.0.1:6080` (с поддержкой WebSockets).

*Альтернативно: можно использовать Cloudflare Tunnel (cloudflared), добавив Public Hostnames для портов `5000` и `6080`.*

### 6. Сборка и деплой Frontend
Перейдите в папку `frontend` на локальной машине или сервере.
Задайте URL вашего API:
```bash
VITE_API_URL=https://api.yourdomain.com/api npm run build
```
Содержимое папки `dist` можно разместить на GitHub Pages, Vercel, Netlify или отдать через тот же Nginx.

---

## 🛠 Разработка и локальный запуск

1. Запустите Chrome с открытым портом отладки (в терминале или через скрипт `backend/start_chrome.command`).
2. Запустите бекенд: `cd backend && python app.py` (потребуется `.env` файл).
3. Запустите фронтенд: `cd frontend && npm run dev`.
