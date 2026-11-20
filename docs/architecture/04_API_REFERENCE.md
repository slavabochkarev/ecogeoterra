# Справочник API

**Назначение** — зафиксировать маршруты по регистрации FastAPI, локальному OpenAPI и проверкам TestClient. Примеры далее получены на синтетических фикстурах и не описывают реальные участки.

Все прикладные пути same-origin. `CadastralNumber` — строка до 40 символов с удалением крайних пробелов, шаблон `^[0-9]{2}:[0-9]{2}:[0-9]{1,12}:[0-9]{1,14}$`. `{research_id}` — UUID. Вход с неподходящим типом даёт 422; стандартная ошибка FastAPI имеет массив `detail`, прикладная обычно строку. Вложенные поля перечислены в [модели данных](05_DATA_MODEL.md).

## Маршруты геоданных и служебная конфигурация

| Метод и путь | Параметры и тело | Ответ и коды | Модуль и внешний источник |
|---|---|---|---|
| `GET /api/health` | Нет | 200 `{status,version}`. Показывает локальную готовность, доступность поставщиков не проверяет. | `main.health` |
| `GET /api/config` | Нет | 200 `{categories:dict[str,{label:string,color:string}]}`, девять категорий | `main.config`, `classification` |
| `GET /api/cadastre-experiment/basemaps` | Нет | 200 `{esriApiKey:string/null}`, `Cache-Control:no-store`, CORP `same-origin` | `main.experiment_basemaps`, читает локальную конфигурацию браузерного Esri |
| `GET /api/geocode` | Query `q:string`, 3–500 символов, после strip ≥3 | 200 `{results,cache}`, пустой список допустим. 422 вход, 429 пауза, 502 HTTP/формат, 504 таймаут. | `main.search_address` → `services.geocode` → Nominatim |
| `POST /api/research` | JSON `ResearchInput`: конечные `lat:float` ±85, `lon:float` ±180, `radius:int` 500/1000/2000, default 2000 м, `source_label:string` до 1000, default «Точка предприятия» | 200 FeatureCollection с `metadata`; 422/429/502/504. Категории фильтруются в UI после ответа. | `main.research/analyze` → `services.fetch_objects` → Overpass |
| `POST /api/oopt/research` | Строгие числовые координаты WGS 84 `lat` ±85,05112878, `lon` ±180, `diameter_km` 10/40/100/200, по умолчанию 40 | 200 с `center`, `diameter_km`, `radius_m`, `zones`, `coverage`, `legal_status`, `projection`, `source_crs`, `source`, `category`, `retrieved_at` и `warnings`. 403/409/422/429/502/503/504 при отказе. | `oopt.research_oopt` → `NspdGateway.search_oopt` → НСПД, категория 472825 |
| `GET /api/oopt/details` | Query `number`, реестровый номер границы до 32 символов | 200 `{registry_number,details,retrieved_at}`, `details:null` при отсутствии точной дополнительной записи, `Cache-Control: no-store`. 403/409/422/429/502/503/504 при отказе. | `oopt.oopt_details` → `NspdGateway.search_oopt_details` → НСПД, тема 5 и категория записи 36948 |
| `POST /api/cadastre/enrich` | `EnrichInput`: `osm_type` node/way/relation, строгий `osm_id:int>0`, `geometry:object\|null` или `coordinates:[lon,lat]\|null`, `tags:dict[str,str]` default {}. Геометрия приоритетна. | 200 `EnrichResponse` с `status` matched/multiple_matches/not_found/unavailable/error, `error_code/message` при отказе; 422 вход. Линия и площадной контур >10 км² дают unavailable до сети. | `main.enrich_cadastre` → `CadastreService.enrich` → НСПД; deprecated, для совместимости API |
| `GET /api/cadastre-experiment/source` | Query `number:CadastralNumber` | 200 `{parcel,retrieved_at,cache_hit,warnings}`; 403/404/409/422/429/502/503, Retry-After при известной паузе | `main.experiment_source` → `CadastreExperiment.resolve_source` → НСПД |
| `POST /api/cadastre-experiment/research` | `ExperimentInput`: только `number:CadastralNumber` | 200 источник, буфер, FeatureCollection соседей, счётчики, время и coverage; 403/404/409/422/429/502/503 | `main.experiment_research` → `CadastreExperiment.execute` → НСПД; 500 м, deprecated |
| `POST /api/cadastre/source-candidates` | `SourceCandidatesInput`: только строгие конечные числовые `lat` ±85 и `lon` ±180 | 200 `point,candidates,radius_m:15,retrieved_at,cache_hit,coverage,warnings`; 403/409/422/429/502/503. Небезопасный круг даёт 422. | `main.source_candidates` → `CadastreExperiment.find_source_candidates` → НСПД |
| `GET /api/cadastre/capabilities` | Нет | 200 `{source_candidates:bool,radii:list[{radius_m,enabled,reason}]}`, флаги текущего процесса | `main.cadastre_capabilities`, `CadastreResearch.capabilities` |
| `POST /api/cadastre/research` | `CadastreResearchInput`: только `number:CadastralNumber`, `radius_m` как JSON integer 500/1000/2000, опционально `source_revision` null или 64 строчных hex | 200 кадастровый результат с ревизией и `coverage_details`. Ошибки ниже. Дедлайн и пределы в [интеграциях](03_INTEGRATION_DESIGN.md). | `main.research_cadastre` → `CadastreResearch.execute` → НСПД |

### Ошибки ограниченного кадастрового API

