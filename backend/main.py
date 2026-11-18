"""
GEOVISOR DE PUNTOS DE CONTROL TOPOGRÁFICO - QUINDÍO
Backend (API REST): FastAPI + Psycopg 3 + PostgreSQL/PostGIS

Ejecutar desde la carpeta raíz del proyecto:
    uvicorn backend.main:app --reload
Visor:            http://127.0.0.1:8000
Documentación:    http://127.0.0.1:8000/docs

Pregunta geográfica que resuelve el proyecto:
    ¿Con qué puntos de control cuenta un levantamiento topográfico en el Quindío,
    en qué estado están, cuáles sirven de amarre para una ubicación o una zona de
    proyecto y qué municipios tienen menor cobertura de la red?

Sistema de referencia:
    Toda la geodatabase está en EPSG:9377 (MAGNA-SIRGAS / Origen-Nacional, metros).
    Las distancias, áreas, perímetros y azimutes se calculan en ese plano.
    Solo al construir el GeoJSON de salida se transforma a WGS 84 (EPSG:4326),
    porque el estándar GeoJSON (RFC 7946) y Leaflet trabajan en longitud/latitud.
"""
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Optional

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from pydantic import BaseModel

# =====================================================================
# 1. CONFIGURACIÓN (las credenciales viven en .env, fuera del repositorio)
# =====================================================================
RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

CONFIG_BD = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "geovisor_control"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "connect_timeout": 5,
}

SRID = 9377                      # MAGNA-SIRGAS / Origen-Nacional
MARGEN_M = 5000                  # tolerancia alrededor del límite departamental para aceptar una ubicación
Estado = Literal["Bueno", "Deteriorado", "Destruido"]

# Ubicación de entrada en EPSG:9377: llega como Este/Norte o como lon/lat (clic en Leaflet) y se transforma
SQL_ORIGEN = """
    CASE WHEN %(este)s::float8 IS NOT NULL
         THEN ST_SetSRID(ST_MakePoint(%(este)s, %(norte)s), 9377)
         ELSE ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 9377)
    END
"""

app = FastAPI(
    title="API · Red de puntos de control topográfico del Quindío",
    description="Servicios web que conectan el geovisor Leaflet con la geodatabase PostgreSQL/PostGIS (EPSG:9377).",
    version="3.0.0",
)


# =====================================================================
# 2. ACCESO A DATOS Y CONSTRUCCIÓN DE RESPUESTAS
# =====================================================================
def consultar(sql: str, parametros: Any = None) -> list[dict]:
    """Ejecuta SQL parametrizado (nunca concatenado) y devuelve filas como diccionarios."""
    try:
        with psycopg.connect(**CONFIG_BD, row_factory=dict_row) as conexion:
            with conexion.cursor() as cursor:
                cursor.execute(sql, parametros)
                return cursor.fetchall()
    except psycopg.OperationalError:
        raise HTTPException(status_code=503, detail="No hay conexión con la base de datos PostgreSQL/PostGIS.")
    except psycopg.Error as error:
        raise HTTPException(status_code=400, detail=f"Error al ejecutar la consulta: {error.diag.message_primary}")


def limpiar(valor: Any) -> Any:
    """Convierte tipos de PostgreSQL (numeric, date) a tipos JSON."""
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, date):
        return valor.isoformat()
    return valor


def feature_collection(filas: list[dict], campo_geom: str = "geometria", resumen: Optional[dict] = None) -> JSONResponse:
    """Arma una FeatureCollection GeoJSON a partir de filas con geometría en texto (ST_AsGeoJSON)."""
    features = []
    for fila in filas:
        geometria = fila.pop(campo_geom)
        features.append({
            "type": "Feature",
            "geometry": json.loads(geometria) if geometria else None,
            "properties": {k: limpiar(v) for k, v in fila.items()},
        })
    contenido = {"type": "FeatureCollection", "features": features}
    if resumen is not None:
        contenido["resumen"] = resumen
    return JSONResponse(content=contenido, media_type="application/geo+json")


