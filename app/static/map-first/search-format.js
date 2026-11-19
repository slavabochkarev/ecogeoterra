export const maxMapLatitude = 85.05112878;
export const isCadastralNumber = value => /^[0-9]{2}:[0-9]{2}:[0-9]{1,12}:[0-9]{1,14}$/.test(value);

const decimal = "[+-]?(?:[0-9]+(?:\\.[0-9]+)?|\\.[0-9]+)";
const localDecimal = "[+-]?(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+)";
const commaOrSpacePair = new RegExp(`^(${decimal})(?:\\s*,\\s*|\\s+)(${decimal})$`);
const semicolonPair = new RegExp(`^(${localDecimal})\\s*;\\s*(${localDecimal})$`);
const ambiguousDecimal = /^[+-]?\d+,[+-]?\d+$/;
const coordinateLike = /^[0-9+.,;\s-]+$/;

export function validMapPoint(lat, lon) {
  return Number.isFinite(lat) && Math.abs(lat) <= maxMapLatitude &&
    Number.isFinite(lon) && Math.abs(lon) <= 180;
}

export function parseSearch(input) {
  const value = input.trim();
  if (isCadastralNumber(value)) return { kind: "parcel", number: value };
  if (/^\d+\s*:/.test(value)) {
    throw new Error("Проверьте формат кадастрового номера, например 63:09:0102159:753.");
  }
  const coordinateText = value.replaceAll("−", "-");
  if (coordinateLike.test(coordinateText.replace(/\b(?:NaN|Infinity)\b/gi, "0"))) {
    const pair = semicolonPair.exec(coordinateText) ||
      (!ambiguousDecimal.test(coordinateText) && commaOrSpacePair.exec(coordinateText));
    if (!pair) {
      throw new Error("Введите широту и долготу, например 53.5072, 49.4204 или 53,5072; 49,4204.");
    }
    const [lat, lon] = pair.slice(1).map(part => Number(part.replace(",", ".")));
    if (!validMapPoint(lat, lon)) {
      throw new Error("Широта для карты должна быть в пределах ±85,05112878°, долгота — ±180°.");
    }
    return { kind: "point", lat, lon };
  }
  if (value.length < 3) throw new Error("Введите кадастровый номер, координаты или не менее трёх символов адреса.");
  return { kind: "address", query: value };
}
