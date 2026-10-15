"""Simulador de GPS: sustituye a los dispositivos reales en el MVP.

Cada 30 s avanza las tres unidades (que van y vienen entre las dos terminales) y
reescribe data/posiciones.json. Los agentes solo leen ese archivo, igual que
leerían una API de GPS real.
"""
import json
import os
import random
import threading
from datetime import datetime, timedelta
from typing import Callable, Optional

from .util import DATA_DIR, ahora

POS_FILE = DATA_DIR / "posiciones.json"
INTERVALO_S = 30
MAX_HISTORIAL = 10
PAUSA_TERMINAL_PASOS = 6  # 3 minutos esperando en la terminal antes de volver


def leer_posiciones(ruta_archivo=POS_FILE) -> dict:
    try:
        with open(ruta_archivo, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class Simulador:
    def __init__(self, ruta_id: str, longitud_km: float, reloj: Callable[[], datetime] = ahora,
                 archivo=POS_FILE, semilla: Optional[int] = None):
        self.ruta_id = ruta_id
        self.longitud = longitud_km
        self.reloj = reloj
        self.archivo = archivo
        self.rng = random.Random(semilla)
        self.lock = threading.Lock()
        self._parar = threading.Event()
        # dir = +1 va hacia Ciudad Quesada (ida); -1 hacia La Fortuna (vuelta).
        # La posición inicial es antes del "calentamiento" que genera el historial.
        self.buses = [
            {"id": "Bus 1", "km": 14.0, "dir": 1, "pausa": 0, "congelado": False, "fixes": []},
            {"id": "Bus 2", "km": 44.0, "dir": -1, "pausa": 0, "congelado": False, "fixes": []},
            {"id": "Bus 3", "km": 0.0, "dir": 1, "pausa": 0, "congelado": False, "fixes": []},
        ]
        base = self.reloj()
        for i in range(MAX_HISTORIAL):
            self._avanzar(base - timedelta(seconds=INTERVALO_S * (MAX_HISTORIAL - 1 - i)))
        self._escribir()

    def _avanzar(self, t: datetime) -> None:
        for b in self.buses:
            if b["pausa"] > 0:  # esperando en la terminal
                b["pausa"] -= 1
            else:
                v = self.rng.uniform(35, 60)  # km/h
                b["km"] += b["dir"] * v * INTERVALO_S / 3600
                if b["km"] >= self.longitud or b["km"] <= 0:
                    b["km"] = min(max(b["km"], 0.0), self.longitud)
                    b["dir"] = -b["dir"]  # ya va a salir en sentido contrario
                    b["pausa"] = PAUSA_TERMINAL_PASOS
            if not b["congelado"]:
                b["fixes"].append({"t": t.isoformat(timespec="seconds"), "km": round(b["km"], 3),
                                   "sentido": "ida" if b["dir"] == 1 else "vuelta"})
                del b["fixes"][:-MAX_HISTORIAL]

    def _escribir(self) -> None:
        datos = {
            "actualizado": self.reloj().isoformat(timespec="seconds"),
            "rutas": {self.ruta_id: {"buses": [{"id": b["id"], "fixes": b["fixes"]} for b in self.buses]}},
        }
        tmp = str(self.archivo) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False)
        os.replace(tmp, self.archivo)  # escritura atómica: nunca se lee a medias

    def paso(self) -> None:
        with self.lock:
            self._avanzar(self.reloj())
            self._escribir()

    def congelar(self, bus_id: str, congelado: bool) -> bool:
        with self.lock:
            for b in self.buses:
                if b["id"] == bus_id:
                    b["congelado"] = congelado
                    return True
        return False

    def estado_congelados(self) -> dict:
        return {b["id"]: b["congelado"] for b in self.buses}

    def iniciar(self) -> None:
        def bucle():
            while not self._parar.wait(INTERVALO_S):
                self.paso()
        threading.Thread(target=bucle, daemon=True).start()

    def detener(self) -> None:
        self._parar.set()