def parametros_origen(lon, lat, este, norte) -> dict:
    """Valida que la ubicación llegue completa y dentro del Quindío (con margen de 5 km)."""
    if este is not None and norte is not None:
        p = {"este": este, "norte": norte, "lon": None, "lat": None}
    elif lon is not None and lat is not None:
        p = {"este": None, "norte": None, "lon": lon, "lat": lat}
    else:
        raise HTTPException(status_code=422, detail="Indique la ubicación como Este/Norte (EPSG:9377) o como lon/lat.")
    dentro = consultar(f"SELECT ST_DWithin(d.geom, {SQL_ORIGEN}, %(margen)s) AS ok FROM departamento d;",
                       {**p, "margen": MARGEN_M})
    if not dentro or not dentro[0]["ok"]:
        raise HTTPException(status_code=422, detail="La ubicación está fuera del área de trabajo (Quindío).")
    return p


# =====================================================================
# 3. SERVICIOS DE APOYO
# =====================================================================
@app.get("/api/salud", tags=["Apoyo"])
def salud():
    """Verifica que la API responde y que PostgreSQL/PostGIS está disponible."""
    fila = consultar("""
        SELECT current_database() AS base, postgis_lib_version() AS postgis,
               (SELECT srid FROM geometry_columns WHERE f_table_name = 'punto_control') AS srid
    """)[0]
    return {"estado": "operativo", **fila}


@app.get("/api/catalogos", tags=["Apoyo"])
def catalogos():
    """Listas para los filtros del visor, leídas desde la geodatabase (no escritas a mano en el frontend)."""
    tipos = consultar("SELECT id_tipo, nombre FROM tipo_punto ORDER BY id_tipo;")
    municipios = consultar("SELECT cod_municipio, nombre FROM municipio ORDER BY nombre;")
    return {"tipos": tipos, "municipios": municipios, "estados": ["Bueno", "Deteriorado", "Destruido"]}


# =====================================================================
# F1. VISUALIZACIÓN GEOGRÁFICA: capas consultadas en PostGIS
# =====================================================================
@app.get("/api/departamento", tags=["F1 · Visualización"])
def capa_departamento():
    """Límite departamental del Quindío con su área en el plano EPSG:9377."""
    filas = consultar("""
        SELECT d.cod_departamento, d.nombre,
               ROUND((ST_Area(d.geom) / 1e6)::numeric, 2) AS area_km2,
               COUNT(m.cod_municipio) AS municipios,
               ST_AsGeoJSON(ST_Transform(d.geom, 4326), 7) AS geometria
          FROM departamento d
          LEFT JOIN municipio m ON m.cod_departamento = d.cod_departamento
         GROUP BY d.cod_departamento, d.nombre, d.geom;
    """)
    return feature_collection(filas)


@app.get("/api/municipios", tags=["F1 · Visualización"])
def capa_municipios():
    """Límites municipales con su área en el plano EPSG:9377 (km²)."""
    filas = consultar("""
        SELECT m.cod_municipio, m.nombre, d.nombre AS departamento,
               ROUND((ST_Area(m.geom) / 1e6)::numeric, 2) AS area_km2,
               ST_AsGeoJSON(ST_Transform(m.geom, 4326), 7) AS geometria
          FROM municipio m
          JOIN departamento d ON d.cod_departamento = m.cod_departamento
         ORDER BY m.cod_municipio;
    """)
    return feature_collection(filas)


@app.get("/api/puntos", tags=["F1 · Visualización"])
def capa_puntos():
    """Red completa de puntos de control con su tipo, estado vigente y coordenadas Este/Norte (EPSG:9377)."""
    filas = consultar("""
        SELECT p.codigo, t.nombre AS tipo, p.id_tipo, p.orden, p.estado,
               ROUND(ST_X(p.geom)::numeric, 3) AS este, ROUND(ST_Y(p.geom)::numeric, 3) AS norte,
               ST_AsGeoJSON(ST_Transform(p.geom, 4326), 8) AS geometria
          FROM punto_control p
          JOIN tipo_punto t ON t.id_tipo = p.id_tipo
         ORDER BY p.codigo;
    """)
    return feature_collection(filas)


