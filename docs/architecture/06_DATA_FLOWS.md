# Потоки данных

**Назначение** — показать последовательность основных операций, реальные точки входа и ветви восстановления.

## A. Поиск по кадастровому номеру

`parseSearch` распознаёт номер, `installSearch` вызывает `cadastre.findNumber`. `CadastreExperiment.execute` и `resolve_source` сначала смотрят локальный кеш; при miss шлюз `by_number` запрашивает площадной объект. `isParcel` проверяет ответ `{parcel,retrieved_at,cache_hit,warnings}`, затем `adopt` обновляет карту и ориентир OSM. Контур другого исходного номера очищает прежнюю кадастровую выборку. Синтаксическая ошибка не отправляет запрос, 404/невалидный контур сохраняют прежний контекст.

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant UI as search.js / cadastre.js
    participant B as CadastreExperiment
    participant C as Кеш
    participant N as НСПД
    U->>UI: Ввести номер и отправить форму
    UI->>UI: parseSearch
    UI->>B: GET /api/cadastre-experiment/source
    B->>C: resolve_source по точному номеру
    alt Cache miss
        B->>N: GET by_number, слой 36048
        N-->>B: FeatureCollection или отказ
    else Cache hit
        C-->>B: Сохранённый площадной участок
    end
    alt Валидный точный контур
        B-->>UI: parcel, retrieved_at, cache_hit, warnings
        UI->>UI: adopt, fit к участку, ориентир OSM
    else Ошибка
        B-->>UI: HTTP detail
        UI->>UI: Показать сообщение
    end
```

## B. Поиск по адресу

`searchAddress` вызывает `GET /api/geocode` с query `q`. `services.geocode` нормализует пробелы и возвращаемые типы. Клиент оставляет адреса с пригодными координатами, один результат выбирает сразу, при нескольких показывает список. `locate` и `coordinates.goTo` перемещают карту; ориентир передаётся поиску кандидатов и OSM. При пустом списке или ошибке исходный кадастровый контур не заменяется.

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant UI as search.js
    participant B as services.geocode
    participant C as CachedService
    participant N as Nominatim
    U->>UI: Отправить адрес
    UI->>B: GET /api/geocode?q=...
    B->>C: Нормализованный запрос
    opt Нет свежей записи
        C->>N: GET search, limit=8, jsonv2
        N-->>C: Массив или ошибка
    end
    C-->>B: Данные и cache либо ServiceError
    B-->>UI: results и cache либо HTTP detail
    alt Один валидный вариант
        UI->>UI: locate и переход к координатам
    else Несколько вариантов
        UI-->>U: Список адресов
        U->>UI: Выбрать вариант
        UI->>UI: locate и переход к координатам
    else Нет вариантов или ошибка
        UI-->>U: Уточнить запрос либо показать отказ
    end
```

## C. Подбор участка у координат

Ввод координат или адреса создаёт ориентир. После «Найти участок у точки» `createPointSearch` отправляет `{lat,lon}`. `_source_candidates` читает собственный кеш либо строит круг 15 м и обращается к `search_land`. `point_candidates` возвращает проверенные и непроверенные варианты. Ответ предыдущего запроса игнорируется при смене ориентира. `confirmSource` вызывается только для проверенного контура после отдельного подтверждения. Отказ и отсутствие кандидатов не выбирают участок автоматически.

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant P as point-candidates.js
    participant B as CadastreExperiment
    participant N as НСПД
    U->>P: Ориентир WGS 84, затем «Найти участок у точки»
    P->>B: POST /api/cadastre/source-candidates
    opt Нет результата в кеше
        B->>B: Круг 15 м в AEQD
        B->>N: POST intersects по землям
        N-->>B: Кадастровые записи
    end
    B-->>P: point, candidates, coverage, warnings либо HTTP detail
    alt Актуальный ответ и валидный контур
        P-->>U: Показать и выделить кандидата
        U->>P: Подтвердить исходный участок
        P->>P: cadastre.confirmSource
    else Пусто, устаревший токен, непроверенный контур или отказ
        P->>P: Исходный участок не заменяется
    end