| HTTP | `detail.code` |
|---|---|
| 403 | `upstream_forbidden` |
| 404 | `source_not_found` |
| 409 | `busy`, `source_changed` |
| 422 | `invalid_input`, `radius_disabled`, `missing_contour`, `request_budget_exceeded` |
| 429 | `cooldown`, при известной паузе `Retry-After` |
| 499 | `cancelled`, отмена операции 2000 м. При закрытом соединении клиент не получит ответ. |
| 502 | `incomplete_response`, `response_budget_exceeded`, `invalid_response` и прочие непереведённые ошибки |
| 503 | `upstream_unavailable`, `tls_configuration` |
| 504 | `deadline_exceeded` |

Тело ошибки `{detail:{code,message},coverage,request_id}`. `coverage:"partial"` означает усечение; у прочих отказов `"failed"`. У legacy-кадастра `detail` строковый для HTTPException, Nominatim и Overpass также возвращают строку при `ServiceError`. Legacy обогащение кодирует бизнес-отказ в HTTP 200.

При 200 поле `coverage` для кадастрового исследования имеет значение `unknown` при отсутствии явных пропусков или `partial` при отброшенных непригодных записях. `coverage_details.confirmed` остаётся `false`. Повтор того же результата из кеша сохраняет исходное значение `retrieved_at`.

### Ответ поиска ООПТ

Элемент `zones` содержит `registry_number`, `name` или null, `distance_m`, `boundary_distance_m`, `inside`, `nearest_boundary`, `marker`, `geometry` и доступные `attributes`. Координаты объектов карты и ближайшей границы заданы в метрах локальной AEQD относительно `center`. Геометрия карты обрезана по области и упрощена с допуском 1 м. Для касания без площади `geometry: null`, доступна точка `marker`. Расстояния вычислены по исходному контуру после преобразования EPSG:3857 → AEQD.

`coverage` имеет значение `unknown` или `partial`, `legal_status` всегда `unverified`. Пустой список имеет HTTP 200. Отказы используют объект `detail` с кодом и сообщением. При 429 передаётся `Retry-After`, подтверждённое сервером усечение приводит к 502 с `coverage: partial`.

Запрос карточки возвращает только доступные атрибуты записи с точно совпадающим реестровым номером. Среди них название, вид границы, кадастровый код, даты, реквизиты документа и режим охраны. Контур и измерения берутся из пространственного ответа. [Поля и проверка CR-011.1](../CR-011.1/README.md)

## Рабочие исследования

Все маршруты обслуживает [research_api.py](../../app/research_api.py) через `ResearchStore`. Внешние геосервисы в этом router не вызываются. HTTP 422 возможен при неверном UUID, ошибке структуры или повреждённом файле; отсутствующая папка UUID даёт 404. При ошибке Pydantic строковый `detail` содержит путь к первому проблемному полю, если поле определено. Значения из запроса в сообщение не включаются. Для записываемого тела лимит `MAX_BODY`, также действуют лимиты файлов и геометрии.

| Метод и путь | Параметры и тело | Ответ и ошибки |
|---|---|---|
| `GET /api/researches` | Нет | 200 `{items:list[Summary],skipped:int}`, по убыванию даты/UUID; 503 I/O. Summary: id, title, number, updated_at, cadastre_radius (фактический или null), has_osm. |
| `POST /api/researches` | `NewResearch` с необязательными `state:WorkingState` и `cadastre:CadastreData\|null`; допускается источник с `result:null` | 201 `{research,cadastre,osm}`, активирует новый UUID; 413/422/503 |
| `GET /api/researches/active` | Нет | 200 `{active:Summary\|null}`, возможен `warning:string` при проблемном указателе или манифесте. Ошибка I/O входа в блокировку — 503. |
| `GET /api/researches/{research_id}` | Path UUID | 200 полный документ `{research,cadastre,osm}`, активный UUID прежний; 404/422/503 |
| `PUT /api/researches/{research_id}` | UUID и `UpdateResearch`: обязательны `revision:int≥1` и `state:WorkingState`, необязательны `title:string\|null` до 600, `cadastre:CadastreData\|null`, `osm:OsmData\|null` | 200 `{research}` с новой ревизией; 404/409/413/422/503. Пропущенный набор оставляет файл, явный null очищает. |
| `POST /api/researches/{research_id}/open` | UUID, тело отсутствует | 200 полный документ и установка активного UUID после проверки; 404/422/503. Ревизия прежняя. |
| `POST /api/researches/{research_id}/close` | UUID и `CloseResearch` с `revision:int≥1` | 200 `{closed:true}` и снятие активного указателя; 404/409/413/422/503. Файлы сохраняются. |
| `GET /api/researches/{research_id}/reports` | UUID, тело и query отсутствуют | 200 `{research:{title,source_number,revision},report}`, где `report:null` означает отсутствие сохранённой выборки. Иначе возвращаются обе таблицы, фактический радиус, дата получения и полнота. 404/409/422/503, `Cache-Control:no-store`. Данные читаются из сохранённого активного кадастра. |

## Страницы, статические файлы и OpenAPI

Указанные GET идут без тела. HTML ниже приведён по типу ответа и маркеру вместо полного содержимого страницы.