# =====================================================================
# F2. CONSULTA DE ATRIBUTOS: búsqueda por criterios y ficha técnica
# =====================================================================
@app.get("/api/puntos/buscar", tags=["F2 · Consulta de atributos"])
def buscar_puntos(
    tipo: Optional[int] = Query(None, ge=1, le=3, description="id_tipo"),
    estado: Optional[Estado] = Query(None),
    orden_max: Optional[int] = Query(None, ge=1, le=4, description="Orden máximo (1 = mayor jerarquía)"),
    municipio: Optional[str] = Query(None, pattern=r"^\d{5}$", description="Código DIVIPOLA"),
    texto: Optional[str] = Query(None, max_length=20, description="Parte del código del punto"),
):
    """Filtro combinado sobre punto_control ⋈ tipo_punto ⋈ municipio. Todos los criterios son opcionales."""
    filas = consultar("""
        SELECT p.codigo, t.nombre AS tipo, p.orden, p.estado, p.altura_m,
               m.nombre AS municipio,
               ROUND(ST_X(p.geom)::numeric, 3) AS este, ROUND(ST_Y(p.geom)::numeric, 3) AS norte,
               ST_AsGeoJSON(ST_Transform(p.geom, 4326), 8) AS geometria
          FROM punto_control p
          JOIN tipo_punto t ON t.id_tipo = p.id_tipo
          JOIN municipio  m ON m.cod_municipio = p.cod_municipio
         WHERE (%(tipo)s::smallint   IS NULL OR p.id_tipo = %(tipo)s)
           AND (%(estado)s::text     IS NULL OR p.estado = %(estado)s)
           AND (%(orden)s::smallint  IS NULL OR p.orden <= %(orden)s)
           AND (%(mpio)s::text       IS NULL OR p.cod_municipio = %(mpio)s)
           AND (%(texto)s::text      IS NULL OR p.codigo ILIKE '%%' || %(texto)s || '%%')
         ORDER BY p.orden, p.codigo;
    """, {"tipo": tipo, "estado": estado, "orden": orden_max, "mpio": municipio, "texto": texto})
    return feature_collection(filas, resumen={"total": len(filas)})


@app.get("/api/puntos/{codigo}", tags=["F2 · Consulta de atributos"])
def ficha_punto(codigo: str):
    """Ficha técnica de un punto: atributos, coordenadas Este/Norte (EPSG:9377) e historial de inspecciones."""
    filas = consultar("""
        SELECT p.codigo, t.nombre AS tipo, t.descripcion AS descripcion_tipo, p.orden, p.estado,
               p.altura_m, p.fecha_materializacion, m.nombre AS municipio, d.nombre AS departamento,
               ROUND(ST_X(p.geom)::numeric, 3) AS este,
               ROUND(ST_Y(p.geom)::numeric, 3) AS norte,
               ST_AsGeoJSON(ST_Transform(p.geom, 4326), 8) AS geometria
          FROM punto_control p
          JOIN tipo_punto   t ON t.id_tipo = p.id_tipo
          JOIN municipio    m ON m.cod_municipio = p.cod_municipio
          JOIN departamento d ON d.cod_departamento = m.cod_departamento
         WHERE p.codigo = %(codigo)s;
    """, {"codigo": codigo})
    if not filas:
        raise HTTPException(status_code=404, detail=f"No existe el punto {codigo}.")
    historial = consultar("""
        SELECT i.fecha, i.estado_encontrado, i.observacion
          FROM inspeccion i
          JOIN punto_control p ON p.id_punto = i.id_punto
         WHERE p.codigo = %(codigo)s
         ORDER BY i.fecha DESC;
    """, {"codigo": codigo})
    return feature_collection(filas, resumen={"inspecciones": [{k: limpiar(v) for k, v in h.items()} for h in historial]})


