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

# Prueba de grounding (google_search), que es lo que usa narrateArchiveSummary de feeder
import json as _j2, urllib.request as _u2, urllib.error as _e2
out["grounding"] = []
for _m in ("gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-flash-latest", "gemini-2.5-flash"):
    _it = {"modelo": _m}
    try:
        _body = _j2.dumps({"contents": [{"role": "user", "parts": [{"text": "En una oracion: quien gano Super Smash Bros. Melee en CEO 2026? Si no podes confirmarlo, decilo."}]}],
                           "tools": [{"google_search": {}}], "generationConfig": {"maxOutputTokens": 300}}).encode()
        _req = _u2.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{_m}:generateContent",
                           data=_body, headers={"Content-Type": "application/json", "x-goog-api-key": T.GEMINI_API_KEY})
        with _u2.urlopen(_req, timeout=60) as _r:
            _d = _j2.loads(_r.read().decode("utf-8"))
        _c = _d["candidates"][0]
        _it["ok"] = True
        _it["texto"] = "".join(p.get("text", "") for p in _c["content"]["parts"])[:200]
        _it["con_grounding"] = bool(_c.get("groundingMetadata"))
    except _e2.HTTPError as e:
        _it["ok"] = False
        _it["error"] = f"HTTP {e.code} " + e.read().decode("utf-8", "replace")[:160].replace(T.GEMINI_API_KEY, "***")
    except Exception as e:
        _it["ok"] = False
        _it["error"] = str(e).replace(T.GEMINI_API_KEY, "***")[:160]
    out["grounding"].append(_it)

pruebas = [("ja", "古い心"), ("ko", "안녕하세요"), ("ja", "ふるい心")]
for lang, texto in pruebas:
    item = {"lang": lang, "input": texto}
    try:
        item["romanizacion"] = T.gemini_generate(
            "Transcribi a caracteres latinos (solo la pronunciacion), devolve UNICAMENTE el texto: " + texto)
        item["traduccion_es"] = T.gemini_generate(
            "Traduci al espanol el significado, devolve UNICAMENTE la traduccion: " + texto)
        item["ok"] = True
        item["modelo_usado"] = T.LAST_MODEL["name"]
    except Exception as e:
        item["ok"] = False
        item["error"] = str(e)[:300]
    out["gemini"].append(item)

out["romanizacion_prod"] = []
for _lang, _txt in (("ja", "宇多田ヒカル"), ("zh", "我的呼伦贝尔"), ("ko", "방탄소년단"), ("ru", "Кино - Группа крови"),
                    ("mn-cyr", "Хөх Тэнгэр"), ("mn-trad", "ᠮᠣᠩᠭᠣᠯ"), ("mixto", "Beyoncé 中文 Remix")):
    _n0 = T.STATS["gemini_fail"]
    _res = T.romanize_with_gemini(_txt)
    out["romanizacion_prod"].append({"lang": _lang, "entrada": _txt, "salida": _res,
                                     "ok": T.STATS["gemini_fail"] == _n0 and not T.contains_non_latin_script(_res),
                                     "tiene_diacriticos": _res != T.strip_diacritics(_res)})

try:
    r = T.discogs_get("/database/search", {"q": "Boris Flood", "type": "release"})
    out["discogs"] = {"ok": True, "resultados": len(r.get("results") or [])}
except Exception as e:
    out["discogs"] = {"ok": False, "error": str(e)[:200]}

STATE_DIR.mkdir(parents=True, exist_ok=True)
(STATE_DIR / "selftest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