```

## D. Кадастровое исследование

`installCadastre` читает capabilities и допускает выбор радиуса. На явное действие отправляет `number,radius_m,source_revision?`. Основной маршрут проверяет исходник и кеш выборки, вызывает `buffer_geometry`, `land_collection` и `attach_display_data`. Ответ содержит `source,buffer,parcels,stats,radius_m,coverage` и сопутствующие метаданные. При сбое основной запрос не переотправляется через legacy. Fallback на фиксированные 500 м выбирается раньше, если нет пригодного capabilities. Новый набор применяется после проверки радиуса, геометрии, ревизии и текущего поколения. Успешное уведомление исчезает через пять секунд, постоянный индикатор отражает применённый радиус.

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant UI as cadastre.js
    participant API as CadastreResearch
    participant Cache as Кеш НСПД
    participant N as НСПД
    U->>UI: Исследовать выбранный радиус от исходного участка
    UI->>API: POST /api/cadastre/research
    API->>API: Проверить допуск, бюджеты и общий lock
    API->>Cache: Получить исходный контур
    opt Исходный контур отсутствует в кеше
        API->>N: GET поиск по кадастровому номеру
        N-->>API: Площадной контур
    end
    API->>Cache: Проверить выборку по ревизии и радиусу
    alt Выборка найдена по актуальному ключу
        Cache-->>API: Результат с cache_hit
        API-->>UI: Сохранённая выборка
    else Требуется пространственный запрос
        API->>API: AEQD -> buffer от всей геометрии -> WGS 84
        API->>N: POST intersects c fullObject
        N-->>API: Коллекция или отказ
        alt Коллекция прошла проверки
            API->>API: Пересечение в AEQD, дедупликация, расстояния
            opt Coverage unknown
                API->>Cache: Записать нормализованный результат
            end
            API-->>UI: GeoJSON, фактический радиус, coverage
        else Ошибка или явное усечение
            API-->>UI: HTTP-ошибка и код
        end
    end
    UI->>UI: Принять только результат текущего поколения
```

## E. OSM-исследование

`installOsm` собирает `lat,lon,radius,source_label` по текущему ориентиру и радиусу. `main.research` вызывает `fetch_objects`, затем `analyze`. После `FeatureCollection` контроллер сверяет параметры ответа и применяет локальные фильтры. Если ориентир изменился, ответ может остаться в кеше старого ключа. При отказе предыдущий набор остаётся доступен в панели с признаком несоответствия. Выгрузку получает `exportOsm` из отфильтрованных объектов.

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant UI as osm.js
    participant API as app.main
    participant Cache as CachedService
    participant O as Overpass
    U->>UI: Загрузить объекты OSM
    alt Свежая запись в памяти для точки и радиуса
        UI->>UI: Использовать набор, применить фильтры
    else Нет свежей записи
        UI->>API: POST /api/research
        API->>Cache: Запрос по URL и Overpass QL
        alt Дисковый кеш актуален
            Cache-->>API: Сырые elements и метаданные
        else Cache miss
            Cache->>O: POST form data, out body geom
            O-->>Cache: elements или ошибка
            Cache-->>API: Записать и вернуть валидный ответ либо ServiceError
        end
        API->>API: Классификация, геометрия, ближайшая точка, отсечение
        API-->>UI: FeatureCollection с metadata либо HTTP-ошибка
        UI->>UI: Сверить ключ, применить фильтры и обновить слой
    end
    opt Явный экспорт
        U->>UI: CSV или GeoJSON
        UI-->>U: Blob из отфильтрованных объектов
    end
