from __future__ import annotations

import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from simcore.engine import Engine

ROOT = Path(__file__).resolve().parent
engine = Engine()

app = FastAPI(title="nabha-sim", docs_url="/api/docs")


@app.on_event("startup")
def _startup():
    engine.start_scheduler()
    engine.log("sys", "nabha-sim started")


@app.on_event("shutdown")
def _shutdown():
    engine.shutdown()


@app.get("/api/state")
def get_state():
    return engine.state()


@app.get("/api/logs")
def get_logs(since: int = 0):
    return {"logs": [l for l in engine.logs if l["seq"] > since]}


@app.get("/api/messages")
def get_messages():
    return {"messages": list(engine.sub_messages)}


@app.put("/api/connections/{index}")
async def put_connection(index: int, fields: dict):
    if index not in engine.connections:
        raise HTTPException(404, "no such connection slot")
    return engine.set_connection(index, fields)


@app.post("/api/connections/{index}/start")
def start_connection(index: int):
    if index not in engine.connections:
        raise HTTPException(404, "no such connection slot")
    ok = engine.start_connection(index)
    return {"ok": ok, "status": engine.connections[index].status}


@app.post("/api/connections/{index}/stop")
def stop_connection(index: int):
    if index not in engine.connections:
        raise HTTPException(404, "no such connection slot")
    engine.stop_connection(index)
    return {"ok": True}


@app.post("/api/devices")
async def upsert_device(fields: dict):
    return engine.upsert_device(fields)


@app.delete("/api/devices/{did}")
def delete_device(did: str):
    engine.delete_device(did)
    return {"ok": True}


@app.post("/api/devices/{did}/point")
async def set_point(did: str, body: dict):
    ok = engine.set_point(did, body.get("name", ""), body.get("value"))
    if not ok:
        raise HTTPException(400, "unknown device or point")
    return {"ok": True}


@app.post("/api/bindings")
async def upsert_binding(fields: dict):
    return engine.upsert_binding(fields)


@app.delete("/api/bindings/{bid}")
def delete_binding(bid: str):
    engine.delete_binding(bid)
    return {"ok": True}


@app.post("/api/bindings/{bid}/publish")
def publish_now(bid: str):
    return {"ok": engine.publish_now(bid)}


@app.post("/api/subs")
async def upsert_sub(fields: dict):
    return engine.upsert_sub(fields)


@app.delete("/api/subs/{sid}")
def delete_sub(sid: str):
    engine.delete_sub(sid)
    return {"ok": True}


@app.post("/api/publish")
async def raw_publish(body: dict):
    idx = int(body.get("connection", 1))
    conn = engine.connections.get(idx)
    if not conn:
        raise HTTPException(404, "no such connection slot")
    if conn.status != "Connected":
        raise HTTPException(409, f"connection {idx} is {conn.status}")
    payload = body.get("payload", "")
    if not isinstance(payload, str):
        payload = json.dumps(payload)
    topic = body.get("topic", "")
    if not topic:
        raise HTTPException(400, "topic required")
    ok = conn.publish(topic, payload, int(body.get("qos", 1)), bool(body.get("retain", False)))
    if ok:
        engine.log("pub", f"[conn {idx}] → {topic} (one-shot)  {payload[:160]}")
    return {"ok": ok}


@app.get("/")
def index():
    return FileResponse(ROOT / "ui" / "index.html")


app.mount("/ui", StaticFiles(directory=ROOT / "ui"), name="ui")


if __name__ == "__main__":
    import socket
    import sys
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if probe.connect_ex(("127.0.0.1", 8765)) == 0:
        probe.close()
        print("nabha-sim already running on :8765 — refusing to start a second instance")
        sys.exit(1)
    probe.close()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
