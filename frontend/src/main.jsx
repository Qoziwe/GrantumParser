import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";

/**
 * Точка входа Grantum.
 *
 * Дефолтный ./index.css от Vite НЕ импортируем намеренно:
 * он центрирует #root и переопределяет цвета, ломая каркас из App.jsx.
 * Вся визуальная база живёт в shellCss (App.jsx) + полиш ниже.
 */

/**
 * Глобальный полиш, которого нет в каркасе:
 *  - кастомный скроллбар в тон палитре;
 *  - цвет выделения текста;
 *  - единый focus-visible для клавиатурной навигации;
 *  - мягкий fade-in корня при первой загрузке.
 *
 * Инжектим один раз. Защита по id спасает от дублей при HMR в dev.
 * var(--gt-*) резолвятся лениво, поэтому fallback-цвета на первый кадр.
 * Анимация — только opacity: она не создаёт containing block для
 * position:fixed, значит амбиентный фон из App.jsx остаётся привязан
 * к viewport, а не к #root.
 */
function injectGlobalPolish() {
  const id = "gt-global-polish";
  if (document.getElementById(id)) return;

  const style = document.createElement("style");
  style.id = id;
  style.textContent = `
    ::selection {
      background: rgba(240, 168, 80, 0.32);
      color: #fff;
    }

    :focus-visible {
      outline: 2px solid var(--gt-amber, #f0a850);
      outline-offset: 2px;
      border-radius: 4px;
    }

    html {
      scrollbar-color: rgba(240, 168, 80, 0.45) transparent;
      scroll-behavior: smooth;
    }

    ::-webkit-scrollbar {
      width: 11px;
      height: 11px;
    }
    ::-webkit-scrollbar-track {
      background: transparent;
    }
    ::-webkit-scrollbar-thumb {
      background: linear-gradient(
        180deg,
        rgba(240, 168, 80, 0.55),
        rgba(88, 214, 192, 0.45)
      );
      border-radius: 999px;
      border: 3px solid var(--gt-bg, #0f1320);
    }
    ::-webkit-scrollbar-thumb:hover {
      background: linear-gradient(
        180deg,
        rgba(240, 168, 80, 0.8),
        rgba(88, 214, 192, 0.7)
      );
    }

    #root {
      animation: gt-root-in 0.55s cubic-bezier(0.22, 1, 0.36, 1) both;
    }
    @keyframes gt-root-in {
      from { opacity: 0; }
      to   { opacity: 1; }
    }

    @media (prefers-reduced-motion: reduce) {
      html { scroll-behavior: auto; }
      #root { animation: none; }
    }
  `;

  document.head.appendChild(style);
}

injectGlobalPolish();

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
