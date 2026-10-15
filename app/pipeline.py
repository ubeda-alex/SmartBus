"""Flujo lineal: recepción -> consulta de ubicación -> respuesta."""
from datetime import datetime
from typing import Optional

from .agentes import recepcion, respuesta, ubicacion
from .util import cargar_config, ruta_piloto


def procesar(mensaje: str, parada_ui: Optional[str], ahora: datetime, posiciones: dict,
             usar_llm: bool = True, historial: Optional[list] = None,
             sentido_ui: Optional[str] = None) -> dict:
    cfg = cargar_config()
    _, ruta = ruta_piloto(cfg)
    ids = [p["id"] for p in ruta["paradas"]]
    clasif = recepcion.clasificar(mensaje, usar_llm=usar_llm)

    parada_id = clasif["parada_id"] or parada_ui
    if parada_id not in ids:
        parada_id = None
    sentido = clasif.get("sentido") or (sentido_ui if sentido_ui in ruta["sentidos"] else None)
    # En las terminales el sentido se deduce: de La Fortuna solo se sale hacia Quesada y viceversa.
    if parada_id == ids[0]:
        sentido = "ida"
    elif parada_id == ids[-1]:
        sentido = "vuelta"

    datos = None
    if (clasif["categoria"] == "ubicacion_tiempo" and not clasif["sensible"]
            and clasif["ruta_id"] != "sin-datos" and parada_id and sentido):
        datos = ubicacion.consultar(parada_id, sentido, ahora, posiciones, cfg)

    sigs = {k: ubicacion.siguiente_salida(ahora, cfg, k) for k in ruta["sentidos"]}
    if clasif["categoria"] == "horario" and clasif.get("horario_parada") and parada_id and sentido:
        sigs["en_parada"] = {"parada": next(p["nombre"] for p in ruta["paradas"] if p["id"] == parada_id),
                             "destino": ruta["sentidos"][sentido]["destino"],
                             "pasos": ubicacion.pasos_por_parada(ahora, cfg, parada_id, sentido)}
    res = respuesta.responder(clasif, datos, sigs, parada_id, sentido, usar_llm=usar_llm,
                              mensaje=mensaje, historial=historial)
    return {"clasificacion": clasif, "datos": datos, "respuesta": res, "parada_id": parada_id,
            "sentido": sentido, "sigs": sigs}