```

## F. Автосохранение исследования

`main.snapshot` объединяет камеру, параметры и результаты. `schedule` в `researches.js` ждёт 480 мс. `savePending` последовательно отправляет `PUT` с `revision,state` и изменёнными наборами/названием. `research_api.payload` читает тело, `ResearchStore.update` проверяет активный ID/ревизию, записывает данные и манифест. Следующий PUT использует новую ревизию. При 409/422/503 неподтверждённое состояние остаётся в памяти; при переводе на другой UUID или закрытии незавершённое сохранение блокирует переход. «Сохранить сейчас» форсирует PUT.

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant F as researches.js
    participant API as research_api
    participant S as ResearchStore
    U->>F: Изменение состояния
    F->>F: snapshot и debounce 480 мс
    F->>API: PUT /api/researches/{research_id}
    API->>API: Ограничить тело и валидировать UpdateResearch
    API->>S: update UUID и revision
    alt Активный UUID и совпадающая ревизия
        S->>S: Наборы, временные копии, commit манифеста
        S-->>F: research и revision+1
        F-->>U: Подтверждение сохранения
    else Конфликт, повреждение или I/O
        S-->>F: HTTP detail
        F-->>U: Ошибка и возможность повтора
    end
```

## G. Восстановление после перезапуска

`GET /api/researches/active` возвращает сводку. По «Продолжить исследование» или выбору из `GET /api/researches` выполняется `POST …/open`. UI завершает сохранение прежнего состояния, если оно открыто. `ResearchStore.get(open_research=True)` проверяет файлы и устанавливает активный UUID. `main.restore` восстанавливает кадастр, OSM, поиск, координаты, слои, панели, камеру и подложку по `{research,cadastre,osm}`. Возможны локальные GET конфигурации и обычные запросы тайлов. НСПД, Overpass и Nominatim при восстановлении выборок не вызываются. Если манифест или набор повреждён, UI показывает отказ.

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant F as Map First / researches.js
    participant API as research_api
    participant S as ResearchStore
    F->>API: GET /api/researches/active
    API->>S: active.json и сводка
    API-->>F: active либо null с возможным warning
    U->>F: Продолжить или выбрать исследование
    F->>API: POST /api/researches/{research_id}/open
    API->>S: get(open_research=True)
    alt Файлы и суммы согласованы
        S->>S: Установить активный UUID
        S-->>F: research, cadastre, osm
        F->>F: restore контроллеров и камеры
    else Отсутствие или повреждение
        S-->>F: 404, 422 или 503
        F-->>U: Сообщение
    end
```

## H. Отчёты и Markdown

`installLandReports` читает `GET /api/researches/{research_id}/reports` один раз при открытии диалога. `active_cadastre` сверяет активный UUID и загружает манифест и кадастр. `build_reports` проверяет буфер, образует `all` и `normalized`, сортирует по расстоянию и номеру, классифицирует ВРИ и вычисляет румбы. UI видит одну сохранённую ревизию даже при несохранённом изменении. `markdown` форматирует строки выбранной вкладки в UTF-8, дата формирования добавляется при скачивании.

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant F as land-reports.js
    participant API as research_api / land_reports
    participant S as ResearchStore
    U->>F: Открыть «Отчёты»
    F->>API: GET /api/researches/{research_id}/reports
    API->>S: active_cadastre
    S-->>API: research.json и cadastre.json либо ошибка
    alt Имеется сохранённая выборка
        API->>API: build_reports, буфер, румбы и правила ВРИ
        API-->>F: research и report с all / normalized
        U->>F: Скачать MD
        F-->>U: UTF-8 Blob выбранного отчёта
    else Нет выборки или конфликт
        API-->>F: report null либо HTTP detail
        F-->>U: Сообщение
    end
```

**Код** — [search.js](../../app/static/map-first/search.js), [point-candidates.js](../../app/static/map-first/point-candidates.js), [cadastre.js](../../app/static/map-first/cadastre.js), [osm.js](../../app/static/map-first/osm.js), [researches.js](../../app/static/map-first/researches.js), [land-reports.js](../../app/static/map-first/land-reports.js).

**Связанные документы** — [интеграции](03_INTEGRATION_DESIGN.md), [API](04_API_REFERENCE.md), [модель](05_DATA_MODEL.md), [кеш и хранение](07_CACHING_AND_STORAGE.md), [кадастр Mermaid](diagrams/cadastre-flow.mmd), [OSM Mermaid](diagrams/osm-flow.mmd).
