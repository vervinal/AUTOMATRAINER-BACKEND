from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx, os
from typing import Optional
from datetime import datetime, timedelta

app = FastAPI(title="AUTOMATRAINER API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
INTERVALS_KEY = os.environ.get("INTERVALS_API_KEY", "")
ATHLETE_ID    = os.environ.get("INTERVALS_ATHLETE_ID", "")
INTERVALS_URL = "https://intervals.icu/api/v1"

SYSTEM_PROMPT = """Eres el entrenador elite de ciclismo de Alex.
- 50 años, 80kg, FTP 295w ruta / 275w CRI, FCmáx 182lpm
- Objetivo: Nacionales CRI + Ruta 25-26 julio 2026. Departamentales 28-29 junio 2026.
- Zonas ruta: Z2 125-145lpm/177-236w | Z4 155-164lpm/265-295w | Z7 >413w
- Zonas CRI (275w): Z4 155-163lpm/247-280w
- Semana 6 activa. Pico sprint 1032w ruta / 879w CRI. FCRec 34. W' 25.8kJ. Desacopl mejor -22.2%.
- Sé directo, técnico y motivador. Responde en español. Máximo 150 palabras."""

class ChatRequest(BaseModel):
    message: str
    history: Optional[list] = []

@app.get("/", response_class=HTMLResponse)
async def serve_app():
    try:
        with open("static.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except:
        return HTMLResponse(content="<h1>AUTOMATRAINER</h1><p>static.html not found</p>")

@app.get("/health")
def health():
    return {"status": "AUTOMATRAINER API running", "version": "2.0"}

@app.get("/fitness")
async def get_fitness():
    today = datetime.now()
    oldest = (today - timedelta(days=3)).strftime("%Y-%m-%d")
    newest = today.strftime("%Y-%m-%d")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{INTERVALS_URL}/athlete/{ATHLETE_ID}/wellness",
            auth=("API_KEY", INTERVALS_KEY),
            params={"oldest": oldest, "newest": newest}
        )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()

@app.get("/activities")
async def get_activities(days: int = 3):
    today = datetime.now()
    oldest = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    newest = today.strftime("%Y-%m-%d")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{INTERVALS_URL}/athlete/{ATHLETE_ID}/activities",
            auth=("API_KEY", INTERVALS_KEY),
            params={"oldest": oldest, "newest": newest}
        )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return data[-1]
        return data

@app.post("/chat")
async def chat(req: ChatRequest):
    messages = req.history + [{"role": "user", "content": req.message}]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "system": SYSTEM_PROMPT,
                "messages": messages
            }
        )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return {"reply": r.json()["content"][0]["text"]}
