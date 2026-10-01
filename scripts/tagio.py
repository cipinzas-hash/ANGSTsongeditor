#!/usr/bin/env python3
"""
Lectura/escritura de tags para mp3 y m4a, con las reglas de proteccion:

  * Un tag con valor REAL nunca se pisa. Un campo vacio o con valor
    "placeholder" (Unknown Artist, Track 01, etc.) se considera ausente.
  * Los placeholders se escriben como campo VACIO, nunca como "Unknown ...".
  * Una caratula incrustada existente nunca se reemplaza.
  * El comentario de traduccion va en un frame propio (COMM con descripcion
    'Traduccion' en mp3, o el campo comment en m4a si estaba vacio) y no
    pisa un comentario existente.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4
from mutagen.id3 import ID3, APIC, COMM, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4Cover

AUDIO_EXTS = {".mp3", ".m4a"}
TRANSLATION_DESC = "Traduccion"

# ---------------------------------------------------------------- placeholders

_COMMON = {"", "?", "unknown", "desconocido", "desconocida", "n/a", "na", "null", "none"}
_ARTIST = _COMMON | {"unknown artist", "artista desconocido", "untitled artist", "no artist", "artist"}
_ALBUM = _COMMON | {"unknown album", "unknown disc", "unknown disk", "album desconocido",
                    "disco desconocido", "untitled album", "no album", "album"}
_TITLE = _COMMON | {"unknown title", "titulo desconocido", "no title", "title"}
_TRACK_RE = re.compile(r"^(track|pista|audiotrack|audio track|titel|piste)\s*\d{1,3}$")

PLACEHOLDER_SETS = {"artist": _ARTIST, "album": _ALBUM, "title": _TITLE}


def _norm_placeholder(value: str) -> str:
    v = unicodedata.normalize("NFKC", value or "").casefold().strip()
    v = re.sub(r"[<>\[\]\(\)_]+", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


def is_placeholder(field: str, value) -> bool:
    """True si el valor esta vacio o es un placeholder conocido para ese campo."""
    if value is None:
        return True
    v = _norm_placeholder(str(value))
    if v in PLACEHOLDER_SETS.get(field, _COMMON):
        return True
    if field == "title" and _TRACK_RE.match(v):
        return True
    return False


# ------------------------------------------------------------------- lectura

def kind(path: Path) -> str:
    return "m4a" if Path(path).suffix.lower() == ".m4a" else "mp3"


def _first(audio, key):
    try:
        vals = audio.get(key)
    except Exception:
        return None
    if not vals:
        return None
    v = vals[0] if isinstance(vals, (list, tuple)) else vals
    v = str(v).strip()
    return v or None


def has_cover(path: Path) -> bool:
    try:
        raw = MutagenFile(path)
        if raw is None or not getattr(raw, "tags", None):
            return False
        keys = list(raw.tags.keys())
        return any(str(k).startswith("APIC") for k in keys) or "covr" in keys
    except Exception:
        return False


def read_tags(path: Path) -> dict:
    """Lee los tags EXISTENTES tal cual (None si el campo no esta). readable
    es False si el archivo no se pudo abrir como audio."""
    out = {"readable": False, "artist": None, "album": None, "title": None,
           "albumartist": None, "track": None, "year": None, "genre": None,
           "comment": None, "has_cover": False}
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        audio = None
    if audio is None:
        return out
    out["readable"] = True
    if audio.tags is None:
        return out
    out["artist"] = _first(audio, "artist")
    out["album"] = _first(audio, "album")
    out["title"] = _first(audio, "title")
    out["albumartist"] = _first(audio, "albumartist")
    out["track"] = _first(audio, "tracknumber")
    out["year"] = _first(audio, "date")
    out["genre"] = _first(audio, "genre")
    if kind(path) == "m4a":
        out["comment"] = _first(audio, "comment")
    out["has_cover"] = has_cover(path)
    return out


def effective(tags: dict, field: str):
    """Valor real del campo (None si esta vacio o es placeholder)."""
    v = tags.get(field)
    return None if is_placeholder(field, v) else v


def is_complete(tags: dict) -> bool:
    return bool(tags.get("readable")) and all(effective(tags, f) for f in ("artist", "album", "title"))


# ------------------------------------------------------------------ escritura

def _open_easy(path: Path):
    if kind(path) == "mp3":
        try:
            return EasyID3(path)
        except ID3NoHeaderError:
            m = MP3(path)
            m.add_tags()
            m.save()
            return EasyID3(path)
    audio = EasyMP4(path)
    if audio.tags is None:
        audio.add_tags()
    return audio


def _image_mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def write_tags(path: Path, proposed: dict, existing: dict, cover_bytes=None, comment=None, overwrite=()) -> dict:
    """Escribe SOLO lo que corresponde segun las reglas de proteccion.

    proposed: valores nuevos (artist, album, title, track, year, genre).
    existing: resultado de read_tags() ANTES de tocar el archivo.
    Devuelve {campo: valor} con lo que efectivamente se escribio.

    - artist/album/title: se escriben solo si el existente esta vacio o es
      placeholder, y el valor propuesto es real.
    - albumartist: se escribe (= artista) solo si estaba vacio.
    - track/year/genre: solo si estaban vacios.
    - Un placeholder existente sin reemplazo real se LIMPIA (campo vacio).
    - overwrite: campos (artist/album/title) cuyo valor REAL puede reemplazarse;
      se usa solo para romanizar tags en script no latino (el original queda
      en el comentario y en el informe).
    """
    written = {}
    audio = _open_easy(path)

    for field in ("artist", "album", "title"):
        cur = existing.get(field)
        new = proposed.get(field)
        if field in overwrite and new and not is_placeholder(field, new):
            audio[field] = new
            written[field] = new
        elif is_placeholder(field, cur):
            if new and not is_placeholder(field, new):
                audio[field] = new
                written[field] = new
            elif cur is not None and field in audio:
                del audio[field]           # limpia el placeholder -> campo vacio
                written[field] = None

    artist_now = written.get("artist") or (existing.get("artist") if not is_placeholder("artist", existing.get("artist")) else None)
    if artist_now and (not existing.get("albumartist")
                       or ("artist" in overwrite and existing.get("albumartist") == existing.get("artist"))):
        audio["albumartist"] = artist_now
        written["albumartist"] = artist_now

    for field, tagkey in (("track", "tracknumber"), ("year", "date"), ("genre", "genre")):
        new = proposed.get(field)
        if new and not existing.get(field):
            audio[tagkey] = str(new)
            written[field] = str(new)

    if comment and kind(path) == "m4a":
        full = comment if not existing.get("comment") else (
            existing["comment"] if comment in existing["comment"] else existing["comment"] + "\n" + comment)
        audio["comment"] = full
        written["comment"] = comment

    audio.save()

    if kind(path) == "mp3":
        if comment or (cover_bytes and not existing.get("has_cover")):
            id3 = ID3(path)
            if comment:
                id3.delall(f"COMM:{TRANSLATION_DESC}:spa")
                id3.add(COMM(encoding=3, lang="spa", desc=TRANSLATION_DESC, text=comment))
                written["comment"] = comment
            if cover_bytes and not existing.get("has_cover"):
                id3.add(APIC(encoding=3, mime=_image_mime(cover_bytes), type=3, desc="Cover", data=cover_bytes))
                written["cover"] = True
            id3.save(path)
    else:
        if cover_bytes and not existing.get("has_cover"):
            m = MutagenFile(path)
            fmt = MP4Cover.FORMAT_PNG if _image_mime(cover_bytes) == "image/png" else MP4Cover.FORMAT_JPEG
            m.tags["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]
            m.save()
            written["cover"] = True
    return written


# ------------------------------------------------------- confianza de un match

def _norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\(\d+\)\s*$", "", s.strip())           # sufijo de desambiguacion de Discogs "(2)"
    s = re.sub(r"^(the|el|la|los|las)\s+", "", s.strip().casefold())
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def same_name(a: str, b: str, threshold: float = 0.82) -> bool:
    """Compara dos nombres de artista tolerando mayusculas, acentos, 'The ',
    sufijo '(2)' de Discogs y diferencias menores. Un match de Discogs solo se
    acepta si su artista coincide con el artista real conocido."""
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    if na == nb or set(na.split()) == set(nb.split()):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def strip_discogs_suffix(name: str) -> str:
    """'Artista (2)' -> 'Artista' (sufijo de desambiguacion de Discogs)."""
    return re.sub(r"\s*\(\d+\)\s*$", "", name or "").strip()
