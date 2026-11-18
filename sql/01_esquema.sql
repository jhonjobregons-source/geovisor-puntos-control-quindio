-- =====================================================================
--  GEOVISOR DE PUNTOS DE CONTROL TOPOGRÁFICO - QUINDÍO
--  Script 01: creación del esquema de la geodatabase
--  Motor: PostgreSQL 14+ con PostGIS 3.x
--  SRC único de la geodatabase: EPSG:9377 (MAGNA-SIRGAS / Origen-Nacional, metros)
-- ---------------------------------------------------------------------
--  Uso (desde la consola, con la base ya creada):
--      psql -U postgres -d geovisor_control -f sql/01_esquema.sql
--  O bien abrir el archivo en pgAdmin 4 > Query Tool y ejecutarlo.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------------
-- Sistema de referencia MAGNA-SIRGAS / Origen-Nacional (EPSG:9377).
-- Es el sistema de referencia de TODAS las geometrías de esta base.
-- Las versiones recientes de PostGIS ya lo incluyen; si no existe, se registra aquí.
-- ---------------------------------------------------------------------
INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, proj4text, srtext)
SELECT 9377, 'EPSG', 9377,
       '+proj=tmerc +lat_0=4 +lon_0=-73 +k=0.9992 +x_0=5000000 +y_0=2000000 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs',
       'PROJCS["MAGNA-SIRGAS / Origen-Nacional",GEOGCS["MAGNA-SIRGAS",DATUM["Marco_Geocentrico_Nacional_de_Referencia",SPHEROID["GRS 1980",6378137,298.257222101]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",4],PARAMETER["central_meridian",-73],PARAMETER["scale_factor",0.9992],PARAMETER["false_easting",5000000],PARAMETER["false_northing",2000000],UNIT["metre",1]]'
WHERE NOT EXISTS (SELECT 1 FROM spatial_ref_sys WHERE srid = 9377);

-- Se eliminan las tablas si ya existían (permite re-ejecutar el script)
DROP TABLE IF EXISTS inspeccion     CASCADE;
DROP TABLE IF EXISTS punto_control  CASCADE;
DROP TABLE IF EXISTS tipo_punto     CASCADE;
DROP TABLE IF EXISTS municipio      CASCADE;
DROP TABLE IF EXISTS departamento   CASCADE;

-- ---------------------------------------------------------------------
-- 1. DEPARTAMENTO
-- ---------------------------------------------------------------------
CREATE TABLE departamento (
    cod_departamento   VARCHAR(2)   PRIMARY KEY,
    nombre             VARCHAR(100) NOT NULL,
    geom               geometry(MultiPolygon, 9377) NOT NULL,
    CONSTRAINT ck_departamento_geom_valida CHECK (ST_IsValid(geom))
);

-- ---------------------------------------------------------------------
-- 2. MUNICIPIO (polígonos)
-- ---------------------------------------------------------------------
CREATE TABLE municipio (
    cod_municipio      VARCHAR(5)   PRIMARY KEY,           -- código DIVIPOLA
    nombre             VARCHAR(100) NOT NULL,
    cod_departamento   VARCHAR(2)   NOT NULL REFERENCES departamento (cod_departamento),
    geom               geometry(MultiPolygon, 9377) NOT NULL,
    CONSTRAINT ck_municipio_geom_valida CHECK (ST_IsValid(geom))
);

-- ---------------------------------------------------------------------
-- 3. TIPO_PUNTO (catálogo)
-- ---------------------------------------------------------------------
CREATE TABLE tipo_punto (
    id_tipo            SMALLINT     PRIMARY KEY,
    nombre             VARCHAR(80)  NOT NULL UNIQUE,
    descripcion        TEXT
);