| Метод и путь | Пример запроса | Ответ | Код |
|---|---|---|---|
| `GET /` | `GET / HTTP/1.1` | 200 HTML Map First с `id="workspace"` | `main.map_first_page` |
| `GET /map` | `GET /map HTTP/1.1` | 200 тот же HTML Map First | `main.map_first_page` |
| `GET /static/{path}`, HEAD | `GET /static/map-first/index.html HTTP/1.1` | 200 HTML для примера; тип зависит от файла. HEAD без тела, 304 условный, 404 отсутствующий ресурс, 405 неподдерживаемый метод. Снимок статики процесса, `Cache-Control: no-store`. | `FrontendFiles` поверх `StaticFiles` |
| `GET /openapi.json`, HEAD | `GET /openapi.json HTTP/1.1` | 200 JSON с `openapi`, `info` и `paths`, HEAD без тела | FastAPI |
| `GET /docs`, HEAD | `GET /docs HTTP/1.1` | 200 HTML Swagger UI, HEAD без тела | FastAPI |
| `GET /docs/oauth2-redirect`, HEAD | `GET /docs/oauth2-redirect HTTP/1.1` | 200 HTML вспомогательной страницы Swagger. OAuth приложения не настроен. | FastAPI |
| `GET /redoc`, HEAD | `GET /redoc HTTP/1.1` | 200 HTML ReDoc, HEAD без тела | FastAPI |

## Совместимость

`/api/research` загружает OSM по точке, `/api/researches` хранит исследования. Map First использует `/api/cadastre-experiment/source` и `/api/cadastre-experiment/basemaps`, а исследование выполняет только через `POST /api/cadastre/research`. Удалённые URL страниц `/legacy/osm`, `/legacy/cadastre-experiment` и `/cadastre-experiment` возвращают 404. Два deprecated POST остаются доступны внешним клиентам. Файлы Markdown, CSV и GeoJSON формируются в браузере.

**Код** — [main.py](../../app/main.py), [research_api.py](../../app/research_api.py), [кадастровые контракты](../../app/cadastre_research.py), [хранилище](../../app/research_schema.py), [экспорт OSM](../../app/static/map-first/osm-export.js), [экспорт Markdown](../../app/static/map-first/land-reports.js).

**Связанные документы** — [интеграции](03_INTEGRATION_DESIGN.md), [модель данных](05_DATA_MODEL.md), [потоки](06_DATA_FLOWS.md), [кеш и JSON](07_CACHING_AND_STORAGE.md).

## Примеры запросов и ответов

### `GET /api/health · 200`

```http
GET /api/health HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "status": "ok",
  "version": "0.2.0"
}
```

### `GET /api/config · 200`

```http
GET /api/config HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "categories": {"building":{"label":"Здания","color":"#64748b"},"industrial":{"label":"Промышленность","color":"#9866c4"},"residential":{"label":"Жилая среда","color":"#d49a30"},"commercial":{"label":"Коммерция","color":"#3585bd"},"education":{"label":"Образование","color":"#d37048"},"healthcare":{"label":"Медицина","color":"#cf6685"},"recreation":{"label":"Отдых и озеленение","color":"#45966a"},"fuel":{"label":"АЗС","color":"#b6814c"},"infrastructure":{"label":"Инфраструктура","color":"#4d929d"}}
}
```

### `GET /api/cadastre-experiment/basemaps · 200`

```http
GET /api/cadastre-experiment/basemaps HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "esriApiKey": null
}
```

### `GET /api/geocode · 200`

```http
GET /api/geocode?q=%D0%9F%D1%80%D0%B8%D0%BC%D0%B5%D1%80%20%D0%B0%D0%B4%D1%80%D0%B5%D1%81%D0%B0 HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "results": [{"display_name":"Пример адреса","lat":53.5501,"lon":49.2801,"osm_type":"way","osm_id":12,"class":"building","type":"yes","address":{"road":"Пример улицы"}}],
  "cache": {"hit":false,"fetched_at":"2026-11-19T08:00:00+00:00","endpoint":"https://nominatim.openstreetmap.org/search","warning":null}
}
```

### `POST /api/research · 200`

```http
POST /api/research HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json

{"lat":53.5501,"lon":49.2801,"radius":500,"source_label":"Пример точки"}
```

```json
{
  "type": "FeatureCollection",
  "features": [{"type":"Feature","id":"node/11","geometry":{"type":"Point","coordinates":[49.2806,53.5501]},"properties":{"osm_type":"node","osm_id":11,"osm_key":"node/11","name":"Пример школы","has_name":true,"category":"education","type":"Образовательное учреждение","classification_basis":"amenity=school","distance_m":33.14039917114744,"direction":"В","bearing_deg":89.99979890583195,"nearest_point":{"lat":53.5501,"lon":49.2806},"tags":{"amenity":"school","name":"Пример школы"}}}],
  "metadata": {"source":{"lat":53.5501,"lon":49.2801,"radius":500,"source_label":"Пример точки"},"total":1,"counts":{"building":0,"industrial":0,"residential":0,"commercial":0,"education":1,"healthcare":0,"recreation":0,"fuel":0,"infrastructure":0},"cache":{"hit":false,"fetched_at":"2026-11-19T08:00:00+00:00","endpoint":"https://overpass-api.de/api/interpreter","warning":null},"osm_base_timestamp":null,"projection":"+proj=aeqd +lat_0=53.5501 +lon_0=49.2801 +datum=WGS84 +units=m +no_defs +type=crs","distance_method":"nearest_geometry_point_local_aeqd_meters","warnings":[],"skipped_geometry":0,"attribution":"© OpenStreetMap contributors, ODbL 1.0","disclaimer":"OSM не является официальным кадастровым источником. Классификация по тегам не определяет юридический ВРИ."}
}
```

### `GET /api/cadastre-experiment/source · 200`

