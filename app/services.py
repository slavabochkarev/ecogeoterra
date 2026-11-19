"""Small synchronous OSM clients: disk cache, serialized requests, no retries."""
import hashlib
import json
import logging
from math import isfinite
import os
import sys
from pathlib import Path
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx

from .classification import overpass_filters

ROOT = Path(__file__).resolve().parent.parent
NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
USER_AGENT = os.getenv("OSM_USER_AGENT", "EcoburoGeo/0.1 (local territory research prototype)")


def default_cache_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "EcoGeoTerra" / "cache"
    return ROOT / ".cache"


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 502):
        self.message, self.status = message, status


class CachedService:
    def __init__(self, name: str, url: str, ttl: int, interval: float, cache_dir: Path | None = None,
                 *, timeout: httpx.Timeout | None = None):
        self.name, self.url, self.ttl, self.interval = name, url, ttl, interval
        self.directory = (cache_dir or Path(os.getenv("CACHE_DIR", str(default_cache_dir())))) / name
        self.lock = threading.Lock()
        self.next_allowed = 0.0
        self.timeout = timeout or httpx.Timeout(100.0, connect=15.0)

    def request(self, payload: dict, method: str = "GET") -> tuple[dict | list, dict]:
        key = hashlib.sha256(json.dumps([self.url, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        path = self.directory / f"{key}.json"
        # The lock also prevents identical simultaneous requests from missing cache twice.
        with self.lock:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if 0 <= time.time() - cached["saved_at"] < self.ttl:
                    return cached["data"], {"hit": True, "fetched_at": cached["fetched_at"], "endpoint": self.url, "warning": None}
            except (OSError, ValueError, KeyError, TypeError):
                pass
            delay = self.next_allowed - time.monotonic()
            if delay > self.interval + 0.1:
                raise ServiceError(f"{self.name}: сервис попросил подождать. Повторите запрос примерно через {int(delay) + 1} с.", 429)
            if delay > 0:
                time.sleep(delay)
            self.next_allowed = time.monotonic() + self.interval
            try:
                with httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                                  timeout=self.timeout, follow_redirects=True) as client:
                    response = (client.get(self.url, params=payload) if method == "GET"
                                else client.post(self.url, data=payload))
                if response.status_code == 429:
                    try:
                        cooldown = max(60, min(3600, int(response.headers.get("Retry-After", "60"))))
                    except ValueError:
                        cooldown = 60
                    self.next_allowed = time.monotonic() + cooldown
                    raise ServiceError(f"{self.name}: превышен лимит публичного API. Подождите {cooldown} с.", 429)
                if response.status_code >= 400:
                    self.next_allowed = time.monotonic() + 15
                    raise ServiceError(f"{self.name} вернул HTTP {response.status_code}. Попробуйте позже или смените сервер в настройках окружения.")
                data = response.json()
            except httpx.TimeoutException as exc:
                hint = "Попробуйте позже; для Overpass можно уменьшить радиус." if self.name == "Overpass" else "Попробуйте повторить поиск позже."
                raise ServiceError(f"{self.name} не ответил вовремя. {hint}", 504) from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise ServiceError(f"Не удалось получить данные {self.name}. Проверьте интернет, доступность API и настройки прокси.") from exc
            if self.name == "Overpass":
                if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
                    raise ServiceError("Overpass вернул неожиданный формат данных.")
                if data.get("remark"):
                    raise ServiceError("Overpass сообщил об ошибке выполнения. Частичный ответ не используется. Попробуйте позже или уменьшите радиус.")
            elif not isinstance(data, list):
                raise ServiceError("Nominatim вернул неожиданный формат данных.")
            fetched_at = datetime.now(timezone.utc).isoformat()
            warning = None
            temporary = None
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
                temporary.write_text(json.dumps({"saved_at": time.time(), "fetched_at": fetched_at, "data": data}, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
            except OSError:
                logging.warning("Cannot write %s cache", self.name, exc_info=True)
                warning = "Не удалось записать файловый кеш. Проверьте права на папку .cache."
            finally:
                if temporary:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
            return data, {"hit": False, "fetched_at": fetched_at, "endpoint": self.url, "warning": warning}


nominatim = CachedService("Nominatim", NOMINATIM_URL, ttl=7 * 86400, interval=1.1,
                          timeout=httpx.Timeout(10.0, connect=5.0))
overpass = CachedService("Overpass", OVERPASS_URL, ttl=86400, interval=5.0)


def geocode(query: str) -> dict:
    data, cache = nominatim.request({"q": " ".join(query.split()), "format": "jsonv2", "limit": 8,
                                    "addressdetails": 1, "accept-language": "ru"})
    try:
        results = [{"display_name": item["display_name"], "lat": float(item["lat"]),
                    "lon": float(item["lon"]), "osm_type": item.get("osm_type"),
                    "osm_id": item.get("osm_id"), "class": item.get("category", item.get("class")),
                    "type": item.get("type"), "address": item.get("address", {})} for item in data]
        if any(not isfinite(item["lat"]) or not isfinite(item["lon"]) for item in results):
            raise ValueError("Невалидные координаты адреса")
    except (KeyError, ValueError, TypeError) as exc:
        raise ServiceError("Nominatim вернул неполные результаты.") from exc
    return {"results": results, "cache": cache}


def build_query(lat: float, lon: float, radius: int) -> str:
    # A small margin prevents upstream spherical/ellipsoidal differences at the edge.
    # is_in + pivot adds large enclosing areas whose boundary is outside the circle.
    around = f"(around:{radius + 25},{lat:.7f},{lon:.7f})"
    nearby = "\n".join(f"nwr{f}{around};" for f in overpass_filters())
    enclosing = "\n".join(
        f'{kind}(pivot.enclosing)["{key}"];'
        for kind in ("way", "rel") for key in ("landuse", "leisure", "building")
    )
    return (f"[out:json][timeout:75][maxsize:134217728];\n"
            f"is_in({lat:.7f},{lon:.7f})->.enclosing;\n(\n{nearby}\n{enclosing}\n);\nout body geom;")


def fetch_objects(lat: float, lon: float, radius: int):
    return overpass.request({"data": build_query(lat, lon, radius)}, method="POST")
