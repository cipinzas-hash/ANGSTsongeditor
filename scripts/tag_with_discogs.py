#!/usr/bin/env python3
"""
Tagea por texto (artista + titulo del nombre de archivo, o de los tags que ya
tenga) contra Discogs, con respaldo de iTunes para año/caratula. Sin
decodificar ni fingerprintear audio. Pensado para musica de nicho.

Reglas de proteccion (ver tagio.py):
  * un tag con valor real nunca se pisa; un placeholder (Unknown Artist, etc.)
    cuenta como ausente y se completa o se limpia (campo vacio);
  * una caratula incrustada existente nunca se reemplaza;
  * un match de Discogs solo se acepta si su artista coincide con el artista
    real conocido.

Requiere DISCOGS_TOKEN en el ambiente.
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tagio
from tagio import (AUDIO_EXTS, effective, is_complete, read_tags, same_name,
                   strip_discogs_suffix, write_tags)

DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# gemini-2.0-flash fue retirado (404). El alias -latest sigue al flash vigente.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-flash-latest"
USER_AGENT = "UntitledTrackKiller/1.0 +https://github.com/cipinzas-hash/ANGSTsongeditor"
API_BASE = "https://api.discogs.com"
RATE_LIMIT_SLEEP = 1.1  # 60 req/min autenticado -> margen de sobra

RAW_DIR = Path(os.environ.get("RAW_DIR", "/tmp/musica_raw"))
PROCESSED_DIR = Path(os.environ.get("PROCESSED_DIR", "/tmp/musica_procesada"))

# Contadores de la corrida (los lee job.py para el informe). Un fallo de
# Gemini ya no es silencioso: se cuenta y se guarda el motivo.
STATS = {"gemini_ok": 0, "gemini_fail": 0, "gemini_errors": []}


class DiscogsError(Exception):
    pass


NON_LATIN_RE = re.compile(
    "["
    "\u3040-\u30ff"   # hiragana + katakana
    "\u4e00-\u9fff"   # CJK unificado (chino + kanji)
    "\uac00-\ud7af"   # hangul (coreano)
    "\u0400-\u04ff"   # cirílico
    "\u0370-\u03ff"   # griego
    "\u0600-\u06ff"   # árabe
    "\u0590-\u05ff"   # hebreo
    "\u0e00-\u0e7f"   # tailandés
    "\u0900-\u097f"   # devanagari (hindi)
    "]"
)


def contains_non_latin_script(text):
    return bool(text) and bool(NON_LATIN_RE.search(text))


# ------------------------------------------------------------------- Gemini

def _gem_fail(reason: str):
    STATS["gemini_fail"] += 1
    r = reason[:200]
    if r not in STATS["gemini_errors"] and len(STATS["gemini_errors"]) < 5:
        STATS["gemini_errors"].append(r)
    print(f"    [gemini] FALLO: {r}")


def gemini_generate(prompt: str, timeout: int = 20) -> str:
    """Llama a Gemini y devuelve el texto. Lanza excepcion si falla. La API
    key viaja en la URL: nunca se imprime la URL ni se la incluye en errores."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:160].replace(GEMINI_API_KEY, "***")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} modelo={GEMINI_MODEL} {body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"red: {e.reason}") from None
    return data["candidates"][0]["content"]["parts"][0]["text"].strip().strip('"')


def romanize_with_gemini(text):
    """Romanizacion a caracteres latinos de un tag en script no latino. Si
    falla, devuelve el original y CUENTA el fallo (STATS) para que el informe
    lo muestre."""
    if not contains_non_latin_script(text):
        return text
    if not GEMINI_API_KEY:
        _gem_fail("falta GEMINI_API_KEY")
        return text
    try:
        prompt = (
            "Transcribi el siguiente texto a caracteres latinos (romaji si es "
            "japones, romanizacion revisada si es coreano, pinyin si es "
            "chino, o el sistema de romanizacion estandar que corresponda "
            "segun el idioma real del texto), tal como se veria en un tag de "
            "metadata de musica (nombre de artista, album o cancion). No "
            "traduzcas el significado, solo transcribi la pronunciacion. "
            "Devolve UNICAMENTE el texto transcripto, sin comillas, sin "
            f"explicacion, sin texto adicional. Texto: {text}"
        )
        out = gemini_generate(prompt)
        time.sleep(1.5)
        if not out or contains_non_latin_script(out):
            _gem_fail("respuesta vacia o aun en script no latino")
            return text
        STATS["gemini_ok"] += 1
        return out
    except Exception as e:
        _gem_fail(str(e))
        return text


