import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Costa Rica no tiene horario de verano: UTC-6 fijo.
TZ_CR = timezone(timedelta(hours=-6))


def ahora() -> datetime:
    return datetime.now(TZ_CR)


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar palabras clave."""
    t = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def hora_corta(dt: datetime) -> str:
    return f"{dt.hour}:{dt.minute:02d}"


def cargar_config() -> dict:
    with open(DATA_DIR / "ruta_piloto.json", encoding="utf-8") as f:
        return json.load(f)


def numeros(texto: str) -> set:
    """Cifras presentes en un texto (enteros u horas tipo 5:46)."""
    return set(re.findall(r"\d+(?::\d+)?", texto))


def ruta_piloto(cfg: dict) -> tuple:
    """(id, datos) de la ruta piloto."""
    rid = cfg["ruta_piloto"]
    return rid, cfg["rutas"][rid]


def hora_txt(hhmm: str) -> str:
    """'05:00' -> '5:00' (como se dice en voz alta)."""
    h, m = hhmm.split(":")
    return f"{int(h)}:{m}"
