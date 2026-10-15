"""Agente 2: consulta de ubicación.

Sin modelo de lenguaje: busca la posición más reciente de cada bus que va en el
sentido del pasajero y calcula el tiempo estimado con reglas (distancia /
velocidad promedio reciente).
"""
import math
from datetime import datetime
from typing import Optional

from ..util import cargar_config, hora_corta, ruta_piloto

VEL_MIN_KMH = 10.0     # evita tiempos absurdos si el bus va casi detenido
VEL_DEFECTO_KMH = 40.0
DATO_ANTIGUO_S = 120   # pasado este tiempo, baja la confianza
DATO_DESCARTE_S = 600  # pasado este tiempo, no se usa la unidad
LLEGANDO_KM = 0.3


def _t(fix: dict) -> datetime:
    return datetime.fromisoformat(fix["t"])


def velocidad_reciente(fixes: list, n: int = 6) -> Optional[float]:
    """km/h según el camino recorrido en los últimos datos (sirve en cualquier sentido)."""
    ult = fixes[-n:]
    if len(ult) < 2:
        return None
    horas = (_t(ult[-1]) - _t(ult[0])).total_seconds() / 3600
    recorrido = sum(abs(b["km"] - a["km"]) for a, b in zip(ult, ult[1:]))
    return recorrido / horas if horas > 0 else None


def _rango(eta_min: float, confianza: str) -> tuple:
    margen = 0.2 if confianza == "alta" else 0.4
    lo = max(0, math.floor(eta_min * (1 - margen)))
    hi = math.ceil(eta_min * (1 + margen))
    return lo, max(hi, lo + 2)


def siguiente_salida(ahora: datetime, cfg: dict, sentido: str) -> Optional[str]:
    """Próxima salida desde la terminal de origen de ese sentido (ej. '6:20')."""
    _, ruta = ruta_piloto(cfg)
    s = ruta["sentidos"][sentido]
    h1, m1 = map(int, s["primera"].split(":"))
    h2, m2 = map(int, s["ultima"].split(":"))
    m_ahora = ahora.hour * 60 + ahora.minute
    for m in range(h1 * 60 + m1, h2 * 60 + m2 + 1, ruta["frecuencia_min"]):
        if m > m_ahora:
            return f"{m // 60}:{m % 60:02d}"
    return None


def pasos_por_parada(ahora: datetime, cfg: dict, parada_id: str, sentido: str, n: int = 2) -> list:
    """Próximas horas aproximadas de paso por la parada según el horario: salida de la terminal
    + tiempo de viaje hasta ahí a la velocidad programada. No usa el GPS ni el tráfico."""
    _, ruta = ruta_piloto(cfg)
    parada = next((p for p in ruta["paradas"] if p["id"] == parada_id), None)
    if parada is None:
        return []
    s = ruta["sentidos"][sentido]
    dist = parada["km"] if sentido == "ida" else ruta["longitud_km"] - parada["km"]
    viaje = round(dist / ruta["velocidad_programada_kmh"] * 60)
    h1, m1 = map(int, s["primera"].split(":"))
    h2, m2 = map(int, s["ultima"].split(":"))
    m_ahora = ahora.hour * 60 + ahora.minute
    pasos = []
    for m in range(h1 * 60 + m1, h2 * 60 + m2 + 1, ruta["frecuencia_min"]):
        if m + viaje > m_ahora:
            pasos.append(f"{(m + viaje) // 60}:{(m + viaje) % 60:02d}")
            if len(pasos) == n:
                break
    return pasos


def consultar(parada_id: str, sentido: str, ahora: datetime, posiciones: dict,
              cfg: Optional[dict] = None) -> dict:
    cfg = cfg or cargar_config()
    ruta_id, ruta = ruta_piloto(cfg)
    parada = next((p for p in ruta["paradas"] if p["id"] == parada_id), None)
    if parada is None or sentido not in ruta["sentidos"]:
        return {"estado": "sin_datos"}
    buses = posiciones.get("rutas", {}).get(ruta_id, {}).get("buses", [])

    activos = []
    for b in buses:
        if not b["fixes"]:
            continue
        ultimo = b["fixes"][-1]
        edad = (ahora - _t(ultimo)).total_seconds()
        if edad > DATO_DESCARTE_S:
            continue
        vel = max(velocidad_reciente(b["fixes"]) or VEL_DEFECTO_KMH, VEL_MIN_KMH)
        activos.append({"id": b["id"], "km": ultimo["km"], "sentido": ultimo.get("sentido"),
                        "edad": max(edad, 0), "vel": vel, "hora_dato": hora_corta(_t(ultimo))})
    if not activos:
        return {"estado": "sin_datos"}

    s = ruta["sentidos"][sentido]
    res = {"estado": "ok", "ruta": ruta["nombre"], "parada": parada["nombre"], "sentido": sentido,
           "destino": s["destino"], "origen": s["origen"], "proximo": None, "ultimo_pasado": None,
           "siguiente_salida": siguiente_salida(ahora, cfg, sentido)}

    sg = 1 if sentido == "ida" else -1  # posiciones medidas "en el sentido de la marcha"
    parada_rel = sg * parada["km"]
    mios = [dict(b, rel=sg * b["km"]) for b in activos if b["sentido"] == sentido]
    por_llegar = [b for b in mios if b["rel"] <= parada_rel]
    pasados = [b for b in mios if b["rel"] > parada_rel]
    if por_llegar:
        b = max(por_llegar, key=lambda x: x["rel"])  # el más cercano a la parada
        dist = parada_rel - b["rel"]
        confianza = "baja" if b["edad"] > DATO_ANTIGUO_S else "alta"
        lo, hi = _rango(dist / b["vel"] * 60, confianza)
        res["proximo"] = {"bus": b["id"], "distancia_km": round(dist, 1), "eta_min": (lo, hi),
                          "llegando": dist < LLEGANDO_KM, "confianza": confianza,
                          "hora_dato": b["hora_dato"], "antiguedad_s": int(b["edad"])}
    if pasados:
        b = min(pasados, key=lambda x: x["rel"])  # el que pasó más recientemente
        hace = round((b["rel"] - parada_rel) / b["vel"] * 60)
        if hace <= 30:
            res["ultimo_pasado"] = {"bus": b["id"], "hace_min": hace}
    return res
