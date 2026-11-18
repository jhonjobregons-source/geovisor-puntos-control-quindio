-- =====================================================================
--  Script 04: consultas de contraste (independientes de la interfaz)
--  Reproducen en PostgreSQL las mismas operaciones que ejecutan los
--  endpoints de la API, con valores fijos de prueba. Los resultados deben
--  coincidir con las respuestas JSON guardadas en evidencias/api/.
--  Todas las geometrías y cálculos están en EPSG:9377 (Origen Nacional).
--  Ejecutar en pgAdmin (Query Tool) o con:
--      psql -U postgres -d geovisor_control -f sql/04_consultas_contraste.sql
-- =====================================================================

-- ---------------------------------------------------------------------
-- Sistema de referencia de la geodatabase
-- ---------------------------------------------------------------------
SELECT f_table_name AS tabla, type AS tipo, srid FROM geometry_columns ORDER BY 1;

-- ---------------------------------------------------------------------
-- F1 · Visualización  ↔  GET /api/departamento, /api/municipios y /api/puntos
-- ---------------------------------------------------------------------
SELECT 'departamento' AS capa, COUNT(*) AS entidades, ROUND((SUM(ST_Area(geom)) / 1e6)::numeric, 2) AS area_km2 FROM departamento
UNION ALL
SELECT 'municipios', COUNT(*), ROUND((SUM(ST_Area(geom)) / 1e6)::numeric, 2) FROM municipio
UNION ALL
SELECT 'puntos', COUNT(*), NULL FROM punto_control;

-- ---------------------------------------------------------------------
-- F2 · Consulta de atributos  ↔  GET /api/puntos/buscar?tipo=1&estado=Bueno&orden_max=1
-- ---------------------------------------------------------------------
SELECT p.codigo, t.nombre AS tipo, p.orden, p.estado, m.nombre AS municipio
  FROM punto_control p
  JOIN tipo_punto t ON t.id_tipo = p.id_tipo
  JOIN municipio  m ON m.cod_municipio = p.cod_municipio
 WHERE p.id_tipo = 1 AND p.estado = 'Bueno' AND p.orden <= 1
 ORDER BY p.orden, p.codigo;

-- F2 · Ficha técnica  ↔  GET /api/puntos/NIV-001-001
SELECT p.codigo, p.estado,
       ROUND(ST_X(p.geom)::numeric, 3) AS este,
       ROUND(ST_Y(p.geom)::numeric, 3) AS norte
  FROM punto_control p WHERE p.codigo = 'NIV-001-001';

SELECT i.fecha, i.estado_encontrado, i.observacion
  FROM inspeccion i JOIN punto_control p USING (id_punto)
 WHERE p.codigo = 'NIV-001-001'
 ORDER BY i.fecha DESC;

-- ---------------------------------------------------------------------
-- F3 · Consulta espacial  ↔  GET /api/amarre?este=4702599.15&norte=2059542.011&n=3&solo_buenos=true
--      Ubicación de prueba: Plaza de Bolívar de Armenia (aprox.)
-- ---------------------------------------------------------------------
WITH origen AS (SELECT ST_SetSRID(ST_MakePoint(4702599.150, 2059542.011), 9377) AS g)
SELECT p.codigo,
       ROUND(ST_Distance(o.g, p.geom)::numeric, 3)                          AS distancia_plana_m,
       ROUND(degrees(ST_Azimuth(o.g, p.geom))::numeric, 4)                  AS azimut_cuadricula_grados,
       ROUND(ST_Distance(ST_Transform(o.g, 4326)::geography,
                         ST_Transform(p.geom, 4326)::geography)::numeric, 3) AS distancia_geodesica_m
  FROM punto_control p CROSS JOIN origen o
 WHERE p.estado = 'Bueno'
 ORDER BY p.geom <-> o.g
 LIMIT 3;

-- Comprobación independiente del ordenamiento KNN: el mismo resultado
-- ordenando por la distancia exacta (sin usar el índice).
WITH origen AS (SELECT ST_SetSRID(ST_MakePoint(4702599.150, 2059542.011), 9377) AS g)
SELECT p.codigo, ROUND(ST_Distance(o.g, p.geom)::numeric, 3) AS distancia_plana_m
  FROM punto_control p CROSS JOIN origen o
 WHERE p.estado = 'Bueno'
 ORDER BY 2
 LIMIT 3;

-- Cálculo manual de la distancia y el azimut de cuadrícula del primer punto
-- (fórmulas de topografía plana: d = √(ΔE² + ΔN²), Az = atan2(ΔE, ΔN))
WITH o AS (SELECT 4702599.150 AS e0, 2059542.011 AS n0),
     p AS (SELECT codigo, ST_X(geom) AS e, ST_Y(geom) AS n FROM punto_control
            WHERE estado = 'Bueno'
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(4702599.150, 2059542.011), 9377) LIMIT 1)
SELECT p.codigo,
       ROUND(sqrt((p.e - o.e0)^2 + (p.n - o.n0)^2)::numeric, 3)                          AS distancia_manual_m,
       ROUND(mod((degrees(atan2(p.e - o.e0, p.n - o.n0)) + 360)::numeric, 360), 4)        AS azimut_manual_grados
  FROM p, o;

-- ---------------------------------------------------------------------
-- F4 · Complementaria  ↔  POST /api/zona/analisis  (rectángulo de prueba en WGS 84,
--      transformado a EPSG:9377 igual que lo hace la API)
-- ---------------------------------------------------------------------
WITH z AS (SELECT ST_Transform(ST_MakeEnvelope(-75.75, 4.48, -75.62, 4.60, 4326), 9377) AS g)
SELECT ROUND((ST_Area(z.g) / 1e6)::numeric, 4)                AS area_km2,
       ROUND(ST_Perimeter(z.g)::numeric, 2)                     AS perimetro_m,
       COUNT(p.id_punto)                                        AS total_puntos,
       COUNT(p.id_punto) FILTER (WHERE p.estado = 'Bueno')      AS puntos_utilizables
  FROM z LEFT JOIN punto_control p ON ST_Covers(z.g, p.geom)
 GROUP BY z.g;

WITH z AS (SELECT ST_Transform(ST_MakeEnvelope(-75.75, 4.48, -75.62, 4.60, 4326), 9377) AS g)
SELECT m.nombre,
       ROUND((ST_Area(ST_Intersection(m.geom, z.g)) / 1e6)::numeric, 4) AS area_km2
  FROM municipio m CROSS JOIN z
 WHERE ST_Intersects(m.geom, z.g)
 ORDER BY 2 DESC;

-- ---------------------------------------------------------------------
-- F5 · Adicional  ↔  GET /api/municipios/cobertura?estado=Bueno
--      Contraste: el conteo por unión ESPACIAL (ST_Covers) debe coincidir
--      con el conteo por la llave foránea cod_municipio (asignada por trigger).
-- ---------------------------------------------------------------------
SELECT m.nombre,
       COUNT(p.id_punto)                                         AS por_union_espacial,
       (SELECT COUNT(*) FROM punto_control q
         WHERE q.cod_municipio = m.cod_municipio AND q.estado = 'Bueno') AS por_llave_foranea,
       ROUND((COUNT(p.id_punto) / (ST_Area(m.geom) / 1e8))::numeric, 2) AS puntos_100km2
  FROM municipio m
  LEFT JOIN punto_control p ON ST_Covers(m.geom, p.geom) AND p.estado = 'Bueno'
 GROUP BY m.cod_municipio, m.nombre, m.geom
 ORDER BY puntos_100km2 DESC;
