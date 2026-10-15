"""Acceso opcional a un modelo de lenguaje. Si no hay ninguno disponible, o ante
cualquier error, devuelve None y los agentes usan reglas y plantillas.

Proveedores (se elige con SMARTBUS_PROVIDER, o automático en este orden):
  ollama     local y gratis, sin cuenta (detecta el servidor en localhost:11434)
  gemini     plan gratuito de Google AI Studio  (GEMINI_API_KEY)
  groq       plan gratuito de Groq              (GROQ_API_KEY)
  anthropic  de pago                            (ANTHROPIC_API_KEY)
SMARTBUS_MODELO cambia el modelo; SMARTBUS_TIMEOUT (segundos) el tiempo máximo de espera.
"""
import os
import time
from pathlib import Path
from typing import Optional

import httpx

def _cargar_env() -> None:
    """Lee KEY=valor de .env (en la carpeta del proyecto) sin pisar variables ya definidas."""
    archivo = Path(__file__).resolve().parent.parent / ".env"
    if not archivo.exists():
        return
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, v = linea.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_cargar_env()
TIMEOUT = float(os.getenv("SMARTBUS_TIMEOUT", "8"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# Gemini y Groq usan el formato de OpenAI. Los nombres de modelo cambian con el tiempo:
# si uno deja de existir, definí SMARTBUS_MODELO.
OPENAI_COMPAT = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               "GEMINI_API_KEY", "gemini-3.1-flash-lite"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions",
             "GROQ_API_KEY", "llama-3.1-8b-instant"),
}

_cache = {"t": 0.0, "ollama": None}
_anthropic = None


def _modelo_ollama() -> Optional[str]:
    """Modelo de Ollama a usar, o None si el servidor no responde (se cachea 30 s)."""
    if time.time() - _cache["t"] < 30:
        return _cache["ollama"]
    modelo = None
    try:
        nombres = [m["name"] for m in httpx.get(f"{OLLAMA_URL}/api/tags", timeout=0.5).json()["models"]]
        nombres = [n for n in nombres if "embed" not in n]
        modelo = os.getenv("SMARTBUS_MODELO") or (nombres[0] if nombres else None)
    except Exception:
        pass
    _cache.update(t=time.time(), ollama=modelo)
    return modelo


def proveedor() -> Optional[str]:
    elegido = os.getenv("SMARTBUS_PROVIDER", "").lower()
    candidatos = [elegido] if elegido else ["ollama", "gemini", "groq", "anthropic"]
    for p in candidatos:
        if p == "ollama" and _modelo_ollama():
            return p
        if p in OPENAI_COMPAT and os.getenv(OPENAI_COMPAT[p][1]):
            return p
        if p == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
            return p
    return None


def disponible() -> bool:
    return proveedor() is not None


def redactar_con_llm() -> bool:
    """¿Debe el modelo redactar las respuestas? Por defecto no con modelos locales
    pequeños: inventan frases que ningún validador de cifras detecta. Se fuerza con
    SMARTBUS_REDACTAR_LLM=1 (o 0)."""
    forzado = os.getenv("SMARTBUS_REDACTAR_LLM")
    if forzado is not None:
        return forzado == "1" and disponible()
    return disponible() and proveedor() != "ollama"


def solo_ia() -> bool:
    """Modo 'solo IA': todo texto que ve el pasajero lo escribe el modelo (sin plantillas
    como respuesta visible). Se apaga con SMARTBUS_SOLO_IA=0. Solo aplica si el modelo redacta."""
    return os.getenv("SMARTBUS_SOLO_IA", "1") != "0" and redactar_con_llm()


def descripcion() -> str:
    p = proveedor()
    if p == "ollama":
        return f"ollama ({_modelo_ollama()})"
    return p or "ninguno (solo reglas)"


def _openai_compat(url: str, clave: Optional[str], modelo: str, system: str, user: str,
                   max_tokens: int, extra: Optional[dict] = None) -> str:
    cuerpo = {"model": modelo, "max_tokens": max_tokens, "temperature": 0,
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    cuerpo.update(extra or {})
    cab = {"Authorization": f"Bearer {clave}"} if clave else {}
    r = httpx.post(url, json=cuerpo, headers=cab, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"].strip()


def pedir(system: str, user: str, max_tokens: int = 300) -> Optional[str]:
    global _anthropic
    p = proveedor()
    try:
        if p == "ollama":
            # sin fase de razonamiento previa, para responder rápido
            return _openai_compat(f"{OLLAMA_URL}/v1/chat/completions", None, _modelo_ollama(),
                                  system, user, max_tokens, {"reasoning_effort": "none"})
        if p in OPENAI_COMPAT:
            url, var, por_defecto = OPENAI_COMPAT[p]
            return _openai_compat(url, os.getenv(var), os.getenv("SMARTBUS_MODELO", por_defecto),
                                  system, user, max_tokens)
        if p == "anthropic":
            if _anthropic is None:
                import anthropic
                _anthropic = anthropic.Anthropic(timeout=TIMEOUT, max_retries=0)
            r = _anthropic.messages.create(
                model=os.getenv("SMARTBUS_MODELO", "claude-haiku-5-5"), max_tokens=max_tokens,
                system=system, messages=[{"role": "user", "content": user}])
            return "".join(b.text for b in r.content if b.type == "text").strip()
    except Exception as e:
        print(f"[llm:{p}] fallo: {e}", flush=True)
        return None
    return None