```http
GET /api/cadastre-experiment/source?number=63%3A09%3A0102159%3A100 HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "parcel": {"type":"Feature","id":"63:09:0102159:100","geometry":{"type":"Polygon","coordinates":[[[49.2803,53.55],[49.2803,53.5503],[49.28,53.5503],[49.28,53.55],[49.2803,53.55]]]},"properties":{"number":"63:09:0102159:100","land_category":"Земли населённых пунктов","category_bucket":"settlement","permitted_use":"6.0","area_m2":null,"address":null,"geometry_status":"Корректный площадной контур","label_point":[49.28015,53.55015],"measurement":{"relation":"source"}}},
  "retrieved_at": "2026-11-19T16:25:48.147558+00:00",
  "cache_hit": false,
  "warnings": []
}
```

### `POST /api/cadastre/source-candidates · 200`

```http
POST /api/cadastre/source-candidates HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json

{"lat":53.5501,"lon":49.2801}
```

```json
{
  "point": {"lat":53.5501,"lon":49.2801},
  "candidates": [{"number":"63:09:0102159:100","relation":"inside","distance_m":null,"parcel":{"type":"Feature","id":"63:09:0102159:100","geometry":{"type":"Polygon","coordinates":[[[49.2803,53.55],[49.2803,53.5503],[49.28,53.5503],[49.28,53.55],[49.2803,53.55]]]},"properties":{"number":"63:09:0102159:100","land_category":"Земли населённых пунктов","category_bucket":"settlement","permitted_use":"6.0","area_m2":null,"address":null,"geometry_status":"Корректный площадной контур","label_point":[49.28015,53.55015]}}}],
  "radius_m": 15,
  "retrieved_at": "2026-11-19T16:25:48.151561+00:00",
  "cache_hit": false,
  "coverage": "unknown",
  "warnings": []
}
```

### `POST /api/cadastre-experiment/research · 200`

```http
POST /api/cadastre-experiment/research HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json

{"number":"63:09:0102159:100"}
```

```json
{
  "source": {"type":"Feature","id":"63:09:0102159:100","geometry":{"type":"Polygon","coordinates":[[[49.2803,53.55],[49.2803,53.5503],[49.28,53.5503],[49.28,53.55],[49.2803,53.55]]]},"properties":{"number":"63:09:0102159:100","land_category":"Земли населённых пунктов","category_bucket":"settlement","permitted_use":"6.0","area_m2":null,"address":null,"geometry_status":"Корректный площадной контур","label_point":[49.28015,53.55015],"measurement":{"relation":"source"}}},
  "buffer": {"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[49.28029998411623,53.54550749796777],[49.28000001588386,53.54550749796777],[49.27852847432139,53.54559380949879],[49.27711346542425,53.54584943189288],[49.27580935259044,53.54626454461112],[49.27466624242411,53.546823199421105],[49.27372806066199,53.54750393261682],[49.273030864655134,53.54828058907635],[49.27260145717625,53.54912332660837],[49.27245635490294,53.54999976210997],[49.27245630155277,53.55029976210798],[49.27260109834336,53.551176213224814],[49.273030247405394,53.55201899986404],[49.273727271578835,53.552795731381565],[49.27466539454793,53.55347655414017],[49.27580856833506,53.554035299428826],[49.27711285807209,53.554450489892126],[49.27852813091414,53.55470616567426],[49.2799999841121,53.554792498409995],[49.28030001588799,53.55479249840998],[49.28177186908594,53.55470616567426],[49.28318714192797,53.554450489892105],[49.28449143166498,53.554035299428804],[49.2856346054521,53.55347655414015],[49.28657272842118,53.55279573138154],[49.287269752594604,53.552018999864025],[49.28769890165664,53.55117621322481],[49.287843698447226,53.55029976210798],[49.287843645097055,53.54999976210996],[49.287698542823755,53.54912332660837],[49.28726913534488,53.54828058907636],[49.28657193933801,53.54750393261684],[49.285633757575916,53.54682319942113],[49.284490647409605,53.54626454461114],[49.28318653457581,53.545849431892904],[49.281771525678685,53.5455938094988],[49.28029998411623,53.54550749796777]]]},"properties":{"radius_m":500}},
  "parcels": {"type":"FeatureCollection","features":[{"type":"Feature","id":"63:09:0102159:101","geometry":{"type":"Polygon","coordinates":[[[49.2812,53.55],[49.2812,53.5502],[49.281,53.5502],[49.281,53.55],[49.2812,53.55]]]},"properties":{"number":"63:09:0102159:101","land_category":"Земли населённых пунктов","category_bucket":"settlement","permitted_use":"Для индивидуального жилищного строительства","area_m2":null,"address":null,"geometry_status":"Корректный площадной контур","label_point":[49.281099999999995,53.5501],"measurement":{"relation":"disjoint","distance_m":46.39644946381199,"endpoints":[[49.280300000000004,53.550200002048356],[49.281,53.55019999999999]]}}}]},
  "stats": {"received":2,"valid_geometry":2,"mapped":2,"neighbors":1,"with_category":2,"without_category":0,"invalid_geometry":0,"invalid_record":0,"outside_buffer":0,"duplicate_neighbors":0,"geojson_prepare_ms":1.72},
  "radius_m": 500,
  "retrieved_at": "2026-11-19T16:25:48.162277+00:00",
  "cache_hit": false,
  "coverage": "unknown",
  "warnings": []
}
```

### `GET /api/cadastre/capabilities · 200`

