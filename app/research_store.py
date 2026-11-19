"""Local drafts with atomic metadata commits and recoverable dataset replacement."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import Lock
from uuid import UUID, uuid4

from .research_schema import (
    FILE_LIMITS, SCHEMA_VERSION, DatasetInfo, Manifest, clean_cadastre, clean_osm,
)

_ADDRESS_ABBREVIATIONS = re.compile(
    r"(?<![\w.])((?i:ул|обл|респ|р-н|пр|просп|пр-кт|пер|пос|ст|стр|корп|кв|мкр|бул|б-р|наб|тер|уч|зд|лит|пгт|жк)"
    r"|г|д|п|с|к|ш|^Г)\.(?=\s|,|$|\d)",
)
_DATASETS = {"cadastre": clean_cadastre, "osm": clean_osm}


class ResearchError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def encode(value, slot):
    body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(body) > FILE_LIMITS[slot]:
        raise ResearchError(413, "Исследование превышает допустимый размер файла.")
    return body


def read_bytes(path, limit):
    # A data file must be a regular local child of the already validated directory.
    if path.resolve().parent != path.parent or path.is_symlink() or not path.is_file():
        raise ValueError("Unexpected data file")
    with path.open("rb") as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError("File exceeds its limit")
    return body


def atomic_write(path, body):
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _title(address, number, state):
    if number:
        if not address:
            return number
        clean = _ADDRESS_ABBREVIATIONS.sub(r"\1", address)
        return f"{clean[: 597 - len(number)].rstrip()} ({number})"
    point = state.location or state.osm_origin or state.coordinates
    return f"{point.lat:.6f}, {point.lon:.6f}" if point else "Новое исследование"


def _describe(slot, data, body):
    result = data.get("result") if data else None
    radius = retrieved = None
    if result:
        if slot == "cadastre":
            radius, retrieved = result["radius_m"], result["retrieved_at"]
        else:
            meta = result["metadata"]
            radius, retrieved = meta["source"]["radius"], meta["cache"]["fetched_at"]
    return DatasetInfo(sha256=hashlib.sha256(body).hexdigest(), size=len(body), present=data is not None,
                       radius_m=radius, retrieved_at=retrieved or None)


class ResearchStore:
    def __init__(self, root=None):
        default = Path(__file__).resolve().parent.parent / "data" / "researches"
        self.root = Path(root or os.getenv("RESEARCHES_DIR") or default).resolve()
        self.lock = Lock()

    @contextmanager
    def _locked(self):
        try:
            with self.lock:
                self.root.mkdir(parents=True, exist_ok=True)
                lock_path = self.root / ".lock"
                if lock_path.is_symlink():
                    raise OSError("Invalid storage lock")
                with lock_path.open("a+b") as stream:
                    if os.name == "nt":
                        import msvcrt
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                    # Closing the descriptor releases the operating-system lock.
                    yield
        except OSError as exc:
            raise ResearchError(503, "Не удалось записать или прочитать хранилище исследований. Проверьте доступ к каталогу или повторите позже.") from exc

    def _directory(self, research_id):
        path = self.root / str(research_id)
        try:
            valid = path.is_dir() and path.resolve() == path
        except (OSError, RuntimeError):
            valid = False
        if not valid:
            raise ResearchError(404, "Исследование не найдено.")
        return path

    def _manifest(self, folder):
        try:
            manifest = Manifest.model_validate_json(read_bytes(folder / "research.json", FILE_LIMITS["research"]))
            if manifest.id != folder.name or set(manifest.datasets) != set(_DATASETS):
                raise ValueError("Manifest does not match its directory")
            return manifest
        except (ValueError, OSError, RecursionError, RuntimeError) as exc:
            raise ResearchError(422, "Файл research.json повреждён, отсутствует или имеет неподдерживаемую версию.") from exc

    def _dataset_bytes(self, folder, slot, info):
        for name in (f"{slot}.json", f".{slot}.previous"):
            try:
                body = read_bytes(folder / name, FILE_LIMITS[slot])
                if len(body) == info.size and hashlib.sha256(body).hexdigest() == info.sha256:
                    return body
            except (OSError, ValueError, RuntimeError):
                continue
        raise ResearchError(422, f"Файл {slot}.json повреждён или отсутствует.")

    def _document(self, folder, *, slots=_DATASETS):
        manifest = self._manifest(folder)
        result = {"research": manifest.model_dump()}
        for slot in slots:
            info = manifest.datasets[slot]
            body = self._dataset_bytes(folder, slot, info)
            try:
                result[slot] = _DATASETS[slot](json.loads(body))
                if _describe(slot, result[slot], body) != info:
                    raise ValueError("Dataset metadata does not match")
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError) as exc:
                raise ResearchError(422, f"Файл {slot}.json содержит некорректную выборку.") from exc
        source = result["cadastre"]["source"] if result["cadastre"] else None
        if manifest.number != (source["id"] if source else None):
            raise ResearchError(422, "Исходный участок в файлах исследования не совпадает.")
        return result

    def _active(self):
        path = self.root / "active.json"
        if not path.exists():
            return None
        try:
            identifier = json.loads(read_bytes(path, 4096))["id"]
            return str(UUID(identifier)) if identifier is not None else None
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, RecursionError) as exc:
            raise ResearchError(422, "Указатель активного исследования повреждён. Откройте исследование из списка.") from exc

    def _set_active(self, identifier):
        atomic_write(self.root / "active.json", encode({"id": identifier}, "research"))

    @staticmethod
    def _summary(manifest):
        return {"id": manifest.id, "title": manifest.title, "number": manifest.number,
                "updated_at": manifest.updated_at, "cadastre_radius": manifest.datasets["cadastre"].radius_m,
                "has_osm": manifest.datasets["osm"].present}

    def list(self):
        with self._locked():
            items = []
            skipped = 0
            for folder in self.root.iterdir():
                if not folder.is_dir():
                    continue
                try:
                    identifier = UUID(folder.name)
                except ValueError:
                    continue
                if str(identifier) != folder.name:
                    continue
                try:
                    items.append(self._summary(self._manifest(self._directory(identifier))))
                except ResearchError:
                    skipped += 1
            return {"items": sorted(items, key=lambda item: (item["updated_at"], item["id"]), reverse=True),
                    "skipped": skipped}

    def active(self):
        with self._locked():
            try:
                identifier = self._active()
                if identifier:
                    return {"active": self._summary(self._manifest(self._directory(identifier)))}
            except ResearchError as exc:
                return {"active": None, "warning": str(exc)}
            return {"active": None}

    def get(self, research_id, *, open_research=False):
        with self._locked():
            document = self._document(self._directory(research_id))
            if open_research:
                self._set_active(str(research_id))
            return document

    def active_cadastre(self, research_id):
        with self._locked():
            if self._active() != str(research_id):
                raise ResearchError(409, "Активное исследование изменилось. Откройте его из списка.")
            return self._document(self._directory(research_id), slots=("cadastre",))

    def create(self, payload):
        # Only the preselected source is carried into a new draft.
        cadastre = clean_cadastre(payload.cadastre.model_dump() if payload.cadastre else None)
        if cadastre and cadastre["result"]:
            raise ResearchError(422, "Новое исследование принимает только исходный участок.")
        data = {"cadastre": cadastre, "osm": None}
        files = {slot: encode(value, slot) for slot, value in data.items()}
        state = payload.state.model_copy(update={"cadastre_selected": cadastre["source"]["id"] if cadastre else None,
                                                 "osm_selected": None})
        number = cadastre["source"]["id"] if cadastre else None
        address = cadastre["source"]["properties"].get("address") if cadastre else None
        identifier = str(uuid4())
        now = _utc_now()
        manifest = Manifest(id=identifier, title=_title(address, number, state), manual_title=False,
                            address=address, number=number, created_at=now, updated_at=now, schema_version=SCHEMA_VERSION,
                            revision=1, state=state, datasets={
                                slot: _describe(slot, value, files[slot]) for slot, value in data.items()
                            })
        with self._locked():
            folder = self.root / identifier
            folder.mkdir()
            try:
                for slot, body in files.items():
                    atomic_write(folder / f"{slot}.json", body)
                atomic_write(folder / "research.json", encode(manifest.model_dump(), "research"))
                self._set_active(identifier)
            except (OSError, ResearchError):
                for name in ("research.json", "cadastre.json", "osm.json"):
                    try:
                        (folder / name).unlink(missing_ok=True)
                    except OSError:
                        pass
                try:
                    folder.rmdir()
                except OSError:
                    pass
                raise
        return {"research": manifest.model_dump(), **data}

    def _check_revision(self, manifest, revision):
        if manifest.id != self._active():
            raise ResearchError(409, "Активное исследование изменилось в другом окне. Откройте его заново.")
        if manifest.revision != revision:
            raise ResearchError(409, "На сервере уже есть более новое состояние. Откройте исследование заново.")

    def update(self, research_id, payload):
        changed = {}
        for slot, normalize in _DATASETS.items():
            if slot in payload.model_fields_set:
                value = getattr(payload, slot)
                data = normalize(value.model_dump() if value else None)
                body = encode(data, slot)
                changed[slot] = (data, body, _describe(slot, data, body))
        with self._locked():
            folder = self._directory(research_id)
            old = self._manifest(folder)
            self._check_revision(old, payload.revision)
            number, address = old.number, old.address
            if "cadastre" in changed:
                data = changed["cadastre"][0]
                source = data["source"] if data else None
                number = source["id"] if source else None
                address = source["properties"].get("address") if source else None
            title = payload.title.strip() if payload.title is not None else None
            if payload.title is not None and not title:
                raise ResearchError(422, "Введите название исследования.")
            manual = old.manual_title or title is not None
            title = title or (old.title if manual else _title(address, number, payload.state))
            datasets = {**old.datasets, **{slot: values[2] for slot, values in changed.items()}}
            manifest = old.model_copy(update={"number": number, "address": address, "title": title, "manual_title": manual,
                "state": payload.state, "datasets": datasets, "revision": old.revision + 1, "updated_at": _utc_now()})
            self._commit(folder, old, manifest, changed)
            return {"research": manifest.model_dump()}

    def _commit(self, folder, old, manifest, changed):
        metadata = encode(manifest.model_dump(), "research")
        prepared = []
        try:
            for slot, (_, body, info) in changed.items():
                previous = old.datasets[slot]
                if previous.sha256 == info.sha256:
                    continue
                path = folder / f"{slot}.json"
                backup = folder / f".{slot}.previous"
                committed = self._dataset_bytes(folder, slot, previous)
                atomic_write(backup, committed)
                prepared.append((path, backup))
                atomic_write(path, body)
            # This is the commit point. The previous files match the old manifest until it is replaced.
            atomic_write(folder / "research.json", metadata)
        except (OSError, ResearchError):
            for path, backup in prepared:
                try:
                    os.replace(backup, path)
                except OSError:
                    pass  # Readers can recover from the backup by its checksum.
            raise
        for _, backup in prepared:
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass

    def close(self, research_id, revision):
        with self._locked():
            self._check_revision(self._manifest(self._directory(research_id)), revision)
            self._set_active(None)
            return {"closed": True}
