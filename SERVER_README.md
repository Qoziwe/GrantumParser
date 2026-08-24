# Инструкция по администрированию сервера (Termux + Debian proot)

Эта документация предназначена для ИИ-ассистентов и системных администраторов, работающих с проектом **Grantum Parser**, развернутым на Android-смартфоне (Samsung) через Termux.

Здесь описана архитектура, принципы демонизации процессов, проброс портов и маршрутизация через Cloudflare Tunnel, а также механизмы выживания системы при сбоях сети и отключении питания.

---

## 1. Архитектура системы

Проект развернут на связке **Termux (Android)** + **Debian (proot-distro)**. 

### Почему такая связка?
1. Playwright и Chromium требуют glibc, который отсутствует в Android (там bionic). Поэтому браузер и бекенд на Python (со всеми зависимостями) запускаются **внутри Debian**.
2. Менеджер служб (Runit), автозапуск (Termux:Boot) и туннели (cloudflared) работают **нативно в Termux**, так как они управляют Android-процессами.

### Схема портов и маршрутизации
Туннель управляется через дашборд Cloudflare Zero Trust (Public Hostnames).
- `grantumapi.andatra.space` → `localhost:5004` (Бекенд Flask/Gunicorn).
- `grantumbrowser.andatra.space` → `localhost:6080` (Виртуальный экран noVNC).
- `localhost:9222` → Порт отладки Chromium (CDP), к которому бекенд подключается напрямую. Порт наружу не проброшен.

---

## 2. Управление службами (Runit)

В Android нет `systemd`. Для демонизации используется легковесный менеджер служб `runit` (установлен через `termux-services`).

### Основные службы проекта:
1. **`grantum-chrome`**: Запускает виртуальный дисплей (`Xvfb`), Chromium, VNC-сервер (`x11vnc`) и web-клиент (`websockify`).
2. **`grantum-backend`**: Запускает Gunicorn-сервер с приложением Flask.
3. **`cloudflared`**: Держит исходящий туннель до Cloudflare (внешних открытых портов на телефоне нет).

### Команды управления (выполнять в Termux)
Проверить статус всех служб:
```bash
sv status grantum-chrome grantum-backend cloudflared
```
Перезапустить бекенд (например, после обновления кода через `git pull`):
```bash
sv restart grantum-backend
proot-distro login debian --shared-tmp -- pkill -9 gunicorn
```
*(Важно: `pkill` обязателен, так как сигнал TERM от runit не всегда доходит внутрь proot, и старый процесс может "зависнуть" на порту).*

Остановить/Запустить браузер:
```bash
sv down grantum-chrome
sv up grantum-chrome
```
*(При старте `grantum-chrome` сам убивает все старые процессы Xvfb, x11vnc, websockify и чистит Lock-файлы).*

### Просмотр логов
```bash
tail -f ~/grantum-backend.log
tail -f ~/grantum-chrome.log
```

---

## 3. Внутреннее устройство служб (Run-скрипты)

Службы находятся в `$PREFIX/var/service/`.

### 3.1. Бекенд (`$PREFIX/var/service/grantum-backend/run`)
```bash
#!/data/data/com.termux/files/usr/bin/sh
exec 2>&1
exec proot-distro login debian --shared-tmp -- /root/GrantumParser/backend/venv/bin/gunicorn \
  --workers 1 --threads 8 --worker-class gthread \
  --bind 127.0.0.1:5004 --chdir /root/GrantumParser/backend app:app \
  >> /data/data/com.termux/files/home/grantum-backend.log 2>&1
```
*Важно: `workers 1` обязателен, так как лимиты пула LLM-ключей (RPM/TPM) хранятся в памяти процесса.*

### 3.2. Браузер (`$PREFIX/var/service/grantum-chrome/run`)
```bash
#!/data/data/com.termux/files/usr/bin/sh
exec 2>&1
exec proot-distro login debian --shared-tmp -- /bin/bash /root/start-browser.sh \
  >> /data/data/com.termux/files/home/grantum-chrome.log 2>&1
```

