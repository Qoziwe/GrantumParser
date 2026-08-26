# Grantum Parser — Universal Edition

A universal LLM-based parser for automatically collecting and structuring information about grants, accelerators, startup programs, and events.

The project has evolved from a narrowly specialized script into a **self-contained engine** that can analyze unfamiliar websites, write parsing instructions for them, bypass blocks with human help (human-in-the-loop), and asynchronously structure the collected data into strict JSON.

---

## 🌟 Key Features

1. **Automatic website analysis (Zero-shot scraping)**
   When given a new URL, the parser (via the `analyzer` module) downloads the DOM, extracts recurring blocks, and sends them to the LLM. The model returns a JSON instruction (selectors, pagination strategies), which is stored as a `SiteProfile`.
2. **Two operating modes: Fast and Smart**
   * **Fast:** Collects only listing cards (surface-level collection).
   * **Smart:** Visits each card's detail page. If the detail page structure is unknown, it dynamically creates a `ChildProfile` via the LLM and caches it for subsequent cards.
3. **AI Structuring (Structurer)**
   A background worker asynchronously takes the raw texts of collected cards and processes them through the LLM in batches. The output is strict JSON with standardized fields (category, stage, industry, location, deadlines, etc.).
4. **Human-in-the-loop (Telegram + noVNC)**
   If the parser encounters a captcha or login screen, the job transitions to the `WAITING_HUMAN` status. A notification with a link to the virtual screen (noVNC) is sent to Telegram. Once the human solves the captcha, the parser automatically continues.
5. **LLM Key Pool (Key Rotation)**
   The built-in API key manager automatically balances load, works around provider per-minute (RPM/TPM) and daily limits, and temporarily blocks keys on 429/401 errors.
6. **Built-in Security (Auth)**
   Access to the API and frontend is password-protected. Includes sessions (HttpOnly cookies), brute-force protection, CSRF tokens, and rate limiting.

---

## 🏗 Project Architecture

The project is split into two main parts communicating over a REST API. The browser runs as a separate isolated process that the backend connects to via CDP (Chrome DevTools Protocol).

```text
[ Frontend (React/Vite) ] <--- REST API ---> [ Backend (Flask/SQLite) ]
                                                      |
                                                (CDP :9222)
                                                      v
[ Telegram (Notifications) ] <--- [ Xvfb (Virtual display) + Chromium ]
                                                      |
[ noVNC (Remote access)] <----------------------------+
```

---

## 📂 Backend Structure

The backend is written in Python (Flask + SQLAlchemy) and uses Playwright to control the browser.

### Database (SQLite / `models.py`)
- **SiteProfile**: Parsing instruction for listings (domain, path prefix, JSON schema, active status).
- **ChildProfile**: Instruction for detail pages, linked to a parent `SiteProfile`.
- **Job**: A parsing task (URL, status, limits, mode, timestamps).
- **ParsedItem**: Parsing result. Stores raw text (`raw_text`) and structured JSON (`structured_data`), plus structuring status.
- **Log**: Job execution logs displayed in real time on the frontend.

### Core Modules
- `app.py`: Flask initialization, CORS setup, cookie configuration (SameSite, Secure), and REST API endpoint registration.
- `parser.py`: Execution core. Connects to Chromium, performs navigation, applies instructions from `SiteProfile`/`ChildProfile`, handles pagination, and detects captchas.
- `analyzer.py`: Primary analysis module. Cleans junk out of the DOM (`script`, `style`), applies heuristics to find cards, and asks the LLM to generate a JSON instruction.
- `structurer.py`: Background thread `StructuringWorker`. Takes `ParsedItem`s with pending status, sends them to the LLM in batches for standardization, and saves the results.
- `llm_client.py` & `llm_key_pool.py`: Client for OpenAI-compatible APIs with key pool rotation, token tracking, and rate limit handling.
- `auth.py`: Security module. Brute-force protection, session management (HttpOnly cookies), and CSRF tokens.
- `notifier.py`: Telegram integration. Aggregates block notifications and sends the virtual screen link.

---

## 💻 Frontend Structure

