/* =====================================================================
   GEOVISOR · RED DE PUNTOS DE CONTROL DEL QUINDÍO
   Frontend: Leaflet + fetch() hacia la API FastAPI.
   Ningún resultado se calcula ni se precarga aquí: todo llega de PostGIS.
   ===================================================================== */

const API = window.location.origin + "/api";

const COLOR_ESTADO = { Bueno: "#1f8a70", Deteriorado: "#e6a117", Destruido: "#c0392b" };
const COLOR_TIPO = { 1: "#1f5fbf", 2: "#8e44ad", 3: "#d35400" };

// ---------------------------------------------------------------------
// Mapa base
// ---------------------------------------------------------------------
const mapa = L.map("mapa").setView([4.50, -75.68], 10);
const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: "&copy; OpenStreetMap"
}).addTo(mapa);
const satelite = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19, attribution: "Imágenes &copy; Esri" });
L.control.layers({ "OpenStreetMap": osm, "Satélite (Esri)": satelite }, null, { position: "topright" }).addTo(mapa);
L.control.scale({ imperial: false }).addTo(mapa);

let capaDepartamento = null, capaMunicipios = null, capaPuntos = null, datosPuntos = null;
const capaResultados = L.featureGroup().addTo(mapa);
const capaDibujo = L.featureGroup().addTo(mapa);
let leyendaTematica = null, modoActivo = null, herramientaDibujo = null;

// ---------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------
function aviso(texto, error = false, ms = 3500) {
  const el = document.getElementById("aviso");
  el.textContent = texto;
  el.className = error ? "error" : "";
  clearTimeout(el._t);
  if (ms) el._t = setTimeout(() => el.classList.add("oculto"), ms);
}

/** Toda petición pasa por aquí: queda registrada en el panel "Peticiones a la API". */
async function pedir(ruta, opciones = {}) {
  const inicio = performance.now();
  const metodo = opciones.method || "GET";
  let respuesta;
  try {
    respuesta = await fetch(API + ruta, opciones);
  } catch (e) {
    registrar(metodo, ruta, "sin respuesta", false, 0);
    throw new Error("No se pudo contactar la API. ¿Está FastAPI en ejecución?");
  }
  const datos = await respuesta.json();
  registrar(metodo, ruta, respuesta.status, respuesta.ok, performance.now() - inicio, datos);
  if (!respuesta.ok) {
    const d = datos.detail;
    throw new Error(Array.isArray(d) ? d.map(x => x.msg).join("; ") : (d || "Error en la petición"));
  }
  return datos;
}

function registrar(metodo, ruta, estado, ok, ms, datos) {
  const lista = document.getElementById("lista-peticiones");
  const li = document.createElement("li");
  const n = datos && datos.features ? ` · ${datos.features.length} entidades` : "";
  li.innerHTML = `<span class="${ok ? "ok" : "err"}">${estado}</span> ${metodo} /api${ruta} · ${Math.round(ms)} ms${n}`;
  lista.prepend(li);
  while (lista.children.length > 8) lista.lastChild.remove();
}

const fmt = (n, dec = 2) =>
  Number(n).toLocaleString("es-CO", { minimumFractionDigits: dec, maximumFractionDigits: dec });

function mostrar(html) {
  const el = document.getElementById("contenido-resultados");
  el.classList.remove("vacio");
  el.innerHTML = html;
}

const dato = (etiqueta, valor) => `<div class="dato"><span>${etiqueta}</span><b>${valor}</b></div>`;

function marcador(latlng, color, radio = 6) {
  return L.circleMarker(latlng, { radius: radio, color: "#fff", weight: 1.5, fillColor: color, fillOpacity: 0.95 });
}

function activarTarjeta(id) {
  document.querySelectorAll(".funcion").forEach(s => s.classList.toggle("activa", s.id === id));
}

function limpiar() {
  capaResultados.clearLayers();
  capaDibujo.clearLayers();
  if (leyendaTematica) { mapa.removeControl(leyendaTematica); leyendaTematica = null; }
  desactivarModos();
  activarTarjeta(null);
  const el = document.getElementById("contenido-resultados");
  el.classList.add("vacio");
  el.textContent = "Seleccione una funcionalidad.";
}

