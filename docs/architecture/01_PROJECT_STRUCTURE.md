# Состав проекта

**Назначение** — указать, где реализована каждая операция и какие компоненты изменяются совместно.

## Фактическое дерево

```text
ecoburo-geo/
├── app/
│   ├── __init__.py, main.py
│   ├── cadastre.py, cadastre_experiment.py, cadastre_research.py
│   ├── nspd.py, geo.py, services.py, classification.py, osm.py
│   ├── research_api.py, research_schema.py, research_store.py, land_reports.py
│   ├── certs/                      CA НСПД и происхождение сертификата
│   └── static/
│       ├── index.html, app.js, styles.css
│       ├── cadastre-experiment.html, cadastre-experiment.js, cadastre-experiment.css
│       ├── cadastre-esri.js
│       ├── map-first/               index.html, map.css, 19 JS-модулей, правила ВРИ
│       └── vendor/                  Leaflet, Esri Leaflet, CSS, изображения, лицензии
├── data/                            локальные данные, default для исследований
├── .cache/                          локальный кеш поставщиков
├── tests/                           восемь модулей unittest
├── scripts/                         check_nspd.py, diagnose_nspd_tls.py, package_windows.py
├── docs/
│   ├── architecture/               техническая документация As-Is
│   ├── CR-006/                     проектирование, приёмка, макеты и примеры
│   └── материалы НСПД, Esri и Windows
├── desktop.py, start.ps1
├── build_windows.ps1, build_windows.cmd, EcoburoGeo.spec
├── requirements.txt, requirements-build.txt, .env.example
└── README.md, BUILD_WINDOWS.md, START_HERE.txt, VERIFICATION*.md
```

`.venv`, `__pycache__`, `build`, `dist` и ZIP/SHA256 — локальные или генерируемые артефакты. `.env` и `esri.env` могут содержать секретные значения. Автоматическая загрузка `esri.env` не подтверждена кодом. Исторические материалы `docs/research/`, `docs/research-v03/` и `docs/research-satellite/` хранятся локально.

## Backend

14 файлов в `app`, включая пустой инициализатор пакета.