```http
GET /api/cadastre/capabilities HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "source_candidates": true,
  "radii": [{"radius_m":500,"enabled":true,"reason":null},{"radius_m":1000,"enabled":true,"reason":null},{"radius_m":2000,"enabled":false,"reason":"Радиус выключен до проверки условий НСПД."}]
}
```

### `POST /api/cadastre/research · 200`

```http
POST /api/cadastre/research HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json

{"number":"63:09:0102159:100","radius_m":500}
```

```json
{
  "source": {"type":"Feature","id":"63:09:0102159:100","geometry":{"type":"Polygon","coordinates":[[[49.2803,53.55],[49.2803,53.5503],[49.28,53.5503],[49.28,53.55],[49.2803,53.55]]]},"properties":{"number":"63:09:0102159:100","land_category":"Земли населённых пунктов","category_bucket":"settlement","permitted_use":"6.0","area_m2":null,"address":null,"geometry_status":"Корректный площадной контур","label_point":[49.28015,53.55015],"measurement":{"relation":"source"}}},
  "source_revision": "fda3837b715e7fd84009d191b1d16777acd0365aef178e7763400aaef547ec83",
  "buffer": {"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[49.28029998411623,53.54550749796777],[49.28000001588386,53.54550749796777],[49.27852847432139,53.54559380949879],[49.27711346542425,53.54584943189288],[49.27580935259044,53.54626454461112],[49.27466624242411,53.546823199421105],[49.27372806066199,53.54750393261682],[49.273030864655134,53.54828058907635],[49.27260145717625,53.54912332660837],[49.27245635490294,53.54999976210997],[49.27245630155277,53.55029976210798],[49.27260109834336,53.551176213224814],[49.273030247405394,53.55201899986404],[49.273727271578835,53.552795731381565],[49.27466539454793,53.55347655414017],[49.27580856833506,53.554035299428826],[49.27711285807209,53.554450489892126],[49.27852813091414,53.55470616567426],[49.2799999841121,53.554792498409995],[49.28030001588799,53.55479249840998],[49.28177186908594,53.55470616567426],[49.28318714192797,53.554450489892105],[49.28449143166498,53.554035299428804],[49.2856346054521,53.55347655414015],[49.28657272842118,53.55279573138154],[49.287269752594604,53.552018999864025],[49.28769890165664,53.55117621322481],[49.287843698447226,53.55029976210798],[49.287843645097055,53.54999976210996],[49.287698542823755,53.54912332660837],[49.28726913534488,53.54828058907636],[49.28657193933801,53.54750393261684],[49.285633757575916,53.54682319942113],[49.284490647409605,53.54626454461114],[49.28318653457581,53.545849431892904],[49.281771525678685,53.5455938094988],[49.28029998411623,53.54550749796777]]]},"properties":{"radius_m":500}},
  "parcels": {"type":"FeatureCollection","features":[{"type":"Feature","id":"63:09:0102159:101","geometry":{"type":"Polygon","coordinates":[[[49.2812,53.55],[49.2812,53.5502],[49.281,53.5502],[49.281,53.55],[49.2812,53.55]]]},"properties":{"number":"63:09:0102159:101","land_category":"Земли населённых пунктов","category_bucket":"settlement","permitted_use":"Для индивидуального жилищного строительства","area_m2":null,"address":null,"geometry_status":"Корректный площадной контур","label_point":[49.281099999999995,53.5501],"measurement":{"relation":"disjoint","distance_m":46.39644946381199,"endpoints":[[49.280300000000004,53.550200002048356],[49.281,53.55019999999999]]}}}]},
  "stats": {"received":2,"valid_geometry":2,"mapped":2,"neighbors":1,"with_category":2,"without_category":0,"invalid_geometry":0,"invalid_record":0,"outside_buffer":0,"duplicate_neighbors":0,"geojson_prepare_ms":3.04},
  "radius_m": 500,
  "retrieved_at": "2026-11-19T16:25:48.171201+00:00",
  "cache_hit": false,
  "coverage": "unknown",
  "coverage_details": {"reason":"provider_coverage_unverified","confirmed":false,"records_skipped":0},
  "warnings": []
}
```

### `POST /api/cadastre/research · 422`

```http
POST /api/cadastre/research HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json

{"number":"63:09:0102159:100","radius_m":2000}
```

```json
{
  "detail": {"code":"radius_disabled","message":"Этот радиус выключен на сервере. Выберите доступный радиус."},
  "coverage": "failed",
  "request_id": "161c83c01938456ba302981a99c69a20"
}
```

### `POST /api/cadastre/enrich · 200`

```http
POST /api/cadastre/enrich HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json

{"osm_type":"node","osm_id":11,"coordinates":[49.28,53.55]}
```

```json
{
  "status": "not_found",
  "source": "NSPD",
  "retrieved_at": "2026-11-19T16:25:48.176511+00:00",
  "cache_hit": false,
  "candidates": [],
  "selected_candidate": null,
  "warnings": ["OSM и кадастр описывают разные сущности. ВРИ участка не присваивается зданию и не определяет деятельность организации."],
  "search_method": "point_wms",
  "error_code": null,
  "message": "Кадастровые сведения не найдены"
}
```

### `GET /api/researches/active · 200`

```http
GET /api/researches/active HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "active": null
}
```

### `POST /api/researches · 201`

```http
POST /api/researches HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json

{}
```

