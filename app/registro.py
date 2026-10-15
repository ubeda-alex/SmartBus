"""Registro anónimo en SQLite: sin texto del mensaje, sin nombre, sin ubicación,
sin identificador de sesión. Solo lo necesario para medir el sistema."""
import sqlite3
import threading
from typing import Optional

from .util import DATA_DIR, ahora

DB_FILE = DATA_DIR / "registro.db"


class Registro:
    def __init__(self, ruta=DB_FILE):
        self.db = sqlite3.connect(str(ruta), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS consultas ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, categoria TEXT, ruta TEXT,"
                "derivada INTEGER, sensible INTEGER, usa_llm INTEGER, tiempo_ms INTEGER,"
                "valoracion INTEGER)")
            self.db.commit()

    def guardar(self, categoria: str, ruta: Optional[str], derivada: bool, sensible: bool,
                usa_llm: bool, tiempo_ms: int) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO consultas (ts, categoria, ruta, derivada, sensible, usa_llm, tiempo_ms)"
                " VALUES (?,?,?,?,?,?,?)",
                (ahora().isoformat(timespec="seconds"), categoria, ruta, int(derivada),
                 int(sensible), int(usa_llm), tiempo_ms))
            self.db.commit()
            return cur.lastrowid

    def valorar(self, consulta_id: int, valor: int) -> bool:
        with self.lock:
            cur = self.db.execute("UPDATE consultas SET valoracion=? WHERE id=?", (valor, consulta_id))
            self.db.commit()
            return cur.rowcount == 1

    def metricas(self) -> dict:
        with self.lock:
            c = self.db
            total = c.execute("SELECT COUNT(*) FROM consultas").fetchone()[0]
            por_cat = dict(c.execute("SELECT categoria, COUNT(*) FROM consultas GROUP BY categoria"))
            derivadas = c.execute("SELECT COUNT(*) FROM consultas WHERE derivada=1").fetchone()[0]
            t_medio, rapidas = c.execute(
                "SELECT AVG(tiempo_ms), SUM(tiempo_ms <= 5000) FROM consultas").fetchone()
            sat, n_val = c.execute(
                "SELECT AVG(valoracion), COUNT(valoracion) FROM consultas").fetchone()
        return {
            "total_consultas": total,
            "por_categoria": por_cat,
            "derivadas_a_persona": derivadas,
            "tiempo_medio_ms": round(t_medio) if t_medio is not None else None,
            "pct_respuestas_5s_o_menos": round(100 * rapidas / total, 1) if total else None,
            "satisfaccion_media": round(sat, 2) if sat is not None else None,
            "valoraciones": n_val,
        }
