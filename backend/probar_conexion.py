"""Script auxiliar: verifica la conexión con la geodatabase antes de levantar la API.
Uso (desde la carpeta del proyecto):  python backend/probar_conexion.py
"""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

try:
    with psycopg.connect(
        host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "geovisor_control"), user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    ) as con:
        cur = con.cursor()
        cur.execute("SELECT postgis_lib_version()")
        print("Conexión correcta. PostGIS", cur.fetchone()[0])
        for tabla in ("departamento", "municipio", "tipo_punto", "punto_control", "inspeccion"):
            cur.execute(f"SELECT COUNT(*) FROM {tabla}")
            print(f"  {tabla}: {cur.fetchone()[0]} registros")
except Exception as error:
    print("No fue posible conectar:", error)