function desactivarModos() {
  modoActivo = null;
  document.getElementById("aviso").classList.add("oculto");
  mapa.getContainer().classList.remove("modo-clic");
  if (herramientaDibujo) { herramientaDibujo.disable(); herramientaDibujo = null; }
}

// ---------------------------------------------------------------------
// F1. Visualización: capas consultadas en PostGIS
// ---------------------------------------------------------------------
async function cargarDepartamento() {
  const datos = await pedir("/departamento");
  capaDepartamento = L.geoJSON(datos, {
    style: { color: "#0b2540", weight: 3.5, fill: false },
    interactive: false
  }).addTo(mapa);
  mapa.fitBounds(capaDepartamento.getBounds());
}

async function cargarMunicipios() {
  const datos = await pedir("/municipios");
  capaMunicipios = L.geoJSON(datos, {
    style: { color: "#12395b", weight: 1.5, fillColor: "#7fb3d5", fillOpacity: 0.06 },
    onEachFeature: (f, capa) => capa.bindTooltip(`${f.properties.nombre} · ${fmt(f.properties.area_km2)} km²`, { sticky: true })
  }).addTo(mapa);
}

async function cargarPuntos() {
  datosPuntos = await pedir("/puntos");
  dibujarPuntos();
}

function colorPunto(p) {
  return document.getElementById("sel-simbologia").value === "tipo" ? COLOR_TIPO[p.id_tipo] : COLOR_ESTADO[p.estado];
}

function dibujarPuntos() {
  if (capaPuntos) mapa.removeLayer(capaPuntos);
  capaPuntos = L.geoJSON(datosPuntos, {
    pointToLayer: (f, ll) => marcador(ll, colorPunto(f.properties), 5),
    onEachFeature: (f, capa) => {
      capa.bindTooltip(f.properties.codigo);
      capa.on("click", (e) => { if (!modoActivo) { L.DomEvent.stopPropagation(e); verFicha(f.properties.codigo); } });
    }
  });
  if (document.getElementById("chk-puntos").checked) capaPuntos.addTo(mapa);
  actualizarLeyenda();
}

const leyenda = L.control({ position: "bottomright" });
leyenda.onAdd = () => L.DomUtil.create("div", "leyenda");
leyenda.addTo(mapa);

function actualizarLeyenda() {
  const porTipo = document.getElementById("sel-simbologia").value === "tipo";
  const div = leyenda.getContainer();
  if (porTipo) {
    const nombres = {};
    datosPuntos.features.forEach(f => nombres[f.properties.id_tipo] = f.properties.tipo);
    div.innerHTML = "<b>Tipo de punto</b><br>" +
      Object.keys(COLOR_TIPO).map(k => `<i style="background:${COLOR_TIPO[k]}"></i>${nombres[k] || k}`).join("<br>");
  } else {
    div.innerHTML = "<b>Estado vigente</b><br>" +
      Object.entries(COLOR_ESTADO).map(([k, c]) => `<i style="background:${c}"></i>${k}`).join("<br>");
  }
}

// ---------------------------------------------------------------------
// F2. Consulta de atributos: búsqueda combinada y ficha técnica
// ---------------------------------------------------------------------
async function cargarCatalogos() {
  const c = await pedir("/catalogos");
  const llenar = (id, items, valor, texto) => {
    const sel = document.getElementById(id);
    items.forEach(it => sel.insertAdjacentHTML("beforeend", `<option value="${valor(it)}">${texto(it)}</option>`));
  };
  llenar("f2-tipo", c.tipos, t => t.id_tipo, t => t.nombre);
  llenar("f2-estado", c.estados, e => e, e => e);
  llenar("f2-municipio", c.municipios, m => m.cod_municipio, m => m.nombre);
}