# =====================================================================
# F3. CONSULTA ESPACIAL: puntos de amarre para una ubicación
# =====================================================================
@app.get("/api/amarre", tags=["F3 · Consulta espacial"])
def puntos_de_amarre(
    este: Optional[float] = Query(None, description="Este en EPSG:9377 (m)"),
    norte: Optional[float] = Query(None, description="Norte en EPSG:9377 (m)"),
    lon: Optional[float] = Query(None, ge=-180, le=180, description="Longitud WGS 84 (clic en el mapa)"),
    lat: Optional[float] = Query(None, ge=-90, le=90, description="Latitud WGS 84 (clic en el mapa)"),
    n: int = Query(3, ge=1, le=10),
    solo_buenos: bool = Query(True, description="Excluir puntos deteriorados o destruidos"),
):
    """
    Los n puntos de control más cercanos a la ubicación de un levantamiento, en el plano EPSG:9377.
    - Ordenamiento KNN con el operador <-> sobre geometrías en metros (usa el índice GiST).
    - Distancia plana (ST_Distance) y azimut de cuadrícula (ST_Azimuth) en EPSG:9377.
    - Distancia geodésica sobre el elipsoide, como comparación de la deformación de la proyección.
    """
    p = parametros_origen(lon, lat, este, norte)
    filas = consultar(f"""
        WITH origen AS (SELECT {SQL_ORIGEN} AS g)
        SELECT p.codigo, t.nombre AS tipo, p.orden, p.estado,
               ROUND(ST_X(p.geom)::numeric, 3)                                      AS este,
               ROUND(ST_Y(p.geom)::numeric, 3)                                      AS norte,
               ROUND(ST_Distance(o.g, p.geom)::numeric, 3)                          AS distancia_plana_m,
               ROUND(degrees(ST_Azimuth(o.g, p.geom))::numeric, 4)                  AS azimut_cuadricula_grados,
               ROUND(ST_Distance(ST_Transform(o.g, 4326)::geography,
                                 ST_Transform(p.geom, 4326)::geography)::numeric, 3) AS distancia_geodesica_m,
               ST_AsGeoJSON(ST_Transform(p.geom, 4326), 8)                          AS geometria,
               ST_AsGeoJSON(ST_Transform(ST_MakeLine(o.g, p.geom), 4326), 8)        AS linea
          FROM punto_control p
          JOIN tipo_punto t ON t.id_tipo = p.id_tipo
         CROSS JOIN origen o
         WHERE (NOT %(solo_buenos)s OR p.estado = 'Bueno')
         ORDER BY p.geom <-> o.g
         LIMIT %(n)s;
    """, {**p, "n": n, "solo_buenos": solo_buenos})
    for fila in filas:
        fila["linea"] = json.loads(fila["linea"])
    origen = consultar(f"""
        WITH o AS (SELECT {SQL_ORIGEN} AS g)
        SELECT m.nombre AS municipio,
               ROUND(ST_X(o.g)::numeric, 3) AS este,
               ROUND(ST_Y(o.g)::numeric, 3) AS norte,
               ST_AsGeoJSON(ST_Transform(o.g, 4326), 8) AS punto
          FROM o LEFT JOIN municipio m ON ST_Covers(m.geom, o.g)
         LIMIT 1;
    """, p)[0]
    origen["punto"] = json.loads(origen["punto"])
    return feature_collection(filas, resumen={k: limpiar(v) for k, v in origen.items()})


# =====================================================================
# F4. FUNCIONALIDAD COMPLEMENTARIA: análisis de una zona de proyecto dibujada
# =====================================================================
class ZonaProyecto(BaseModel):
    geometry: dict  # GeoJSON Polygon en WGS 84 (así lo entrega Leaflet.draw); se transforma a EPSG:9377


SQL_ZONA = "ST_Transform(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%(g)s), 4326)), 9377)"


