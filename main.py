import json
import re
import requests
import streamlit as st
import pydeck as pdk
import pandas as pd
from shapely.geometry import Polygon, shape
from datetime import datetime

# --- 1. CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="AgroClima Inteligente", layout="wide")

# --- 2. EXTRACTORES GEOGRÁFICOS DE SEGURIDAD ---
def extraer_poligono_geojson(bytes_data):
    """Parsea GeoJSON usando la librería nativa json y shapely."""
    js = json.loads(bytes_data.decode('utf-8'))
    if js.get("type") == "FeatureCollection":
        geom = js["features"][0]["geometry"]
    elif js.get("type") == "Feature":
        geom = js["geometry"]
    else:
        geom = js
    
    sh_geom = shape(geom)
    if sh_geom.geom_type == 'Polygon':
        return sh_geom
    elif sh_geom.geom_type == 'MultiPolygon':
        return max(sh_geom.geoms, key=lambda p: p.area) # Tomamos el más grande
    else:
        raise ValueError("El archivo GeoJSON no contiene un polígono cerrado válido.")

def extraer_poligono_kml(bytes_data):
    """Parsea archivos KML de Google Earth mediante búsquedas de patrones estructurados."""
    raw_str = bytes_data.decode('utf-8')
    coord_matches = re.findall(r'<coordinates>(.*?)</coordinates>', raw_str, re.DOTALL)
    
    if not coord_matches:
        raise ValueError("No se encontraron coordenadas válidas dentro de las etiquetas estructuradas del KML.")
    
    # Extraemos el primer bloque de coordenadas (perímetro principal)
    coord_str = coord_matches[0].strip()
    puntos = []
    
    # Los KML separan puntos por espacios y coordenadas por comas (Lon, Lat, Alt)
    for p_str in coord_str.split():
        parts = p_str.strip().split(',')
        if len(parts) >= 2:
            lon = float(parts[0])
            lat = float(parts[1])
            puntos.append((lon, lat))
            
    if len(puntos) < 3:
        raise ValueError("El mapa KML no tiene suficientes vértices para cerrar un área.")
    return Polygon(puntos)

# --- 3. CONSULTA API METEOROLÓGICA ---
def consultar_api_agro(lat, lon, dias):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "forecast_days": dias,
        "hourly": ["temperature_2m", "relative_humidity_2m", "precipitation", "et0_fao_evapotranspiration"],
        "timezone": "auto"
    }
    r = requests.get(url, params=params, timeout=15)
    return r.json()

# --- 4. INTERFAZ EN BARRA LATERAL (SIDEBAR) ---
st.sidebar.header("🍇 Entrada de Mapas Digitales")

archivo_mapa = st.sidebar.file_uploader(
    "Sube el mapa de tu predio:", 
    type=["geojson", "kml"],
    help="Puedes exportar este archivo desde Google Earth o plataformas de mapeo agrícola."
)

DIAS_ANALISIS = st.sidebar.slider("Días de proyección técnica", 1, 7, 3)

# --- 5. CONTROLADOR CENTRAL ---
poligono_predio = None
vertices_para_render = []

if archivo_mapa is not None:
    try:
        bytes_archivo = archivo_mapa.read()
        nombre_archivo = archivo_mapa.name.lower()
        
        if nombre_archivo.endswith('.geojson'):
            poligono_predio = extraer_poligono_geojson(bytes_archivo)
        elif nombre_archivo.endswith('.kml'):
            poligono_predio = extraer_poligono_kml(bytes_archivo)
            
        # Extraer vértices para Pydeck e interpolaciones
        vertices_para_render = list(poligono_predio.exterior.coords)
        centroide = poligono_predio.centroid
        
        # Factor matemático de conversión métrica local (Zona central de Chile)
        fact_lat = 111320
        fact_lon = 111320 * 0.82 
        vertices_m = [(p[0] * fact_lon, p[1] * fact_lat) for p in vertices_para_render]
        area_m2 = Polygon(vertices_m).area
        hectareas = area_m2 / 10000
        
        st.sidebar.success(f"✅ Mapa cargado: {hectareas:.2f} Ha detectadas.")
        
    except Exception as e:
        st.sidebar.error(f"❌ Error al procesar el archivo: {e}")

# --- 6. EJECUCIÓN DEL ANÁLISIS ---
if poligono_predio and st.sidebar.button("📊 Analizar Cuadrícula del Predio", use_container_width=True):
    st.title("🍇 AgroClima de Precisión por Polígonos")
    
    # Panel métrico superior
    c1, c2, c3 = st.columns(3)
    c1.metric("Superficie Calculada", f"{area_m2:,.0f} m²")
    c2.metric("Hectáreas Productivas", f"{hectareas:.2f} Ha")
    c3.metric("Georreferencia Central", f"{centroide.y:.4f}, {centroide.x:.4f}")

    try:
        # Consulta climática basada en el centroide del lote subido
        data = consultar_api_agro(centroide.y, centroide.x, DIAS_ANALISIS)
        horario = data['hourly']
        
        df = pd.DataFrame({
            "Fecha/Hora": pd.to_datetime(horario['time']),
            "Temp (°C)": horario['temperature_2m'],
            "Humedad (%)": horario['relative_humidity_2m'],
            "Evapotranspiración ET0 (mm/h)": horario['et0_fao_evapotranspiration'],
            "Precipitación (mm)": horario['precipitation']
        })

        # Alertas críticas
        df["Estado Helada"] = df["Temp (°C)"].apply(lambda t: "❄️ RIESGO" if t <= 2 else "✅ Ok")
        
        # Volumen de agua perdido por transpiración vegetal total (1mm = 1L/m2)
        et0_total_mm = df["Evapotranspiración ET0 (mm/h)"].sum()
        litros_totales_perdidos = et0_total_mm * area_m2

        st.subheader("💧 Pérdida Hídrica Estimada del Polígono")
        st.warning(f"La masa vegetal de tus {hectareas:.2f} Ha transpirará aproximadamente **{litros_totales_perdidos:,.0f} Litros de agua** en el periodo seleccionado. Planifica turnos de riego equivalentes.")

        # --- MAPA SATELITAL DE ALTA RESOLUCIÓN ---
        st.subheader("🗺️ Capa de Mapeo del Cuartel")
        
        # Formatear la lista de puntos para las capas de polígonos de Pydeck
        puntos_ajustados = [[p[0], p[1]] for p in vertices_para_render]
        df_capa = pd.DataFrame([{"polygon": puntos_ajustados}])

        capa_poligono = pdk.Layer(
            "PolygonLayer",
            df_capa,
            get_polygon="polygon",
            get_fill_color=[142, 68, 173, 90], # Color uva traslúcido
            get_line_color=[142, 68, 173, 255],
            get_line_width=3,
            stroked=True,
            filled=True
        )

        view_state = pdk.ViewState(latitude=centroide.y, longitude=centroide.x, zoom=16)
        st.pydeck_chart(pdk.Deck(map_provider="carto", map_style="satellite", initial_view_state=view_state, layers=[capa_poligono]))

        st.subheader("📋 Datos Operativos Horarios")
        st.dataframe(df.set_index("Fecha/Hora"), use_container_width=True)

    except Exception as e:
        st.error(f"❌ Error en la matriz de análisis: {e}")
else:
    if archivo_mapa is None:
        st.info("💡 Por favor, selecciona y sube un archivo `.kml` o `.geojson` desde el menú lateral para iniciar la telemetría.")
