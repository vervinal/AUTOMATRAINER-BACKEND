from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import os
from typing import Optional

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
Datos clave del atleta:
- 50 años, 80kg, FTP 295w ruta / 275w CRI, FCmáx 182lpm
- Objetivo principal: Nacionales CRI + Ruta 25-26 julio 2026
- Objetivo secundario: Departamentales 28-29 junio 2026
- Debilidades: durabilidad (calambres km 60+), asimetría izquierda 48/52
- Zonas ruta: Z1 <125lpm/<177w | Z2 125-145lpm/177-236w | Z3 145-155lpm/236-265w | Z4 155-164lpm/265-295w | Z5 164-172lpm/295-354w | Z7 >413w
- Zonas CRI (FTP 275w): Z4 155-163lpm/247-280w
- Plan: 12 semanas iniciado 7 abril. Semana 6 activa.
- Entrenamientos: Martes CRI simulador, Miércoles Z1 ruta, Jueves fuerza+gimnasio, Viernes Z2+durabilidad, Sábado Over-Unders CRI, Domingo fondo+ataques
- Historial reciente: test FTP 295w (5 mayo), Over-Unders CRI carretera 9 mayo (desacoplamiento 0.7%), fondo+ataques 100km/752m 10 mayo
- Pico sprint: 1032w | Desacoplamiento mejor: -22.2% | FCRec: 34
- Cadencia natural: 78-85 rpm
- Sé directo, técnico y motivador. Responde en español. Máximo 150 palabras."""

class ChatRequest(BaseModel):
    message: str
    history: Optional[list] = []

class WorkoutRequest(BaseModel):
    name: str
    description: str
    date: str
    duration_seconds: int
    training_load: int
    type: str = "Ride"
    category: str = "WORKOUT"

@app.get("/")
def root():
    return {"status": "AUTOMATRAINER API running", "version": "1.0"}

@app.get("/fitness")
async def get_fitness():
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{INTERVALS_URL}/athlete/{ATHLETE_ID}/wellness",
            auth=("API_KEY", INTERVALS_KEY),
            params={"oldest": "2026-05-10", "newest": "2026-05-12"}
        )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail="Intervals error")
        return r.json()

@app.get("/activities")
async def get_activities(days: int = 7):
    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=days)
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{INTERVALS_URL}/athlete/{ATHLETE_ID}/activities",
            auth=("API_KEY", INTERVALS_KEY),
            params={
                "oldest": start.strftime("%Y-%m-%dT00:00:00"),
                "newest": end.strftime("%Y-%m-%dT23:59:59")
            }
        )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail="Intervals error")
        return r.json()

@app.post("/chat")
async def chat(req: ChatRequest):
    messages = req.history + [{"role": "user", "content": req.message}]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "system": SYSTEM_PROMPT,
                "messages": messages
            }
        )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        data = r.json()
        return {"reply": data["content"][0]["text"]}

@app.post("/workout")
async def create_workout(req: WorkoutRequest):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{INTERVALS_URL}/athlete/{ATHLETE_ID}/events",
            auth=("API_KEY", INTERVALS_KEY),
            json={
                "category": req.category,
                "start_date_local": f"{req.date}T06:00:00",
                "name": req.name,
                "description": req.description,
                "moving_time": req.duration_seconds,
                "load": req.training_load,
                "type": req.type
            }
        )
        if r.status_code not in [200, 201]:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()