@app.post("/api/zona/analisis", tags=["F4 · Complementaria"])
def analizar_zona(zona: ZonaProyecto):
    """
    Recibe el polígono de una zona de proyecto, lo transforma a EPSG:9377 y calcula:
    área y perímetro planos, puntos de control contenidos (ST_Covers) por estado
    y reparto del área entre municipios (ST_Intersection + ST_Area).
    """
    if zona.geometry.get("type") != "Polygon":
        raise HTTPException(status_code=422, detail="Se espera una geometría GeoJSON de tipo Polygon.")
    g = json.dumps(zona.geometry)
    resumen = consultar(f"""
        WITH z AS (SELECT {SQL_ZONA} AS g)
        SELECT ROUND((ST_Area(z.g) / 1e6)::numeric, 4)                   AS area_km2,
               ROUND(ST_Perimeter(z.g)::numeric, 2)                        AS perimetro_m,
               COUNT(p.id_punto)                                           AS total_puntos,
               COUNT(p.id_punto) FILTER (WHERE p.estado = 'Bueno')         AS puntos_utilizables
          FROM z LEFT JOIN punto_control p ON ST_Covers(z.g, p.geom)
         GROUP BY z.g;
    """, {"g": g})[0]
    if resumen["area_km2"] > 2500:
        raise HTTPException(status_code=422, detail="La zona es demasiado grande (máximo 2 500 km²).")
    municipios = consultar(f"""
        WITH z AS (SELECT {SQL_ZONA} AS g)
        SELECT m.nombre AS municipio,
               ROUND((ST_Area(ST_Intersection(m.geom, z.g)) / 1e6)::numeric, 4)                      AS area_km2,
               ROUND((100 * ST_Area(ST_Intersection(m.geom, z.g)) / NULLIF(ST_Area(z.g), 0))::numeric, 1) AS porcentaje
          FROM municipio m CROSS JOIN z
         WHERE ST_Intersects(m.geom, z.g)
         ORDER BY area_km2 DESC;
    """, {"g": g})
    puntos = consultar(f"""
        WITH z AS (SELECT {SQL_ZONA} AS g)
        SELECT p.codigo, t.nombre AS tipo, p.orden, p.estado,
               ROUND(ST_X(p.geom)::numeric, 3) AS este, ROUND(ST_Y(p.geom)::numeric, 3) AS norte,
               ST_AsGeoJSON(ST_Transform(p.geom, 4326), 8) AS geometria
          FROM punto_control p
          JOIN tipo_punto t ON t.id_tipo = p.id_tipo
         CROSS JOIN z
         WHERE ST_Covers(z.g, p.geom)
         ORDER BY p.estado, p.codigo;
    """, {"g": g})
    return feature_collection(puntos, resumen={
        **{k: limpiar(v) for k, v in resumen.items()},
        "municipios": [{k: limpiar(v) for k, v in m.items()} for m in municipios],
    })


# =====================================================================
# F5 (adicional). Cobertura de la red por municipio: mapa temático
# =====================================================================
@app.get("/api/municipios/cobertura", tags=["F5 · Adicional"])
def cobertura_municipal(estado: Optional[Estado] = Query(None, description="Contar solo puntos en este estado (vacío = todos)")):
    """Puntos por cada 100 km² en cada municipio (unión espacial ST_Covers + COUNT + ST_Area en EPSG:9377)."""
    filas = consultar("""
        SELECT m.cod_municipio, m.nombre AS municipio,
               ROUND((ST_Area(m.geom) / 1e6)::numeric, 2) AS area_km2,
               COUNT(p.id_punto) AS total_puntos,
               ROUND((COUNT(p.id_punto) / (ST_Area(m.geom) / 1e8))::numeric, 2) AS puntos_100km2,
               ST_AsGeoJSON(ST_Transform(m.geom, 4326), 7) AS geometria
          FROM municipio m
          LEFT JOIN punto_control p
                 ON ST_Covers(m.geom, p.geom)
                AND (%(estado)s::text IS NULL OR p.estado = %(estado)s)
         GROUP BY m.cod_municipio, m.nombre, m.geom
         ORDER BY puntos_100km2 DESC;
    """, {"estado": estado})
    return feature_collection(filas, resumen={"estado": estado or "Todos"})


# =====================================================================
# 4. PUBLICACIÓN DEL FRONTEND (el mismo servidor entrega el visor)
# =====================================================================
app.mount("/static", StaticFiles(directory=RAIZ / "frontend"), name="static")


@app.get("/", include_in_schema=False)
def inicio():
    return FileResponse(RAIZ / "frontend" / "index.html")
