# Развёртывание и запуск

**Назначение** — воспроизвести локальный запуск, проверить конфигурацию и перенести сохранённые исследования.

## Среда и установка

Обследован Windows x64, PowerShell и Python 3.12.10. `start.ps1` требует Python 3.12 и созданную им либо совместимую `.venv`. Backend из исходников запускается через Uvicorn, frontend раздаётся из `app/static` без сборки Node.js. Требуется браузер с ES modules, Fetch, AbortSignal и ResizeObserver. [requirements.txt](../../requirements.txt) закрепляет FastAPI 0.162.5, Uvicorn 0.54.0, HTTPX 0.28.1, Shapely 2.2.0, pyproj 3.8.0 и pynspd 1.1.15. Pydantic 2.14.3 обнаружен как установленная зависимость среды, его версия отдельно не закреплена в requirements.

```powershell
Set-Location -LiteralPath 'C:\Project\ecoburo-geo'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
```

Если `py` недоступен, используйте `python -m venv .venv` после проверки, что `python --version` показывает 3.12.x. При готовом окружении повторная установка для ручного запуска не требуется. `start.ps1 -Port <порт> -NoBrowser` устанавливает зависимости при каждом запуске, привязывает Uvicorn к loopback и запускает один worker. Без `-NoBrowser` открывает корневую legacy-страницу. Порт скрипта по умолчанию 8000.

## Рабочая конфигурация