| Модуль | Назначение | Основные функции и классы | Зависимости |
|---|---|---|---|
| [__init__.py](../../app/__init__.py) | Инициализация пакета | Прикладной логики нет | Python |
| [main.py](../../app/main.py) | Собирает FastAPI, страницы, статику, router исследований и общие экземпляры кадастрового сервиса. Валидирует OSM-ввод, строит свойства и метрики OSM, отображает ошибки новых и старых API | `ResearchInput`, `analyze`, `research`, `research_cadastre`, `research_error`, `esri_api_key` | FastAPI, Pydantic, Shapely, `cadastre*`, `research_api`, `services`, `classification`, `osm`, `geo` |
| [nspd.py](../../app/nspd.py) | HTTPS-шлюз НСПД, проверка сертификатов, поиск номера, пересечение и WMS, контроль формата и усечения | `nspd_tls_context`, `SecureNspd`, `NspdGateway.by_number/search_land/search`, `collection` | `pynspd`, HTTPX, `ssl`, локальный CA |
| [cadastre.py](../../app/cadastre.py) | Общий lock, cooldown и кеш НСПД. Конверсия CRS, legacy-обогащение одного OSM-объекта и сопоставление геометрии | `CadastreService`, `EnrichInput`, `EnrichResponse`, `wgs84_geometry`, `candidate` | `nspd`, `geo`, `services.default_cache_dir`, Pydantic, Shapely, pyproj |
| [cadastre_experiment.py](../../app/cadastre_experiment.py) | Точный исходный участок, подбор у координат, фиксированный буфер 500 м и геометрические примитивы для основного исследования | `CadastralNumber`, `CadastreExperiment.resolve_source/find_source_candidates/execute`, `buffer_geometry`, `land_collection`, `parcel_measurement`, `attach_display_data` | `cadastre`, `geo`, `nspd`, Shapely, Pydantic, FastAPI |
| [cadastre_research.py](../../app/cadastre_research.py) | Основной кадастровый API 500/1000/2000 м. Допуск, лимиты, общий дедлайн, кеш результата и single-flight | `CadastreResearchInput`, `ResearchLimits`, `ResearchBudget`, `CadastreResearch.execute/capabilities`, `source_revision` | `cadastre_experiment`, `nspd`, Shapely, Pydantic, `Future` |
| [geo.py](../../app/geo.py) | Локальная AEQD, ближайшая точка и восемь румбов | `MetricContext`, `direction`, `DIRECTIONS` | Shapely, pyproj |
| [services.py](../../app/services.py) | HTTP-клиенты Nominatim/Overpass, raw-кеш, сериализация запросов. Геокодирование и построение Overpass QL | `CachedService`, `ServiceError`, `geocode`, `build_query`, `fetch_objects`, `default_cache_dir` | HTTPX, `overpass_filters` |
| [classification.py](../../app/classification.py) | Девять категорий, упорядоченные правила тегов и фильтры Overpass | `CATEGORIES`, `RULES`, `classify`, `overpass_filters` | Python |
| [osm.py](../../app/osm.py) | Геометрия из node/way/relation, сборка колец и отверстий, защита от отсутствующей геометрии | `element_geometry`, `coordinates`, `rings`, `polygonal`, `GeometryError` | Shapely |
| [research_api.py](../../app/research_api.py) | Маршруты списка, открытия, сохранения, закрытия, активного UUID и отчётов. Ограничение потокового тела и перевод ошибок | `payload`, `invoke`, `list_researches`, `active_research`, `new_research`, `get_research`, `open_research`, `update_research`, `close_research`, `get_land_reports` | FastAPI, Starlette, `research_schema`, `research_store`, `land_reports` |
| [research_schema.py](../../app/research_schema.py) | Типы манифеста и UI, проверка и нормализация GeoJSON с ограничениями | `WorkingState`, `Manifest`, `DatasetInfo`, `NewResearch`, `UpdateResearch`, `CloseResearch`, `clean_cadastre`, `clean_osm` | Pydantic, `CadastralNumber`, `classification.CATEGORIES` |
| [research_store.py](../../app/research_store.py) | UUID-каталоги, блокировка, атомарные записи и согласованность набора с манифестом | `ResearchStore.create/update/get/active_cadastre/list/active/close`, `atomic_write`, `read_bytes`, `ResearchError` | `research_schema`, `msvcrt` или `fcntl` |
| [land_reports.py](../../app/land_reports.py) | Читает согласованный сохранённый кадастр, отбирает участки в буфере, сортирует, вычисляет румб и классифицирует ВРИ | `build_reports`, `_rules`, `_classification`, `_direction`, `_distance` | Shapely, pyproj, `DIRECTIONS`, `ResearchError` и локальный JSON правил |

## Frontend Map First

[Разметка](../../app/static/map-first/index.html) загружает Leaflet, Esri Leaflet, общий адаптер и `main.js` как ES module. [CSS](../../app/static/map-first/map.css) задаёт холст, плавающие панели и адаптивность. [land-use-rules.v1.json](../../app/static/map-first/land-use-rules.v1.json) используется сервером для отчётов.

