import json
import re
import os
import requests
import streamlit as st
import pandas as pd
from shapely.geometry import Polygon, shape
from timezonefinder import TimezoneFinder
from datetime import datetime
import pytz

# --- 1. CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="AgroClima Pro - Viñedos", layout="wide")

# --- 2. DICCIONARIO DE CÓDIGOS WMO (TORMENTAS Y GRANIZO) ---
def interpretar_wmo(codigo):
    if codigo in [95, 96, 99]:
        if codigo == 95: return "⛈️ Tormenta Eléctrica"
        if codigo == 96: return "⛈️ Tormenta + Granizo Leve"
        if codigo == 99: return "🚨 Tormenta + Granizo Severo"
    return "☀️ Estable"

# --- 3. EXTRACTOR GEOGRÁFICOS CON RUTA ABSOLUTA ---
def cargar_poligono_local(nombre_archivo):
    ruta_actual = os.path.dirname(__file__) if "__file__" in locals() else os.getcwd()
    ruta_completa = os.path.join(ruta_actual, nombre_archivo)
    
    with open(ruta_completa, 'r', encoding='utf-8') as f:
        js = json.load(f)
    geom = js["features"][0]["geometry"] if js.get("type") == "FeatureCollection" else js.get("geometry", js)
    sh_geom = shape(geom)
    if sh_geom.geom_type == 'Polygon': return sh_geom
    if sh_geom.geom_type == 'MultiPolygon': return max(sh_geom.geoms, key=lambda p: p.area)
    raise ValueError("No se encontró un polígono válido.")

# --- 4. CONSULTA API METEOROLÓGICA (FORZANDO UTC) ---
def consultar_api_agro(lat, lon, dias):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "forecast_days": dias,
        "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,precipitation,weather_code,windspeed_10m,et0_fao_evapotranspiration,shortwave_radiation",
        "daily": "sunrise,sunset",
        "timezone": "UTC"  # <-- CAMBIO CLAVE: Obligamos a la API a responder en UTC puro
    }
    
    for intento in range(3):
        try:
            r = requests.get(url, params=params, timeout=30)
            return r.json()
        except requests.exceptions.Timeout:
            if intento == 2: raise Exception("API Saturada.")
            continue

# --- 5. INTERFAZ LATERAL (CONTROL DE TIEMPO) ---
st.sidebar.header("🍇 Parámetros Operativos")
DIAS_ANALISIS = st.sidebar.slider("Días de proyección", 1, 7, 3)

# --- 6. PROCESAMIENTO AUTOMÁTICO ---
NOMBRE_PREDIO = "Viñedo Quilquiwine"
archivo_fijo = "vinedo_quilquiwine.geojson"

try:
    poligono_predio = cargar_poligono_local(archivo_fijo)
    vertices_para_render = list(poligono_predio.exterior.coords)
    centroide = poligono_predio.centroid
    
    # Conversión métrica
    vertices_m = [(p[0] * 111320 * 0.82, p[1] * 111320) for p in vertices_para_render]
    area_m2 = Polygon(vertices_m).area
    hectareas = area_m2 / 10000
    
    st.sidebar.success(f"✅ Predio Enlazado\n({hectareas:.2f} Ha Detectadas)")
except Exception as e:
    st.error(f"❌ Error al cargar la base de datos geográfica del repositorio: {e}")
    st.stop()

# --- 7. EJECUCIÓN DEL REPORTE AUTOMÁTICO ---
st.title(f"🍇 Reporte Agrometeorológico Automatizado")

# Determinación matemática del huso horario real basado en las coordenadas
tf = TimezoneFinder()
zona_horaria_local = tf.timezone_at(lng=centroide.x, lat=centroide.y)
if not zona_horaria_local:
    zona_horaria_local = "UTC" # Respaldo en caso de error extremo

st.markdown(f"**Predio Activo:** {NOMBRE_PREDIO} | **Zona Horaria Detectada:** {zona_horaria_local}")