The frontend is a Single Page Application (SPA) built with React and Vite.
- **`src/api.js`**: Unified Axios client. Automatically intercepts 401 errors and injects CSRF tokens. Takes the base URL from the `VITE_API_URL` variable.
- **`src/pages/Dashboard.jsx`**: Launching the parser, configuring limits, choosing the mode (Fast/Smart).
- **`src/pages/Catalog.jsx`**: Powerful client-side filtering and search across all collected and structured cards (search by industries, locations, deadlines).
- **`src/pages/Profiles.jsx`**: Managing AI-created site profiles (viewing, deleting, forced rescan).
- **`src/pages/LogsPage.jsx`**: Real-time parser log viewer (polling). Shows a banner when a job is waiting for human input.

---

## 🚀 Server Deployment (Ubuntu / Debian)

This guide covers installation on a standard Linux server (VPS). To implement the "virtual screen" (so captchas can be solved right in the server's browser via a link), we use the Xvfb + x11vnc + noVNC stack.

### 1. System preparation and dependency installation
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git curl nginx
sudo apt install -y chromium-browser xvfb x11vnc novnc websockify
```
*(Note: on Ubuntu, `chromium-browser` may be installed via snap. For servers, plain Debian is preferable, where Chromium installs through apt).*

### 2. Cloning and setting up the Backend
```bash
git clone https://github.com/YOUR_USER/GrantumParser.git /opt/GrantumParser
cd /opt/GrantumParser/backend

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt gunicorn
```

Create the `.env` configuration file:
```bash
cp .env.example .env
nano .env
```
Required parameters:
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

### 3. Setting up the virtual screen (Browser + noVNC)
Set a VNC password so no one else can control the browser:
```bash
x11vnc -storepasswd YOUR_VNC_PASSWORD /etc/x11vnc.pass
```

Create the browser launch script `/opt/start-browser.sh`:
```bash
#!/bin/bash
export DISPLAY=:99
# Clean up old processes
pkill -f "Xvfb :99" ; pkill -f "remote-debugging-port=9222"
pkill x11vnc ; pkill websockify
sleep 2

# Virtual display
Xvfb :99 -screen 0 1280x800x24 &
sleep 1

# Launch Chromium with an open debug port (CDP)
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

# Password-protected VNC server
x11vnc -display :99 -forever -rfbauth /etc/x11vnc.pass -quiet -rfbport 5900 &
# noVNC web client
exec websockify --web=/usr/share/novnc/ 6080 localhost:5900
```
Make the script executable: `chmod +x /opt/start-browser.sh`

### 4. Creating Systemd services

Create a service for the browser `/etc/systemd/system/grantum-browser.service`:
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

Create a service for the backend `/etc/systemd/system/grantum-backend.service`:
```ini
[Unit]
Description=Grantum Parser Backend (Gunicorn)
After=network.target

[Service]
User=root
WorkingDirectory=/opt/GrantumParser/backend
Environment="PATH=/opt/GrantumParser/backend/venv/bin"
# Important: exactly 1 worker is required for the in-memory key pool to work correctly!
ExecStart=/opt/GrantumParser/backend/venv/bin/gunicorn --workers 1 --threads 8 --worker-class gthread --bind 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Start the services:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now grantum-browser grantum-backend
```

### 5. Nginx configuration (Reverse proxy)
Configure Nginx to proxy requests to the backend and noVNC.
- API: `api.yourdomain.com` -> `http://127.0.0.1:5000`
- Browser: `browser.yourdomain.com` -> `http://127.0.0.1:6080` (with WebSocket support).

*Alternatively: you can use Cloudflare Tunnel (cloudflared) by adding Public Hostnames for ports `5000` and `6080`.*

### 6. Building and deploying the Frontend
Go to the `frontend` folder on your local machine or server.
Set your API URL:
```bash
VITE_API_URL=https://api.yourdomain.com/api npm run build
```
The contents of the `dist` folder can be hosted on GitHub Pages, Vercel, Netlify, or served through the same Nginx.

---

## 🛠 Development and Local Setup

1. Start Chrome with an open debug port (via terminal or the `backend/start_chrome.command` script).
2. Start the backend: `cd backend && python app.py` (requires a `.env` file).
3. Start the frontend: `cd frontend && npm run dev`.