async function buscarPuntos() {
  desactivarModos();
  activarTarjeta("sec-f2");
  const params = new URLSearchParams();
  const agregar = (clave, id) => { const v = document.getElementById(id).value.trim(); if (v) params.append(clave, v); };
  agregar("tipo", "f2-tipo"); agregar("estado", "f2-estado"); agregar("orden_max", "f2-orden");
  agregar("municipio", "f2-municipio"); agregar("texto", "f2-texto");
  try {
    const datos = await pedir("/puntos/buscar?" + params.toString());
    capaResultados.clearLayers();
    L.geoJSON(datos, {
      pointToLayer: (f, ll) => marcador(ll, COLOR_ESTADO[f.properties.estado], 9),
      onEachFeature: (f, c) => c.on("click", () => verFicha(f.properties.codigo))
    }).addTo(capaResultados);
    if (datos.features.length) mapa.fitBounds(capaResultados.getBounds(), { padding: [40, 40], maxZoom: 14 });
    const filas = datos.features.map(f => {
      const p = f.properties;
      return `<tr class="clic" data-codigo="${p.codigo}"><td>${p.codigo}</td><td>${p.orden}</td><td>${p.estado}</td><td>${p.municipio}</td></tr>`;
    }).join("");
    mostrar(`<b>F2 · ${datos.resumen.total} punto(s) encontrados</b>
      ${filas ? `<table><tr><th>Código</th><th>Orden</th><th>Estado</th><th>Municipio</th></tr>${filas}</table>
      <p class="ayuda">Clic en una fila para ver la ficha técnica.</p>` : "<p>Ningún punto cumple los criterios.</p>"}`);
    document.querySelectorAll("tr.clic").forEach(tr => tr.addEventListener("click", () => verFicha(tr.dataset.codigo)));
  } catch (e) { aviso(e.message, true); }
}

async function verFicha(codigo) {
  try {
    const datos = await pedir("/puntos/" + encodeURIComponent(codigo));
    const f = datos.features[0], p = f.properties;
    const [x, y] = f.geometry.coordinates;
    const historial = datos.resumen.inspecciones.map(i =>
      `<tr><td>${i.fecha}</td><td>${i.estado_encontrado}</td><td>${i.observacion || ""}</td></tr>`).join("");
    const html = `<b>Ficha técnica · ${p.codigo}</b>` +
      dato("Tipo", p.tipo) + dato("Orden", p.orden) + dato("Estado vigente", p.estado) +
      dato("Municipio", `${p.municipio} (${p.departamento})`) +
      dato("Materialización", p.fecha_materializacion) +
      dato("Altura aprox.", `${fmt(p.altura_m, 3)} m`) +
      dato("Este (EPSG:9377)", `${fmt(p.este, 3)} m`) +
      dato("Norte (EPSG:9377)", `${fmt(p.norte, 3)} m`) +
      `<table><tr><th>Inspección</th><th>Estado</th><th>Observación</th></tr>${historial}</table>`;
    mostrar(html);
    L.popup().setLatLng([y, x]).setContent(`<b>${p.codigo}</b><br>${p.tipo}<br>Estado: ${p.estado}`).openOn(mapa);
  } catch (e) { aviso(e.message, true); }
}

// ---------------------------------------------------------------------
// F3. Consulta espacial: puntos de amarre
// ---------------------------------------------------------------------
function activarAmarre() {
  desactivarModos();
  activarTarjeta("sec-f3");
  modoActivo = "f3";
  mapa.getContainer().classList.add("modo-clic");
  aviso("F3 activa: haga clic en el mapa en la ubicación del levantamiento.");
}

/** Origen de F3: clic en el mapa (lon/lat) o coordenadas Este/Norte digitadas en EPSG:9377. */
async function ejecutarAmarre(origen) {
  const n = document.getElementById("f3-n").value;
  const buenos = document.getElementById("f3-buenos").checked;
  const ubicacion = origen.este !== undefined
    ? `este=${origen.este}&norte=${origen.norte}`
    : `lon=${origen.lon.toFixed(8)}&lat=${origen.lat.toFixed(8)}`;
  try {
    const datos = await pedir(`/amarre?${ubicacion}&n=${n}&solo_buenos=${buenos}`);
    capaResultados.clearLayers();
    datos.features.forEach((f, i) => {
      const p = f.properties;
      L.geoJSON(p.linea, { style: { color: "#12395b", weight: 2, dashArray: "6 4" } })
        .bindTooltip(`${fmt(p.distancia_plana_m, 3)} m · Az ${fmt(p.azimut_cuadricula_grados, 4)}°`, { sticky: true })
        .addTo(capaResultados);
      const [x, y] = f.geometry.coordinates;
      marcador([y, x], COLOR_ESTADO[p.estado], 9)
        .bindTooltip(`${i + 1}. ${p.codigo}`, { permanent: true, direction: i % 2 ? "left" : "right", offset: [i % 2 ? -8 : 8, 0] })
        .on("click", () => verFicha(p.codigo)).addTo(capaResultados);
    });
    const r = datos.resumen;
    const [ox, oy] = r.punto.coordinates;
    marcador([oy, ox], "#12395b", 8).bindTooltip("Levantamiento").addTo(capaResultados);
    mapa.fitBounds(capaResultados.getBounds(), { padding: [50, 50] });
    const filas = datos.features.map((f, i) => {
      const p = f.properties;
      return `<tr><td>${i + 1}</td><td>${p.codigo}</td><td>${fmt(p.distancia_plana_m, 3)}</td><td>${fmt(p.azimut_cuadricula_grados, 4)}°</td><td>${fmt(p.distancia_geodesica_m, 3)}</td></tr>`;
    }).join("");
    mostrar(`<b>F3 · Puntos de amarre (EPSG:9377)</b>` +
      dato("Ubicación", `${r.municipio || "Fuera de los municipios"}`) +
      dato("Este / Norte", `${fmt(r.este, 3)} / ${fmt(r.norte, 3)} m`) +
      `<table><tr><th>#</th><th>Código</th><th>Distancia plana (m)</th><th>Azimut cuadrícula</th><th>Geodésica (m)</th></tr>${filas}</table>`);
  } catch (e) { aviso(e.message, true); }
}