def translate_with_gemini(text):
    """Traduccion al español del SIGNIFICADO (para contexto, no reemplaza el
    tag). Nunca sobre nombres de artista. None si falla (se cuenta)."""
    if not contains_non_latin_script(text):
        return None
    if not GEMINI_API_KEY:
        _gem_fail("falta GEMINI_API_KEY")
        return None
    try:
        prompt = (
            "Traduci al espanol el SIGNIFICADO (no la pronunciacion) del "
            "siguiente titulo de cancion o album de musica. Traduccion "
            "natural y breve, como quedaria el titulo si se publicara en "
            "espanol -- no una traduccion literal palabra por palabra si "
            "sale forzada. Devolve UNICAMENTE la traduccion, sin comillas, "
            f"sin explicacion, sin texto adicional. Texto: {text}"
        )
        out = gemini_generate(prompt)
        time.sleep(1.5)
        if not out:
            _gem_fail("traduccion vacia")
            return None
        STATS["gemini_ok"] += 1
        return out
    except Exception as e:
        _gem_fail(str(e))
        return None


def build_translation_comment(title_original, title_es, album_original, album_es):
    partes = []
    if title_es and title_es != title_original:
        partes.append(f'Cancion: "{title_es}"')
    if album_es and album_es != album_original:
        partes.append(f'Disco: "{album_es}"')
    return f"Traducción -- {' / '.join(partes)}" if partes else None


# ------------------------------------------------------------------ Discogs

def discogs_get(path, params):
    params = dict(params)
    params["token"] = DISCOGS_TOKEN
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise DiscogsError(str(e).replace(DISCOGS_TOKEN, "***")) from None
    time.sleep(RATE_LIMIT_SLEEP)
    return data


def clean_token(s):
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[_\.]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_filename(path: Path, album_hint_artist):
    """'NN.Artista - Titulo.mp3' | 'Artista - Titulo.mp3' -> (artista, titulo).
    Sin separador ' - ' claro: (album_hint_artist, stem)."""
    stem = path.stem
    stem = re.sub(r"^\d{1,3}[.\s]+", "", stem)
    stem = clean_token(stem)
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return clean_token(artist), clean_token(title)
    return album_hint_artist, stem


def parse_album_folder(folder_name: str):
    name = clean_token(folder_name)
    if " - " in name:
        artist, album = name.split(" - ", 1)
        return clean_token(artist), clean_token(album)
    return None, None


def search_release(artist, album, track_title):
    """Lista de resultados de Discogs (puede ser vacia). Lanza DiscogsError si
    la API falla -- distinto de 'no hay resultados'."""
    if artist and album:
        params = {"q": f"{artist} {album}", "type": "release", "artist": artist, "release_title": album}
    elif artist and track_title:
        params = {"q": f"{artist} {track_title}", "type": "release", "artist": artist}
    else:
        return []
    data = discogs_get("/database/search", params)
    return data.get("results") or []


def pick_candidate(results, artist):
    """Primer resultado (de los 5 primeros) cuyo artista coincide con el real.
    El titulo de un resultado de Discogs viene como 'Artista - Album'."""
    for r in results[:5]:
        t = r.get("title", "")
        if " - " not in t:
            continue
        if same_name(artist, t.split(" - ", 1)[0]):
            return r
    return None


def fetch_release_detail(release_id):
    return discogs_get(f"/releases/{release_id}", {})


def best_track_match(tracklist, guessed_title):
    if not tracklist or not guessed_title:
        return None
    guessed_norm = clean_token(guessed_title).lower()
    for t in tracklist:
        if clean_token(t.get("title", "")).lower() == guessed_norm:
            return t
    for t in tracklist:
        tt = clean_token(t.get("title", "")).lower()
        if tt and (tt in guessed_norm or guessed_norm in tt):
            return t
    return None