try:
    data = consultar_api_agro(centroide.y, centroide.x, DIAS_ANALISIS)
    
    if 'hourly' not in data or 'daily' not in data:
        st.error(f"❌ La API no retornó la estructura de datos requerida.")
        st.stop()
        
    horario = data['hourly']
    diario = data['daily']
    
    # FUNCIÓN CORREGIDA: Convierte los ISO timestamps UTC de la API al huso horario del predio
    def formatear_hora_local(timestamp_str, zona_str):
        if timestamp_str.endswith('Z'):
            timestamp_str = timestamp_str[:-1]
            
        dt_utc = datetime.fromisoformat(timestamp_str).replace(tzinfo=pytz.utc)
        tz_local = pytz.timezone(zona_str)
        dt_local = dt_utc.astimezone(tz_local)
        return dt_local.strftime('%H:%M')

    # Conversión precisa para Hoy (Índice 0) y Mañana (Índice 1)
    hora_salida = formatear_hora_local(diario['sunrise'][0], zona_horaria_local)
    hora_oculto = formatear_hora_local(diario['sunset'][0], zona_horaria_local)
    hora_salida_manana = formatear_hora_local(diario['sunrise'][1], zona_horaria_local)
    hora_oculto_manana = formatear_hora_local(diario['sunset'][1], zona_horaria_local)

    # Conversión base astronómica corregida por huso horario
    dt_sunrise_0 = datetime.fromisoformat(diario['sunrise'][0].replace('Z','')).replace(tzinfo=pytz.utc).astimezone(pytz.timezone(zona_horaria_local))
    dt_sunset_0 = datetime.fromisoformat(diario['sunset'][0].replace('Z','')).replace(tzinfo=pytz.utc).astimezone(pytz.timezone(zona_horaria_local))
    
    dt_sunrise_1 = datetime.fromisoformat(diario['sunrise'][1].replace('Z','')).replace(tzinfo=pytz.utc).astimezone(pytz.timezone(zona_horaria_local))
    dt_sunset_1 = datetime.fromisoformat(diario['sunset'][1].replace('Z','')).replace(tzinfo=pytz.utc).astimezone(pytz.timezone(zona_horaria_local))

    # --- MODELO OPERATIVO DE CONO DE SOMBRA (MÁSCARA TOPOGRÁFICA) ---
    # Debido a la proximidad del Cerro Lolog al norte/noreste y cordones del oeste,
    # el sol físico aparece más tarde y se oculta antes tras el cordón montañoso.
    # En latitudes -40° (Patagonia) en invierno, el sol viaja con un ángulo crítico muy bajo.
    
    MINUTOS_RETRASO_AMANECER = 25  # El sol tarda en superar el filo oriental de la sierra
    MINUTOS_ADELANTO_OCASO = 42    # El cono de sombra del cerro y cordón oeste cubre el viñedo antes
    
    # Aplicación de los deltas topográficos corregidos por relieve
    hora_salida = (dt_sunrise_0 + pd.Timedelta(minutes=MINUTOS_RETRASO_AMANECER)).strftime('%H:%M')
    hora_oculto = (dt_sunset_0 - pd.Timedelta(minutes=MINUTOS_ADELANTO_OCASO)).strftime('%H:%M')
    
    hora_salida_manana = (dt_sunrise_1 + pd.Timedelta(minutes=MINUTOS_RETRASO_AMANECER)).strftime('%H:%M')
    hora_oculto_manana = (dt_sunset_1 - pd.Timedelta(minutes=MINUTOS_ADELANTO_OCASO)).strftime('%H:%M')
    
    # Generar DataFrame Horario Inicial
    df_raw = pd.DataFrame({
        "Fecha/Hora": pd.to_datetime(horario.get('time')),
        "Temp (°C)": horario.get('temperature_2m'),
        "Humedad (%)": horario.get('relative_humidity_2m'),
        "Pto Rocío (°C)": horario.get('dew_point_2m'),
        "Precip (mm)": horario.get('precipitation'),
        "Viento (km/h)": horario.get('windspeed_10m'),
        "ET0 (mm/h)": horario.get('et0_fao_evapotranspiration'),
        "Radiacion (W/m²)": horario.get('shortwave_radiation'),
        "wmo": horario.get('weather_code')
    })

    # --- FILTRO ESTRATÉGICO: CADA 3 HORAS ---
    df = df_raw[df_raw["Fecha/Hora"].dt.hour % 3 == 0].copy().reset_index(drop=True)

    # --- EVALUACIÓN DE ALERTAS AGRO ---
    alertas_fito = []
    alertas_clima = []

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

    df["R-Fito (Hongos)"] = alertas_fito
    df["Alertas Clima"] = alertas_clima

    # --- VISUALIZACIÓN DE MÉTRICAS OPERATIVAS ---
    c1, c2, c3 = st.columns(3)
    c1.metric("Superficie Viñedo", f"{hectareas:.2f} Ha")
    
    et0_total_mm = df_raw["ET0 (mm/h)"].sum()
    litros_totales = et0_total_mm * area_m2
    c2.metric("Evapotranspiración Total", f"{et0_total_mm:.1f} mm", f"-{litros_totales:,.0f} L H₂O")
    
    heladas_h = df_raw[df_raw["Temp (°C)"] <= 1.5].shape[0]
    c3.metric("Horas críticas de Helada", f"{heladas_h} hrs")

    # Fila horizontal con los datos de luz solar corregidos por huso horario
    st.markdown("---")
    c_luz1, c_luz2, c_luz3, c_luz4 = st.columns(4)
    c_luz1.metric("🌅 Salida del Sol (Hoy)", f"{hora_salida} hrs")
    c_luz2.metric("🌇 Puesta del Sol (Hoy)", f"{hora_oculto} hrs")
    c_luz3.metric("🌅 Salida del Sol (Mañana)", f"{hora_salida_manana} hrs")
    c_luz4.metric("🌇 Puesta del Sol (Mañana)", f"{hora_oculto_manana} hrs")
    st.markdown("---")

    # --- TABLA DE DATOS OPTIMIZADA (CADA 3 HORAS, SIN COLUMNA LUZ SOLAR) ---
    st.subheader("📋 Matriz Operativa de Campo (Bloques de 3 Horas)")
    
    columnas_finales = [
        "Temp (°C)", "Humedad (%)", "Pto Rocío (°C)", 
        "Precip (mm)", "Viento (km/h)", 
        "R-Fito (Hongos)", "Alertas Clima"
    ]
    st.dataframe(df.set_index("Fecha/Hora")[columnas_finales], use_container_width=True)

except Exception as e:
    st.error(f"❌ Error en la matriz de análisis: {e}")