// ---------------------------------------------------------------------
// F4. Análisis de zona de proyecto (polígono dibujado)
// ---------------------------------------------------------------------
function activarZona() {
  desactivarModos();
  activarTarjeta("sec-f4");
  modoActivo = "f4";
  aviso("F4 activa: haga clic en cada vértice y cierre el polígono sobre el primer vértice.", false, 6000);
  herramientaDibujo = new L.Draw.Polygon(mapa, {
    allowIntersection: false, showArea: true, shapeOptions: { color: "#8e44ad", weight: 2, fillOpacity: 0.12 }
  });
  herramientaDibujo.enable();
}

mapa.on(L.Draw.Event.CREATED, (e) => {
  capaDibujo.clearLayers();
  capaDibujo.addLayer(e.layer);
  herramientaDibujo = null;
  modoActivo = null;
  ejecutarZona(e.layer.toGeoJSON().geometry);
});

async function ejecutarZona(geometria) {
  try {
    const datos = await pedir("/zona/analisis", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ geometry: geometria })
    });
    capaResultados.clearLayers();
    L.geoJSON(datos, {
      pointToLayer: (f, ll) => marcador(ll, COLOR_ESTADO[f.properties.estado], 9),
      onEachFeature: (f, c) => c.bindTooltip(f.properties.codigo).on("click", () => verFicha(f.properties.codigo))
    }).addTo(capaResultados);
    const r = datos.resumen;
    const mpios = r.municipios.map(m => `<tr><td>${m.municipio}</td><td>${fmt(m.area_km2, 3)}</td><td>${fmt(m.porcentaje, 1)} %</td></tr>`).join("");
    const pts = datos.features.map(f => `<tr><td>${f.properties.codigo}</td><td>${f.properties.estado}</td></tr>`).join("");
    mostrar(`<b>F4 · Zona de proyecto</b>` +
      dato("Área (plano EPSG:9377)", `${fmt(r.area_km2, 4)} km²`) +
      dato("Perímetro (plano EPSG:9377)", `${fmt(r.perimetro_m, 2)} m`) +
      dato("Puntos contenidos", r.total_puntos) +
      dato("Utilizables (estado Bueno)", r.puntos_utilizables) +
      (mpios ? `<table><tr><th>Municipio</th><th>km²</th><th>% zona</th></tr>${mpios}</table>` : "<p>La zona no toca municipios del Quindío.</p>") +
      (pts ? `<table><tr><th>Punto</th><th>Estado</th></tr>${pts}</table>` : ""));
  } catch (e) { aviso(e.message, true); }
}

// ---------------------------------------------------------------------
// F5. Cobertura municipal (coropleta)
// ---------------------------------------------------------------------
const RAMPA = ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"];

