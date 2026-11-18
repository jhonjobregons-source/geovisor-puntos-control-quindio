"""
Prueba automática de los endpoints de la API y contraste con PostgreSQL.

1. Llama cada endpoint (F1 a F5 y casos de error) y guarda la respuesta JSON
   en evidencias/api/.
2. Ejecuta la MISMA operación directamente en PostgreSQL (sin pasar por la API)
   y compara los resultados campo a campo. Todas las geometrías están en EPSG:9377.

Requisitos: la API en ejecución (uvicorn backend.main:app) y el archivo .env.
Uso, desde la carpeta raíz del proyecto:
    python pruebas/probar_api.py
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import psycopg
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "evidencias" / "api"
SALIDA.mkdir(parents=True, exist_ok=True)
load_dotenv(RAIZ / ".env")
API = os.getenv("API_URL", "http://127.0.0.1:8000/api")

# Ubicación de prueba: Plaza de Bolívar de Armenia (aprox.) en EPSG:9377
ESTE, NORTE = 4702599.150, 2059542.011
# Zona de prueba (rectángulo) en WGS 84, como la enviaría Leaflet.draw
ZONA = {"type": "Polygon", "coordinates": [[[-75.75, 4.48], [-75.62, 4.48], [-75.62, 4.60],
                                             [-75.75, 4.60], [-75.75, 4.48]]]}
SQL_ZONA = "ST_Transform(ST_MakeEnvelope(-75.75, 4.48, -75.62, 4.60, 4326), 9377)"
resultados = []


def llamar(nombre, ruta, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    peticion = urllib.request.Request(API + ruta, data=datos, method="POST" if cuerpo else "GET",
                                      headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(peticion, timeout=30) as r:
            estado, contenido = r.status, json.load(r)
    except urllib.error.HTTPError as e:
        estado, contenido = e.code, json.load(e)
    (SALIDA / f"{nombre}.json").write_text(json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(contenido.get("features", [])) if isinstance(contenido, dict) else "-"
    print(f"{'POST' if cuerpo else 'GET ':4} {estado}  {ruta[:75]:<75} entidades={n}")
    return estado, contenido


def sql(consulta, parametros=None):
    with psycopg.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                         user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD")) as c:
        return c.execute(consulta, parametros).fetchall()


def comprobar(descripcion, condicion):
    resultados.append(condicion)
    print(f"   [{'OK' if condicion else 'FALLA'}] {descripcion}")


print("=" * 100)
print("1. PETICIONES HTTP A LA API")
print("=" * 100)
_, salud = llamar("00_salud", "/salud")
_, depto = llamar("F1_departamento", "/departamento")
_, mpios = llamar("F1_municipios", "/municipios")
_, puntos = llamar("F1_puntos", "/puntos")
_, buscar = llamar("F2_buscar_tipo1_bueno_orden1", "/puntos/buscar?tipo=1&estado=Bueno&orden_max=1")
_, ficha = llamar("F2_ficha_NIV-001-001", "/puntos/NIV-001-001")
_, amarre = llamar("F3_amarre_plaza_bolivar_9377", f"/amarre?este={ESTE}&norte={NORTE}&n=3&solo_buenos=true")
_, amarre_clic = llamar("F3_amarre_plaza_bolivar_clic", "/amarre?lon=-75.6811&lat=4.5339&n=3&solo_buenos=true")
_, zona = llamar("F4_zona_rectangulo_prueba", "/zona/analisis", {"geometry": ZONA})
_, cobertura = llamar("F5_cobertura_bueno", "/municipios/cobertura?estado=Bueno")
e1, _ = llamar("ERR_punto_inexistente", "/puntos/NO-EXISTE")
e2, _ = llamar("ERR_fuera_del_quindio", "/amarre?este=4880000&norte=2050000")
e3, _ = llamar("ERR_municipio_mal_formado", "/puntos/buscar?municipio=abc")
e4, _ = llamar("ERR_geometria_no_poligono", "/zona/analisis", {"geometry": {"type": "Point", "coordinates": [-75.6, 4.5]}})
e5, _ = llamar("ERR_sin_ubicacion", "/amarre?n=3")

print()
print("=" * 100)
print("2. CONTRASTE API  vs  CONSULTA DIRECTA EN POSTGRESQL")
print("=" * 100)

print("Sistema de referencia")
srids = [r[0] for r in sql("SELECT DISTINCT srid FROM geometry_columns")]
comprobar(f"todas las geometrías de la base en EPSG:9377: {srids}  (API informa {salud['srid']})",
          srids == [9377] and salud["srid"] == 9377)

print("F1 · Visualización")
n_m, n_p = sql("SELECT (SELECT COUNT(*) FROM municipio), (SELECT COUNT(*) FROM punto_control)")[0]
comprobar(f"municipios: API={len(mpios['features'])}  SQL={n_m}", len(mpios["features"]) == n_m)
comprobar(f"puntos:     API={len(puntos['features'])}  SQL={n_p}", len(puntos["features"]) == n_p)
a_d, a_m = sql("""SELECT ROUND((ST_Area(geom)/1e6)::numeric, 2),
                         (SELECT ROUND((SUM(ST_Area(geom))/1e6)::numeric, 2) FROM municipio) FROM departamento""")[0]
comprobar(f"área plana del departamento: API={depto['features'][0]['properties']['area_km2']}  SQL={a_d}  "
          f"(suma de municipios {a_m})", depto["features"][0]["properties"]["area_km2"] == float(a_d)
          and abs(float(a_d) - float(a_m)) < 0.05)

print("F2 · Consulta de atributos")
filas = sql("""SELECT codigo FROM punto_control WHERE id_tipo = 1 AND estado = 'Bueno' AND orden <= 1
               ORDER BY orden, codigo""")
api_cod = [f["properties"]["codigo"] for f in buscar["features"]]
comprobar(f"búsqueda: API={api_cod}\n         SQL={[f[0] for f in filas]}", api_cod == [f[0] for f in filas])
e, n = sql("SELECT ROUND(ST_X(geom)::numeric, 3), ROUND(ST_Y(geom)::numeric, 3) FROM punto_control WHERE codigo = 'NIV-001-001'")[0]
p = ficha["features"][0]["properties"]
comprobar(f"ficha Este/Norte: API=({p['este']}, {p['norte']})  SQL=({e}, {n})", (p["este"], p["norte"]) == (float(e), float(n)))
n_i = sql("SELECT COUNT(*) FROM inspeccion i JOIN punto_control p USING (id_punto) WHERE p.codigo = 'NIV-001-001'")[0][0]
comprobar(f"inspecciones: API={len(ficha['resumen']['inspecciones'])}  SQL={n_i}", len(ficha["resumen"]["inspecciones"]) == n_i)

print("F3 · Consulta espacial")
filas = sql("""WITH o AS (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 9377) AS g)
               SELECT p.codigo, ROUND(ST_Distance(o.g, p.geom)::numeric, 3), ROUND(degrees(ST_Azimuth(o.g, p.geom))::numeric, 4)
                 FROM punto_control p CROSS JOIN o WHERE p.estado = 'Bueno' ORDER BY 2 LIMIT 3""", (ESTE, NORTE))
api_f3 = [(f["properties"]["codigo"], f["properties"]["distancia_plana_m"], f["properties"]["azimut_cuadricula_grados"])
          for f in amarre["features"]]
sql_f3 = [(c, float(d), float(a)) for c, d, a in filas]
comprobar(f"3 más cercanos (orden por distancia exacta, sin índice):\n         API={api_f3}\n         SQL={sql_f3}",
          api_f3 == sql_f3)
clic = [f["properties"]["codigo"] for f in amarre_clic["features"]]
comprobar(f"clic en el mapa (lon/lat transformado a 9377) da los mismos puntos: {clic}", clic == [c for c, _, _ in sql_f3])

print("F4 · Complementaria")
a, per, tot = sql(f"""WITH z AS (SELECT {SQL_ZONA} AS g)
                      SELECT ROUND((ST_Area(z.g)/1e6)::numeric, 4), ROUND(ST_Perimeter(z.g)::numeric, 2),
                             (SELECT COUNT(*) FROM punto_control p WHERE ST_Covers(z.g, p.geom)) FROM z""")[0]
r = zona["resumen"]
comprobar(f"área/perímetro/puntos: API=({r['area_km2']}, {r['perimetro_m']}, {r['total_puntos']})  "
          f"SQL=({a}, {per}, {tot})", (r["area_km2"], r["perimetro_m"], r["total_puntos"]) == (float(a), float(per), tot))
suma = round(sum(m["porcentaje"] for m in r["municipios"]), 1)
comprobar(f"la suma de % por municipio es ~100 % (zona dentro del Quindío): {suma} %", abs(suma - 100) <= 0.5)

print("F5 · Adicional")
filas = dict(sql("""SELECT m.nombre, COUNT(p.id_punto) FROM municipio m
                    LEFT JOIN punto_control p ON p.cod_municipio = m.cod_municipio AND p.estado = 'Bueno'
                    GROUP BY m.nombre"""))
api_f5 = {f["properties"]["municipio"]: f["properties"]["total_puntos"] for f in cobertura["features"]}
comprobar("conteo por unión espacial (API) = conteo por llave foránea (SQL) en los 12 municipios", api_f5 == filas)

print("Validaciones y errores controlados")
comprobar(f"punto inexistente -> 404 (obtenido {e1})", e1 == 404)
comprobar(f"ubicación fuera del Quindío -> 422 (obtenido {e2})", e2 == 422)
comprobar(f"código DIVIPOLA mal formado -> 422 (obtenido {e3})", e3 == 422)
comprobar(f"geometría que no es polígono -> 422 (obtenido {e4})", e4 == 422)
comprobar(f"F3 sin ubicación -> 422 (obtenido {e5})", e5 == 422)

print()
print(f"RESULTADO: {sum(resultados)} de {len(resultados)} comprobaciones correctas")
sys.exit(0 if all(resultados) else 1)
