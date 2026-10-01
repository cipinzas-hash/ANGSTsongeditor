#!/usr/bin/env python3
"""Diagnostico de un minuto: prueba Gemini (romanizar + traducir japones y
coreano) y Discogs, y escribe el resultado en STATE_DIR/selftest.json. No toca
MEGA ni ningun archivo de musica. Nunca imprime ni guarda secretos."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tag_with_discogs as T

STATE_DIR = Path(os.environ.get("STATE_DIR", "state"))
out = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "gemini_model": T.GEMINI_MODEL, "gemini_key_present": bool(T.GEMINI_API_KEY),
       "gemini_key_length": len(T.GEMINI_API_KEY), "gemini": [], "discogs": None}

# Modelos disponibles para esta key (solo nombres; la key nunca se imprime)
try:
    import json as _j, urllib.request as _u
    _url = f"https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key={T.GEMINI_API_KEY}"
    with _u.urlopen(_url, timeout=20) as _r:
        _d = _j.loads(_r.read().decode("utf-8"))
    out["modelos_generateContent"] = sorted(
        m["name"].split("/")[-1] for m in _d.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", []))
except Exception as e:
    out["modelos_error"] = str(e).replace(T.GEMINI_API_KEY, "***")[:200]

pruebas = [("ja", "古い心"), ("ko", "안녕하세요"), ("ja", "ふるい心")]
for lang, texto in pruebas:
    item = {"lang": lang, "input": texto}
    try:
        item["romanizacion"] = T.gemini_generate(
            "Transcribi a caracteres latinos (solo la pronunciacion), devolve UNICAMENTE el texto: " + texto)
        item["traduccion_es"] = T.gemini_generate(
            "Traduci al espanol el significado, devolve UNICAMENTE la traduccion: " + texto)
        item["ok"] = True
    except Exception as e:
        item["ok"] = False
        item["error"] = str(e)[:300]
    out["gemini"].append(item)

try:
    r = T.discogs_get("/database/search", {"q": "Boris Flood", "type": "release"})
    out["discogs"] = {"ok": True, "resultados": len(r.get("results") or [])}
except Exception as e:
    out["discogs"] = {"ok": False, "error": str(e)[:200]}

STATE_DIR.mkdir(parents=True, exist_ok=True)
(STATE_DIR / "selftest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
