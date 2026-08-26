#!/usr/bin/env python3
"""
Reemplaza a beets/chroma: tagea por texto (artista + titulo extraidos del
nombre de archivo) contra la API de Discogs, sin decodificar/fingerprintear
audio. Pensado para musica de nicho (industrial/EBM/underground) donde
Discogs suele tener mejor cobertura que MusicBrainz.

Requiere DISCOGS_TOKEN en el ambiente (token personal gratuito, se genera en
discogs.com -> Settings -> Developers -> Generate new token).
"""
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import json
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.mp3 import MP3

DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN", "").strip()
USER_AGENT = "UntitledTrackKiller/1.0 +https://github.com/cipinzas-hash/ANGSTsongeditor"
API_BASE = "https://api.discogs.com"
RATE_LIMIT_SLEEP = 1.1  # 60 req/min autenticado -> margen de sobra

RAW_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/musica_raw")
PROCESSED_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/musica_procesada")
AUDIO_EXTS = {".mp3"}  # el resto del pipeline (MEGA, verify_tags) asume mp3 en este bot

if not DISCOGS_TOKEN:
    print("ERROR: falta DISCOGS_TOKEN en el ambiente.", file=sys.stderr)
    sys.exit(2)


def discogs_get(path, params):
    params = dict(params)
    params["token"] = DISCOGS_TOKEN
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    time.sleep(RATE_LIMIT_SLEEP)
    return data


def clean_token(s):
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[_\.]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_filename(path: Path, album_hint_artist: str | None):
    """Extrae (artista, titulo) del nombre de archivo.
    Soporta patrones vistos en la coleccion real:
      'NN.Artista - Titulo.mp3'
      'N.Artista - Titulo.mp3'
      'Artista - Titulo.mp3'
    Si no hay separador ' - ' claro, devuelve (None, titulo_crudo).
    """
    stem = path.stem
    stem = re.sub(r"^\d{1,3}[.\s]+", "", stem)  # saca prefijo de numero de pista
    stem = clean_token(stem)

    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return clean_token(artist), clean_token(title)

    return album_hint_artist, stem


def parse_album_folder(folder_name: str):
    """'Artista - Album' -> (artista, album). Si no matchea, (None, None)."""
    name = clean_token(folder_name)
    if " - " in name:
        artist, album = name.split(" - ", 1)
        return clean_token(artist), clean_token(album)
    return None, None


def search_release(artist, album, track_title):
    """Busca en Discogs. Devuelve dict con datos del release o None."""
    if artist and album:
        q = f"{artist} {album}"
        params = {"q": q, "type": "release", "artist": artist, "release_title": album}
    elif artist and track_title:
        q = f"{artist} {track_title}"
        params = {"q": q, "type": "release", "artist": artist}
    elif track_title:
        params = {"q": track_title, "type": "release"}
    else:
        return None

    try:
        data = discogs_get("/database/search", params)
    except Exception as e:
        print(f"    [discogs] busqueda fallo: {e}")
        return None

    results = data.get("results") or []
    if not results:
        return None
    return results[0]


def fetch_release_detail(resource_url_or_id):
    try:
        if isinstance(resource_url_or_id, str) and resource_url_or_id.startswith("http"):
            req = urllib.request.Request(resource_url_or_id, headers={"User-Agent": USER_AGENT})
            params_suffix = f"?token={DISCOGS_TOKEN}"
            req.full_url += params_suffix
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        else:
            data = discogs_get(f"/releases/{resource_url_or_id}", {})
        time.sleep(RATE_LIMIT_SLEEP)
        return data
    except Exception as e:
        print(f"    [discogs] detalle de release fallo: {e}")
        return None


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


def search_itunes_track(artist, album, track_title):
    """Respaldo cuando Discogs no tiene el release: busca la CANCION puntual
    (entity=song) en la API publica de Apple (sin auth). Devuelve dict con
    lo que haya disponible: track_num, genre, year, cover_bytes. Cualquier
    campo ausente en la respuesta se deja en None, no se inventa."""
    out = {"track_num": None, "genre": None, "year": None, "cover_bytes": None}
    if not artist or not track_title:
        return out
    try:
        params = {
            "term": f"{artist} {track_title}",
            "entity": "song",
            "limit": 1,
        }
        url = f"https://itunes.apple.com/search?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results") or []
        if not results:
            return out
        r = results[0]

        out["track_num"] = r.get("trackNumber")
        out["genre"] = r.get("primaryGenreName")

        release_date = r.get("releaseDate")  # no siempre viene, no se garantiza
        if release_date and len(release_date) >= 4:
            out["year"] = release_date[:4]

        art_url = r.get("artworkUrl100")
        if art_url:
            art_url_hires = art_url.replace("100x100bb", "600x600bb")
            out["cover_bytes"] = download(art_url_hires) or download(art_url)
    except Exception as e:
        print(f"    [itunes] busqueda fallo: {e}")
    time.sleep(3.5)  # iTunes limita a ~20 req/min, esto da margen
    return out