Содержимое скрипта `/root/start-browser.sh` (внутри Debian):
```bash
#!/bin/bash
export DISPLAY=:99

# Жесткая очистка остатков прошлого запуска
pkill -f "Xvfb :99" ; pkill -f "remote-debugging-port=9222"
pkill x11vnc ; pkill websockify
rm -f /root/chrome-profile/SingletonLock
sleep 2

Xvfb :99 -screen 0 1280x800x24 &
sleep 1

chromium \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --remote-debugging-port=9222 \
  --user-data-dir=/root/chrome-profile \
  --no-first-run \
  --no-default-browser-check \
  --disable-session-crashed-bubble \
  about:blank &

sleep 4
x11vnc -display :99 -forever -rfbauth /root/vncpass -quiet -rfbport 5900 &
exec websockify --web=/usr/share/novnc/ 6080 localhost:5900
```
*Пароль VNC сгенерирован командой `x11vnc -storepasswd 'ПАРОЛЬ' /root/vncpass` внутри Debian.*

---

## 4. Автоматизация выживания (Boot & Wake Lock)

Физический смартфон агрессивно экономит энергию. Для обеспечения бесперебойной работы настроены две системы защиты:

1. **Wake Lock (Предотвращение засыпания)**
   ОС Android не уводит процессор в режим глубокого сна (Deep Sleep) благодаря утилите `termux-wake-lock`. 
   Дополнительно работает фоновый `ping` до 8.8.8.8, чтобы Wi-Fi модуль не уходил в спячку.

2. **Termux:Boot (Автозапуск при перезагрузке)**
   При включении телефона приложение Termux:Boot перехватывает системное событие `BOOT_COMPLETED` и выполняет скрипт `~/.termux/boot/start_services.sh`:
   ```bash
   #!/data/data/com.termux/files/usr/bin/sh
   termux-wake-lock
   sv up eventum-backend
   sv up cloudflared
   sv up grantum-chrome
   sv up grantum-backend
   
   nohup sh -c 'while true; do ping -c 1 8.8.8.8 >/dev/null 2>&1; sleep 10; done' &
   ```
   С этого момента сервер автономен.

---

## 5. Обновление кода и деплой

### Обновление Бекенда (на телефоне)
```bash
proot-distro login debian --shared-tmp -- git -C /root/GrantumParser pull
sv restart grantum-backend
proot-distro login debian --shared-tmp -- pkill -9 gunicorn
```

### Обновление Фронтенда (на ПК)
Фронтенд хостится на GitHub Pages. Сборка и деплой автоматизированы через GitHub Actions.
Просто сделайте `git push` в ветку `main`. Action сам подставит `VITE_API_URL=https://grantumapi.andatra.space/api`, соберет проект и обновит сайт.

---

## 6. Частые проблемы и решения

- **Ошибка CORS Preflight (OPTIONS возвращает 401)**: В `app.py` добавлено исключение `if request.method == "OPTIONS": return None`. Убедитесь, что эта логика не сломана.
- **Логин не работает (ошибка 401 после ввода пароля)**: Кросс-доменные куки с GitHub Pages на API требуют `SameSite=None` и `Secure=True`. Проверьте, что в `.env` стоит `SERVER_LOCATION=vps` и `SESSION_COOKIE_SECURE=auto` (или `true`).
- **Симптом: `down: log: 1s, normally up` в статусе службы**: Косметический баг runit из-за пустой папки логов. Игнорируйте или удалите папку `log` внутри директории сервиса.
- **Не могу зайти в VNC, пароль не подходит**: Протокол VNC обрезает пароль до 8 символов. Вводите только первые 8 символов.
- **В VNC висит плашка "Restore pages?"**: Значит при последнем рестарте chromium не был корректно убит (не сработал `pkill` в скрипте запуска). Сделайте `sv down grantum-chrome`, жестко убейте процессы внутри debian и сделайте `sv up`.
