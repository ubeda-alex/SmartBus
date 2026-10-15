import time
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import llm
from .agentes.ubicacion import velocidad_reciente
from .gps import Simulador, leer_posiciones
from .pipeline import procesar
from .registro import Registro
from .util import ahora, cargar_config, hora_txt, ruta_piloto

STATIC = Path(__file__).parent / "static"
estado = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    ruta_id, ruta = ruta_piloto(cargar_config())
    estado["sim"] = Simulador(ruta_id, ruta["longitud_km"])
    estado["sim"].iniciar()
    estado["registro"] = Registro()
    yield
    estado["sim"].detener()


app = FastAPI(title="SmartBus Asistente", lifespan=lifespan)


class Turno(BaseModel):
    rol: Literal["usuario", "asistente"]
    texto: str = Field(max_length=500)


class Consulta(BaseModel):
    mensaje: str = Field(min_length=1, max_length=300)
    parada: Optional[str] = None
    sentido: Optional[Literal["ida", "vuelta"]] = None
    # Memoria corta que envía el navegador; el servidor no la guarda.
    historial: List[Turno] = Field(default_factory=list, max_length=6)


class Valoracion(BaseModel):
    id: int
    valor: int = Field(ge=1, le=5)


class Congelar(BaseModel):
    bus: str
    congelado: bool


def _tarjeta(r: dict) -> Optional[dict]:
    """Datos estructurados para que la interfaz dibuje una tarjeta (mismos datos que el texto)."""
    cl, resp, d = r["clasificacion"], r["respuesta"], r["datos"]
    cfg = cargar_config()
    contacto = cfg["contacto"]
    if cl["sensible"]:
        return {"tipo": "emergencia", "contacto": contacto}
    if resp["derivar"]:
        motivo = {"ubicacion_tiempo": "sin_datos", "horario": "sin_servicio"}.get(cl["categoria"], "atencion")
        return {"tipo": "contacto", "motivo": motivo, "contacto": contacto}
    if d and d.get("estado") == "ok":
        base = {"destino": d["destino"], "parada": d["parada"], "origen": d["origen"],
                "siguiente": d["siguiente_salida"]}
        if d["proximo"]:
            p = d["proximo"]
            return {"tipo": "eta", **base, "eta": list(p["eta_min"]), "distancia_km": p["distancia_km"],
                    "hora_dato": p["hora_dato"], "confianza": p["confianza"], "llegando": p["llegando"],
                    "sugerir_siguiente": resp["siguiente_paso"] == "siguiente_bus"}
        if d["ultimo_pasado"]:
            return {"tipo": "pasado", **base, "hace_min": d["ultimo_pasado"]["hace_min"]}
        return {"tipo": "sin_bus", **base}
    e = r["sigs"].get("en_parada")
    if e and e["pasos"] and cl["categoria"] == "horario":
        return {"tipo": "pasos", **e}
    if cl["categoria"] == "horario":
        _, ruta = ruta_piloto(cfg)
        return {"tipo": "horario", "frecuencia_min": ruta["frecuencia_min"],
                "sentidos": [{"id": k, "origen": s["origen"], "destino": s["destino"],
                              "primera": hora_txt(s["primera"]), "ultima": hora_txt(s["ultima"]),
                              "proxima": r["sigs"].get(k)} for k, s in ruta["sentidos"].items()]}
    if cl["categoria"] == "otra":
        return {"tipo": "ayuda"}
    return None


@app.post("/api/chat")
def chat(c: Consulta):
    t0 = time.perf_counter()
    r = procesar(c.mensaje.strip(), c.parada, ahora(), leer_posiciones(),
                 historial=[t.model_dump() for t in c.historial], sentido_ui=c.sentido)
    ms = int((time.perf_counter() - t0) * 1000)
    resp, cl = r["respuesta"], r["clasificacion"]
    # Solo se guardan categoría, ruta y tiempos: nunca el texto del pasajero.
    consulta_id = estado["registro"].guardar(
        cl["categoria"], cl["ruta_id"], resp["derivar"], cl["sensible"], resp["usa_llm"], ms)
    return {"id": consulta_id, "respuesta": resp["texto"], "categoria": cl["categoria"],
            "derivada": resp["derivar"], "sugerencias": resp["sugerencias"],
            "parada": r["parada_id"], "sentido": r["sentido"], "tiempo_ms": ms,
            "tarjeta": _tarjeta(r),
            "pedir": {"indicar_parada": "parada", "indicar_sentido": "sentido"}.get(resp["siguiente_paso"]),
            "traza": {"modelo": llm.descripcion(),
                      "recepcion": "IA" if cl["metodo"] == "llm" else "reglas",
                      "llm_consultado": bool(cl.get("llm_consultado")),
                      "categoria": cl["categoria"],
                      "ubicacion": "reglas" if r["datos"] else "no aplica",
                      "respuesta": {"ia": "IA", "plantilla": "plantilla", "error_ia": "sin IA: error", "seguridad": "plantilla de seguridad"}[resp["origen"]]}}


@app.post("/api/valoracion")
def valorar(v: Valoracion):
    if not estado["registro"].valorar(v.id, v.valor):
        raise HTTPException(404, "Consulta no encontrada")
    return {"ok": True}


@app.get("/api/paradas")
def paradas():
    _, ruta = ruta_piloto(cargar_config())
    return {"paradas": [{"id": p["id"], "nombre": p["nombre"]} for p in ruta["paradas"]],
            "sentidos": [{"id": k, "nombre": f"Hacia {v['destino']}"} for k, v in ruta["sentidos"].items()]}


@app.get("/api/metricas")
def metricas():
    return estado["registro"].metricas()


@app.get("/api/demo/estado")
def demo_estado():
    ruta_id, ruta = ruta_piloto(cargar_config())
    pos = leer_posiciones().get("rutas", {}).get(ruta_id, {}).get("buses", [])
    n = ahora()
    buses = []
    for b in pos:
        if b["fixes"]:
            u = b["fixes"][-1]
            edad = int((n - datetime.fromisoformat(u["t"])).total_seconds())
            vel = velocidad_reciente(b["fixes"]) or 0.0
            en_terminal = (u["km"] <= 0.01 or u["km"] >= ruta["longitud_km"] - 0.01) and vel < 5
            buses.append({"id": b["id"], "km": u["km"], "sentido": u.get("sentido"), "edad_s": edad,
                          "vel_kmh": round(vel), "en_terminal": en_terminal})
    return {"ia_activa": llm.disponible(), "ia_proveedor": llm.descripcion(),
            "longitud_km": ruta["longitud_km"], "paradas": ruta["paradas"], "buses": buses,
            "congelados": estado["sim"].estado_congelados(),
            "sentidos": {k: v["destino"] for k, v in ruta["sentidos"].items()}}


@app.post("/api/demo/congelar")
def demo_congelar(c: Congelar):
    if not estado["sim"].congelar(c.bus, c.congelado):
        raise HTTPException(404, "Bus no encontrado")
    return {"ok": True}


@app.get("/demo")
def demo():
    return FileResponse(STATIC / "demo.html")


@app.get("/")
def inicio():
    return FileResponse(STATIC / "index.html")
