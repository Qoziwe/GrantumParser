import { useEffect } from "react";

/**
 * setInterval с паузой, когда вкладка скрыта (document.hidden):
 * фоновые поллинги не гоняют запросы впустую и не будят CPU.
 *
 * Семантика как у «load(); setInterval(load, ms)»:
 * при монтировании (и при возврате на вкладку) колбэк вызывается сразу,
 * затем по интервалу. Пока вкладка скрыта — интервал снят.
 */
export function useVisibleInterval(callback, delayMs) {
  useEffect(() => {
    if (delayMs == null) return undefined;

    let timerId = null;

    const stop = () => {
      if (timerId != null) {
        clearInterval(timerId);
        timerId = null;
      }
    };

    const onVisibility = () => {
      if (document.hidden) {
        stop();
        return;
      }
      callback();
      if (timerId == null) timerId = setInterval(callback, delayMs);
    };

    if (!document.hidden) {
      callback();
      timerId = setInterval(callback, delayMs);
    }
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [callback, delayMs]);
}
