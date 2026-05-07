from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient

# 1. INCOLLA QUI LA TUA STRINGA
# Ricorda di sostituire <password> con la password vera che hai appena creato!
MONGO_DETAILS = "mongodb+srv://lorenzotesi:Zekrom03!@progettotesi.eyjcybv.mongodb.net/?appName=ProgettoTesi"

client = AsyncIOMotorClient(MONGO_DETAILS)
database = client.sistema_eventi
collection = database.get_collection("eventi_osservati")

app = FastAPI()
# 1. Definiamo lo schema dei dati (Pydantic)
class Event(BaseModel):
    timestamp: datetime
    camera_id: str
    location: str
    description: str
    event_type: str

# 2. Creiamo l'applicazione (FastAPI)
app = FastAPI()

@app.get("/")
def home():
    return {"message": "Sistema di archiviazione eventi attivo"}

@app.post("/events")
def create_event(event: Event):
    # Qui in futuro aggiungerai il codice per salvare su MongoDB
    print(f"Ricevuto evento: {event.description} da {event.camera_id}")
    return {"status": "success", "received": event}