export async function request(url, options = {}, timeout) {
  let response;
  try {
    const signals = [options.signal, timeout && AbortSignal.timeout(timeout)].filter(Boolean);
    const signal = signals.length > 1 ? AbortSignal.any(signals) : signals[0];
    response = await fetch(url, { ...options, signal });
  } catch (error) {
    if (error.name === "TimeoutError") throw new Error("Поиск не завершился вовремя. Повторите позже.");
    throw new Error("Нет связи с локальным приложением. Проверьте, что сервер запущен.");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    const detail = typeof data?.detail === "string" ? data.detail : data?.detail?.message;
    const error = new Error(detail || "Сервер вернул неожиданный ответ.");
    error.status = response.status;
    error.coverage = data?.coverage;
    throw error;
  }
  return data;
}
