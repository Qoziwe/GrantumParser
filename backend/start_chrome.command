#!/usr/bin/env bash
# Поднимает отдельный экземпляр Chrome/Chromium с отладочным портом 9222.
# К этому порту потом подключается парсер через CDP.
# Профиль отдельный, чтобы не конфликтовать с уже открытым браузером.

PROFILE="$HOME/f6s-chrome-profile"
mkdir -p "$PROFILE"

CHROME=""
for cand in google-chrome-stable google-chrome chromium chromium-browser; do
  if command -v "$cand" >/dev/null 2>&1; then
    CHROME="$(command -v "$cand")"
    break
  fi
done

if [ -z "$CHROME" ]; then
  echo "Chrome/Chromium не найден в PATH."
  echo "Установи, например:"
  echo "  sudo pacman -S chromium"
  echo "  # или из AUR: yay -S google-chrome"
  read -p "Нажми Enter для выхода..." _
  exit 1
fi

echo "Использую браузер: $CHROME"
echo "В открывшемся окне: залогинься на F6S по email и открой листинг."
echo "НЕ закрывай это окно терминала и окно браузера, пока парсишь."
echo

"$CHROME" \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  --no-sandbox \
  "https://www.f6s.com/login"