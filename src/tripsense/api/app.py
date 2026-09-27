from __future__ import annotations

import os
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException, Request, status
    from pydantic import BaseModel, Field
    from starlette.responses import FileResponse, HTMLResponse, JSONResponse
except ImportError as exc:  # pragma: no cover - clear setup error for optional dependency
    raise RuntimeError('Install the API dependencies with: pip install -e ".[api]"') from exc

from tripsense.config import load_project_env
from tripsense.core.journey import JourneyStore
from tripsense.core.service import TripSenseService
from tripsense.knowledge.realtime import realtime_provider_from_env
from tripsense.llm import llm_provider_from_env

load_project_env()

app = FastAPI(title="TripSense API", version="0.1.0")


def build_service() -> TripSenseService:
    return TripSenseService(
        realtime_provider=realtime_provider_from_env(),
        llm_provider=llm_provider_from_env(),
    )


service = build_service()
store = JourneyStore(Path(os.getenv("TRIPSENSE_DB_PATH", Path.cwd() / "data" / "tripsense.db")))
WEB_DIR = Path(__file__).resolve().parents[3] / "web"
PROTOTYPE_PATH = WEB_DIR / "TripSense_产品原型_v2.0.html"
API_CLIENT_PATH = WEB_DIR / "tripsense-api.js"
UI_HELPERS_PATH = WEB_DIR / "tripsense-ui.js"


class PlanRequest(BaseModel):
    city: str = Field(default="beijing", pattern="^(beijing|shanghai)$")
    text: str = Field(min_length=2, max_length=500)
    apply_realtime: bool = False
    current_plan: dict | None = None
    preference_state: dict | None = None
    previous_intent: dict | None = None


class SaveRequest(PlanRequest):
    pass


class KnowledgeSearchRequest(PlanRequest):
    limit: int = Field(default=20, ge=1, le=100)


class StopUpdateRequest(BaseModel):
    completed: bool | None = None
    actual_dwell_minutes: float | None = Field(default=None, ge=0, le=1440)
    note: str | None = Field(default=None, max_length=500)


class JourneyRecordRequest(BaseModel):
    day_index: int = Field(default=1, ge=1, le=60)
    stop_position: int | None = Field(default=None, ge=1, le=100)
    place_name: str | None = Field(default=None, min_length=1, max_length=120)
    mood: str | None = Field(default=None, min_length=1, max_length=30)
    note: str | None = Field(default=None, min_length=1, max_length=2000)
    photo_refs: list[str] = Field(default_factory=list, max_length=12)


@app.exception_handler(KeyError)
def handle_not_found(_request: Request, exc: KeyError) -> JSONResponse:
    message = str(exc.args[0]) if exc.args else "resource not found"
    if message.startswith("journey not found"):
        message = "journey not found"
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": message})


@app.exception_handler(ValueError)
def handle_invalid_state(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": str(exc)},
    )


@app.get("/api/v1/health")
def health(request: Request):
    import json

    llm = service.llm_provider
    configured = llm.provider_name != "local-baseline"
    payload = {
        "status": "ok",
        "planner": "kalman-ccp-greedy",
        "realtime": service.realtime_provider.provider_name,
        "preference": service.preference.to_dict(),
        "llm": {
            "provider": llm.provider_name,
            "model": llm.model_name,
            "configured": configured,
        },
    }
    accept = (request.headers.get("accept") or "").lower()
    wants_html = "text/html" in accept and not accept.strip().startswith("application/json")
    if wants_html:
        realtime = str(payload["realtime"])
        weather_ok = realtime == "amap-weather"
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>TripSense Health</title>"
            "<style>body{font:15px/1.6 system-ui,sans-serif;margin:32px;color:#241f38;background:#faf7f2}"
            "h1{font-size:22px;margin:0 0 8px}p{color:#5c5668}.ok{color:#1f7a57;font-weight:700}"
            ".warn{color:#9a5b1f;font-weight:700}code{background:#efeae3;padding:2px 6px;border-radius:6px}"
            "pre{white-space:pre-wrap;background:#fff;border:1px solid #e4ddd4;border-radius:12px;padding:14px}"
            "a{color:#5b4a8a}</style></head><body>"
            "<h1>TripSense 健康检查</h1>"
            f"<p>状态：<span class='ok'>{payload['status']}</span></p>"
            f"<p>实时天气：<span class='{'ok' if weather_ok else 'warn'}'>{realtime}</span>"
            f"{'（高德已接入）' if weather_ok else '（未读取到 AMAP_WEB_SERVICE_KEY）'}</p>"
            f"<p>大模型：<code>{payload['llm']['provider']}</code> / <code>{payload['llm']['model']}</code>"
            f"{' · 已配置' if configured else ' · 未配置'}</p>"
            "<p>说明：天气提醒只在雨雪雾等需要改线时出现在路线页；晴天不会弹横幅。"
            "<a href='/?demo_weather=rain'>打开雨天演示</a></p>"
            f"<pre>{json.dumps(payload, ensure_ascii=False, indent=2)}</pre>"
            "</body></html>"
        )
    return payload