def write_tags(path: Path, artist, album, title, track_num, year, genre, cover_bytes):
    try:
        audio = EasyID3(path)
    except ID3NoHeaderError:
        audio = MP3(path)
        audio.add_tags()
        audio.save()
        audio = EasyID3(path)

    if artist:
        audio["artist"] = artist
        audio["albumartist"] = artist
    if album:
        audio["album"] = album
    if title:
        audio["title"] = title
    if track_num:
        audio["tracknumber"] = str(track_num)
    if year:
        audio["date"] = str(year)
    if genre:
        audio["genre"] = genre
    audio.save()

    if cover_bytes:
        id3 = ID3(path)
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_bytes))
        id3.save(path)


def download(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception:
        return None


def already_tagged(path: Path) -> bool:
    try:
        audio = EasyID3(path)
    except ID3NoHeaderError:
        return False
    return bool(audio.get("artist")) and bool(audio.get("album")) and bool(audio.get("title"))


def main():
    files = sorted(p for p in RAW_DIR.rglob("*") if p.suffix.lower() in AUDIO_EXTS)
    print(f"== Tageando {len(files)} archivo(s) via Discogs (busqueda por texto) ==\n")

    for f in files:
        rel = f.relative_to(RAW_DIR)
        print(f"[{rel}]")

        if already_tagged(f):
            print("    ya tenia tags completos, se deja tal cual")
            dest = PROCESSED_DIR / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            f.rename(dest)
            continue

        folder_artist, folder_album = parse_album_folder(f.parent.name)
        file_artist, file_title = parse_filename(f, folder_artist)
        artist = file_artist or folder_artist

        if not artist or not file_title:
            print(f"    NO SE PUDO PARSEAR el nombre de archivo, se deja sin tagear")
            dest = PROCESSED_DIR / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            f.rename(dest)
            continue

        result = search_release(artist, folder_album, file_title)
        if not result:
            print(f"    sin resultados en Discogs para '{artist} - {file_title}' -> tageando con lo parseado del nombre/carpeta")
            dest = PROCESSED_DIR / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            f.rename(dest)
            if folder_album:
                itunes = search_itunes_track(artist, folder_album, file_title)
                # pista y genero via itunes (sin restriccion de album) demostraron
                # ser poco confiables en la practica (matchean contra singles/otros
                # releases con el mismo nombre de cancion) - se dejan sin escribir
                # en vez de arriesgar un dato equivocado con apariencia de certeza.
                write_tags(dest, artist, folder_album, file_title,
                           None, itunes["year"], None, itunes["cover_bytes"])
                extras = []
                if itunes["year"]:
                    extras.append(f"año={itunes['year']}")
                extras.append(f"caratula={'si' if itunes['cover_bytes'] else 'no'}")
                if itunes["track_num"] or itunes["genre"]:
                    extras.append(f"(descartado por poco confiable: pista={itunes['track_num']}, genero={itunes['genre']})")
                print(f"    FALLBACK: {artist} - {folder_album} - {file_title} (sin confirmar en Discogs; itunes: {', '.join(extras)})")
            else:
                print(f"    sin carpeta de album disponible, queda sin tagear")
            continue

        detail = fetch_release_detail(result.get("id"))
        tracklist = (detail or {}).get("tracklist", [])
        matched_track = best_track_match(tracklist, file_title)

        album_name = (detail or {}).get("title") or folder_album or result.get("title", "").split(" - ")[-1]
        real_artist = artist
        if detail and detail.get("artists"):
            real_artist = detail["artists"][0].get("name", artist)
        track_title = matched_track["title"] if matched_track else file_title
        track_num = matched_track.get("position") if matched_track else None
        year = (detail or {}).get("year") or result.get("year")
        genres = (detail or {}).get("genres") or result.get("genre") or []
        genre = ", ".join(genres) if genres else None

        cover_url = result.get("cover_image") or result.get("thumb")
        cover_bytes = download(cover_url) if cover_url else None

        dest = PROCESSED_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        f.rename(dest)

        write_tags(dest, real_artist, album_name, track_title, track_num, year, genre, cover_bytes)
        print(f"    OK: {real_artist} - {album_name} - {track_title} ({year or '?'})")

    print("\n== Tageo con Discogs terminado ==")


if __name__ == "__main__":
    main()