async function ejecutarCobertura() {
  desactivarModos();
  activarTarjeta("sec-f5");
  const estado = document.getElementById("f5-estado").value;
  try {
    const datos = await pedir("/municipios/cobertura" + (estado ? `?estado=${estado}` : ""));
    capaResultados.clearLayers();
    const max = Math.max(...datos.features.map(f => f.properties.puntos_100km2), 0.01);
    const cortes = RAMPA.map((_, i) => max * (i + 1) / RAMPA.length);
    const color = v => RAMPA[cortes.findIndex(c => v <= c + 1e-9)];
    L.geoJSON(datos, {
      style: f => ({ color: "#555", weight: 1, fillColor: color(f.properties.puntos_100km2), fillOpacity: 0.75 }),
      onEachFeature: (f, c) => c.bindTooltip(`${f.properties.municipio}: ${fmt(f.properties.puntos_100km2)} pts/100 km²`, { sticky: true })
    }).addTo(capaResultados);
    mapa.fitBounds(capaResultados.getBounds());
    if (leyendaTematica) mapa.removeControl(leyendaTematica);
    leyendaTematica = L.control({ position: "bottomleft" });
    leyendaTematica.onAdd = () => {
      const div = L.DomUtil.create("div", "leyenda");
      div.innerHTML = "<b>Puntos / 100 km²</b><br>" +
        RAMPA.map((c, i) => `<i class="cuadro" style="background:${c}"></i>${fmt(i ? cortes[i - 1] : 0, 1)} – ${fmt(cortes[i], 1)}`).join("<br>");
      return div;
    };
    leyendaTematica.addTo(mapa);
    const filas = datos.features.map(f => {
      const p = f.properties;
      return `<tr><td>${p.municipio}</td><td>${fmt(p.area_km2, 1)}</td><td>${p.total_puntos}</td><td>${fmt(p.puntos_100km2)}</td></tr>`;
    }).join("");
    mostrar(`<b>F5 · Cobertura (${datos.resumen.estado})</b>
      <table><tr><th>Municipio</th><th>km²</th><th>Puntos</th><th>Pts/100 km²</th></tr>${filas}</table>`);
  } catch (e) { aviso(e.message, true); }
}

// ---------------------------------------------------------------------
// Eventos
// ---------------------------------------------------------------------
mapa.on("click", (e) => { if (modoActivo === "f3") ejecutarAmarre({ lon: e.latlng.lng, lat: e.latlng.lat }); });
document.getElementById("btn-f3-coord").addEventListener("click", () => {
  const este = parseFloat(document.getElementById("f3-este").value.replace(",", "."));
  const norte = parseFloat(document.getElementById("f3-norte").value.replace(",", "."));
  if (isNaN(este) || isNaN(norte)) { aviso("Escriba Este y Norte en metros (EPSG:9377).", true); return; }
  desactivarModos();
  activarTarjeta("sec-f3");
  ejecutarAmarre({ este, norte });
});
document.getElementById("btn-f2").addEventListener("click", buscarPuntos);
document.getElementById("f2-texto").addEventListener("keydown", e => { if (e.key === "Enter") buscarPuntos(); });
document.getElementById("btn-f3").addEventListener("click", activarAmarre);
document.getElementById("btn-f4").addEventListener("click", activarZona);
document.getElementById("btn-f5").addEventListener("click", ejecutarCobertura);
document.getElementById("btn-limpiar").addEventListener("click", limpiar);
document.getElementById("sel-simbologia").addEventListener("change", dibujarPuntos);
document.getElementById("chk-departamento").addEventListener("change", e => {
  if (capaDepartamento) e.target.checked ? capaDepartamento.addTo(mapa) : mapa.removeLayer(capaDepartamento);
});
document.getElementById("chk-municipios").addEventListener("change", e => {
  if (capaMunicipios) e.target.checked ? capaMunicipios.addTo(mapa) : mapa.removeLayer(capaMunicipios);
});
document.getElementById("chk-puntos").addEventListener("change", e => {
  if (capaPuntos) e.target.checked ? capaPuntos.addTo(mapa) : mapa.removeLayer(capaPuntos);
});

// ---------------------------------------------------------------------
// Inicio
// ---------------------------------------------------------------------
(async function iniciar() {
  const el = document.getElementById("estado-conexion");
  try {
    const s = await pedir("/salud");
    el.textContent = `Conectado a ${s.base} · PostGIS ${s.postgis} · EPSG:${s.srid}`;
    el.className = "estado ok";
    await Promise.all([cargarCatalogos(), cargarDepartamento(), cargarMunicipios(), cargarPuntos()]);
  } catch (e) {
    el.textContent = "Sin conexión con la API o la base de datos";
    el.className = "estado error";
    aviso(e.message, true, 0);
  }
})();