| Переменная | Что делает | По умолчанию |
|---|---|---|
| `ESRI_API_KEY` | Браузерный ключ Esri. Окружение, включая явно пустое значение, имеет приоритет. При отсутствии читается строка ключа из `.env` в корне исходников или рядом с EXE. Другие переменные из `.env` приложение таким способом не загружает. | Пусто без настройки; `.env.example` содержит только имя |
| `CACHE_DIR` | Корень кешей Nominatim, Overpass и NSPD | Из исходников `.cache` в проекте; EXE `%LOCALAPPDATA%\EcoGeoTerra\cache` |
| `RESEARCHES_DIR` | Корень долговременного хранилища, путь вычисляется при старте | `data\researches` относительно корня приложения; закрепите внешнюю папку для переноса EXE |
| `NOMINATIM_URL`, `OVERPASS_URL` | Замена endpoints внешних провайдеров | [Матрица интеграций](03_INTEGRATION_DESIGN.md#матрица-интеграций) |
| `OSM_USER_AGENT` | HTTP User-Agent для Nominatim/Overpass | `EcoburoGeo/0.1 (local territory research prototype)` |
| `NSPD_TIMEOUT_SECONDS` | Целые секунды фаз транспорта НСПД | 12 |
| `NSPD_CA_FILE` | Путь к доверенному CA, добавляемому к системному TLS context | Комплектный root с проверкой SHA-256 |
| `NSPD_CACHE_TTL_SECONDS` | Целые секунды TTL всех веток НСПД; 0 исключает чтение как свежего | 86400 |
| `NSPD_RESEARCH_ENABLE_1000`, `NSPD_RESEARCH_ENABLE_2000` | Отдельный допуск радиуса; принимаются `1` или `true` без учёта регистра | Выключены; 500 м разрешён всегда |
| `NSPD_RESEARCH_MAX_AREA_KM2` | Площадь метрического буфера | 60 км² |
| `NSPD_RESEARCH_MAX_REQUEST_VERTICES` | Предел позиций источника/буфера | 6000 |
| `NSPD_RESEARCH_MAX_REQUEST_BYTES` | Размер upstream тела | 262144 байт |
| `NSPD_RESEARCH_MAX_RESPONSE_BYTES` | Размер upstream тела и готового результата | 16777216 байт |
| `NSPD_RESEARCH_MAX_RESPONSE_VERTICES` | Предел координат ответа | 250000 |
| `NSPD_RESEARCH_MAX_OBJECTS` | Предел числа объектов | 5000 |
| `NSPD_RESEARCH_DEADLINE_SECONDS` | Полный серверный бюджет ограниченного исследования | 35 с |
| `NSPD_RESEARCH_CACHE_MAX_BYTES` | Квота каталога bounded-кеша `research-v2` | 536870912 байт |

Профиль `NSPD_RESEARCH_MAX_*`, дедлайн и квота требуют положительных целых. Изменение большинства значений вступает в силу при новом импорте приложения. `esri_api_key()` читает окружение/`.env` при запросе. `esri.env` не загружается автоматически. Не включайте значения ключей в документацию, команды проверки, журналы или архив данных.

## Запуск и подтверждённые порты

Следующая команда воспроизводит допуски обследованного 8003. Выполняйте в отдельном окне PowerShell при свободном порте либо выберите свой свободный порт. Переменные обрабатываются дочерним процессом; изменение значений в другой сессии не изменит уже запущенный экземпляр.

```powershell
Set-Location -LiteralPath 'C:\Project\ecoburo-geo'
$env:NSPD_RESEARCH_ENABLE_1000 = '1'
$env:NSPD_RESEARCH_ENABLE_2000 = '0'
# При необходимости задайте абсолютный постоянный каталог:
# $env:RESEARCHES_DIR = 'C:\EcoGeoData\researches'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8003 --workers 1
```

| Порт | Что обнаружено на 19.11.2026 |
|---|---|
| 8001 | Loopback, Uvicorn `app.main:app`, workers 1. Старый набор API без capabilities, basemaps и `/map`. |
| 8002 | Loopback, Uvicorn `app.main:app`, параметр workers в командной строке отсутствует; эффективное значение отдельно не подтверждено. Набор содержит basemaps, capabilities и `/map` отсутствуют. |
| 8003 | Loopback, Uvicorn `app.main:app`, workers 1, без reload, запуск с `--no-access-log`. Map First и актуальные API. capabilities вернул 500/1000 включены, 2000 выключен. |

Все три health возвращают `0.2.0`, поэтому номер версии не определяет набор маршрутов. Точный Git SHA старых процессов не подтверждён. В коде порт не выбирает интерфейс. Флаг 2000 м можно включить отдельно для контролируемого стенда после проверки условий поставщика; живой допуск на 8003 отложен. История измерения и ограничения записаны в [приёмке CR-006.6.1](../CR-006/06_ACCEPTANCE_TESTS.md#уточнения-интерфейса-и-радиусов-cr-00661).

Проверка без внешних геосервисов:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8003/api/health'
(Invoke-RestMethod 'http://127.0.0.1:8003/api/cadastre/capabilities').radii
(Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8003/map').StatusCode
```

В запуске из PowerShell Uvicorn пишет в консоль; `--no-access-log` скрывает журнал запросов, системные сообщения остаются. Для остановки своего экземпляра нажмите Ctrl+C в той же консоли. После обслуживания или изменения флагов вновь запустите свой экземпляр. При занятом порте сохраните работающие процессы и используйте другой свободный порт.

## Перенос и резервное копирование

Исходники: перенесите проект, создайте новое окружение 3.12 и установите зависимости. Скопируйте весь каталог исследований при закрытом собственном backend. На новом компьютере задайте `RESEARCHES_DIR` абсолютным путём до запуска и проверьте открытие нужного UUID. Права на чтение и запись нужны владельцу процесса. Отдельно перенесите конфигурацию, ключи и дополнительный CA через подходящий закрытый канал. Кеш поставщиков необязателен, переносить `.venv` обычно не требуется.

```powershell
# Пример для штатного каталога при остановленном сервере пользователя
$backupName = 'researches-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$backupPath = Join-Path 'D:\EcoGeoBackups' $backupName
Copy-Item -LiteralPath 'C:\Project\ecoburo-geo\data\researches' -Destination $backupPath -Recurse
```

Родительский каталог резервного копирования должен существовать. Для настроенного размещения подставьте фактический `RESEARCHES_DIR`. Копируйте файлы восстановления вместе с манифестами и наборами. Перед возобновлением записи проверьте права и открытие своих исследований. [Файлы хранения](07_CACHING_AND_STORAGE.md#резервное-копирование).

Windows launcher `desktop.py` и сборка PyInstaller описаны в [BUILD_WINDOWS.md](../../BUILD_WINDOWS.md). Launcher резервирует свободный loopback-порт в диапазоне с 8000 или динамический, запускает дочерний сервер и открывает `/`. Логи `server.log` и `launcher.log` лежат в `%LOCALAPPDATA%\EcoGeoTerra\logs`, ротация по 2 000 000 байт с одной копией. Его код и конфигурация сборки есть в проекте. Соответствие готового ZIP актуальной Map First-ревизии не подтверждено. Dockerfile/Compose в обследованной копии не обнаружены.

Активный UUID один на каталог, аккаунтов нет. Для этого режима используется один worker. Локальная файловая блокировка хранилища сама по себе не координирует сетевые лимитеры, single-flight и записи всех кешей между процессами. Многопроцессный и общий серверный запуск отдельно не проверены.

**Код** — [start.ps1](../../start.ps1), [desktop.py](../../desktop.py), [app/main.py](../../app/main.py), [кадастровый профиль](../../app/cadastre_research.py), [хранилище](../../app/research_store.py).

**Связанные документы** — [интеграции](03_INTEGRATION_DESIGN.md), [хранение](07_CACHING_AND_STORAGE.md), [границы доступа](09_SECURITY_AND_LIMITS.md).
