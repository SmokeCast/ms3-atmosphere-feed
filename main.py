import os
import json
import re
from urllib.request import urlopen
from urllib.error import HTTPError, URLError
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from fastapi import FastAPI, HTTPException, Path as PathParam, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from dotenv import load_dotenv

# Cargar variables de entorno desde .env si existe
load_dotenv(Path(__file__).with_name(".env"))

PORT = int(os.getenv("PORT", 8083))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "db3_atmosphere_feed")
WEATHER_COLLECTION = os.getenv("WEATHER_COLLECTION", "weather_readings")
MS2_URL = os.getenv("MS2_URL", "http://127.0.0.1:8082").rstrip("/")

@asynccontextmanager
async def lifespan(app):
    yield
    client.close()


app = FastAPI(
    lifespan=lifespan,
    title="Ms3 - Atmosphere Feed API",
    description="Consulta de lecturas meteorológicas almacenadas de SmokeCast",
    version="1.0.0"
)

# Habilitar CORS para permitir consumo desde el frontend (Amplify / React) u otros servicios
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def mongo_auth_options(env):
    username = env.get("MONGO_ROOT_USERNAME", "")
    password = env.get("MONGO_ROOT_PASSWORD", "")
    if bool(username) != bool(password):
        raise ValueError("Completa MONGO_ROOT_USERNAME y MONGO_ROOT_PASSWORD juntos en .env")
    if not username:
        return {}
    return {
        "username": username,
        "password": password,
        "authSource": env.get("MONGO_AUTH_SOURCE") or "admin",
    }


# Credenciales separadas: PyMongo admite caracteres especiales sin codificar una URI.
client = MongoClient(
    MONGO_URI,
    **mongo_auth_options(os.environ),
    serverSelectionTimeoutMS=3000,
    connectTimeoutMS=3000,
    socketTimeoutMS=5000,
    connect=False,
)
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
    except PyMongoError:
        mongo_ok = False
        total_records = 0

    return JSONResponse(status_code=200 if mongo_ok else 503, content={
        "status": "ok" if mongo_ok else "degraded",
        "service": "ms3-atmosphere-feed",
        "port": PORT,
        "database": MONGO_DB_NAME,
        "collection": WEATHER_COLLECTION,
        "mongo_connected": mongo_ok,
        "total_records": total_records
    })


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
    except PyMongoError:
        raise HTTPException(status_code=503, detail="No se pudo consultar MongoDB")


@app.get("/api/weather/overview", tags=["Clima"])
def weather_overview(
    limit: int = Query(default=48, ge=1, le=500),
    skip: int = Query(default=0, ge=0),
    country: str = Query(default="", max_length=80),
    city: str = Query(default="", max_length=120),
):
    """Última lectura por localidad, filtros y paginación sobre localidades únicas."""
    match = {}
    if country.strip():
        match["city_country"] = country.strip()
    if city.strip():
        match["city_name"] = {"$regex": re.escape(city.strip()), "$options": "i"}
    filtered = [{"$match": match}] if match else []
    pipeline = [
        {"$sort": {"city_id": 1, "timestamp": -1, "_id": -1}},
        {"$group": {"_id": "$city_id", "reading": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$reading"}},
        {"$facet": {
            "data": filtered + [{"$sort": {"city_name": 1, "city_id": 1}},
                                 {"$skip": skip}, {"$limit": limit}, {"$project": {"_id": 0}}],
            "total": filtered + [{"$count": "value"}],
            "countries": [{"$group": {"_id": "$city_country"}}, {"$sort": {"_id": 1}}],
        }},
    ]
    try:
        result = next(iter(collection.aggregate(pipeline, allowDiskUse=True)), {})
        total = result.get("total", [])
        return {"data": result.get("data", []), "total": total[0]["value"] if total else 0,
                "countries": [c["_id"] for c in result.get("countries", []) if c.get("_id")]}
    except PyMongoError:
        raise HTTPException(status_code=503, detail="No se pudo consultar MongoDB")


@app.get("/api/weather/city/{city_id}", tags=["Clima"])
def get_weather_by_city(
    city_id: int = PathParam(..., ge=1),
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
    except PyMongoError:
        raise HTTPException(status_code=503, detail="No se pudo consultar MongoDB")


@app.get("/api/weather/latest", tags=["Clima"])
def get_latest_weather(
    city_id: int = Query(..., ge=1, description="ID de la ciudad a consultar")
):
    """
    Retorna la lectura climática más reciente para una ciudad.
    Endpoint previsto para el cálculo de dispersión de humo de MS4.
    """
    try:
        doc = collection.find_one({"city_id": city_id}, {"_id": 0}, sort=[("timestamp", -1)])
        if not doc:
            raise HTTPException(status_code=404, detail=f"No se encontró clima reciente para la ciudad con ID {city_id}")
        return doc
    except HTTPException:
        raise
    except PyMongoError:
        raise HTTPException(status_code=503, detail="No se pudo consultar MongoDB")


@app.get("/api/weather/city/{city_id}/summary", tags=["Clima"])
def get_city_summary(city_id: int = PathParam(..., ge=1)):
    """Combina el catálogo de MS2 con la última lectura almacenada en MS3."""
    try:
        with urlopen(f"{MS2_URL}/api/cities/{city_id}", timeout=5) as response:
            city = json.load(response)
        if not isinstance(city, dict) or city.get("id") != city_id:
            raise ValueError("Respuesta de MS2 inválida")
    except HTTPError as error:
        error.close()
        if error.code == 404:
            raise HTTPException(status_code=404, detail="Ciudad no encontrada en MS2") from error
        raise HTTPException(status_code=502, detail="MS2 no está disponible") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise HTTPException(status_code=502, detail="No se pudo consultar MS2") from error
    return {"city": city, "weather": get_latest_weather(city_id)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=PORT)
