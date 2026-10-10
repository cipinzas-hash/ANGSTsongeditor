#!/usr/bin/env python3
"""
Trabajo con inicio y fin para ANGSTsongeditor.

Acciones (variable de ambiente ACTION):
  start      activa un trabajo nuevo (o ajusta batch_size si ya hay uno activo)
             y corre el primer lote de inmediato
  run        la corrida del cron: si el trabajo no esta activo, sale sin hacer nada
  stop       desactiva el trabajo
  inventory  cuenta por extension lo que hay en la fuente (no descarga ni mueve nada)
  selftest   ver selftest.py

El estado vive en la rama `job-state` (STATE_DIR): state.json, report.jsonl,
summary.md, leftovers.json, inventory.json. Los originales solo se tocan
despues de confirmar la subida; nada se resetea.

Destinos:
  MEGA_DEST/<Artista>/<Album | Untitled album>/   procesados
  MEGA_NONPROC/<ruta relativa>                    no procesados (con el motivo en el informe)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tagio import AUDIO_EXTS
from resolve import (Knowledge, build_dir_canon, dirkey, is_domain_like, readable_signature,
                     resolve_hints, sanitize_component, sanitize_filename)
from tag_with_discogs import contains_non_latin_script

STATE_DIR = Path(os.environ.get("STATE_DIR", "state"))
MEGA_DEST = os.environ.get("MEGA_DEST", "/untitledless").rstrip("/")
MEGA_NONPROC = os.environ.get("MEGA_NONPROC", "/untitledless-nonprocessed").rstrip("/")
RUN_BUDGET_SEC = int(os.environ.get("RUN_BUDGET_SEC", "2400"))
UNTITLED_ALBUM = "Untitled album"
MAX_ATTEMPTS = 2
UNSUPPORTED_AUDIO = {".flac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".aac", ".aiff", ".aif", ".aifc",
                     ".ape", ".wv", ".mka", ".m4b", ".mp2", ".mpc", ".dsf", ".alac", ".amr"}
AUDIOISH = AUDIO_EXTS | UNSUPPORTED_AUDIO
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
FOLDER_IMAGE_RE = re.compile(r"^(album|albumart.*|cover|folder|front|art|artwork)$", re.I)
CHECKPOINT_EVERY = 20
SESSION_ENTRIES = []      # entradas del informe de ESTA corrida (sesion)
ACCEPTED_EXIT1 = [0]      # veces que MEGAcmd dio exit != 0 con el resultado confirmado
SESSION = {}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AbortRun(Exception):
    pass


# ------------------------------------------------------------------- estado

def load_state():
    p = STATE_DIR / "state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"status": "idle", "mega_source": None, "batch_size": 5, "runs": 0,
            "counts": {}, "attempts": {}, "dir_dest": {}, "gemini": {"ok": 0, "fail": 0, "errors": []},
            "history": []}


def save_state(st):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def append_report(entry):
    SESSION_ENTRIES.append(entry)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "report.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def git(*args, check=False):
    return subprocess.run(["git", "-C", str(STATE_DIR), *args], capture_output=True, text=True, check=check)


def push_state(message):
    """Commit + push de la rama de estado. Reintenta con rebase. No lanza."""
    if not (STATE_DIR / ".git").exists():
        return
    try:
        git("add", "-A")
        if not git("status", "--porcelain").stdout.strip():
            return
        git("commit", "-q", "-m", message)
        for _ in range(3):
            if git("push", "-q", "origin", "HEAD:job-state").returncode == 0:
                return
            git("pull", "-q", "--rebase", "origin", "job-state")
        print("AVISO: no se pudo empujar el estado a job-state")
    except Exception as e:
        print(f"AVISO: push de estado fallo: {e}")


def write_summary(st, note=None):
    c = st.get("counts", {})
    g = st.get("gemini", {})
    lines = [f"# ANGSTsongeditor — estado del trabajo", "",
             f"- Estado: **{st['status']}**  ·  fuente: `{st.get('mega_source')}`  ·  lote: {st.get('batch_size')}",
             f"- Iniciado: {st.get('started_at')}  ·  ultima corrida: {st.get('last_run')}  ·  corridas: {st.get('runs')}",
             f"- Pendientes en la fuente (ultimo conteo): {st.get('pending')}", ""]
    if note:
        lines += [f"> {note}", ""]
    lines += ["## Conteos acumulados", ""]
    for k in ("uploaded", "moved", "replaced", "sin_cambios", "romanizados", "ya_completo", "con_album", "sin_album", "nonprocessed", "retry", "sidecars", "orphans_rescued", "folder_images",
              "no_latin_kept", "dup_renamed", "dirs_removed"):
        lines.append(f"- {k}: {c.get(k, 0)}")
    reasons = c.get("nonprocessed_reasons", {})
    if reasons:
        lines += ["", "## No procesados por motivo", ""] + [f"- {k}: {v}" for k, v in sorted(reasons.items())]
    lines += ["", "## Gemini", "", f"- ok: {g.get('ok', 0)}  ·  fallos: {g.get('fail', 0)}"]
    for e in g.get("errors", []):
        lines.append(f"- error: `{e}`")
    (STATE_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------- MEGA

_timeouts = 0


def mega(args, timeout=300, check=True, verify=None):
    """Corre un comando mega-*, con timeout. Tres timeouts seguidos abortan la corrida."""
    global _timeouts
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        _timeouts = 0
    except subprocess.TimeoutExpired:
        _timeouts += 1
        if _timeouts >= 3:
            raise AbortRun(f"3 timeouts seguidos de mega ({args[0]})")
        raise RuntimeError(f"timeout de {timeout}s en {args[0]}")
    if check and r.returncode != 0:
        # MEGAcmd a veces devuelve exit 1 ("mega-cmd-server process seems to have
        # stopped") aunque la operacion se completo. Si el efecto se puede
        # comprobar, se comprueba y se acepta.
        try:
            confirmed = bool(verify and verify())
        except Exception:
            confirmed = False
        if confirmed:
            ACCEPTED_EXIT1[0] += 1
            print(f"    (aviso: {args[0]} devolvio exit {r.returncode} pero el resultado esta confirmado)")
            return r
        raise RuntimeError(f"{' '.join(args[:2])} fallo (exit {r.returncode}): {clean_err(r)}")
    return r


def clean_err(r):
    """Cola del mensaje de error de mega-*, sin barras de progreso (TRANSFERRING ...)."""
    txt = (r.stderr or "") + "\n" + (r.stdout or "")
    lines = []
    for l in txt.replace("\r", "\n").replace("\x00", "").splitlines():
        l = l.strip()
        if l and "TRANSFERRING" not in l and "Initiating MEGAcmd" not in l and "Resuming session" not in l:
            lines.append(l)
    return " | ".join(lines[-4:])[:300] or "(sin mensaje)"


def list_files(root):
    r = mega(["mega-find", root, "--type=f"])
    return sorted(l.strip() for l in r.stdout.splitlines() if l.strip())


def list_dirs(root):
    r = mega(["mega-find", root, "--type=d"])
    return [l.strip().rstrip("/") for l in r.stdout.splitlines() if l.strip()]


def remote_exists(path):
    return mega(["mega-ls", path], check=False).returncode == 0


def sanitize_folder_name(name):
    return sanitize_component(name)


def unique_name(remote_dir, name):
    """Nombre que no choque con algo existente en remote_dir."""
    if not remote_exists(f"{remote_dir}/{name}"):
        return name
    stem, suffix = os.path.splitext(name)
    for i in range(2, 60):
        cand = f"{stem} ({i}){suffix}"
        if not remote_exists(f"{remote_dir}/{cand}"):
            return cand
    raise RuntimeError(f"demasiados duplicados de {name}")


def move_remote(src, dest_dir, new_name=None):
    mega(["mega-mkdir", "-p", dest_dir], check=False)
    target = f"{dest_dir}/{new_name}" if new_name else dest_dir + "/"
    final = f"{dest_dir}/{new_name or os.path.basename(src)}"
    mega(["mega-mv", src, target], verify=lambda: remote_exists(final) and not remote_exists(src))


# ------------------------------------------------------------------ indices

def ext(p):
    return os.path.splitext(p)[1].lower()


def build_index(files):
    """(carpeta, nombre-base sin extension en minusculas) -> [archivos]"""
    idx = {}
    for f in files:
        d, name = os.path.split(f)
        idx.setdefault((d, os.path.splitext(name)[0].casefold()), []).append(f)
    return idx


def sidecars_of(song, index):
    """Archivos no audio con el mismo nombre base que la cancion, en la misma
    carpeta. Si hay mas de un archivo de audio con ese nombre base, es ambiguo
    y no se mueve ninguno."""
    d, name = os.path.split(song)
    group = index.get((d, os.path.splitext(name)[0].casefold()), [])
    audio = [x for x in group if ext(x) in AUDIOISH]
    if len(audio) != 1:
        return [], len(group) > len(audio)
    return [x for x in group if ext(x) not in AUDIOISH], False


# ------------------------------------------------------------- mover cosas

class Ctx:
    def __init__(self, st, source, files):
        self.st = st
        self.source = source
        self.files = files
        self.index = build_index(files)
        self.handled = set()
        self.t0 = time.time()
        self.since_checkpoint = 0
        self.dl_streak = 0
        self.done = set()
        self.knowledge = Knowledge()
        self.anomalies = []
        self.dir_canon = {}

    def canon_artist(self, name):
        return self.dir_canon.setdefault(("a", dirkey(name)), name)

    def canon_album(self, artist_dir, name):
        return self.dir_canon.setdefault(("b", dirkey(artist_dir), dirkey(name)), name)

    def rel(self, remote):
        return remote[len(self.source):].lstrip("/")

    def folder_parts(self, remote):
        """(artista, album) segun la carpeta <MEGA_DEST>/<Artista>/<Album>/ donde esta el archivo."""
        root = MEGA_DEST.rstrip("/") + "/"
        if remote.startswith(root):
            parts = remote[len(root):].split("/")
            if len(parts) == 3:
                return parts[0], parts[1]
        return None, None

    def count(self, key, n=1):
        c = self.st["counts"]
        c[key] = c.get(key, 0) + n

    def track_dir(self, remote, dest_label):
        d = os.path.dirname(remote)
        s = set(self.st["dir_dest"].get(d, []))
        s.add(dest_label)
        self.st["dir_dest"][d] = sorted(s)


def move_sidecars(ctx, song, dest_dir, old_stem, new_stem):
    moved, ambiguous = [], False
    sc, ambiguous = sidecars_of(song, ctx.index)
    for s in sc:
        name = os.path.basename(s)
        suffix = name[len(old_stem):] if name.casefold().startswith(old_stem.casefold()) else os.path.splitext(name)[1]
        new_name = new_stem + suffix
        try:
            final = unique_name(dest_dir, new_name)
            move_remote(s, dest_dir, None if final == name else final)
            moved.append(os.path.basename(s))
            ctx.handled.add(s)
            ctx.count("sidecars")
        except RuntimeError as e:
            print(f"    AVISO: no se pudo mover el acompañante {name}: {e}")
    return moved, ambiguous


def to_nonprocessed(ctx, remote, reason):
    rel_dir = os.path.dirname(ctx.rel(remote))
    dest_dir = f"{MEGA_NONPROC}/{rel_dir}".rstrip("/")
    name = os.path.basename(remote)
    final = unique_name(dest_dir, name) if remote_exists(dest_dir) else name
    move_remote(remote, dest_dir, None if final == name else final)
    ctx.handled.add(remote)
    stem_old = os.path.splitext(name)[0]
    moved, _ = move_sidecars(ctx, remote, dest_dir, stem_old, os.path.splitext(final)[0])
    ctx.count("nonprocessed")
    reasons = ctx.st["counts"].setdefault("nonprocessed_reasons", {})
    reasons[reason] = reasons.get(reason, 0) + 1
    ctx.track_dir(remote, "NONPROC")
    ctx.st["attempts"].pop(remote, None)
    print(f"    -> NO PROCESADO ({reason}): {dest_dir}/{final}")
    return dest_dir, moved


def handle_audio(ctx, remote, raw_dir, processed_dir):
    import tag_with_discogs as T
    from verify_tags import check_file

    st = ctx.st
    n = st["attempts"].get(remote, 0) + 1
    rel = ctx.rel(remote)
    entry = {"ts": now(), "run": st["runs"], "path": rel, "attempt": n}
    print(f"--- {rel} (intento {n}) ---")

    def retry_or_giveup(reason, status):
        if n >= MAX_ATTEMPTS:
            to_nonprocessed(ctx, remote, reason)
            entry.update(status="nonprocessed", reason=reason, detail=status)
        else:
            st["attempts"][remote] = n
            ctx.count("retry")
            entry.update(status="retry", reason=reason)
        append_report(entry)

    local_dir = raw_dir / Path(rel).parent
    local_dir.mkdir(parents=True, exist_ok=True)
    def download_failed(err):
        """Un fallo de descarga NO gasta intentos: suele ser sistemico (cuota de
        transferencia de MEGA, red). 3 seguidos abortan la corrida sin mover
        nada. Solo un archivo que falla en 4 corridas distintas va a no procesados."""
        ctx.dl_streak += 1
        fails = st.setdefault("dl_fail", {})
        fails[remote] = fails.get(remote, 0) + 1
        print(f"    ERROR descargando ({fails[remote]}): {err}")
        entry.update(status="retry", reason="error_descarga", error=err[:200])
        if fails[remote] >= 4:
            to_nonprocessed(ctx, remote, "error_descarga")
            fails.pop(remote, None)
            entry.update(status="nonprocessed")
        else:
            ctx.count("retry")
        append_report(entry)
        if ctx.dl_streak >= 3:
            raise AbortRun(f"3 descargas seguidas fallaron (posible cuota de MEGA): {err[:200]}")

    from tagio import read_tags
    local = local_dir / Path(rel).name
    try:
        mega(["mega-get", remote, str(local_dir) + "/"], timeout=900,
             verify=lambda: local.exists() and local.stat().st_size > 0 and read_tags(local)["readable"])
    except RuntimeError as e:
        return download_failed(str(e))
    if not local.exists():
        return download_failed("la descarga no dejo el archivo")
    ctx.dl_streak = 0
    st.get("dl_fail", {}).pop(remote, None)

    stem0 = os.path.splitext(os.path.basename(remote))[0]
    fa, fb = ctx.folder_parts(remote)
    hints = resolve_hints(stem0, ctx.knowledge, fa, fb)
    dest, info = T.process_file_ex(local, raw_dir, processed_dir, attempt=n, hints=hints)
    status = info["status"]
    entry.update({k: info.get(k) for k in ("before", "after", "source", "written", "note", "gemini_fail", "no_latin", "hint_source")})
    if hints and info.get("hint_source") in (hints.get("source"), "nombre"):
        entry["hints"] = {k: hints.get(k) for k in ("artist", "album", "title", "performer", "source")}
    entry["firma"] = readable_signature(stem0)

    if status == "reintentar":
        st["attempts"][remote] = n
        ctx.count("retry")
        entry.update(status="retry", reason=info.get("note"))
        append_report(entry)
        return
    if status == "error":
        return retry_or_giveup("error_lectura" if info.get("note") == "archivo no legible" else "error_tageo", info.get("note"))
    if status in ("sin_artista", "sin_titulo", "incompleto"):
        to_nonprocessed(ctx, remote, status)
        entry.update(status="nonprocessed", reason=status)
        append_report(entry)
        return

    # ---- ok / ya_completo ----
    v = check_file(dest)
    if not v["ok"]:
        to_nonprocessed(ctx, remote, "incompleto")
        entry.update(status="nonprocessed", reason="incompleto")
        append_report(entry)
        return
    artist_dir = ctx.canon_artist(sanitize_component(v["artist"]))
    album_dir = ctx.canon_album(artist_dir, sanitize_component(v["album"])) if v["album"] else UNTITLED_ALBUM
    remote_dir = f"{MEGA_DEST}/{artist_dir}/{album_dir}"
    name = sanitize_filename(dest.name)
    target = f"{remote_dir}/{name}"
    changed_tags = status == "ok" and bool(info.get("written"))
    old_stem = os.path.splitext(os.path.basename(remote))[0]

    def learn(final_name):
        if v["album"]:
            ctx.knowledge.learn(v["artist"], v["album"], os.path.splitext(final_name)[0])

    def finish(action, final_path, moved, ambiguous=False):
        ctx.handled.add(remote)
        ctx.done.add(final_path)
        st["attempts"].pop(remote, None)
        ctx.track_dir(remote, os.path.dirname(final_path))
        learn(os.path.basename(final_path))
        entry.update(status="uploaded" if action in ("subida", "mv", "reemplazo") else "sin_cambios", accion=action, dest=final_path,
                     sidecars=moved, ambiguous_sidecars=ambiguous)
        append_report(entry)
        print(f"    OK ({action}): {final_path}" + (f" (+{len(moved)} acompañante/s)" if moved else ""))

    # 1) misma ruta
    if target == remote:
        if not changed_tags:
            ctx.count("sin_cambios")
            return finish("sin_cambios", remote, [])
        # reemplazo en el sitio: sube con nombre temporal, borra el original, renombra
        tmp_name = name + ".tmp"
        tmp_remote = f"{remote_dir}/{tmp_name}"
        try:
            up = dest.with_name(tmp_name)
            dest.rename(up)
            mega(["mega-put", "-c", str(up), remote_dir + "/"], timeout=900, verify=lambda: remote_exists(tmp_remote))
            if not remote_exists(tmp_remote):
                raise RuntimeError("la subida temporal no se pudo confirmar")
        except RuntimeError as e:
            print(f"    ERROR subiendo (reemplazo), el original NO se toca: {e}")
            return retry_or_giveup("error_subida", str(e)[:120])
        try:
            mega(["mega-rm", "-f", remote], verify=lambda: not remote_exists(remote))
        except RuntimeError as e:
            try:
                mega(["mega-rm", "-f", tmp_remote], check=False)
            except RuntimeError:
                pass
            print(f"    ERROR reemplazando, original intacto: {e}")
            return retry_or_giveup("error_reemplazo", str(e)[:120])
        try:
            mega(["mega-mv", tmp_remote, target], verify=lambda: remote_exists(target) and not remote_exists(tmp_remote))
        except RuntimeError as e:
            st.setdefault("pending_rename", []).append([tmp_remote, target])
            ctx.anomalies.append(f"renombre pendiente: {tmp_remote} -> {target} ({str(e)[:80]})")
        ctx.count("replaced")
        ctx.count("romanizados") if info.get("romanized") else None
        return finish("reemplazo", target, [])

    # 2) otra ruta, tags sin cambios: movimiento del lado del servidor (sin subir nada)
    try:
        mega(["mega-mkdir", "-p", remote_dir], check=False)
        final = unique_name(remote_dir, name)
        if final != name:
            ctx.count("dup_renamed")
        if not changed_tags:
            move_remote(remote, remote_dir, final)
            moved, ambiguous = move_sidecars(ctx, remote, remote_dir, old_stem, os.path.splitext(final)[0])
            ctx.count("moved")
            return finish("mv", f"{remote_dir}/{final}", moved, ambiguous)
        # 3) otra ruta y tags nuevos: subir y borrar el original
        upload = dest
        if final != dest.name:
            upload = dest.with_name(final)
            dest.rename(upload)
        mega(["mega-put", "-c", str(upload), remote_dir + "/"], timeout=900,
             verify=lambda: remote_exists(f"{remote_dir}/{final}"))
        if not remote_exists(f"{remote_dir}/{final}"):
            raise RuntimeError("la subida no se pudo confirmar")
    except RuntimeError as e:
        print(f"    ERROR moviendo/subiendo, el original NO se borra: {e}")
        return retry_or_giveup("error_subida", str(e)[:120])

    moved, ambiguous = move_sidecars(ctx, remote, remote_dir, old_stem, os.path.splitext(final)[0])
    try:
        mega(["mega-rm", "-f", remote], verify=lambda: not remote_exists(remote))
    except RuntimeError as e:
        # Ya esta subido: NO se reprocesa (duplicaria). El borrado queda pendiente.
        print(f"    AVISO: subido OK pero no se pudo borrar el original ({e}); borrado pendiente")
        st.setdefault("pending_rm", []).append(remote)
    ctx.count("uploaded")
    ctx.count("con_album" if v["album"] else "sin_album")
    if info.get("romanized"):
        ctx.count("romanizados")
    if info.get("no_latin"):
        ctx.count("no_latin_kept")
    return finish("subida", f"{remote_dir}/{final}", moved, ambiguous)


def handle_folder_images(ctx, remaining_audio):
    """Imagen de carpeta (album.jpg, folder.jpg...): se mueve solo si TODAS las
    canciones de esa carpeta de origen terminaron en la MISMA carpeta de album
    del destino y ya no queda audio en la carpeta de origen."""
    remaining_dirs = {os.path.dirname(x) for x in remaining_audio}
    for d, dests in list(ctx.st["dir_dest"].items()):
        if d in remaining_dirs or not d.startswith(ctx.source):
            continue
        imgs = [f for f in ctx.files if os.path.dirname(f) == d and f not in ctx.handled and ext(f) in IMAGE_EXTS
                and FOLDER_IMAGE_RE.match(os.path.splitext(os.path.basename(f))[0])]
        if len(dests) == 1 and dests[0] not in ("NONPROC", d) and imgs:
            for img in imgs:
                try:
                    final = unique_name(dests[0], os.path.basename(img))
                    move_remote(img, dests[0], None if final == os.path.basename(img) else final)
                    ctx.handled.add(img)
                    ctx.count("folder_images")
                    append_report({"ts": now(), "run": ctx.st["runs"], "path": ctx.rel(img), "status": "folder_image", "dest": dests[0]})
                except RuntimeError as e:
                    print(f"    AVISO: imagen de carpeta no movida: {e}")
        del ctx.st["dir_dest"][d]


def rescue_orphans(ctx):
    """Acompañantes huerfanos: archivos no audio que quedaron en el origen
    porque su cancion ya se proceso en una corrida anterior (o en el flujo
    viejo). Si su nombre base coincide con UNA sola cancion ya ubicada en el
    destino (o en no procesados), se mueven junto a ella."""
    left = [f for f in list_files(ctx.source) if ext(f) not in AUDIOISH and f not in ctx.handled]
    if not left:
        return 0
    targets = {}
    for root in (MEGA_DEST, MEGA_NONPROC):
        if not remote_exists(root):
            continue
        for f in list_files(root):
            if ext(f) in AUDIOISH:
                targets.setdefault(os.path.splitext(os.path.basename(f))[0].casefold(), []).append(f)
    moved = 0
    for f in left:
        if time.time() - ctx.t0 > RUN_BUDGET_SEC:
            break
        stem = os.path.splitext(os.path.basename(f))[0].casefold()
        cands = targets.get(stem, [])
        if len(cands) != 1:
            continue
        dest_dir = os.path.dirname(cands[0])
        if os.path.dirname(f) == dest_dir:
            continue      # ya esta junto a su cancion
        try:
            final = unique_name(dest_dir, os.path.basename(f))
            move_remote(f, dest_dir, None if final == os.path.basename(f) else final)
            ctx.handled.add(f)
            ctx.count("orphans_rescued")
            append_report({"ts": now(), "run": ctx.st["runs"], "path": ctx.rel(f), "status": "orphan_rescued", "dest": dest_dir})
            moved += 1
        except RuntimeError as e:
            print(f"    AVISO: huerfano no movido ({os.path.basename(f)}): {e}")
    return moved


def sweep_empty_dirs(ctx):
    """Borra subcarpetas VACIAS del origen (nunca la raiz, nunca con archivos adentro)."""
    files = list_files(ctx.source)
    with_files = set()
    for f in files:
        d = os.path.dirname(f)
        while d and d != ctx.source and d not in with_files:
            with_files.add(d)
            d = os.path.dirname(d)
    dirs = [d for d in list_dirs(ctx.source) if d != ctx.source]
    empty = {d for d in dirs if d not in with_files}
    top = [d for d in empty if os.path.dirname(d) not in empty]
    removed = 0
    for d in sorted(top):
        if time.time() - ctx.t0 > RUN_BUDGET_SEC:
            return removed, False
        # doble chequeo justo antes de borrar
        if mega(["mega-find", d, "--type=f"], check=False).stdout.strip():
            continue
        mega(["mega-rm", "-r", "-f", d])
        removed += 1
    return removed, True


# ---------------------------------------------------------- done / prioridad

def load_done():
    p = STATE_DIR / "done.json"
    return set(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else set()


def save_done(done):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "done.json").write_text(json.dumps(sorted(done), ensure_ascii=False), encoding="utf-8")


def priority(path):
    """Orden de proceso (menor = antes), deducido solo de la ruta: primero lo que casi seguro
    necesita arreglo (carpetas Unknown/Untitled/dominio), despues nombres no latinos o invalidos,
    despues nombres-slug, despues tildes, al final lo demas. Si MEGA corta por cuota, lo que
    queda sin procesar es lo menos valioso."""
    low = path.casefold()
    stem = os.path.splitext(os.path.basename(path))[0]
    if re.search(r"/(unknown|untitled|desconocid)[^/]*/", low) or is_domain_like(os.path.basename(os.path.dirname(path))):
        return 0
    if contains_non_latin_script(path) or re.search(r'[<>:"|?*]', path) or any(x != x.rstrip(" .") for x in path.split("/")):
        return 1
    if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", stem.lower()):
        return 2
    if any(ord(c) > 127 for c in path):
        return 3
    return 4


def write_session_report(st, note):
    """sessions/NNN-<fecha>.md (detalle de ESTA corrida) + sessions/INDEX.md (una linea por sesion)."""
    sess = SESSION
    if not sess:
        return
    import tag_with_discogs as T
    seq = st["session_seq"] = st.get("session_seq", 0) + 1
    d = STATE_DIR / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    entries = list(SESSION_ENTRIES)
    c1, c0 = st.get("counts", {}), sess.get("counts_before", {})
    delta = {k: v - c0.get(k, 0) for k, v in c1.items() if isinstance(v, int) and v - c0.get(k, 0)}
    by_status = Counter(e.get("status") for e in entries if e.get("path") and e.get("status"))
    by_action = Counter(e.get("accion") for e in entries if e.get("accion"))
    by_src = Counter((e.get("hint_source") or e.get("source")) for e in entries if e.get("status") in ("uploaded", "sin_cambios"))
    ctx = sess.get("ctx")
    lines = [f"# Sesión {seq:03d} — {sess['t0']}", "",
             f"- Acción: **{os.environ.get('ACTION', 'run')}** · trabajo: corrida {st.get('runs')} · estado final: **{st.get('status')}**",
             f"- Inicio {sess['t0']} · fin {now()} · duración {int(time.time() - sess['t0_epoch'])} s · lote {st.get('batch_size')}",
             f"- Fuente `{st.get('mega_source')}` · destino `{MEGA_DEST}` · no procesados `{MEGA_NONPROC}`",
             f"- Pendientes antes: {sess.get('pending_before')} · después: {st.get('pending')}"]
    if note:
        lines.append(f"- Nota: {note}")
    lines += ["", "## Resultado de la sesión", ""]
    lines.append("- Archivos tocados: " + (", ".join(f"{k}: {v}" for k, v in by_status.most_common()) or "ninguno"))
    lines.append("- Acciones: " + (", ".join(f"{k}: {v}" for k, v in by_action.most_common()) or "ninguna"))
    lines.append("- Fuente del dato: " + (", ".join(f"{k}: {v}" for k, v in by_src.most_common()) or "-"))
    lines.append("- Conteos de la sesión: " + (", ".join(f"{k}: +{v}" for k, v in sorted(delta.items())) or "sin cambios"))
    lines += ["", "## Gemini (esta sesión)", "",
              f"- ok: {T.STATS['gemini_ok']} · fallos: {T.STATS['gemini_fail']}" + (f" · modelo: {T.LAST_MODEL.get('name')}" if T.LAST_MODEL.get('name') else "")]
    for e in T.STATS["gemini_errors"]:
        lines.append(f"- error: `{e}`")
    anomalies = list(ctx.anomalies) if ctx else []
    if ACCEPTED_EXIT1[0]:
        anomalies.append(f"{ACCEPTED_EXIT1[0]} comandos de MEGAcmd dieron exit != 0 pero el resultado estaba confirmado (aceptados)")
    for e in entries:
        if e.get("status") == "retry":
            anomalies.append(f"reintento: {e.get('path')} ({e.get('reason')})")
    if note and "abort" in note.lower():
        anomalies.append(note)
    lines += ["", "## Anomalías", ""] + ([f"- {a}" for a in anomalies[:40]] or ["- ninguna"])
    # casos no resueltos agrupados por patron (alimenta la siguiente vuelta)
    groups = defaultdict_list()
    for e in entries:
        if e.get("status") == "nonprocessed":
            groups[(e.get("reason"), e.get("firma") or "?")].append(e.get("path"))
    lines += ["", "## Casos no resueltos, por patrón", ""]
    if groups:
        for (reason, sig), paths in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"- **{reason}** · patrón `{sig}` · {len(paths)} archivo(s) · ej.: `{paths[0]}`")
    else:
        lines.append("- ninguno")
    lines += ["", "## Detalle por archivo", "", "| Ruta | Estado | Acción | Fuente | Antes → Después | Destino |", "|---|---|---|---|---|---|"]

    def tg(t):
        t = t or {}
        return " / ".join(str(t.get(k) or "·") for k in ("artist", "album", "title"))

    def cell(x):
        return str(x if x is not None else "").replace("|", "/").replace("\n", " ")[:90]
    shown = [e for e in entries if e.get("path") and e.get("status")]
    for e in shown[:400]:
        lines.append(f"| {cell(e['path'])} | {cell(e.get('status'))}{' (' + cell(e.get('reason')) + ')' if e.get('reason') else ''} | "
                     f"{cell(e.get('accion'))} | {cell(e.get('hint_source') or e.get('source'))} | "
                     f"{cell(tg(e.get('before')))} → {cell(tg(e.get('after')))} | {cell(e.get('dest'))} |")
    if len(shown) > 400:
        lines.append(f"| … {len(shown) - 400} más en report.jsonl | | | | | |")
    stamp = sess["t0"].replace(":", "").replace("-", "")
    fname = f"{seq:03d}-{stamp}.md"
    (d / fname).write_text("\n".join(lines) + "\n", encoding="utf-8")
    idx = d / "INDEX.md"
    if not idx.exists():
        idx.write_text("# Sesiones del bot\n\n", encoding="utf-8")
    with open(idx, "a", encoding="utf-8") as fh:
        fh.write(f"- [{seq:03d}]({fname}) · {sess['t0']} · {os.environ.get('ACTION', 'run')} · tocados {sum(by_status.values())} "
                 f"(subida/mv/reemplazo {sum(by_action.get(a, 0) for a in ('subida', 'mv', 'reemplazo'))}, "
                 f"no procesados {by_status.get('nonprocessed', 0)}, reintentos {by_status.get('retry', 0)}) · pendientes {st.get('pending')} · {st.get('status')}\n")


def defaultdict_list():
    from collections import defaultdict
    return defaultdict(list)


# ------------------------------------------------------------------ acciones

def cmd_run(st):
    import tag_with_discogs as T

    source = st["mega_source"].rstrip("/")
    if not st.get("fix_dl_v3"):      # los fallos previos eran falsos negativos de mega-get (exit 1 con descarga completa)
        st["attempts"] = {}
        st["dl_fail"] = {}
        st["fix_dl_v3"] = True
    batch = int(os.environ.get("BATCH_SIZE") or st.get("batch_size") or 5)
    st["batch_size"] = batch
    st["runs"] = st.get("runs", 0) + 1
    st["last_run"] = now()
    import copy
    SESSION.clear()
    SESSION.update(t0=now(), t0_epoch=time.time(), counts_before=copy.deepcopy(st.get("counts", {})))
    SESSION_ENTRIES.clear()
    print(f"== Corrida {st['runs']}: fuente {source}, destino {MEGA_DEST}, lote {batch} ==")

    # renombres pendientes de corridas anteriores (reemplazo en el sitio que quedo a medias)
    for tmp, target in list(st.get("pending_rename", [])):
        try:
            if remote_exists(tmp) and not remote_exists(target):
                mega(["mega-mv", tmp, target], verify=lambda: remote_exists(target) and not remote_exists(tmp))
            elif remote_exists(tmp) and remote_exists(target):
                mega(["mega-rm", "-f", tmp])
            st["pending_rename"].remove([tmp, target])
        except RuntimeError as e:
            print(f"AVISO: renombre pendiente sigue fallando {tmp}: {e}")

    # borrados pendientes de corridas anteriores (ya subidos, falto el rm)
    for p in list(st.get("pending_rm", [])):
        try:
            if remote_exists(p):
                mega(["mega-rm", "-f", p])
            st["pending_rm"].remove(p)
        except RuntimeError as e:
            print(f"AVISO: borrado pendiente sigue fallando para {p}: {e}")
    pending_rm = set(st.get("pending_rm", []))

    nonproc_prefix = MEGA_NONPROC.rstrip("/") + "/"
    files = [f for f in list_files(source) if f not in pending_rm and not f.startswith(nonproc_prefix)]
    done = load_done()
    audio_all = [f for f in files if ext(f) in AUDIO_EXTS]
    audio = sorted((f for f in audio_all if f not in done), key=lambda f: (priority(f), f))
    unsupported = sorted(f for f in files if ext(f) in UNSUPPORTED_AUDIO)
    print(f"Fuente: {len(files)} archivo(s) ({len(audio_all)} mp3/m4a, {len(audio)} pendientes, {len(unsupported)} audio no soportado)")
    ctx = Ctx(st, source, files)
    ctx.done = done
    ctx.knowledge = Knowledge.from_paths(files, MEGA_DEST)
    ctx.dir_canon = build_dir_canon(files, MEGA_DEST)
    print(f"Conocimiento de la biblioteca: {len(ctx.knowledge.artists)} artistas, {len(ctx.knowledge.tracks)} titulos")
    SESSION["ctx"] = ctx
    SESSION["pending_before"] = len(audio)

    try:
        # 1) formatos de audio no soportados -> no procesados
        for f in unsupported[:150]:
            if time.time() - ctx.t0 > RUN_BUDGET_SEC:
                break
            print(f"--- {ctx.rel(f)} ---")
            try:
                to_nonprocessed(ctx, f, "formato_no_soportado")
                append_report({"ts": now(), "run": st["runs"], "path": ctx.rel(f), "status": "nonprocessed", "reason": "formato_no_soportado"})
            except RuntimeError as e:
                print(f"    ERROR moviendo: {e}")

        # 2) lote de canciones
        todo = audio[:batch]
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir, processed_dir = Path(tmp) / "raw", Path(tmp) / "processed"
            raw_dir.mkdir()
            processed_dir.mkdir()
            for remote in todo:
                if time.time() - ctx.t0 > RUN_BUDGET_SEC:
                    print("Presupuesto de tiempo agotado: el resto sigue en la proxima corrida.")
                    break
                try:
                    handle_audio(ctx, remote, raw_dir, processed_dir)
                except AbortRun:
                    raise
                except Exception as e:
                    print(f"    ERROR inesperado: {e}")
                shutil.rmtree(raw_dir, ignore_errors=True)
                shutil.rmtree(processed_dir, ignore_errors=True)
                raw_dir.mkdir()
                processed_dir.mkdir()
                ctx.since_checkpoint += 1
                if ctx.since_checkpoint >= CHECKPOINT_EVERY:
                    ctx.since_checkpoint = 0
                    save_done(ctx.done)
                    save_state(st)
                    push_state(f"checkpoint corrida {st['runs']}")
    finally:
        g = st.setdefault("gemini", {"ok": 0, "fail": 0, "errors": []})
        g["ok"] += T.STATS["gemini_ok"]
        g["fail"] += T.STATS["gemini_fail"]
        for e in T.STATS["gemini_errors"]:
            if e not in g["errors"] and len(g["errors"]) < 5:
                g["errors"].append(e)

    save_done(ctx.done)
    remaining = [f for f in audio + unsupported if f not in ctx.handled and f not in ctx.done]
    handle_folder_images(ctx, remaining)
    st["pending"] = len(remaining)
    print(f"\n== Lote terminado: quedan {len(remaining)} archivo(s) de audio por procesar ==")

    if remaining:
        st["status"] = "active"
        return None

    # 3) nada de audio pendiente: barrer carpetas vacias y cerrar
    print("== No queda audio en la fuente: rescatando acompañantes huerfanos y barriendo carpetas vacias ==")
    n = rescue_orphans(ctx)
    print(f"Huerfanos movidos junto a su cancion: {n}")
    removed, done = sweep_empty_dirs(ctx)
    ctx.count("dirs_removed", removed)
    if not done:
        st["status"] = "sweeping"
        return "El barrido de carpetas vacias continua en la proxima corrida."
    fresh = list_files(source)
    fidx = build_index(fresh)
    left = []
    for f in fresh:
        if ext(f) in AUDIOISH:
            continue                                  # las canciones ya procesadas siguen en el arbol
        grp = fidx.get((os.path.dirname(f), os.path.splitext(os.path.basename(f))[0].casefold()), [])
        if any(ext(x) in AUDIOISH for x in grp):
            continue                                  # acompañante junto a su cancion: esta bien
        left.append(f)
    (STATE_DIR / "leftovers.json").write_text(json.dumps(left[:500], ensure_ascii=False, indent=1), encoding="utf-8")
    st["pending"] = 0
    st["finished_at"] = now()
    if left:
        st["status"] = "finished_with_leftovers"
        return f"Terminado. Quedan {len(left)} archivo(s) no audio sin acompañante (ver leftovers.json)."
    st["status"] = "finished"
    return "Terminado. La fuente quedo vacia."


def cmd_inventory(st):
    source = (os.environ.get("MEGA_SOURCE") or st.get("mega_source") or "").rstrip("/")
    files = list_files(source)
    exts = Counter(ext(f) or "(sin extension)" for f in files)
    index = build_index(files)
    audio = [f for f in files if ext(f) in AUDIOISH]
    with_sc = sum(1 for f in audio if sidecars_of(f, index)[0])
    ambiguous = sum(1 for f in audio if sidecars_of(f, index)[1])
    dirs = len(list_dirs(source)) - 1
    out = {"ts": now(), "source": source, "total_files": len(files), "subfolders": max(dirs, 0),
           "por_extension": dict(exts.most_common()),
           "audio_soportado": sum(1 for f in files if ext(f) in AUDIO_EXTS),
           "audio_no_soportado": sum(1 for f in files if ext(f) in UNSUPPORTED_AUDIO),
           "canciones_con_acompanante": with_sc, "acompanantes_ambiguos": ambiguous}
    # reparto por carpeta de nivel 1 y 2 (para ver DONDE esta cada cosa)
    lvl = Counter()
    for f in files:
        rel = f[len(source):].strip("/").split("/")
        lvl["/" + "/".join(rel[:2]) if len(rel) > 2 else "/" + "/".join(rel[:1])] += 1
    out["por_carpeta"] = dict(lvl.most_common(40))
    (STATE_DIR / "inventory.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


def cmd_diagnose(st):
    """Diagnostico de descargas: cuenta/cuota y un mega-get de prueba con la salida completa."""
    source = (st.get("mega_source") or os.environ.get("MEGA_SOURCE") or "").rstrip("/")
    out = {"ts": now(), "source": source}
    for name, args in (("whoami", ["mega-whoami", "-l"]), ("df", ["mega-df"]), ("transfers", ["mega-transfers", "--only-downloads"])):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=60)
            out[name] = (r.stdout + r.stderr).replace("\x00", "")[-800:]
        except Exception as e:
            out[name] = f"error: {e}"
    files = [f for f in list_files(source) if ext(f) in AUDIO_EXTS]
    probes = [f for f in files if ext(f) == ".mp3"][:2] + [f for f in files if ext(f) == ".m4a"][:1]
    out["pruebas"] = []
    for f in probes:
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.time()
            try:
                r = subprocess.run(["mega-get", f, tmp + "/"], capture_output=True, text=True, timeout=150)
                res = {"archivo": f, "exit": r.returncode, "segundos": round(time.time() - t0),
                       "mensaje": clean_err(r), "bajado": sorted(os.listdir(tmp))}
            except subprocess.TimeoutExpired:
                res = {"archivo": f, "exit": "timeout", "segundos": 150}
        out["pruebas"].append(res)
    (STATE_DIR / "diagnose.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


def cmd_audit(st):
    """Auditoria de nombres (solo lectura) para entender por que un cliente de
    sincronizacion no baja todo: caracteres que Windows/FAT/exFAT/Android no
    aceptan, rutas largas, espacios/puntos al final, nombres que solo difieren
    por mayusculas dentro de una carpeta y extensiones raras."""
    source = (os.environ.get("MEGA_SOURCE") or st.get("mega_source") or "").rstrip("/")
    files = list_files(source)
    bad_re = re.compile(r'[<>:"\\|?*\x00-\x1f]')
    out = {"ts": now(), "source": source, "total": len(files), "chars_invalidos": [], "ruta_larga": [],
           "espacio_o_punto_final": [], "choque_mayusculas": [], "bytes_nombre_largo": [], "ext_raras": {}}
    seen = {}
    for f in files:
        rel = f[len(source):].lstrip("/")
        parts = rel.split("/")
        if any(bad_re.search(x) for x in parts):
            out["chars_invalidos"].append(rel)
        if len(rel) > 200:
            out["ruta_larga"].append({"largo": len(rel), "ruta": rel})
        if any(x != x.rstrip(" .") for x in parts):
            out["espacio_o_punto_final"].append(rel)
        if any(len(x.encode("utf-8")) > 240 for x in parts):
            out["bytes_nombre_largo"].append(rel)
        seen.setdefault((os.path.dirname(rel).casefold(), os.path.basename(rel).casefold()), []).append(rel)
        e = ext(f)
        if e not in (".mp3", ".m4a", ".lrc", ".jpg", ".png"):
            out["ext_raras"][e] = out["ext_raras"].get(e, 0) + 1
    out["choque_mayusculas"] = [v for v in seen.values() if len(v) > 1]
    resumen = {k: (len(v) if isinstance(v, list) else v) for k, v in out.items() if k not in ("ts", "source")}
    out["resumen"] = resumen
    for k in ("chars_invalidos", "ruta_larga", "espacio_o_punto_final", "choque_mayusculas", "bytes_nombre_largo"):
        out[k] = out[k][:60]
    (STATE_DIR / "names-audit.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(resumen, ensure_ascii=False, indent=1))


def cmd_peek(st):
    """Solo lectura: para los archivos de audio cuya ruta coincida con PATTERN
    (regex, sin distinguir mayusculas), descarga hasta 30, lee sus tags y dice
    que tomaria el bot (artista/album/titulo y en que rama caeria) SIN buscar
    en Discogs ni escribir ni mover nada. Resultado en peek.json."""
    import tag_with_discogs as T
    from tagio import read_tags, effective, is_complete
    source = (os.environ.get("MEGA_SOURCE") or st.get("mega_source") or "").rstrip("/")
    pat = re.compile(os.environ.get("PATTERN") or ".", re.I)
    files = [f for f in list_files(source) if ext(f) in AUDIO_EXTS and pat.search(f)]
    out = {"ts": now(), "source": source, "pattern": pat.pattern, "coinciden": len(files), "archivos": []}
    for f in files[:30]:
        rel = f[len(source):].lstrip("/")
        item = {"ruta": rel}
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / os.path.basename(f)
            try:
                mega(["mega-get", f, tmp + "/"], timeout=600,
                     verify=lambda: local.exists() and local.stat().st_size > 0)
            except RuntimeError as e:
                item["error"] = str(e)[:150]
                out["archivos"].append(item)
                continue
            t = read_tags(local)
            fa, fb = T.parse_album_folder(os.path.basename(os.path.dirname(f)))
            na, nt = T.parse_filename(local, fa)
            artist = effective(t, "artist") or na or fa
            album = effective(t, "album") or fb
            title = effective(t, "title") or nt
            item.update(tags_actuales={k: t.get(k) for k in ("artist", "album", "title", "albumartist", "track", "year", "has_cover")},
                        del_nombre_de_archivo={"artista": na, "titulo": nt},
                        de_la_carpeta={"artista": fa, "album": fb})
            if not t["readable"]:
                item["decision"] = "error_lectura"
            elif is_complete(t):
                item["decision"] = "ya_completo (no se toca" + ("; se romaniza si hay script no latino)" if any(T.contains_non_latin_script(t.get(k)) for k in ("artist", "album", "title")) else ")")
            elif not artist:
                item["decision"] = "sin_artista -> no procesado"
            elif not title:
                item["decision"] = "sin_titulo -> no procesado"
            else:
                item["decision"] = f"buscar en Discogs: artista={artist!r} album={album!r} titulo={title!r}"
        out["archivos"].append(item)
    (STATE_DIR / "peek.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "archivos"}, ensure_ascii=False))


def main():
    global MEGA_DEST, MEGA_NONPROC
    action = (os.environ.get("ACTION") or "run").strip()
    st = load_state()
    note = None
    active = ("active", "sweeping")
    env_dest = os.environ.get("MEGA_DEST", "").strip().rstrip("/")
    env_np = os.environ.get("MEGA_NONPROC", "").strip().rstrip("/")
    if action == "start" and st["status"] not in active:
        st["mega_dest"] = env_dest or st.get("mega_dest") or "/untitledless"
        st["mega_nonproc"] = env_np or st.get("mega_nonproc") or "/untitledless-nonprocessed"
    MEGA_DEST = (st.get("mega_dest") or env_dest or "/untitledless").rstrip("/")
    MEGA_NONPROC = (st.get("mega_nonproc") or env_np or "/untitledless-nonprocessed").rstrip("/")

    if action == "stop":
        st["status"] = "stopped"
        note = "Detenido a mano."
    elif action == "inventory":
        cmd_inventory(st)
    elif action == "diagnose":
        cmd_diagnose(st)
    elif action == "audit":
        cmd_audit(st)
    elif action == "peek":
        cmd_peek(st)
    elif action in ("start", "run"):
        if action == "run" and st["status"] not in active:
            print(f"El trabajo no esta activo (estado: {st['status']}). No hay nada que hacer.")
            return
        if action == "start":
            source = (os.environ.get("MEGA_SOURCE") or "").rstrip("/")
            if st["status"] in active:
                print("Ya hay un trabajo activo: se actualiza el tamaño de lote y se corre un lote.")
                if source and source != st.get("mega_source"):
                    print(f"ERROR: hay un trabajo activo sobre {st.get('mega_source')}; detenelo antes de cambiar la fuente.")
                    sys.exit(1)
            else:
                if not source.strip("/"):
                    print("ERROR: falta mega_source (o es la raiz de MEGA: no se acepta, procesaria toda la cuenta).")
                    sys.exit(1)
                if MEGA_DEST == MEGA_NONPROC or MEGA_NONPROC == source:
                    print("ERROR: destino, no procesados y origen no pueden coincidir.")
                    sys.exit(1)
                if st.get("started_at"):
                    st.setdefault("history", []).append({"source": st.get("mega_source"), "dest": st.get("mega_dest"),
                                                         "started_at": st.get("started_at"), "finished_at": st.get("finished_at"),
                                                         "status": st["status"], "counts": st.get("counts")})
                    # el informe del trabajo anterior se ARCHIVA, no se borra
                    arch = STATE_DIR / "archive"
                    arch.mkdir(parents=True, exist_ok=True)
                    n_job = len(st["history"])
                    for f in ("report.jsonl", "leftovers.json", "summary.md", "done.json"):
                        if (STATE_DIR / f).exists():
                            shutil.move(str(STATE_DIR / f), str(arch / f"job-{n_job:02d}-{f}"))
                if MEGA_DEST.startswith(source.rstrip("/") + "/"):
                    print(f"Modo refinar en el sitio: el destino {MEGA_DEST} esta dentro del origen {source}.")
                st.update(status="active", mega_source=source, started_at=now(), runs=0, counts={}, attempts={}, dir_dest={},
                          gemini={"ok": 0, "fail": 0, "errors": []}, pending_rm=[], pending_rename=[], dl_fail={})
                st.pop("finished_at", None)
        try:
            note = cmd_run(st)
        except AbortRun as e:
            note = f"Corrida abortada: {e}. Se reintenta en la proxima."
            print(note)
        write_session_report(st, note)
    st["last_action"] = action
    save_state(st)
    write_summary(st, note)
    push_state(f"{action}: {st['status']} (pendientes: {st.get('pending')})")
    if note:
        print(note)


if __name__ == "__main__":
    main()
