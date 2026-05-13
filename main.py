from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx, os, json, logging
from typing import Optional
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
HISTORY_FILE  = "/tmp/chat_history.json"

BASE_SYSTEM = """Eres AUTOMATRAINER, el entrenador elite de ciclismo de Alex.
PERFIL DEL ATLETA:
- 50 años, 80kg, FCmáx 182lpm
- FTP 295w ruta / 275w CRI (posición aero -7%)
- Cadencia natural 78-85rpm
- Debilidades: durabilidad (calambres km 60+), asimetría izquierda 49/51
- Plan 12 semanas iniciado 7 abril 2026

OBJETIVOS:
- Departamentales CRI + Ruta: 28-29 junio 2026 (meta secundaria)
- Nacionales CRI + Ruta: 25-26 julio 2026 (meta principal)
- CRI Nacional: 20km ida y vuelta, 150m desnivel, llano

ZONAS RUTA (FTP 295w):
- Z1 <125lpm / <177w
- Z2 125-145lpm / 177-236w
- Z3 145-155lpm / 236-265w
- Z4 155-164lpm / 265-295w
- Z5 164-172lpm / 295-354w
- Z7 >413w

ZONAS CRI (FTP 275w):
- Z3 145-155lpm / 220-247w
- Z4 155-163lpm / 247-280w
- Z5 163-172lpm / 280-320w

ESTRUCTURA SEMANAL:
- Lunes: Descanso total
- Martes: Sprints + W' (CRI simulador)
- Miércoles: Recuperación Z1 (ruta)
- Jueves: Fuerza + Lactato + Gimnasio
- Viernes: Z2 + Durabilidad (ruta)
- Sábado: Over-Unders CRI
- Domingo: Fondo + Ataques o Competencia

HISTORIAL RECIENTE:
- Test FTP 295w: 5 mayo (subida 6%, 20min, 311w promedio)
- Over-Unders CRI carretera: 9 mayo (desacopl 0.7%, W'bal 4.2kJ)
- Fondo+Ataques 100km/752m: 10 mayo (FCRec 34, desacopl 8%)
- Sprints 8x879w + Z4 3x: 12 mayo (RPE 9-10, CRI 58T)
- Mejor desacoplamiento: -22.2% (test FTP)
- Pico sprint: 1032w ruta / 879w CRI

SUPLEMENTACIÓN: Creatina 3g, bicarbonato 2g, beta-alanina 2g, remolacha 5g (2-3h antes). Magnesio 300mg noche.

REGLAS:
- Responde en español
- Máximo 200 palabras por respuesta
- Sé directo, técnico y motivador
- Cuando des parámetros incluye siempre: potencia, FC y cadencia
- Cuando analices un entreno incluye: desacoplamiento, W'bal, balance I/D si están disponibles"""

# ─── HISTORIAL ────────────────────────────────────────────

def load_history():
    try:
        if Path(HISTORY_FILE).exists():
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
                # Keep last 20 messages to avoid token overflow
                return data[-20:]
    except:
        pass
    return []

def save_history(history):
    try:
        # Keep last 40 messages in storage
        trimmed = history[-40:]
        with open(HISTORY_FILE, "w") as f:
            json.dump(trimmed, f)
    except Exception as e:
        logger.error(f"Error saving history: {e}")

def clear_history():
    try:
        Path(HISTORY_FILE).unlink(missing_ok=True)
    except:
        pass

# ─── INTERVALS DATA ───────────────────────────────────────

