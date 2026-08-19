"""BOUNDARY 1. An OpenAI-compatible front door that accepts response_format and
never forwards it.

The schema says the field is valid, so callers get a 200. The code behind it never
passes the field to vLLM, so nothing constrains the model and it answers in prose.
Nothing raises, nothing logs, and whoever is on call debugs the parser downstream.
"""
import os
import httpx
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
TRITON = os.environ.get("TRITON_URL", "http://triton:8001")


class ChatRequest(BaseModel):
    model: str
    messages: list
    temperature: float = 0.7
    response_format: dict | None = None      # accepted, and that is all


@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    payload = {
        "model": req.model,
        "messages": req.messages,
        "temperature": req.temperature,
        # response_format is NOT here. That is boundary 1.
        #
        # Fix: map it onto vLLM's guided decoding settings, something like
        #   if req.response_format and req.response_format.get("type") == "json_object":
        #       payload["guided_json"] = {"type": "object"}
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{TRITON}/v1/chat/completions", json=payload)
    return r.json()