| Модуль | Назначение и события | Точки входа | Импорты, API и зависимости |
|---|---|---|---|
| [main.js](../../app/static/map-first/main.js) | Создаёт карту и контроллеры, собирает состояние, восстанавливает камеру и слои, реагирует на `moveend/zoomend` и ResizeObserver | `snapshot`, `restore`, `updateLayout`, `emptyResearchState` | `basemaps`, `coordinates`, `interface`, `cadastre`, `search`, `attribution`, `osm`, `researches` |
| [interface.js](../../app/static/map-first/interface.js) | Управляет слоями, вкладками, фокусом, Escape и режимом «Вся карта» | `installLayers`, `installPanels` | DOM, состояние `main.js` |
| [basemaps.js](../../app/static/map-first/basemaps.js) | Переключение слоя OSM/Esri, сообщения о тайлах, возврат к OSM | `installBasemaps`, `activate`, `restore` | Leaflet, `esriWorldImagery`, `GET /api/cadastre-experiment/basemaps` |
| [attribution.js](../../app/static/map-first/attribution.js) | Кредиты OSM-тайлов, геокодера, слоя и видимого списка OSM | `installAttribution` | DOM, MutationObserver и ResizeObserver |
| [coordinates.js](../../app/static/map-first/coordinates.js) | Координаты курсора и метки, поля ввода, переход, копирование и передача ориентира | `installCoordinates`, `formatCoordinates`, `goTo` | `search-format`, Leaflet, Clipboard API |
| [search-format.js](../../app/static/map-first/search-format.js) | Распознаёт номер, координаты или адрес, проверяет формат и диапазоны | `parseSearch`, `isCadastralNumber`, `validMapPoint` | Самостоятельный модуль |
| [search.js](../../app/static/map-first/search.js) | Обрабатывает submit, показывает варианты адреса, ставит ориентир поиска и OSM | `installSearch`, `locate`, `searchAddress` | `point-candidates`, `request`, `search-format`, `GET /api/geocode` |
| [point-candidates.js](../../app/static/map-first/point-candidates.js) | Загружает кандидатов у ориентира, показывает временный слой, подтверждает выбранный контур | `createPointSearch`, `display` | `cadastre`, `cadastre-format`, `cadastre-panel`, `coordinates`, `request`, `POST /api/cadastre/source-candidates` |
| [cadastre.js](../../app/static/map-first/cadastre.js) | Источник, допуск радиусов, поколения запросов, результаты и уведомления | `installCadastre`, `isParcel`, `readNeighbors`, `findNumber`, `confirmSource` | `cadastre-map`, `cadastre-panel`, `cadastre-format`, `request`, `search-format`; capabilities/source/основной и старый research |
| [cadastre-map.js](../../app/static/map-first/cadastre-map.js) | Подготовка слоёв участков и буфера, подписи, заливка, выделение, линия измерения и поиск попаданий | `createCadastreMap`, `parcelsAt` | `cadastre-format`, Leaflet |
| [cadastre-panel.js](../../app/static/map-first/cadastre-panel.js) | Карточки, сводка, предупреждения и списки соседей | `createCadastrePanel`, `descriptionList`, `parcelDetails` | `cadastre-format` |
| [cadastre-format.js](../../app/static/map-first/cadastre-format.js) | Палитра и формат расстояния, проверка координат GeoJSON | `categories`, `formatDistance`, `isPosition` | Intl |
| [osm.js](../../app/static/map-first/osm.js) | Независимый радиус, свежий набор по точке, фильтры, выбор, видимость, восстановление | `installOsm`, `setOrigin`, `refreshVisibility`, `current`, `restore` | `osm-map`, `osm-panel`, `osm-export`, `cadastre-format`, `coordinates`, `request`, `GET /api/config`, `POST /api/research` |
| [osm-map.js](../../app/static/map-first/osm-map.js) | Геометрии и выделение OSM, поиск попаданий и взаимодействие с кадастровым слоем | `createOsmMap` | Leaflet |
| [osm-panel.js](../../app/static/map-first/osm-panel.js) | Список порциями по 100, категории, карточка выбранного, ссылка и исходные теги | `createOsmPanel`, `renderList`, `renderCategories`, `select` | `cadastre-panel`, `cadastre-format`, `coordinates` |
| [osm-export.js](../../app/static/map-first/osm-export.js) | Отфильтрованные CSV/GeoJSON, экранирование CSV и защита от формул | `exportOsm`, `csvCell` | Blob, object URL |
| [researches.js](../../app/static/map-first/researches.js) | Меню, диалоги, debounce и последовательное сохранение с ревизиями; переключение после подтверждения | `installResearches`, `schedule`, `savePending`, `flush`, `transition` | `request`, `land-reports`, маршруты `/api/researches` |
| [land-reports.js](../../app/static/map-first/land-reports.js) | Диалог, вкладки, сводка, группировка ВРИ и Markdown | `installLandReports`, `sections`, `markdown` | `request`, Intl, Blob, `GET /api/researches/{research_id}/reports` |
| [request.js](../../app/static/map-first/request.js) | Fetch и JSON, распаковка ошибок и опциональный таймаут | `request` | Fetch, AbortSignal |

UI-панели и флаги слоёв живут в `main.js`, выборки и выбранные ID — в контроллерах, камера и векторные слои — в Leaflet. `snapshot()` сводит их в `WorkingState`. Видимость слоя отделена от набора объектов. Выбор из карты и списка обновляет карточку. Поиск и кадастровая кнопка сами не запускают Overpass. Режим «Вся карта» скрывает DOM-панели; Fullscreen API не вызывается.

## Старые страницы и адаптер