-- ---------------------------------------------------------------------
-- 4. PUNTO_CONTROL (puntos)
-- ---------------------------------------------------------------------
CREATE TABLE punto_control (
    id_punto           SERIAL       PRIMARY KEY,
    codigo             VARCHAR(20)  NOT NULL UNIQUE,
    id_tipo            SMALLINT     NOT NULL REFERENCES tipo_punto (id_tipo),
    cod_municipio      VARCHAR(5)   REFERENCES municipio (cod_municipio),
    orden              SMALLINT     NOT NULL CHECK (orden BETWEEN 1 AND 4),
    estado             VARCHAR(15)  NOT NULL
                       CHECK (estado IN ('Bueno', 'Deteriorado', 'Destruido')),
    altura_m           NUMERIC(8,3),                       -- altura aproximada (m)
    fecha_materializacion DATE,
    observacion        TEXT,
    geom               geometry(Point, 9377) NOT NULL
);

-- ---------------------------------------------------------------------
-- 5. INSPECCION (historial de visitas de campo a cada punto)
--    Relación 1:N con punto_control. El estado vigente del punto es el
--    de su inspección más reciente (lo mantiene un trigger).
-- ---------------------------------------------------------------------
CREATE TABLE inspeccion (
    id_inspeccion      SERIAL       PRIMARY KEY,
    id_punto           INTEGER      NOT NULL REFERENCES punto_control (id_punto) ON DELETE CASCADE,
    fecha              DATE         NOT NULL,
    estado_encontrado  VARCHAR(15)  NOT NULL
                       CHECK (estado_encontrado IN ('Bueno', 'Deteriorado', 'Destruido')),
    observacion        TEXT,
    CONSTRAINT uq_inspeccion_punto_fecha UNIQUE (id_punto, fecha)
);

-- ---------------------------------------------------------------------
-- Índices espaciales (GiST) y de atributos
-- ---------------------------------------------------------------------
CREATE INDEX idx_departamento_geom   ON departamento  USING GIST (geom);
CREATE INDEX idx_municipio_geom      ON municipio     USING GIST (geom);
CREATE INDEX idx_punto_control_geom  ON punto_control USING GIST (geom);
CREATE INDEX idx_punto_control_tipo  ON punto_control (id_tipo);
CREATE INDEX idx_punto_control_mpio  ON punto_control (cod_municipio);
CREATE INDEX idx_inspeccion_punto    ON inspeccion (id_punto, fecha DESC);

-- ---------------------------------------------------------------------
-- Regla de negocio espacial (trigger):
-- al insertar o mover un punto, el municipio se asigna automáticamente
-- por relación topológica (ST_Covers), sin depender del usuario.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_asignar_municipio()
RETURNS TRIGGER AS $$
BEGIN
    SELECT m.cod_municipio
      INTO NEW.cod_municipio
      FROM municipio m
     WHERE ST_Covers(m.geom, NEW.geom)
     ORDER BY m.cod_municipio
     LIMIT 1;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_asignar_municipio
BEFORE INSERT OR UPDATE OF geom ON punto_control
FOR EACH ROW EXECUTE FUNCTION fn_asignar_municipio();

-- ---------------------------------------------------------------------
-- Regla de integridad (trigger): al registrar una inspección, el estado
-- vigente del punto se actualiza con el de la inspección más reciente.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_actualizar_estado_punto()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE punto_control p
       SET estado = i.estado_encontrado
      FROM (SELECT estado_encontrado
              FROM inspeccion
             WHERE id_punto = NEW.id_punto
             ORDER BY fecha DESC
             LIMIT 1) i
     WHERE p.id_punto = NEW.id_punto;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_actualizar_estado_punto
AFTER INSERT OR UPDATE ON inspeccion
FOR EACH ROW EXECUTE FUNCTION fn_actualizar_estado_punto();

-- ---------------------------------------------------------------------
-- Vista de apoyo: resumen por municipio (usada en la verificación)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_resumen_municipio AS
SELECT m.cod_municipio,
       m.nombre,
       ROUND((ST_Area(m.geom) / 1e6)::numeric, 2) AS area_km2,
       COUNT(p.id_punto)                                    AS total_puntos,
       COUNT(p.id_punto) FILTER (WHERE p.estado = 'Bueno')  AS puntos_buenos
  FROM municipio m
  LEFT JOIN punto_control p ON p.cod_municipio = m.cod_municipio
 GROUP BY m.cod_municipio, m.nombre, m.geom;
