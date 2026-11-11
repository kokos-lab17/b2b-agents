import asyncio
import re
import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents import capture_payment, run_procurement

app = FastAPI(title="Agentic B2B Commerce")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

events: list[dict] = []
order_id: str | None = None
approve_url: str | None = None
status: str = "idle"
current_vendors: list = []

_lock = threading.Lock()
_generation = 0

_OFFER_RE = re.compile(
    r"\$\s*(\d+(?:\.\d+)?)\s*(?:/|per)\s*unit",
    re.IGNORECASE,
)
_ANY_MONEY_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)")
_ORDER_ID_RE = re.compile(r"\b([A-Z0-9]{17,32})\b")
_URL_RE = re.compile(r"https?://[^\s\)\]\"'<>]+")


class StartRequest(BaseModel):
    product: str = Field(min_length=1)
    qty: int = Field(gt=0)
    budget: float = Field(gt=0)


def _append(event: dict, generation: int | None = None) -> None:
    with _lock:
        if generation is not None and generation != _generation:
            return
        event["step"] = len(events) + 1
        events.append(event)


def _step_message(step) -> str:
    if isinstance(step, str):
        return step
    if isinstance(step, dict):
        for key in ("text", "log", "output", "raw"):
            value = step.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return str(step)
    for attr in ("text", "log", "output", "raw"):
        value = getattr(step, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return str(step)


def _extract_offer(message: str) -> float | None:
    match = _OFFER_RE.search(message) or _ANY_MONEY_RE.search(message)
    if not match:
        return None
    return float(match.group(1))


def _parse_order(text: str) -> tuple[str | None, str | None]:
    approve = None
    for match in re.finditer(r"\{[^{}]*\}", text):
        block = match.group(0)
        if re.search(r'"rel"\s*:\s*"approve"', block):
            href = re.search(r'"href"\s*:\s*"([^"]+)"', block)
            if href:
                approve = href.group(1).replace("\\u0026", "&")
                break
    if approve is None:
        for url in _URL_RE.findall(text):
            cleaned = url.rstrip(".,")
            if "paypal.com" in cleaned or "token=" in cleaned:
                approve = cleaned
                break

    parsed_id = None
    labeled = re.search(
        r"order[_\s-]?id\s*[:=]\s*['\"]?([A-Z0-9]{17,32})",
        text,
        re.IGNORECASE,
    )
    if labeled:
        parsed_id = labeled.group(1)
    else:
        id_field = re.search(r'"id"\s*:\s*"([A-Z0-9]{17,32})"', text)
        if id_field:
            parsed_id = id_field.group(1)
        else:
            any_id = _ORDER_ID_RE.search(text)
            if any_id:
                parsed_id = any_id.group(1)

    if parsed_id is None and approve and "token=" in approve:
        token = approve.split("token=", 1)[1].split("&", 1)[0]
        if _ORDER_ID_RE.fullmatch(token):
            parsed_id = token
    return parsed_id, approve


async def _run_procurement(product: str, qty: int, budget: float, generation: int) -> None:
    global order_id, approve_url, status, current_vendors

    def on_step(role: str, step) -> None:
        message = _step_message(step)
        _append(
            {
                "agent": role,
                "message": message,
                "offer": _extract_offer(message),
                "status": "negotiating",
            },
            generation,
        )

    def on_vendors(vendors: list) -> None:
        global current_vendors
        with _lock:
            if generation != _generation:
                return
            current_vendors = vendors

    try:
        result = await asyncio.to_thread(
            run_procurement,
            product,
            qty,
            budget,
            on_step,
            on_vendors,
        )
    except Exception as exc:
        with _lock:
            if generation != _generation:
                return
            status = "error"
        _append(
            {
                "agent": "System",
                "message": f"Procurement failed: {exc}",
                "offer": None,
                "status": "error",
            },
            generation,
        )
        return

    transcript = f"{result.get('raw', '')}\n{result.get('transcript', '')}"
    parsed_id, parsed_url = _parse_order(transcript)
    with _lock:
        if generation != _generation:
            return
        order_id = parsed_id
        approve_url = parsed_url
        status = "awaiting_approval"
        if result.get("vendors"):
            current_vendors = result["vendors"]
    _append(
        {
            "agent": "System",
            "message": "Order created. Awaiting buyer approval.",
            "approve_url": approve_url,
            "status": "awaiting_approval",
        },
        generation,
    )


@app.post("/api/start")
async def start(body: StartRequest):
    global events, order_id, approve_url, status, current_vendors, _generation
    _generation += 1
    generation = _generation
    with _lock:
        events = []
        current_vendors = []
    order_id = None
    approve_url = None
    status = "negotiating"
    asyncio.create_task(
        _run_procurement(body.product, body.qty, body.budget, generation)
    )
    return {"status": "started"}


@app.get("/api/status")
async def get_status():
    with _lock:
        snapshot = list(events)
        vendors = list(current_vendors)
    return {
        "events": snapshot,
        "order_id": order_id,
        "approve_url": approve_url,
        "status": status,
        "vendors": vendors,
    }


@app.get("/api/confirm-payment")
async def confirm_payment(token: str):
    global status
    if not re.fullmatch(r"[A-Z0-9]{17,32}", token):
        raise HTTPException(status_code=400, detail="token must be a PayPal order id")
    try:
        await asyncio.to_thread(capture_payment, token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    status = "captured"
    _append(
        {
            "agent": "System",
            "message": "Payment captured. Transaction complete.",
            "status": "captured",
        }
    )
    return {"status": "captured", "order_id": token}
