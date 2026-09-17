import os
import sys
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import requests
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "atmosphere_feed")
WEATHER_COLLECTION = os.getenv("WEATHER_COLLECTION", "weather_readings")
MS2_URL = os.getenv("MS2_URL", "http://localhost:8082")

# Ruta de fallback al CSV de ciudades generado por Ms2
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FALLBACK_CSV = PROJECT_ROOT / "data-ingestion" / "container02-cities" / "cities_20260911_2227.csv"

# Ciudades de muestra si no estuviera disponible ni Ms2 ni el CSV
DEFAULT_FALLBACK_CITIES = [
    {"id": 1, "name": "Lima", "latitude": -12.0464, "longitude": -77.0428},
    {"id": 2, "name": "Bogota", "latitude": 4.7110, "longitude": -74.0721},
    {"id": 3, "name": "Santiago", "latitude": -33.4489, "longitude": -70.6693},
    {"id": 4, "name": "Buenos Aires", "latitude": -34.6037, "longitude": -58.3816},
    {"id": 5, "name": "Ciudad de Mexico", "latitude": 19.4326, "longitude": -99.1332},
    {"id": 6, "name": "Quito", "latitude": -0.1807, "longitude": -78.4678},
    {"id": 7, "name": "La Paz", "latitude": -16.5000, "longitude": -68.1500},
    {"id": 8, "name": "Montevideo", "latitude": -34.9011, "longitude": -56.1645},
    {"id": 9, "name": "Asuncion", "latitude": -25.2637, "longitude": -57.5759},
    {"id": 10, "name": "Sao Paulo", "latitude": -23.5505, "longitude": -46.6333}
]


def obtain_cities(limit: int) -> list:
    """
    Intenta obtener la lista de ciudades consumiendo la API de Ms2 (GET /api/cities).
    Si Ms2 no está activo localmente, activa el modo de contingencia cargando
    el archivo CSV de ciudades reales o la lista de respaldo.
    """
    ms2_endpoint = f"{MS2_URL}/api/cities"
    print(f"[1/3] Intentando conectar con Ms2 en {ms2_endpoint}...")

    try:
        resp = requests.get(ms2_endpoint, params={"limit": limit}, timeout=4)
        if resp.status_code == 200:
            cities = resp.json()[:limit]
            print(f"  --> Exito: Se obtuvieron {len(cities)} ciudades consumiendo Ms2 en vivo (requisito cumplido).")
            return cities
        else:
            print(f"  --> Ms2 respondio con estado {resp.status_code}. Activando contingencia...")
    except requests.exceptions.RequestException as e:
        print(f"  --> No se pudo conectar a Ms2 ({type(e).__name__}). Activando contingencia...")

    # Fallback 1: Archivo CSV real de ciudades de Ms2
    if FALLBACK_CSV.exists():
        print(f"  --> Usando dataset local de contingencia: {FALLBACK_CSV.name}")
        df = pd.read_csv(FALLBACK_CSV, encoding='utf-8')
        df_sample = df.head(limit)
        cities = []
        for _, row in df_sample.iterrows():
            cities.append({
                "id": int(row["id"]),
                "name": str(row["name"]),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"])
            })
        print(f"  --> Cargadas {len(cities)} ciudades reales desde el dataset local.")
        return cities

    # Fallback 2: Lista por defecto
    print("  --> Usando lista predeterminada de ciudades principales.")
    return DEFAULT_FALLBACK_CITIES[:limit]


def fetch_open_meteo_weather(lat: float, lon: float, days: int) -> dict:
    """
    Descarga clima horario historico desde la API publica Open-Meteo Archive.
    """
    end_date = datetime.now() - timedelta(days=1)  # Ayer para datos consolidados
    start_date = end_date - timedelta(days=days - 1)

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m",
        "timezone": "auto"
    }

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def run_seed(cities_count: int, days: int, clear_existing: bool = False):
    print("=" * 65)
    print(" SmokeCast - Carga Masiva de Clima Real (Ms3: atmosphere-feed)")
    print("=" * 65)

    # Conectar a MongoDB
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB_NAME]
    collection = db[WEATHER_COLLECTION]

    if clear_existing:
        deleted = collection.delete_many({}).deleted_count
        print(f"[MongoDB] Coleccion limpiada: {deleted} registros eliminados.")

    # Crear indices para consultas optimas
    collection.create_index([("city_id", 1), ("timestamp", -1)])
    collection.create_index([("timestamp", -1)])

    # 1. Obtener ciudades (consumiendo Ms2 o via contingencia)
    cities = obtain_cities(cities_count)

    print(f"\n[2/3] Descargando clima horario de Open-Meteo para {len(cities)} ciudades ({days} dias)...")
    total_insertados = 0
    start_time = time.time()

    for idx, city in enumerate(cities, start=1):
        lat = city["latitude"]
        lon = city["longitude"]
        city_id = city["id"]
        city_name = city["name"]

        try:
            data = fetch_open_meteo_weather(lat, lon, days)
            hourly = data.get("hourly", {})
            times = hourly.get("time", [])

            docs = []
            for i, t in enumerate(times):
                temp = hourly.get("temperature_2m", [None])[i]
                wind_speed = hourly.get("wind_speed_10m", [None])[i]
                wind_dir = hourly.get("wind_direction_10m", [None])[i]

                docs.append({
                    "city_id": city_id,
                    "city_name": city_name,
                    "latitude": lat,
                    "longitude": lon,
                    "timestamp": t,
                    "wind_speed_kmh": wind_speed,
                    "wind_direction_deg": wind_dir,
                    "temperature_c": temp
                })

            if docs:
                collection.insert_many(docs)
                total_insertados += len(docs)
                print(f"  [{idx:03d}/{len(cities):03d}] {city_name:<20}: {len(docs)} lecturas guardadas.")

            # Pausa breve para respetar las buenas practicas del API de Open-Meteo
            time.sleep(0.15)

        except Exception as e:
            print(f"  [{idx:03d}/{len(cities):03d}] Error en {city_name} (ID: {city_id}): {e}")

    elapsed = time.time() - start_time
    total_en_db = collection.count_documents({})

    print("\n[3/3] Resumen de la Carga Masiva:")
    print("-" * 45)
    print(f"  * Nuevos registros insertados : {total_insertados:,}")
    print(f"  * Total documentos en MongoDB : {total_en_db:,}")
    print(f"  * Meta del Hito 1 (>=20,000)   : {'SUPERADA' if total_en_db >= 20000 else 'PENDIENTE'}")
    print(f"  * Tiempo total transcurrido   : {elapsed:.1f} segundos")
    print("-" * 45)
    print("\n[IMPORTANTE] Nota para Persona 4 (Ms4 smoke-brain):")
    print("  'wind_direction_deg' indica DE DONDE VIENE el viento.")
    print("  Para el calculo de propagacion hacia donde viaja el humo, sumar/restar 180 deg.")
    print("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed de clima real para Ms3 (atmosphere-feed)")
    parser.add_argument("--cities", type=int, default=100, help="Cantidad de ciudades a procesar (default: 100)")
    parser.add_argument("--days", type=int, default=10, help="Dias de historial por ciudad (default: 10)")
    parser.add_argument("--clear", action="store_true", help="Limpiar coleccion antes de insertar")

    args = parser.parse_args()
    run_seed(cities_count=args.cities, days=args.days, clear_existing=args.clear)
