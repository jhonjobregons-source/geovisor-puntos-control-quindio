"""
Genera sql/02_datos.sql a partir de:
  - LIMITE_QND.shp: límite departamental del Quindío (CRQ, EPSG:3115)
  - MUN_QND.shp:    límites de 11 municipios (CRQ / POMCA Quindío, EPSG:3115).
                    Armenia no viene en la capa: se obtiene como el área del
                    departamento que no cubren los otros 11 municipios.
  - una red SINTÉTICA de puntos de control y su historial de inspecciones
    (solo para pruebas académicas)

Uso:
    pip install geopandas
    python sql/herramientas/generar_datos.py carpeta_shapefiles sql/02_datos.sql

La semilla aleatoria es fija, así que el resultado siempre es el mismo.
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import shapely
from shapely.geometry import Point, MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

NOMBRES = {
    "63001": "Armenia", "63111": "Buenavista", "63130": "Calarcá",
    "63190": "Circasia", "63212": "Córdoba", "63272": "Filandia",
    "63302": "Génova", "63401": "La Tebaida", "63470": "Montenegro",
    "63548": "Pijao", "63594": "Quimbaya", "63690": "Salento",
}
# Rango de altura aproximada por municipio (m) para dar verosimilitud a los datos de prueba
ALTURAS = {
    "63001": (1350, 1650), "63111": (1100, 1700), "63130": (1300, 2600),
    "63190": (1500, 1900), "63212": (1300, 2400), "63272": (1600, 2200),
    "63302": (1400, 3000), "63401": (1050, 1300), "63470": (1100, 1400),
    "63548": (1300, 2800), "63594": (1100, 1400), "63690": (1700, 3500),
}
# La capa MUN_QND trae códigos errados en dos municipios (Buenavista 15109,
# Córdoba 52215), por eso el código DIVIPOLA se asigna a partir del nombre (campo MUN).
CODIGO_POR_NOMBRE = {
    "BUENAVISTA": "63111", "CALARCA": "63130", "CIRCASIA": "63190", "CORDOBA": "63212",
    "FILANDIA": "63272", "GENOVA": "63302", "LA TEBAIDA": "63401", "MONTENEGRO": "63470",
    "PIJAO": "63548", "QUIMBAYA": "63594", "SALENTO": "63690",
}
TIPOS = [(1, "GNSS"), (2, "NIV"), (3, "POL")]


def a_multipoligono(g):
    """Deja solo partes poligonales válidas, en 2D, y las devuelve como MultiPolygon."""
    g = make_valid(shapely.force_2d(g))
    partes = [p for p in getattr(g, "geoms", [g]) if isinstance(p, (Polygon, MultiPolygon))]
    poligonos = []
    for p in partes:
        poligonos.extend(getattr(p, "geoms", [p]))
    return MultiPolygon(poligonos)


def cargar_limites(carpeta):
    """Lee las capas de la CRQ, reconstruye Armenia y devuelve todo en EPSG:9377 (Origen Nacional)."""
    carpeta = Path(carpeta)
    depto = gpd.read_file(carpeta / "LIMITE_QND.shp").to_crs(3115)
    mpios = gpd.read_file(carpeta / "MUN_QND.shp").to_crs(3115)
    limite = make_valid(shapely.force_2d(depto.geometry.iloc[0]))   # la capa trae coordenada Z

    registros = []
    for _, fila in mpios.iterrows():
        cod = CODIGO_POR_NOMBRE[fila["MUN"].strip().upper()]
        registros.append((cod, shapely.force_2d(fila.geometry).intersection(limite)))   # recorte al límite departamental

    # Armenia = departamento - unión de los otros 11 municipios (parte principal;
    # se descartan astillas < 1 ha producidas por diferencias de digitalización)
    resto = limite.difference(unary_union([g for _, g in registros]))
    partes = sorted(getattr(resto, "geoms", [resto]), key=lambda p: -p.area)
    astillas = sum(p.area for p in partes[1:])
    registros.append(("63001", partes[0]))
    print(f"Armenia reconstruida: {partes[0].area / 1e6:.2f} km²; astillas descartadas: "
          f"{len(partes) - 1} ({astillas / 1e4:.2f} ha en total)")

    a9377 = lambda g: a_multipoligono(gpd.GeoSeries([g], crs=3115).to_crs(9377).iloc[0])
    municipios = sorted((cod, NOMBRES[cod], a9377(g)) for cod, g in registros)
    return a9377(limite), municipios

OBS = {
    "Bueno": "Placa legible y estructura firme",
    "Deteriorado": "Placa desgastada o base fisurada; usar con verificación",
    "Destruido": "No se encontró la materialización en sitio",
}


def main(entrada, salida):
    random.seed(2026)
    limite, municipios = cargar_limites(entrada)

    out = []
    out.append("-- =====================================================================")
    out.append("--  Script 02: carga de datos")
    out.append("--  * departamento y municipio: límites de la CRQ (POMCA Quindío), EPSG:3115 -> 9377;")
    out.append("--    Armenia reconstruida como departamento menos los otros 11 municipios")
    out.append("--  * punto_control e inspeccion: red SINTÉTICA de prueba (no corresponde a vértices")
    out.append("--    oficiales del IGAC; reemplazar por datos reales si se dispone de ellos)")
    out.append("-- =====================================================================")
    out.append("BEGIN;")
    out.append("INSERT INTO departamento (cod_departamento, nombre, geom) VALUES "
               f"('63', 'Quindío', ST_Multi(ST_MakeValid(ST_GeomFromText('{shapely.to_wkt(limite, rounding_precision=3)}', 9377))));")
    out.append("")
    out.append("INSERT INTO tipo_punto VALUES")
    out.append("  (1, 'Vértice GNSS', 'Punto materializado con coordenadas obtenidas por posicionamiento GNSS'),")
    out.append("  (2, 'Placa de nivelación', 'Punto con cota obtenida por nivelación geométrica'),")
    out.append("  (3, 'Mojón de poligonal', 'Punto de apoyo de poligonales y levantamientos locales');")
    out.append("")
    for cod, nombre, g in municipios:
        out.append(
            f"INSERT INTO municipio (cod_municipio, nombre, cod_departamento, geom) VALUES "
            f"('{cod}', '{nombre}', '63', ST_Multi(ST_MakeValid(ST_GeomFromText('{shapely.to_wkt(g, rounding_precision=3)}', 9377))));"
        )
    out.append("")

    # Puntos: cantidad proporcional al área (entre 6 y 22 por municipio)
    area_max = max(g.area for _, _, g in municipios)
    filas = []
    inspecciones = []
    for cod, nombre, g in municipios:
        n = max(6, round(22 * g.area / area_max))
        minx, miny, maxx, maxy = g.bounds
        contador = {t: 0 for _, t in TIPOS}
        k = 0
        while k < n:
            pt = Point(random.uniform(minx, maxx), random.uniform(miny, maxy))
            if not g.contains(pt):
                continue
            id_tipo, sigla = random.choices(TIPOS, weights=[0.35, 0.35, 0.30])[0]
            contador[sigla] += 1
            codigo = f"{sigla}-{cod[2:]}-{contador[sigla]:03d}"
            orden = {1: random.choice([1, 2]), 2: random.choice([2, 3]), 3: random.choice([3, 4])}[id_tipo]
            estado = random.choices(["Bueno", "Deteriorado", "Destruido"], weights=[0.7, 0.2, 0.1])[0]
            h = round(random.uniform(*ALTURAS[cod]), 3)
            fecha = date(2012, 1, 1) + timedelta(days=random.randint(0, 5000))
            # Historial de inspecciones: la primera en la materialización (Bueno);
            # las siguientes pueden mantener o empeorar el estado hasta el estado final.
            niveles = ["Bueno", "Deteriorado", "Destruido"]
            n_insp = random.randint(1, 4)
            fechas = sorted({fecha + timedelta(days=random.randint(180, (date(2026, 10, 31) - fecha).days))
                             for _ in range(n_insp - 1)})
            visitas = [(fecha, "Bueno", "Materialización y primera ocupación")]
            for i, f_v in enumerate(fechas):
                ultimo = i == len(fechas) - 1
                e_v = estado if ultimo else niveles[random.randint(0, niveles.index(estado))]
                e_v = max(e_v, visitas[-1][1], key=niveles.index)
                visitas.append((f_v, e_v, OBS[e_v]))
            if len(visitas) == 1 and estado != "Bueno":
                f_v = fecha + timedelta(days=random.randint(180, (date(2026, 10, 31) - fecha).days))
                visitas.append((f_v, estado, OBS[estado]))
            for f_v, e_v, o_v in visitas:
                inspecciones.append(f"  ('{codigo}', '{f_v}', '{e_v}', '{o_v}')")
            filas.append(
                f"  ('{codigo}', {id_tipo}, {orden}, 'Bueno', {h}, '{fecha}', "
                f"ST_SetSRID(ST_MakePoint({pt.x:.3f}, {pt.y:.3f}), 9377))"
            )
            k += 1
    out.append("-- cod_municipio se asigna automáticamente con el trigger trg_asignar_municipio")
    out.append("INSERT INTO punto_control (codigo, id_tipo, orden, estado, altura_m, fecha_materializacion, geom) VALUES")
    out.append(",\n".join(filas) + ";")
    out.append("")
    out.append("-- Historial de inspecciones (el trigger trg_actualizar_estado_punto fija el estado vigente)")
    out.append("INSERT INTO inspeccion (id_punto, fecha, estado_encontrado, observacion)")
    out.append("SELECT p.id_punto, v.fecha::date, v.estado, v.obs FROM (VALUES")
    out.append(",\n".join(inspecciones))
    out.append(") AS v (codigo, fecha, estado, obs)")
    out.append("JOIN punto_control p ON p.codigo = v.codigo")
    out.append("ORDER BY p.id_punto, v.fecha::date;")
    out.append("COMMIT;")
    out.append("")
    out.append("ANALYZE municipio;")
    out.append("ANALYZE punto_control;")
    out.append("ANALYZE inspeccion;")
    open(salida, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(f"{len(municipios)} municipios, {len(filas)} puntos, {len(inspecciones)} inspecciones -> {salida}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