def download(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception:
        return None


def search_itunes_track(artist, album, track_title):
    """Respaldo: año y caratula desde iTunes. Pista y genero se descartan a
    proposito (poco confiables en la practica)."""
    out = {"year": None, "cover_bytes": None}
    if not artist or not track_title:
        return out
    try:
        params = {"term": f"{artist} {track_title}", "entity": "song", "limit": 1}
        url = f"https://itunes.apple.com/search?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results") or []
        if results:
            r = results[0]
            # solo se acepta si el artista coincide
            if same_name(artist, r.get("artistName", "")):
                rd = r.get("releaseDate")
                if rd and len(rd) >= 4:
                    out["year"] = rd[:4]
                art_url = r.get("artworkUrl100")
                if art_url:
                    out["cover_bytes"] = download(art_url.replace("100x100bb", "600x600bb")) or download(art_url)
    except Exception as e:
        print(f"    [itunes] busqueda fallo: {e}")
    time.sleep(3.5)  # iTunes limita a ~20 req/min
    return out


# ------------------------------------------------------------------ proceso

def already_tagged(path: Path) -> bool:
    return is_complete(read_tags(path))


def _ensure_moved(f: Path, dest: Path):
    if f.exists() and not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        f.rename(dest)


def _snapshot(tags: dict) -> dict:
    return {k: tags.get(k) for k in ("artist", "album", "title", "albumartist", "track", "year", "genre", "has_cover")}