```json
{
  "research": {"id":"4609c453-3ff7-47c4-9115-ef56a6af81e3","title":"Новое исследование","manual_title":false,"address":null,"number":null,"created_at":"2026-11-19T16:25:48.180515+00:00","updated_at":"2026-11-19T16:25:48.180515+00:00","schema_version":1,"revision":1,"state":{"camera":{"lat":55,"lon":50,"zoom":4},"basemap":"osm","tab":"cadastre","panel_open":false,"map_only":false,"layers":{"boundaries":true,"labels":true,"fill":false,"osm":false},"cadastre_radius":500,"osm_radius":2000,"osm_categories":null,"hide_unnamed":false,"cadastre_selected":null,"osm_selected":null,"osm_origin":null,"location":null,"coordinates":null,"query":""},"datasets":{"cadastre":{"sha256":"74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b","size":4,"present":false,"radius_m":null,"retrieved_at":null},"osm":{"sha256":"74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b","size":4,"present":false,"radius_m":null,"retrieved_at":null}}},
  "cadastre": null,
  "osm": null
}
```

### `PUT /api/researches/{research_id} · 200`

```http
PUT /api/researches/4609c453-3ff7-47c4-9115-ef56a6af81e3 HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json

{"revision":1,"state":{"cadastre_radius":1000}}
```

```json
{
  "research": {"id":"4609c453-3ff7-47c4-9115-ef56a6af81e3","title":"Новое исследование","manual_title":false,"address":null,"number":null,"created_at":"2026-11-19T16:25:48.180515+00:00","updated_at":"2026-11-19T16:25:48.196023+00:00","schema_version":1,"revision":2,"state":{"camera":{"lat":55,"lon":50,"zoom":4},"basemap":"osm","tab":"cadastre","panel_open":false,"map_only":false,"layers":{"boundaries":true,"labels":true,"fill":false,"osm":false},"cadastre_radius":1000,"osm_radius":2000,"osm_categories":null,"hide_unnamed":false,"cadastre_selected":null,"osm_selected":null,"osm_origin":null,"location":null,"coordinates":null,"query":""},"datasets":{"cadastre":{"sha256":"74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b","size":4,"present":false,"radius_m":null,"retrieved_at":null},"osm":{"sha256":"74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b","size":4,"present":false,"radius_m":null,"retrieved_at":null}}}
}
```

### `GET /api/researches/active · 200`

```http
GET /api/researches/active HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "active": {"id":"4609c453-3ff7-47c4-9115-ef56a6af81e3","title":"63:09:0102159:100","number":"63:09:0102159:100","updated_at":"2026-11-19T16:25:48.206029+00:00","cadastre_radius":500,"has_osm":true}
}
```

### `GET /api/researches · 200`

```http
GET /api/researches HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "items": [{"id":"4609c453-3ff7-47c4-9115-ef56a6af81e3","title":"63:09:0102159:100","number":"63:09:0102159:100","updated_at":"2026-11-19T16:25:48.206029+00:00","cadastre_radius":500,"has_osm":true}],
  "skipped": 0
}
```

### `GET /api/researches/{research_id} · 200`