| Модуль | Назначение | Точки входа и зависимости |
|---|---|---|
| [app.js](../../app/static/app.js) | OSM v0.2, точка, радиус, фильтры, выбор, экспорт, кадастровое обогащение выбранного объекта | [index.html](../../app/static/index.html), [styles.css](../../app/static/styles.css), Leaflet; `initializeMap`, `fetchCadastre`, `exportResults`; config/geocode/research/enrich |
| [cadastre-experiment.js](../../app/static/cadastre-experiment.js) | Отдельный кадастровый 500 м, выбор участков, подписи, измерения, координаты и подложки | [HTML](../../app/static/cadastre-experiment.html), [CSS](../../app/static/cadastre-experiment.css), Leaflet; `initialize`, `parcelLabels`, `renderMeasurement`; source/research/basemaps |
| [cadastre-esri.js](../../app/static/cadastre-esri.js) | Проверяет метаданные, показывает кредиты, диагностирует ошибки Esri. Map First передаёт свой контейнер кредитов | `esriWorldImagery`, `esriFailure`, `failedTileCode`; Leaflet, Esri Leaflet |

Итого 22 прикладных JS-модуля, из них 19 в Map First. Библиотеки и макеты CR-006 в подсчёт не входят.

## Тесты и эксплуатационный код

| Файл | Что проверяет или делает | Данные и зависимости |
|---|---|---|
| [test_api.py](../../tests/test_api.py) | Маршруты, OSM pipeline, адресные варианты, TTL, частичный ответ и 429 | TestClient, подменённые `fetch_objects`/`geocode`, синтетические элементы и временный кеш |
| [test_cadastre.py](../../tests/test_cadastre.py) | Обогащение, кандидаты, CRS, TTL, TLS и формат НСПД | `fixture`, `payload`, Mock/MockTransport, синтетические контуры |
| [test_cadastre_experiment.py](../../tests/test_cadastre_experiment.py) | Буфер 500 м, фильтрация, кеш, отказы и контракт legacy | `parcel`, `SOURCE`, `SEED`, `NEAR`, gateway Mock |
| [test_parcel_measurement.py](../../tests/test_parcel_measurement.py), [test_geo.py](../../tests/test_geo.py) | Отверстия, MultiPolygon, касания/пересечения, ближайшая точка и восемь румбов | Shapely и контрольные расстояния |
| [test_osm.py](../../tests/test_osm.py) | Приоритет тегов, линии, relation с отверстиями и неполная геометрия | Словари элементов Overpass |
| [test_esri_config.py](../../tests/test_esri_config.py) | Пустой ключ, приоритет окружения, no-store | Подменённое окружение и тестовый dotenv |
| [test_desktop.py](../../tests/test_desktop.py) | Занятый порт, раздача статики, штатная остановка дочернего сервера | Loopback-сокет и отдельный Uvicorn |
| [desktop.py](../../desktop.py) | Windows launcher с Tkinter, резервированием порта и остановкой своего дочернего процесса | `Launcher`, `reserve_listener`, `serve`; `multiprocessing`, Uvicorn |
| [start.ps1](../../start.ps1), [build_windows.ps1](../../build_windows.ps1), [EcoburoGeo.spec](../../EcoburoGeo.spec), [package_windows.py](../../scripts/package_windows.py) | Запуск из исходников, создание окружения, Windows-сборка и упаковка | Python 3.12, PowerShell, PyInstaller |
| [check_nspd.py](../../scripts/check_nspd.py), [diagnose_nspd_tls.py](../../scripts/diagnose_nspd_tls.py) | Диагностика интеграции НСПД и TLS | При отдельном запуске могут обращаться к внешнему сервису. Это не штатные автономные тесты. |

Фикстуры встроены преимущественно в Python. Отдельного постоянного набора автоматической приёмки Map First, JSON-хранилища и отчётов в `tests` нет. Результаты разовых smoke приведены в [приёмке CR-006](../CR-006/06_ACCEPTANCE_TESTS.md).

**Код** — [точка входа backend](../../app/main.py), [точка входа Map First](../../app/static/map-first/main.js), [тесты](../../tests/).

**Связанные документы** — [компоненты](02_COMPONENT_ARCHITECTURE.md), [API](04_API_REFERENCE.md), [потоки](06_DATA_FLOWS.md), [запуск](08_DEPLOYMENT.md).
