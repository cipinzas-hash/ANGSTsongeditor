#!/usr/bin/env python3
"""
Reglas GENERALES para deducir artista/album/titulo cuando los tags no sirven
(vacios, placeholders, nombres de archivo tipo slug o sin separador).

Principio de diseno: nada de nombres propios. El conocimiento sale de la propia
biblioteca (Knowledge) y de reglas editables (rules.json). Cada deduccion
informa su fuente para que el informe la muestre; si algo es ambiguo no se
adivina.
"""
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

RULES = json.loads((Path(__file__).parent / "rules.json").read_text(encoding="utf-8"))
_JUNK = [re.compile(p, re.I) for p in RULES["junk_patterns"]]
_DOMAIN_RE = re.compile(r"^[\w-]+(\.[\w-]+)*\.(" + "|".join(RULES["domain_tlds"]) + r")$", re.I)
_UNKNOWN_FOLDER_RE = re.compile(r"^(unknown|untitled|desconocid)", re.I)


# ------------------------------------------------------------------ basicos

def is_domain_like(value) -> bool:
    """'SonidosMp3Gratis.com', 'www.hotplayer.ru' -> True. Un tag que es solo un dominio es basura."""
    v = (value or "").strip()
    return bool(v) and bool(_DOMAIN_RE.match(v))


def slug(s) -> str:
    d = unicodedata.normalize("NFKD", s or "")
    d = "".join(c for c in d if unicodedata.category(c) != "Mn")
    return re.sub(r"[\W_]+", "-", d.casefold()).strip("-")


def clean_name_junk(s) -> str:
    """Quita marcas de bitrate, sitios web y numeracion '#N ' de un nombre de archivo."""
    out = s or ""
    for rx in _JUNK:
        out = rx.sub(" ", out)
    out = re.sub(r"\s+", " ", out)
    return out.strip(" -_.,;")


def title_is_raw_filename(title, stem) -> bool:
    """Un tag de titulo que es COPIA del nombre de archivo no es un titulo real, pero solo
    si el nombre muestra que es crudo: marcas de bitrate/sitio/'#N', estilo slug en minusculas
    ('code-64-dawn') o 'Artista - Titulo'. Un 'Intro' que se llama igual que su archivo si es real."""
    if not title or not stem or slug(title) != slug(stem):
        return False
    st = stem.strip()
    return (clean_name_junk(st) != st
            or bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+){2,}", st))
            or " - " in st)


def smart_title(token: str) -> str:
    """Palabra en minusculas (de un slug) -> Capitalizada; si ya trae mayusculas se respeta."""
    return token if token != token.lower() else token[:1].upper() + token[1:]


def title_from_tokens(tokens) -> str:
    return " ".join(smart_title(t) for t in tokens if t)


def _tokens(s: str):
    return [t for t in re.split(r"[\s_\-]+", s) if t]


def readable_signature(stem: str) -> str:
    """Forma legible de un nombre para AGRUPAR casos no resueltos:
    '#4 LOVE LIKE BLOOD Copycat (320 kbps)' -> '#N w w w w (N w)'."""
    t = re.sub(r"[^\W\d_]+", "w", stem or "")
    t = re.sub(r"\d+", "N", t)
    t = re.sub(r"(w\s+){2,}w", "w+", t)
    return re.sub(r"\s+", " ", t).strip()[:60]


def filename_signature(stem: str) -> str:
    """Firma estructural: marcador inicial ('#N '), marcador final ('(N kbps)') y si es slug.
    Vacia = sin estructura distintiva (no sirve para votar entre hermanos)."""
    m = re.match(r"^([^\w]*\d{1,3}[^\w]*)", stem or "")
    prefix = re.sub(r"\d+", "N", m.group(1)) if m else ""
    m2 = re.search(r"([(\[][^)\]]*\d[^)\]]*[)\]])\s*$", stem or "")
    suffix = re.sub(r"\d+", "N", m2.group(1)) if m2 else ""
    shape = "slug" if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", (stem or "").lower()) else ""
    suffix = re.sub(r"\s+", " ", suffix)
    prefix = re.sub(r"\s+", " ", prefix)
    return f"{prefix}|{suffix}|{shape}" if (prefix.strip() or suffix or shape) else ""


def nontrivial_signature(sig: str) -> bool:
    # solo numerar ("N - ") es demasiado comun para votar; hace falta marcador final o '#'
    if not sig:
        return False
    prefix, suffix, _shape = sig.split("|")
    return bool(suffix) or ("#" in prefix)


# ---------------------------------------------------------------- sanitizar