```http
GET /api/researches/4609c453-3ff7-47c4-9115-ef56a6af81e3 HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "research": {"id":"4609c453-3ff7-47c4-9115-ef56a6af81e3","title":"63:09:0102159:100","manual_title":false,"address":null,"number":"63:09:0102159:100","created_at":"2026-11-19T16:25:48.180515+00:00","updated_at":"2026-11-19T16:25:48.206029+00:00","schema_version":1,"revision":3,"state":{"camera":{"lat":55,"lon":50,"zoom":4},"basemap":"osm","tab":"cadastre","panel_open":false,"map_only":false,"layers":{"boundaries":true,"labels":true,"fill":false,"osm":false},"cadastre_radius":1000,"osm_radius":2000,"osm_categories":null,"hide_unnamed":false,"cadastre_selected":null,"osm_selected":null,"osm_origin":null,"location":null,"coordinates":null,"query":""},"datasets":{"cadastre":{"sha256":"8444a963a51525d4841335717c793a14ef628bda9878dca29eed1252f4db013a","size":3260,"present":true,"radius_m":500,"retrieved_at":"2026-11-19T16:25:48.171201+00:00"},"osm":{"sha256":"a667245b7e5cca6b97b2eed7094675b1c0c391169e442a46963e175e89fbb169","size":1387,"present":true,"radius_m":500,"retrieved_at":"2026-11-19T08:00:00+00:00"}}},
  "cadastre": {"source":{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[49.2803,53.55],[49.2803,53.5503],[49.28,53.5503],[49.28,53.55],[49.2803,53.55]]]},"id":"63:09:0102159:100","properties":{"number":"63:09:0102159:100","land_category":"Земли населённых пунктов","permitted_use":"6.0","geometry_status":"Корректный площадной контур","category_bucket":"settlement","label_point":[49.28015,53.55015],"measurement":{"relation":"source"}}},"result":{"radius_m":500,"coverage":"unknown","buffer":{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[49.28029998411623,53.54550749796777],[49.28000001588386,53.54550749796777],[49.27852847432139,53.54559380949879],[49.27711346542425,53.54584943189288],[49.27580935259044,53.54626454461112],[49.27466624242411,53.546823199421105],[49.27372806066199,53.54750393261682],[49.273030864655134,53.54828058907635],[49.27260145717625,53.54912332660837],[49.27245635490294,53.54999976210997],[49.27245630155277,53.55029976210798],[49.27260109834336,53.551176213224814],[49.273030247405394,53.55201899986404],[49.273727271578835,53.552795731381565],[49.27466539454793,53.55347655414017],[49.27580856833506,53.554035299428826],[49.27711285807209,53.554450489892126],[49.27852813091414,53.55470616567426],[49.2799999841121,53.554792498409995],[49.28030001588799,53.55479249840998],[49.28177186908594,53.55470616567426],[49.28318714192797,53.554450489892105],[49.28449143166498,53.554035299428804],[49.2856346054521,53.55347655414015],[49.28657272842118,53.55279573138154],[49.287269752594604,53.552018999864025],[49.28769890165664,53.55117621322481],[49.287843698447226,53.55029976210798],[49.287843645097055,53.54999976210996],[49.287698542823755,53.54912332660837],[49.28726913534488,53.54828058907636],[49.28657193933801,53.54750393261684],[49.285633757575916,53.54682319942113],[49.284490647409605,53.54626454461114],[49.28318653457581,53.545849431892904],[49.281771525678685,53.5455938094988],[49.28029998411623,53.54550749796777]]]},"properties":{"radius_m":500}},"parcels":{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[49.2812,53.55],[49.2812,53.5502],[49.281,53.5502],[49.281,53.55],[49.2812,53.55]]]},"id":"63:09:0102159:101","properties":{"number":"63:09:0102159:101","land_category":"Земли населённых пунктов","permitted_use":"Для индивидуального жилищного строительства","geometry_status":"Корректный площадной контур","category_bucket":"settlement","label_point":[49.281099999999995,53.5501],"measurement":{"relation":"disjoint","distance_m":46.39644946381199,"endpoints":[[49.280300000000004,53.550200002048356],[49.281,53.55019999999999]]}}}]},"stats":{"received":2,"valid_geometry":2,"mapped":2,"neighbors":1,"with_category":2,"without_category":0,"invalid_geometry":0,"invalid_record":0,"outside_buffer":0,"duplicate_neighbors":0,"geojson_prepare_ms":3.04},"retrieved_at":"2026-11-19T16:25:48.171201+00:00","warnings":[],"source_revision":"fda3837b715e7fd84009d191b1d16777acd0365aef178e7763400aaef547ec83","coverage_details":{"reason":"provider_coverage_unverified","confirmed":false,"records_skipped":0}}},
  "osm": {"result":{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[49.2806,53.5501]},"id":"node/11","properties":{"name":"Пример школы","category":"education","type":"Образовательное учреждение","classification_basis":"amenity=school","osm_type":"node","osm_id":11,"osm_key":"node/11","distance_m":33.14039917114744,"has_name":true,"tags":{"amenity":"school","name":"Пример школы"},"direction":"В","bearing_deg":89.99979890583195,"nearest_point":{"lat":53.5501,"lon":49.2806}}}],"metadata":{"source":{"lat":53.5501,"lon":49.2801,"radius":500,"source_label":"Пример точки"},"total":1,"counts":{"building":0,"industrial":0,"residential":0,"commercial":0,"education":1,"healthcare":0,"recreation":0,"fuel":0,"infrastructure":0},"cache":{"hit":false,"fetched_at":"2026-11-19T08:00:00+00:00"},"warnings":[],"projection":"+proj=aeqd +lat_0=53.5501 +lon_0=49.2801 +datum=WGS84 +units=m +no_defs +type=crs","distance_method":"nearest_geometry_point_local_aeqd_meters","attribution":"© OpenStreetMap contributors, ODbL 1.0","disclaimer":"OSM не является официальным кадастровым источником. Классификация по тегам не определяет юридический ВРИ.","skipped_geometry":0}},"requested_categories":["education"]}
}
```

### `GET /api/researches/{research_id}/reports · 200`

```http
GET /api/researches/4609c453-3ff7-47c4-9115-ef56a6af81e3/reports HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "research": {"title":"63:09:0102159:100","source_number":"63:09:0102159:100","revision":3},
  "report": {"radius_m":500,"retrieved_at":"2026-11-19T16:25:48.171201+00:00","coverage":"unknown","invalid_contours":0,"rules_version":"1.0.0","all":[{"number":"63:09:0102159:101","category":"Земли населённых пунктов","use":"Для индивидуального жилищного строительства","direction":"В","distance_m":46.39644946381199}],"normalized":[{"number":"63:09:0102159:101","category":"Земли населённых пунктов","use":"Для индивидуального жилищного строительства","direction":"В","distance_m":46.39644946381199,"group":"Жилая застройка","classification":"matched","review_reason":null}],"classification_counts":{"matched":1,"review":0,"outside":0}}
}
```

### `POST /api/researches/{research_id}/open · 200`

```http
POST /api/researches/4609c453-3ff7-47c4-9115-ef56a6af81e3/open HTTP/1.1
Host: 127.0.0.1:8000
```

