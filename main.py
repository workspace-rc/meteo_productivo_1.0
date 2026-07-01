import json
import re
import requests
import streamlit as st
import pydeck as pdk
import pandas as pd
from shapely.geometry import Polygon, shape

# --- 1. CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="AgroClima Pro - Viñedos", layout="wide")

# --- 2. DICCIONARIO DE CÓDIGOS WMO (TORMENTAS Y GRANIZO) ---
def interpretar_wmo(codigo):
    if codigo in [95, 96, 99]:
        if codigo == 95: return "⛈️ Tormenta Eléctrica"
        if codigo == 96: return "⛈️ Tormenta + Granizo Leve"
        if codigo == 99: return "🚨 Tormenta + Granizo Severo"
    return "☀️ Estable"

# --- 3. EXTRACTORES GEOGRÁFICOS ---
def extraer_poligono_geojson(bytes_data):
    js = json.loads(bytes_data.decode('utf-8'))
    geom = js["features"][0]["geometry"] if js.get("type") == "FeatureCollection" else js.get("geometry", js)
    sh_geom = shape(geom)
    if sh_geom.geom_type == 'Polygon': return sh_geom
    if sh_geom.geom_type == 'MultiPolygon': return max(sh_geom.geoms, key=lambda p: p.area)
    raise ValueError("No se encontró un polígono válido.")

def extraer_poligono_kml(bytes_data):
    raw_str = bytes_data.decode('utf-8')
    coord_matches = re.findall(r'<coordinates>(.*?)</coordinates>', raw_str, re.DOTALL)
    if not coord_matches: raise ValueError("No se encontraron coordenadas en el KML.")
    puntos = []
    for p_str in coord_matches[0].strip().split():
        parts = p_str.strip().split(',')
        if len(parts) >= 2: puntos.append((float(parts[0]), float(parts[1])))
    return Polygon(puntos)

# --- 4. CONSULTA API METEOROLÓGICA ---
def consultar_api_agro(lat, lon, dias):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "forecast_days": dias,
        "hourly": [
            "temperature_2m", "relative_humidity_2m", "dew_point_2m", 
            "precipitation", "weather_code", "windspeed_10m", 
            "et0_fao_evapotranspiration", "shortwave_radiation"
        ],
        "timezone": "auto"
    }
    # Corregido: timeout asignado explícitamente como palabra clave
    r = requests.get(url, params=params, timeout=15)
    return r.json()

# --- 5. INTERFAZ LATERAL ---
st.sidebar.header("🍇 Control de Mapas")
archivo_mapa = st.sidebar.file_uploader("Sube plano del predio:", type=["geojson", "kml"])
DIAS_ANALISIS = st.sidebar.slider("Días de proyección", 1, 7, 3)

# --- 6. PROCESAMIENTO ---
poligono_predio = None
if archivo_mapa is not None:
    try:
        bytes_archivo = archivo_mapa.read()
        nombre = archivo_mapa.name.lower()
        poligono_predio = extraer_poligono_geojson(bytes_archivo) if nombre.endswith('.geojson') else extraer_poligono_kml(bytes_archivo)
        
        vertices_para_render = list(poligono_predio.exterior.coords)
        centroide = poligono_predio.centroid
        
        # Conversión métrica aproximada para Chile Central
        vertices_m = [(p[0] * 111320 * 0.82, p[1] * 111320) for p in vertices_para_render]
        area_m2 = Polygon(vertices_m).area
        hectareas = area_m2 / 10000
        st.sidebar.success(f"✅ {hectareas:.2f} Ha Cargadas.")
    except Exception as e:
        st.sidebar.error(f"❌ Error: {e}")

