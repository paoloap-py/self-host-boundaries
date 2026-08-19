"""BOUNDARY 1. An OpenAI-compatible front door that accepts response_format and,
when BREAK_ROUTER=1, never forwards it.

The schema says the field is valid, so callers get a 200. The code behind it never
passes the field to vLLM, so nothing constrains the model and it answers in prose.
Nothing raises, nothing logs, and whoever is on call debugs the parser downstream.

Set BREAK_ROUTER=0 to take the fixed path, which maps the field onto vLLM's guided
decoding settings. That is the whole repair, and it is the block marked below.
"""
import os
import httpx
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
TRITON = os.environ.get("TRITON_URL", "http://triton:8001")
BROKEN = os.environ.get("BREAK_ROUTER", "1") == "1"


class ChatRequest(BaseModel):
    model: str
    messages: list
    temperature: float = 0.7
    response_format: dict | None = None      # accepted, and when BROKEN that is all


@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    payload = {
        "model": req.model,
        "messages": req.messages,
        "temperature": req.temperature,
    }

    # THE FIX for boundary 1. Everything above is identical either way; a caller
    # cannot tell the two apart from the response envelope, only from the body.
    if not BROKEN and req.response_format:
        if req.response_format.get("type") == "json_object":
            payload["guided_json"] = {"type": "object"}
        elif req.response_format.get("type") == "json_schema":
            payload["guided_json"] = req.response_format["json_schema"]["schema"]

    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{TRITON}/v1/chat/completions", json=payload)
    return r.json()