```json
{
  "research": {"id":"4609c453-3ff7-47c4-9115-ef56a6af81e3","title":"63:09:0102159:100","manual_title":false,"address":null,"number":"63:09:0102159:100","created_at":"2026-11-19T16:25:48.180515+00:00","updated_at":"2026-11-19T16:25:48.206029+00:00","schema_version":1,"revision":3,"state":{"camera":{"lat":55,"lon":50,"zoom":4},"basemap":"osm","tab":"cadastre","panel_open":false,"map_only":false,"layers":{"boundaries":true,"labels":true,"fill":false,"osm":false},"cadastre_radius":1000,"osm_radius":2000,"osm_categories":null,"hide_unnamed":false,"cadastre_selected":null,"osm_selected":null,"osm_origin":null,"location":null,"coordinates":null,"query":""},"datasets":{"cadastre":{"sha256":"8444a963a51525d4841335717c793a14ef628bda9878dca29eed1252f4db013a","size":3260,"present":true,"radius_m":500,"retrieved_at":"2026-11-19T16:25:48.171201+00:00"},"osm":{"sha256":"a667245b7e5cca6b97b2eed7094675b1c0c391169e442a46963e175e89fbb169","size":1387,"present":true,"radius_m":500,"retrieved_at":"2026-11-19T08:00:00+00:00"}}},
  "cadastre": {"source":{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[49.2803,53.55],[49.2803,53.5503],[49.28,53.5503],[49.28,53.55],[49.2803,53.55]]]},"id":"63:09:0102159:100","properties":{"number":"63:09:0102159:100","land_category":"Земли населённых пунктов","permitted_use":"6.0","geometry_status":"Корректный площадной контур","category_bucket":"settlement","label_point":[49.28015,53.55015],"measurement":{"relation":"source"}}},"result":{"radius_m":500,"coverage":"unknown","buffer":{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[49.28029998411623,53.54550749796777],[49.28000001588386,53.54550749796777],[49.27852847432139,53.54559380949879],[49.27711346542425,53.54584943189288],[49.27580935259044,53.54626454461112],[49.27466624242411,53.546823199421105],[49.27372806066199,53.54750393261682],[49.273030864655134,53.54828058907635],[49.27260145717625,53.54912332660837],[49.27245635490294,53.54999976210997],[49.27245630155277,53.55029976210798],[49.27260109834336,53.551176213224814],[49.273030247405394,53.55201899986404],[49.273727271578835,53.552795731381565],[49.27466539454793,53.55347655414017],[49.27580856833506,53.554035299428826],[49.27711285807209,53.554450489892126],[49.27852813091414,53.55470616567426],[49.2799999841121,53.554792498409995],[49.28030001588799,53.55479249840998],[49.28177186908594,53.55470616567426],[49.28318714192797,53.554450489892105],[49.28449143166498,53.554035299428804],[49.2856346054521,53.55347655414015],[49.28657272842118,53.55279573138154],[49.287269752594604,53.552018999864025],[49.28769890165664,53.55117621322481],[49.287843698447226,53.55029976210798],[49.287843645097055,53.54999976210996],[49.287698542823755,53.54912332660837],[49.28726913534488,53.54828058907636],[49.28657193933801,53.54750393261684],[49.285633757575916,53.54682319942113],[49.284490647409605,53.54626454461114],[49.28318653457581,53.545849431892904],[49.281771525678685,53.5455938094988],[49.28029998411623,53.54550749796777]]]},"properties":{"radius_m":500}},"parcels":{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[49.2812,53.55],[49.2812,53.5502],[49.281,53.5502],[49.281,53.55],[49.2812,53.55]]]},"id":"63:09:0102159:101","properties":{"number":"63:09:0102159:101","land_category":"Земли населённых пунктов","permitted_use":"Для индивидуального жилищного строительства","geometry_status":"Корректный площадной контур","category_bucket":"settlement","label_point":[49.281099999999995,53.5501],"measurement":{"relation":"disjoint","distance_m":46.39644946381199,"endpoints":[[49.280300000000004,53.550200002048356],[49.281,53.55019999999999]]}}}]},"stats":{"received":2,"valid_geometry":2,"mapped":2,"neighbors":1,"with_category":2,"without_category":0,"invalid_geometry":0,"invalid_record":0,"outside_buffer":0,"duplicate_neighbors":0,"geojson_prepare_ms":3.04},"retrieved_at":"2026-11-19T16:25:48.171201+00:00","warnings":[],"source_revision":"fda3837b715e7fd84009d191b1d16777acd0365aef178e7763400aaef547ec83","coverage_details":{"reason":"provider_coverage_unverified","confirmed":false,"records_skipped":0}}},
  "osm": {"result":{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[49.2806,53.5501]},"id":"node/11","properties":{"name":"Пример школы","category":"education","type":"Образовательное учреждение","classification_basis":"amenity=school","osm_type":"node","osm_id":11,"osm_key":"node/11","distance_m":33.14039917114744,"has_name":true,"tags":{"amenity":"school","name":"Пример школы"},"direction":"В","bearing_deg":89.99979890583195,"nearest_point":{"lat":53.5501,"lon":49.2806}}}],"metadata":{"source":{"lat":53.5501,"lon":49.2801,"radius":500,"source_label":"Пример точки"},"total":1,"counts":{"building":0,"industrial":0,"residential":0,"commercial":0,"education":1,"healthcare":0,"recreation":0,"fuel":0,"infrastructure":0},"cache":{"hit":false,"fetched_at":"2026-11-19T08:00:00+00:00"},"warnings":[],"projection":"+proj=aeqd +lat_0=53.5501 +lon_0=49.2801 +datum=WGS84 +units=m +no_defs +type=crs","distance_method":"nearest_geometry_point_local_aeqd_meters","attribution":"© OpenStreetMap contributors, ODbL 1.0","disclaimer":"OSM не является официальным кадастровым источником. Классификация по тегам не определяет юридический ВРИ.","skipped_geometry":0}},"requested_categories":["education"]}
}
```

### `POST /api/researches/{research_id}/close · 200`

```http
POST /api/researches/4609c453-3ff7-47c4-9115-ef56a6af81e3/close HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json

{"revision":3}
```

```json
{
  "closed": true
}
```

### `PUT /api/researches/{research_id} · 409`

```http
PUT /api/researches/4609c453-3ff7-47c4-9115-ef56a6af81e3 HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json

{"revision":1,"state":{}}
```

```json
{
  "detail": "Активное исследование изменилось в другом окне. Откройте его заново."
}
```
