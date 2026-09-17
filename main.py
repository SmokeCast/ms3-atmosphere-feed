import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from dotenv import load_dotenv

# Cargar variables de entorno desde .env si existe
load_dotenv()

PORT = int(os.getenv("PORT", 8083))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "atmosphere_feed")
WEATHER_COLLECTION = os.getenv("WEATHER_COLLECTION", "weather_readings")

app = FastAPI(
    title="Ms3 - Atmosphere Feed API",
    description="Microservicio de clima y calidad de aire para SmokeCast (Persona 3)",
    version="1.0.0"
)

# Habilitar CORS para permitir consumo desde el frontend (Amplify / React) u otros servicios
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conexión a MongoDB
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = client[MONGO_DB_NAME]
collection = db[WEATHER_COLLECTION]


@app.get("/health", tags=["Monitoreo"])
def health_check():
    """
    Verifica el estado de salud del microservicio y la conexión a MongoDB.
    """
    try:
        # Ping a MongoDB para asegurar que responde
        client.admin.command("ping")
        mongo_ok = True
        total_records = collection.count_documents({})
    except Exception as e:
        mongo_ok = False
        total_records = 0

    return {
        "status": "ok" if mongo_ok else "degraded",
        "service": "ms3-atmosphere-feed",
        "port": PORT,
        "database": MONGO_DB_NAME,
        "collection": WEATHER_COLLECTION,
        "mongo_connected": mongo_ok,
        "total_records": total_records
    }


@app.get("/api/weather", tags=["Clima"])
def get_all_weather(
    limit: int = Query(default=50, ge=1, le=500, description="Cantidad máxima de lecturas a retornar"),
    skip: int = Query(default=0, ge=0, description="Cantidad de registros a saltar (paginación)")
):
    """
    Lista lecturas meteorológicas generales (últimas registradas).
    """
    try:
        docs = list(collection.find({}, {"_id": 0}).sort("timestamp", -1).skip(skip).limit(limit))
        return {
            "count": len(docs),
            "data": docs
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar MongoDB: {str(e)}")


@app.get("/api/weather/city/{city_id}", tags=["Clima"])
def get_weather_by_city(
    city_id: int,
    limit: int = Query(default=24, ge=1, le=168, description="Horas de historial (por defecto 24h)")
):
    """
    Retorna el historial meteorológico horario más reciente para una ciudad específica.
    """
    try:
        docs = list(
            collection.find({"city_id": city_id}, {"_id": 0})
            .sort("timestamp", -1)
            .limit(limit)
        )
        if not docs:
            raise HTTPException(status_code=404, detail=f"No se encontraron registros de clima para la ciudad con ID {city_id}")
        return {
            "city_id": city_id,
            "city_name": docs[0].get("city_name"),
            "count": len(docs),
            "readings": docs
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar MongoDB: {str(e)}")


@app.get("/api/weather/latest", tags=["Clima"])
def get_latest_weather(
    city_id: int = Query(..., description="ID de la ciudad a consultar")
):
    """
    Retorna la lectura climática más reciente para una ciudad.
    Endpoint clave consumido por Ms4 (smoke-brain) para evaluar la dispersión de humo.
    """
    try:
        doc = collection.find_one({"city_id": city_id}, {"_id": 0}, sort=[("timestamp", -1)])
        if not doc:
            raise HTTPException(status_code=404, detail=f"No se encontró clima reciente para la ciudad con ID {city_id}")
        return doc
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar MongoDB: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