@app.get("/", include_in_schema=False)
def product_prototype() -> HTMLResponse:
    prototype = PROTOTYPE_PATH.read_text(encoding="utf-8")
    api_client = API_CLIENT_PATH.read_text(encoding="utf-8")
    ui_helpers = UI_HELPERS_PATH.read_text(encoding="utf-8")
    prototype = prototype.replace(
        '<script src="tripsense-api.js?v=2"></script>',
        f"<script>{api_client}</script>",
    )
    prototype = prototype.replace(
        '<script src="tripsense-ui.js?v=6"></script>',
        f"<script>{ui_helpers}</script>",
    )
    return HTMLResponse(prototype)


@app.get("/tripsense-api.js", include_in_schema=False)
def browser_api_client() -> FileResponse:
    return FileResponse(API_CLIENT_PATH, media_type="text/javascript; charset=utf-8")


@app.get("/tripsense-ui.js", include_in_schema=False)
def browser_ui_helpers() -> FileResponse:
    return FileResponse(UI_HELPERS_PATH, media_type="text/javascript; charset=utf-8")


@app.post("/api/v1/routes/plan")
def plan_route(request: PlanRequest) -> dict:
    return service.plan(
        request.text,
        request.city,
        apply_realtime=request.apply_realtime,
    ).to_dict()


@app.post("/api/v1/chat/respond")
def chat_respond(request: PlanRequest) -> dict:
    return service.chat(
        request.text,
        request.city,
        apply_realtime=request.apply_realtime,
        current_plan=request.current_plan,
        preference_state=request.preference_state,
        previous_intent=request.previous_intent,
    )


@app.post("/api/v1/knowledge/search")
def search_knowledge(request: KnowledgeSearchRequest) -> dict:
    return service.search_knowledge(
        request.text,
        request.city,
        limit=request.limit,
        apply_realtime=request.apply_realtime,
    )


@app.post("/api/v1/journeys")
def save_journey(request: SaveRequest) -> dict[str, str]:
    plan = service.plan(
        request.text,
        request.city,
        apply_realtime=request.apply_realtime,
    )
    return {"journey_id": store.save(plan)}


@app.get("/api/v1/journeys")
def list_journeys() -> dict[str, list[dict]]:
    return {"items": store.list()}


@app.get("/api/v1/journeys/{journey_id}")
def get_journey(journey_id: str) -> dict:
    return store.get(journey_id)


@app.patch("/api/v1/journeys/{journey_id}/stops/{position}")
def update_journey_stop(journey_id: str, position: int, request: StopUpdateRequest) -> dict:
    return store.update_stop(
        journey_id,
        position,
        completed=request.completed,
        actual_dwell_minutes=request.actual_dwell_minutes,
        note=request.note,
    )


@app.post(
    "/api/v1/journeys/{journey_id}/records",
    status_code=status.HTTP_201_CREATED,
)
def create_journey_record(journey_id: str, request: JourneyRecordRequest) -> dict:
    return store.add_record(
        journey_id,
        day_index=request.day_index,
        stop_position=request.stop_position,
        place_name=request.place_name,
        mood=request.mood,
        note=request.note,
        photo_refs=request.photo_refs,
    )


@app.get("/api/v1/journeys/{journey_id}/records")
def list_journey_records(journey_id: str) -> dict[str, list[dict]]:
    return {"items": store.list_records(journey_id)}


@app.post(
    "/api/v1/journeys/{journey_id}/diaries/generate",
    status_code=status.HTTP_201_CREATED,
)
def generate_travel_diary(journey_id: str) -> dict:
    try:
        return store.generate_diary(journey_id, llm_provider=service.llm_provider)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@app.get("/api/v1/journeys/{journey_id}/diaries/latest")
def get_latest_travel_diary(journey_id: str) -> dict:
    return store.latest_diary(journey_id)