async def get_live_context():
    """Obtiene datos frescos de intervals.icu para el system prompt"""
    context = ""
    try:
        today = datetime.now()
        oldest = (today - timedelta(days=3)).strftime("%Y-%m-%d")
        newest = today.strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=15) as client:
            # Fitness
            r = await client.get(
                f"{INTERVALS_URL}/athlete/{ATHLETE_ID}/wellness",
                auth=("API_KEY", INTERVALS_KEY),
                params={"oldest": oldest, "newest": newest}
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    last = data[-1]
                    sport = last.get("sportInfo", [{}])[0] if last.get("sportInfo") else {}
                    ctl = round(last.get("ctl", 0), 1)
                    atl = round(last.get("atl", 0), 1)
                    tsb = round(last.get("ctl", 0) - last.get("atl", 0), 1)
                    ramp = round(last.get("rampRate", 0), 1)
                    wprime = round(sport.get("wPrime", 0) / 1000, 1) if sport.get("wPrime") else None
                    eftp = round(sport.get("eftp", 0)) if sport.get("eftp") else None

                    context += f"\nDATO EN TIEMPO REAL ({last.get('id', today.strftime('%Y-%m-%d'))}):"
                    context += f"\n- CTL: {ctl} | ATL: {atl} | TSB: {tsb} | Rampa: {ramp}"
                    if wprime: context += f"\n- W': {wprime} kJ"
                    if eftp:   context += f"\n- eFTP: {eftp}w"

            # Last activity
            oldest_act = (today - timedelta(days=2)).strftime("%Y-%m-%d")
            r2 = await client.get(
                f"{INTERVALS_URL}/athlete/{ATHLETE_ID}/activities",
                auth=("API_KEY", INTERVALS_KEY),
                params={"oldest": oldest_act, "newest": newest}
            )
            if r2.status_code == 200:
                acts = r2.json()
                if isinstance(acts, list) and acts:
                    last_act = acts[-1]
                    context += f"\n- Último entreno: {last_act.get('name','?')} ({last_act.get('date','?')})"
                    context += f" | TSS:{last_act.get('training_load','?')} | FC:{last_act.get('avg_hr','?')} | Potencia:{last_act.get('avg_power_w','?')}w"

    except Exception as e:
        logger.error(f"Context error: {e}")

    return context

# ─── ENDPOINTS ────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    history: Optional[list] = []

@app.get("/", response_class=HTMLResponse)
async def serve_app():
    try:
        with open("static.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        return HTMLResponse(content=f"<h1>Error: {e}</h1>")

@app.get("/health")
def health():
    return {
        "status": "running",
        "version": "3.0",
        "anthropic_key": "set" if ANTHROPIC_KEY else "MISSING",
        "intervals_key": "set" if INTERVALS_KEY else "MISSING",
        "athlete_id": ATHLETE_ID or "MISSING"
    }

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
        if isinstance(data, list) and data:
            return data[-1]
        return data


@app.get("/events")
async def get_events(days_back: int = 3, days_forward: int = 60):
    from datetime import datetime, timedelta
    today = datetime.now()
    oldest = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
    newest = (today + timedelta(days=days_forward)).strftime("%Y-%m-%d")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{INTERVALS_URL}/athlete/{ATHLETE_ID}/events",
            auth=("API_KEY", INTERVALS_KEY),
            params={"oldest": oldest, "newest": newest}
        )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()

@app.post("/chat")
async def chat(req: ChatRequest):
    # 1. Cargar historial guardado
    history = load_history()

    # 2. Obtener contexto fresco de intervals.icu
    live_context = await get_live_context()
    system_with_context = BASE_SYSTEM + live_context

    # 3. Añadir mensaje nuevo al historial
    history.append({"role": "user", "content": req.message})

    # 4. Llamar a Claude con historial completo
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
                "system": system_with_context,
                "messages": history
            }
        )
        if r.status_code != 200:
            raise HTTPException(
                status_code=r.status_code,
                detail=f"Anthropic error: {r.text[:500]}"
            )
        reply = r.json()["content"][0]["text"]

    # 5. Guardar historial actualizado
    history.append({"role": "assistant", "content": reply})
    save_history(history)

    return {"reply": reply}

@app.delete("/chat/history")
async def clear_chat_history():
    clear_history()
    return {"status": "Historial borrado"}


@app.get("/events")
async def get_events(days_back: int = 2, days_forward: int = 75):
    today = datetime.now()
    oldest = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
    newest = (today + timedelta(days=days_forward)).strftime("%Y-%m-%d")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{INTERVALS_URL}/athlete/{ATHLETE_ID}/events",
            auth=("API_KEY", INTERVALS_KEY),
            params={"oldest": oldest, "newest": newest}
        )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        data = r.json()
        # Always return array
        if isinstance(data, dict):
            return [data]
        return data if data else []

@app.get("/test-chat")
async def test_chat():
    live = await get_live_context()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Di hola y dime mi CTL actual"}],
                "system": BASE_SYSTEM + live
            }
        )
        return {"status_code": r.status_code, "live_context": live, "response": r.json()}


