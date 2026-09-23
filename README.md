# MS3 — Atmosphere Feed

API FastAPI con MongoDB 7. Puerto predeterminado: 8083.

## Ejecución local

Python 3.11 es la versión de referencia. Desde esta carpeta:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# Solo si todavía no existe .env:
cp env.example .env
# Editar .env con la configuración local.
python main.py
```

En Windows, activar con `.venv\Scripts\activate`.
Los paquetes de `requirements.txt` corresponden únicamente a la API.
Swagger UI está disponible en `/docs` y el contrato en `/openapi.json`.

## Configuración y datos

Se necesita MongoDB accesible. Configurar en `.env`:

- `MONGO_URI`: dirección del servidor, por ejemplo `mongodb://127.0.0.1:27017`.
- `MONGO_ROOT_USERNAME` y `MONGO_ROOT_PASSWORD`: credenciales del usuario existente.
  Deben rellenarse juntas; una configuración incompleta impide el arranque con un mensaje claro.
- `MONGO_AUTH_SOURCE=admin`: base donde se autentica el usuario creado por el Compose.
- `MONGO_DB_NAME=db3_atmosphere_feed`: base que contiene los datos.
- `WEATHER_COLLECTION=weather_readings`: colección consultada.

El `.env` local contiene las credenciales configuradas; `env.example` deja la contraseña
vacía. PyMongo recibe usuario y contraseña como parámetros, sin necesidad de escapar
caracteres especiales. Estas variables conectan al usuario existente; no crean usuarios.
Si se utilizan credenciales dentro de `MONGO_URI`, dejar ambas variables de usuario y
contraseña vacías; cuando están presentes, los parámetros separados tienen prioridad.
Para un MongoDB sin autenticación, usar una URI sin credenciales y dejar ambas vacías.
La API consulta datos existentes; el endpoint bulk permite cargar hasta 10 000
lecturas por solicitud y no consulta Open-Meteo.
`MS2_URL` se usa únicamente en el endpoint de resumen.

Documento esperado: `city_id` entero, `city_name`, `latitude`, `longitude`,
`timestamp` como texto ISO de formato consistente, `wind_speed_kmh`,
`wind_direction_deg` y `temperature_c`. Los campos de partículas son opcionales.
Se conserva el formato temporal existente; unificar zona horaria queda pendiente.

Para las consultas por ciudad y fecha, crear con un cliente MongoDB:

```javascript
use db3_atmosphere_feed
db.weather_readings.createIndex({city_id: 1, timestamp: -1})
db.weather_readings.createIndex({timestamp: -1})
```

## Endpoints

- `GET /health`: 200 con MongoDB disponible; 503 si no responde.
- `GET /api/v1/weather?page=0&size=50`: `{count, data}`; máximo 500 lecturas.
- `GET /api/v1/weather/city/{city_id}?limit=24`: `{city_id, city_name, count, readings}`.
- `GET /api/v1/weather/latest?city_id=1`: última lectura almacenada de esa ciudad.
- `POST /api/v1/weather/bulk`: inserta hasta 10 000 lecturas meteorológicas.
- `GET /api/v1/weather/city/{city_id}/summary`: `{city, weather}`; consulta la ciudad
  en MS2 y combina su información con la última lectura local. Así el consumo
  entre microservicios no depende de un seed.

Sin lecturas, el listado devuelve datos vacíos y las consultas individuales 404.
MongoDB no disponible devuelve 503. El resumen devuelve 502 si no puede consultar MS2.
El endpoint bulk recibe `{ "items": [ ... ] }` y devuelve los IDs Mongo insertados.

Para desarrollo con recarga: `python -m uvicorn main:app --reload --port 8083`.
Con ese comando, el puerto se especifica en la CLI; `python main.py` usa `.env`.

## Pruebas

`python -m pip install -r requirements-dev.txt` y `python -m unittest -v`.
Las pruebas simulan MongoDB/MS2 para verificar errores y contratos sin datos externos.

`GET /api/v1/weather/overview?page=0&size=48&country=Per%C3%BA` devuelve la última
lectura por localidad, `total` de localidades filtradas y `countries` de todo el
catálogo meteorológico. La agrupación precede a la paginación; el listado histórico
`/api/v1/weather` permanece disponible. País procede de `city_country` en las lecturas.

FastAPI publica Swagger UI en `http://127.0.0.1:8083/docs` y el esquema OpenAPI en
`http://127.0.0.1:8083/openapi.json`.