# --- 7. EJECUCIÓN PRINCIPAL ---
if poligono_predio and st.sidebar.button("📊 Generar Reporte Técnico", use_container_width=True):
    st.title(f"🍇 Reporte Agrometeorológico: {archivo_mapa.name.split('.')[0]}")
    
    try:
        data = consultar_api_agro(centroide.y, centroide.x, DIAS_ANALISIS)
        horario = data['hourly']
        
        # Generar DataFrame Horario Inicial
        df_raw = pd.DataFrame({
            "Fecha/Hora": pd.to_datetime(horario['time']),
            "Temp (°C)": horario['temperature_2m'],
            "Humedad (%)": horario['relative_humidity_2m'],
            "Pto Rocío (°C)": horario['dew_point_2m'],
            "Precip (mm)": horario['precipitation'],
            "Viento (km/h)": horario['windspeed_10m'],
            "ET0 (mm/h)": horario['et0_fao_evapotranspiration'],
            "Radiacion (W/m²)": horario['shortwave_radiation'],
            "wmo": horario['weather_code']
        })

        # --- FILTRO ESTRATÉGICO: CADA 3 HORAS ---
        df = df_raw[df_raw["Fecha/Hora"].dt.hour % 3 == 0].copy().reset_index(drop=True)

        # --- EVALUACIÓN DE ALERTAS AGRO ---
        alertas_fito = []
        alertas_clima = []
        luz_sol = []

        for idx, row in df.iterrows():
            # 1. Alerta de Riesgo Fitosanitario (R-Fito)
            if row["Humedad (%)"] >= 80 and (15 <= row["Temp (°C)"] <= 26):
                alertas_fito.append("⚠️ R-Fito ALTO")
            elif row["Humedad (%)"] >= 70 and (12 <= row["Temp (°C)"] <= 28):
                alertas_fito.append("⚠️ R-Fito MEDIO")
            else:
                alertas_fito.append("✅ R-Fito BAJO")
            
            # 2. Tormentas y Heladas
            msg_wmo = interpretar_wmo(row["wmo"])
            if row["Temp (°C)"] <= 1.5:
                alertas_clima.append("❄️ Riesgo Helada")
            elif "Tormenta" in msg_wmo:
                alertas_clima.append(msg_wmo)
            else:
                alertas_clima.append("✅ Estable")
                
            # 3. Estado de Luz
            hora_act = row["Fecha/Hora"].hour
            if row["Radiacion (W/m²)"] > 0:
                if hora_act in [6, 9]:
                    luz_sol.append("🌅 Primera Luz")
                elif hora_act in [18, 21]:
                    luz_sol.append("🌇 Última Luz")
                else:
                    luz_sol.append("☀️ Diurno")
            else:
                luz_sol.append("🌙 Nocturno")

        df["R-Fito (Hongos)"] = alertas_fito
        df["Alertas Clima"] = alertas_clima
        df["Luz Solar"] = luz_sol

        # --- VISUALIZACIÓN DE MÉTRICAS ---
        c1, c2, c3 = st.columns(3)
        c1.metric("Superficie Viñedo", f"{hectareas:.2f} Ha")
        
        et0_total_mm = df_raw["ET0 (mm/h)"].sum()
        litros_totales = et0_total_mm * area_m2
        c2.metric("Evapotranspiración Total", f"{et0_total_mm:.1f} mm", f"-{litros_totales:,.0f} L H₂O")
        
        heladas_h = df_raw[df_raw["temperature_2m"] <= 1.5].shape[0]
        c3.metric("Horas críticas de Helada", f"{heladas_h} hrs")

        # --- MAPA SATELITAL DE ALTA DEFINICIÓN ---
        st.subheader("🗺️ Vista Satelital de Alta Definición (Predio Analizado)")
        puntos_ajustados = [[p[0], p[1]] for p in vertices_para_render]
        puntos_ajustados.append(puntos_ajustados[0]) # Cerrar geometría
        
        capa_poligono = pdk.Layer(
            "PolygonLayer",
            pd.DataFrame([{"polygon": puntos_ajustados}]),
            get_polygon="polygon",
            get_fill_color=[142, 68, 173, 70],
            get_line_color=[255, 255, 255, 255],
            get_line_width=2,
            filled=True,
            stroked=True
        )

        view_state = pdk.ViewState(latitude=centroide.y, longitude=centroide.x, zoom=16, pitch=0)
        
        st.pydeck_chart(pdk.Deck(
            map_style="mapbox://styles/mapbox/satellite-v9", 
            initial_view_state=view_state, 
            layers=[capa_poligono]
        ))

        # --- TABLA DE DATOS OPTIMIZADA (CADA 3 HORAS) ---
        st.subheader("📋 Matriz Operativa de Campo (Bloques de 3 Horas)")
        
        columnas_finales = [
            "Temp (°C)", "Humedad (%)", "Pto Rocío (°C)", 
            "Precip (mm)", "Viento (km/h)", "Luz Solar", 
            "R-Fito (Hongos)", "Alertas Clima"
        ]
        st.dataframe(df.set_index("Fecha/Hora")[columnas_finales], use_container_width=True)

    except Exception as e:
        st.error(f"❌ Error en la matriz de análisis: {e}")
else:
    if archivo_mapa is None:
        st.info("💡 Sube tu archivo `Viñedo_Quilquiwine.geojson` en la barra lateral para desplegar la telemetría satelital.")