def process_file_ex(f: Path, raw_dir: Path = None, processed_dir: Path = None, attempt: int = 1):
    """Procesa un archivo. Devuelve (dest, info).

    info["status"]:
      ya_completo  tags reales completos: no se toca nada
      ok           artista + titulo reales (album real o vacio)
      sin_artista / sin_titulo / incompleto  -> no se pudo; el original queda intacto
      reintentar   fallo transitorio (Discogs/Gemini) y attempt < 2; no se escribio nada
      error        no legible o excepcion
    dest es None cuando el archivo no se modifico ni se movio.
    """
    raw_dir = raw_dir or RAW_DIR
    processed_dir = processed_dir or PROCESSED_DIR
    rel = f.relative_to(raw_dir)
    dest = processed_dir / rel
    info = {"rel": str(rel), "status": None, "source": None, "before": None, "after": None,
            "written": {}, "note": None, "gemini_fail": 0, "no_latin": False}
    g0 = STATS["gemini_fail"]
    print(f"[{rel}]")

    try:
        existing = read_tags(f)
        info["before"] = _snapshot(existing)
        if not existing["readable"]:
            info.update(status="error", note="archivo no legible")
            return None, info

        if is_complete(existing):
            print("    ya tenia tags reales completos, se deja tal cual")
            _ensure_moved(f, dest)
            info.update(status="ya_completo", source="existente", after=info["before"])
            info["no_latin"] = any(contains_non_latin_script(existing.get(k)) for k in ("artist", "album", "title"))
            return dest, info

        folder_artist, folder_album = parse_album_folder(f.parent.name)
        file_artist, file_title = parse_filename(f, folder_artist)
        artist = effective(existing, "artist") or file_artist or folder_artist
        album_known = effective(existing, "album") or folder_album
        title = effective(existing, "title") or file_title

        if not artist:
            info.update(status="sin_artista", note="no hay artista confiable (ni en tags ni en el nombre)")
            print(f"    SIN ARTISTA confiable -> no procesable")
            return None, info
        if not title:
            info.update(status="sin_titulo", note="no hay titulo")
            return None, info

        # ---- busqueda (todas las consultas de red ANTES de escribir) ----
        cand = None
        discogs_failed = False
        try:
            results = search_release(artist, album_known, title)
            cand = pick_candidate(results, artist)
            if results and not cand:
                print(f"    Discogs: resultados descartados (artista no coincide con '{artist}')")
        except DiscogsError as e:
            discogs_failed = True
            print(f"    [discogs] busqueda fallo: {e}")

        detail = None
        if cand:
            try:
                detail = fetch_release_detail(cand.get("id"))
            except DiscogsError as e:
                discogs_failed = True
                print(f"    [discogs] detalle fallo: {e}")

        if discogs_failed and attempt < 2:
            info.update(status="reintentar", note="Discogs no respondio; se reintenta")
            return None, info

        proposed = {"artist": artist, "album": album_known, "title": title}
        cover_bytes = None
        source = "nombre"

        confirmed = False
        if detail:
            d_artists = detail.get("artists") or []
            d_artist = strip_discogs_suffix(d_artists[0].get("name", "")) if d_artists else ""
            d_album = detail.get("title") or ""
            artist_ok = (not d_artist) or same_name(artist, d_artist)
            album_ok = (not album_known) or (not d_album) or same_name(album_known, d_album)
            if artist_ok and album_ok:
                confirmed = True
                matched = best_track_match(detail.get("tracklist", []), title)
                proposed["artist"] = d_artist or artist
                proposed["album"] = d_album or album_known
                proposed["title"] = matched["title"] if matched else title
                proposed["track"] = matched.get("position") if matched else None
                proposed["year"] = detail.get("year") or cand.get("year")
                genres = detail.get("genres") or cand.get("genre") or []
                proposed["genre"] = ", ".join(genres) if genres else None
                cu = cand.get("cover_image") or cand.get("thumb")
                if cu and not existing.get("has_cover"):
                    cover_bytes = download(cu)
                source = "discogs"
            else:
                print(f"    Discogs: match descartado (artista/album no coinciden)")

        if not confirmed and album_known and not existing.get("has_cover"):
            it = search_itunes_track(artist, album_known, title)
            if it["year"]:
                proposed["year"] = it["year"]
            if it["cover_bytes"]:
                cover_bytes = it["cover_bytes"]
                source = "itunes"

        # ---- solo se procesa (romaniza/traduce) lo que realmente se va a escribir ----
        for field in ("artist", "album", "title"):
            if effective(existing, field):
                proposed[field] = None      # valor real existente: no se toca
        title_w, album_w = proposed.get("title"), proposed.get("album")
        comment = None
        if title_w or album_w:
            comment = build_translation_comment(
                title_w, translate_with_gemini(title_w) if title_w else None,
                album_w, translate_with_gemini(album_w) if album_w else None)
        for field in ("artist", "album", "title"):
            if proposed.get(field):
                proposed[field] = romanize_with_gemini(proposed[field])

        info["gemini_fail"] = STATS["gemini_fail"] - g0
        if info["gemini_fail"] and attempt < 2:
            info.update(status="reintentar", note="Gemini fallo; se reintenta antes de subir sin romanizar")
            return None, info

        _ensure_moved(f, dest)
        written = write_tags(dest, proposed, existing, cover_bytes, comment)
        after = read_tags(dest)
        info["written"] = {k: v for k, v in written.items() if k != "comment" or v}
        info["after"] = _snapshot(after)
        info["source"] = source
        info["no_latin"] = any(contains_non_latin_script(after.get(k)) for k in ("artist", "album", "title"))
        if effective(after, "artist") and effective(after, "title"):
            info["status"] = "ok"
            print(f"    OK ({source}): {after.get('artist')} - {after.get('album') or '(sin album)'} - {after.get('title')}")
        else:
            info.update(status="incompleto", note="faltan artista/titulo tras el tageo")
        return dest, info

    except Exception as e:
        print(f"    ERROR procesando este archivo: {e}")
        info.update(status="error", note=str(e)[:200])
        return None, info


def process_file(f: Path, raw_dir: Path = None, processed_dir: Path = None):
    """Compatibilidad con el CLI antiguo: devuelve la ruta de destino."""
    raw_dir = raw_dir or RAW_DIR
    processed_dir = processed_dir or PROCESSED_DIR
    dest, _ = process_file_ex(f, raw_dir, processed_dir)
    return dest or (processed_dir / f.relative_to(raw_dir))


def main():
    if not DISCOGS_TOKEN:
        print("ERROR: falta DISCOGS_TOKEN en el ambiente.", file=sys.stderr)
        sys.exit(2)
    raw = Path(sys.argv[1]) if len(sys.argv) > 1 else RAW_DIR
    processed = Path(sys.argv[2]) if len(sys.argv) > 2 else PROCESSED_DIR
    files = sorted(p for p in raw.rglob("*") if p.suffix.lower() in AUDIO_EXTS)
    print(f"== Tageando {len(files)} archivo(s) via Discogs (busqueda por texto) ==\n")
    for f in files:
        process_file(f, raw, processed)
    print("\n== Tageo con Discogs terminado ==")


if __name__ == "__main__":
    main()