def sanitize_component(name, fallback="Desconocido") -> str:
    """Nombre de carpeta/archivo valido en Windows, FAT/exFAT y Android:
    ':' -> ' - ', '\"' -> \"'\", se quitan '?' y '*', '<>|/\\\\' -> '-', y se
    quitan puntos y espacios finales."""
    s = name or ""
    s = re.sub(r"\s*:\s*", " - ", s)
    s = s.replace('"', "'")
    s = re.sub(r"[?*]", "", s)
    s = re.sub(r"[<>|/\\\\]", "-", s)
    s = re.sub(r"[\x00-\x1f]", "", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(\s*-\s*){2,}", " - ", s)
    s = s.strip().rstrip(". ").strip()
    return s or fallback


def sanitize_filename(name: str) -> str:
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        return sanitize_component(name, "archivo")
    return sanitize_component(stem, "archivo") + "." + suffix


# ------------------------------------------------------------- conocimiento

def _real_folder(name: str) -> bool:
    return bool(name) and not _UNKNOWN_FOLDER_RE.match(name.strip()) and not is_domain_like(name)


class Knowledge:
    """Lo que la biblioteca ya sabe, deducido SOLO de rutas (sin descargar nada):
    layout <dest>/<Artista>/<Album>/<archivo>."""

    def __init__(self):
        self.artists = {}                      # slug -> nombre
        self.tracks = defaultdict(set)         # slug de titulo -> {artistas}
        self.sigs = defaultdict(Counter)       # firma -> {(artista, album): n}

    @classmethod
    def from_paths(cls, files, dest_root):
        k = cls()
        root = dest_root.rstrip("/") + "/"
        for f in files:
            if not f.startswith(root):
                continue
            parts = f[len(root):].split("/")
            if len(parts) != 3:
                continue
            artist, album, fname = parts
            if _real_folder(artist) and _real_folder(album):
                k.learn(artist, album, fname.rsplit(".", 1)[0])
        return k

    def learn(self, artist, album, stem):
        a = slug(artist)
        if not a:
            return
        self.artists[a] = artist
        ts = slug(clean_name_junk(stem))
        if ts.startswith(a + "-"):
            ts = ts[len(a) + 1:]
        if ts:
            self.tracks[ts].add(artist)
        sig = filename_signature(stem)
        if nontrivial_signature(sig) and _real_folder(album):
            self.sigs[sig][(artist, album)] += 1


def _strip_artist_prefix(cleaned: str, aslug: str):
    """Quita del inicio de `cleaned` las palabras que forman el slug del artista. -> restantes o None."""
    toks = _tokens(cleaned)
    acc = []
    for i, t in enumerate(toks):
        acc.append(slug(t))
        joined = "-".join(x for x in acc if x)
        if joined == aslug:
            return toks[i + 1:]
        if not aslug.startswith(joined):
            return None
    return None


def resolve_hints(stem: str, k: Knowledge, folder_artist=None, folder_album=None):
    """Deduce {artist, album, title, performer, source} o None. Orden de evidencia:
      1. slug_biblioteca  el nombre empieza con un artista que la biblioteca ya conoce
                          ('code-64-dawn' -> Code 64 / Dawn)
      2. hermanos_patron  nombres con la misma firma ('#N ... (N kbps)') que >= min_siblings
                          archivos ya ubicados, TODOS en el mismo (artista, album); hace falta
                          ademas que la cola del nombre sea una cancion conocida de ese artista
                          o que la firma tenga marcador inicial Y final
      3. carpeta          la carpeta <Artista>/<Album> donde esta el archivo, si es real
    """
    cleaned = clean_name_junk(stem)
    if not cleaned:
        return None
    min_len = RULES["min_artist_slug_len"]

    # 1) prefijo de artista conocido (gana el mas largo)
    best = None
    for aslug, artist in k.artists.items():
        if len(aslug) >= min_len and (best is None or len(aslug) > len(best[0])):
            rest = _strip_artist_prefix(cleaned, aslug)
            if rest:
                best = (aslug, artist, rest)
    if best:
        return {"artist": best[1], "title": title_from_tokens(best[2]), "album": None,
                "performer": None, "source": "slug_biblioteca"}

    # 2) hermanos por patron de nombre (unanimes)
    sig = filename_signature(stem)
    if nontrivial_signature(sig):
        votes = k.sigs.get(sig)
        if votes and sum(votes.values()) >= RULES["min_siblings"] and len(votes) == 1:
            (artist, album), _n = next(iter(votes.items()))
            ctoks = _tokens(cleaned)
            # el titulo es la cola que coincide con una cancion ya conocida de ese artista
            title, performer = None, None
            for i in range(len(ctoks)):
                tail = slug("-".join(ctoks[i:]))
                if tail and artist in k.tracks.get(tail, ()) and len(tail) >= 4:
                    title = title_from_tokens(ctoks[i:])
                    performer = " ".join(ctoks[:i]) or None
                    break
            prefix, suffix, _shape = sig.split("|")
            fuerte = bool(title) or ("#" in prefix and bool(suffix))   # solo el sufijo ("(N kbps)") no alcanza
            if fuerte:
                return {"artist": artist, "title": title or cleaned, "album": album,
                        "performer": performer, "source": "hermanos_patron+titulo" if title else "hermanos_patron"}

    # 3) carpeta real
    if _real_folder(folder_artist or ""):
        return {"artist": folder_artist, "title": cleaned,
                "album": folder_album if _real_folder(folder_album or "") else None,
                "performer": None, "source": "carpeta"}
    return None
