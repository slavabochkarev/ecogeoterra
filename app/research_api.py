"""Local research API, separate from the existing OSM research route."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .research_schema import MAX_BODY, CloseResearch, NewResearch, UpdateResearch
from .research_store import ResearchError, ResearchStore
from .land_reports import build_reports

router = APIRouter(prefix="/api/researches", tags=["researches"])
store = ResearchStore()
INVALID_RESEARCH = "Некорректная структура рабочего исследования."


def validation_detail(exc: ValidationError):
    error = exc.errors(include_url=False, include_context=False, include_input=False)[0]
    if not error["loc"]:
        return INVALID_RESEARCH
    path = ".".join(str(part) if isinstance(part, int) or (part.isidentifier() and len(part) <= 40) else "?"
                    for part in error["loc"][:8])
    reason = {"extra_forbidden": "неизвестное поле", "missing": "обязательное поле"}.get(
        error["type"], "недопустимое значение")
    return f"{INVALID_RESEARCH} Поле {path}: {reason}."


async def payload(request, model):
    length = request.headers.get("content-length", "")
    if length.isdecimal() and (len(length) > 12 or int(length) > MAX_BODY):
        raise HTTPException(413, "Размер сохранения превышает допустимый.")
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY:
                raise HTTPException(413, "Размер сохранения превышает допустимый.")
            body.extend(chunk)
        return await run_in_threadpool(model.model_validate_json, body)
    except ValidationError as exc:
        raise HTTPException(422, validation_detail(exc)) from exc
    except (ValueError, TypeError, RecursionError, ClientDisconnect) as exc:
        raise HTTPException(422, INVALID_RESEARCH) from exc


def invoke(callback, *args, **kwargs):
    try:
        return callback(*args, **kwargs)
    except ResearchError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except (ValidationError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        raise HTTPException(422, "Некорректная или слишком сложная выборка исследования.") from exc


@router.get("")
def list_researches():
    return invoke(store.list)


@router.get("/active")
def active_research():
    return invoke(store.active)


@router.post("", status_code=201)
async def new_research(request: Request):
    data = await payload(request, NewResearch)
    return await run_in_threadpool(invoke, store.create, data)


@router.get("/{research_id}")
def get_research(research_id: UUID):
    return invoke(store.get, research_id)


@router.get("/{research_id}/reports")
def get_land_reports(research_id: UUID):
    document = invoke(store.active_cadastre, research_id)
    return JSONResponse(invoke(build_reports, document), headers={"Cache-Control": "no-store"})


@router.post("/{research_id}/open")
def open_research(research_id: UUID):
    return invoke(store.get, research_id, open_research=True)


@router.put("/{research_id}")
async def update_research(research_id: UUID, request: Request):
    data = await payload(request, UpdateResearch)
    return await run_in_threadpool(invoke, store.update, research_id, data)


@router.post("/{research_id}/close")
async def close_research(research_id: UUID, request: Request):
    data = await payload(request, CloseResearch)
    return await run_in_threadpool(invoke, store.close, research_id, data.revision)
