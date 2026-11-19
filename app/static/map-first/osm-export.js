const headers = [
  "name", "category", "type", "distance_m", "direction", "osm_id", "classification_basis",
  "nearest_lat", "nearest_lon", "tags_json", "geometry_json", "source_lat", "source_lon",
  "radius_m", "source_label", "fetched_at", "osm_base_timestamp", "attribution", "disclaimer",
];

function csvCell(value) {
  let text = value == null ? "" : String(value);
  // Protect spreadsheets from formula interpretation while keeping GeoJSON unchanged.
  if (typeof value === "string" && /^[\s]*[=+@-]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

export function exportOsm(result, features, filters, format) {
  const meta = result.metadata;
  const now = new Date().toISOString();
  let content;
  let mime;
  let extension;
  if (format === "geojson") {
    content = JSON.stringify({ ...result, features, metadata: {
      ...meta, exported_count: features.length, exported_at: now, filters,
    } }, null, 2);
    mime = "application/geo+json;charset=utf-8";
    extension = "json";
  } else {
    const rows = features.map(({ properties: p, geometry }) => [
      p.name, p.category, p.type, p.distance_m.toFixed(3), p.direction, p.osm_key,
      p.classification_basis, p.nearest_point?.lat, p.nearest_point?.lon, JSON.stringify(p.tags),
      JSON.stringify(geometry), meta.source.lat, meta.source.lon, meta.source.radius,
      meta.source.source_label, meta.cache?.fetched_at, meta.osm_base_timestamp, meta.attribution, meta.disclaimer,
    ]);
    content = "\uFEFF" + [headers, ...rows].map(row => row.map(csvCell).join(";")).join("\r\n");
    mime = "text/csv;charset=utf-8";
    extension = "csv";
  }
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `ecogeoterra-${now.replace(/[:.]/g, "-")}.${extension}`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
