export async function request(url, options = {}, timeout) {
  let response;
  try {
    response = await fetch(url, timeout ? { ...options, signal: AbortSignal.timeout(timeout) } : options);
  } catch (error) {
    if (error.name === "TimeoutError") throw new Error("Поиск не завершился вовремя. Повторите позже.");
    throw new Error("Нет связи с локальным приложением. Проверьте, что сервер запущен.");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    const detail = typeof data?.detail === "string" ? data.detail : data?.detail?.message;
    const error = new Error(detail || "Сервер вернул неожиданный ответ.");
    error.coverage = data?.coverage;
    throw error;
  }
  return data;
}
