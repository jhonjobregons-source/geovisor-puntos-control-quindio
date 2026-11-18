-- =====================================================================
--  Script 03: verificación de la geodatabase (estructura, datos e integridad)
--  Ejecutar después de 01_esquema.sql y 02_datos.sql
-- =====================================================================

-- 1. Versión de PostGIS
SELECT postgis_lib_version() AS postgis, version() AS postgresql;

-- 2. Registros por tabla
SELECT 'departamento' AS tabla, COUNT(*) AS registros FROM departamento
UNION ALL SELECT 'municipio',     COUNT(*) FROM municipio
UNION ALL SELECT 'tipo_punto',    COUNT(*) FROM tipo_punto
UNION ALL SELECT 'punto_control', COUNT(*) FROM punto_control
UNION ALL SELECT 'inspeccion',    COUNT(*) FROM inspeccion;

-- 3. Llaves foráneas declaradas (relaciones del modelo)
SELECT tc.table_name AS tabla, kcu.column_name AS columna,
       ccu.table_name AS referencia, ccu.column_name AS columna_referida
  FROM information_schema.table_constraints tc
  JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name
  JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name
 WHERE tc.constraint_type = 'FOREIGN KEY'
 ORDER BY 1, 2;

-- 4. Columnas geométricas registradas (tipo y SRID: todas deben estar en 9377)
SELECT f_table_name AS tabla, f_geometry_column AS columna, type AS tipo, srid
  FROM geometry_columns ORDER BY 1;

-- 5. Geometrías inválidas (debe dar 0 en ambas capas)
SELECT 'municipio' AS capa, COUNT(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalidas FROM municipio
UNION ALL
SELECT 'punto_control', COUNT(*) FILTER (WHERE NOT ST_IsValid(geom)) FROM punto_control;

-- 5b. Cobertura: los 12 municipios cubren el departamento (diferencias < 0,01 km², áreas en el plano 9377)
SELECT ROUND((ST_Area(d.geom) / 1e6)::numeric, 3)                              AS area_departamento_km2,
       ROUND((SUM(ST_Area(m.geom)) / 1e6)::numeric, 3)                          AS suma_municipios_km2,
       ROUND((ST_Area(ST_Difference(d.geom, ST_Union(m.geom))) / 1e6)::numeric, 4) AS hueco_sin_municipio_km2
  FROM departamento d JOIN municipio m USING (cod_departamento)
 GROUP BY d.geom;

-- 6. Regla espacial: todos los puntos tienen municipio (asignado por trigger) y caen dentro de él
SELECT COUNT(*) FILTER (WHERE cod_municipio IS NULL)                         AS sin_municipio,
       COUNT(*) FILTER (WHERE NOT ST_Covers(m.geom, p.geom))                 AS fuera_de_su_municipio
  FROM punto_control p LEFT JOIN municipio m USING (cod_municipio);

-- 7. Regla temática: el estado vigente coincide con la inspección más reciente (debe dar 0)
SELECT COUNT(*) AS estados_inconsistentes
  FROM punto_control p
 WHERE p.estado <> (SELECT i.estado_encontrado FROM inspeccion i
                     WHERE i.id_punto = p.id_punto ORDER BY i.fecha DESC LIMIT 1);

-- 8. Pruebas de integridad: cada intento inválido debe ser rechazado por la base
DO $$
BEGIN
    BEGIN
        INSERT INTO punto_control (codigo, id_tipo, orden, estado, geom)
        VALUES ('PRUEBA-FK', 99, 1, 'Bueno', ST_SetSRID(ST_MakePoint(4702600, 2059540), 9377));
        RAISE NOTICE 'FALLA: se aceptó un tipo de punto inexistente';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'OK: llave foránea rechaza id_tipo = 99';
    END;
    BEGIN
        INSERT INTO punto_control (codigo, id_tipo, orden, estado, geom)
        VALUES ('PRUEBA-CK', 1, 1, 'Perdido', ST_SetSRID(ST_MakePoint(4702600, 2059540), 9377));
        RAISE NOTICE 'FALLA: se aceptó un estado no válido';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK: CHECK rechaza estado = Perdido';
    END;
    BEGIN
        INSERT INTO punto_control (codigo, id_tipo, orden, estado, geom)
        SELECT codigo, id_tipo, orden, estado, geom FROM punto_control LIMIT 1;
        RAISE NOTICE 'FALLA: se aceptó un código duplicado';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'OK: UNIQUE rechaza código duplicado';
    END;
END $$;

-- 9. Resumen por municipio (área geodésica y puntos)
SELECT * FROM v_resumen_municipio ORDER BY cod_municipio;
