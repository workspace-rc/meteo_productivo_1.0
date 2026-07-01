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
    r = requests.get(url, params=params, timeout
