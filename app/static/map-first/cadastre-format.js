export const categories = {
  settlement: { label: "Земли населённых пунктов", color: "#e6a650" },
  industrial: { label: "Промышленность и спецназначение", color: "#a688c8" },
  agricultural: { label: "Сельскохозяйственные земли", color: "#83ab54" },
  other: { label: "Прочие земли", color: "#62a9bd" },
  unknown: { label: "Не определена", color: "#8c969d" },
};

export const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

export function formatDistance(meters) {
  if (meters >= 1000) return `${numberFormat.format(meters / 1000)} км`;
  if (meters > 0 && meters < 0.01) return "< 0,01 м";
  return `${numberFormat.format(meters)} м`;
}

export function isPosition(point) {
  return Array.isArray(point) && Number.isFinite(point[0]) && Number.isFinite(point[1]) &&
    Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90;
}
