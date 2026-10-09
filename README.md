# Untitled track killer

Bot que lee canciones (mp3 y m4a) desde una carpeta de MEGA, completa su
metadata (artista, álbum, título, carátula) y las reubica en
`/untitledless/<Artista>/<Álbum>/`. Busca por **texto** en Discogs (con
respaldo de iTunes para año y carátula); no huella ni decodifica el audio.
Pensado para música de nicho. **Reemplaza** el original: borra el crudo de la
fuente solo después de confirmar la subida.

## Cómo funciona: un trabajo con inicio y fin

El bot **no procesa nada hasta que se lo pides**. Desde Actions → *Untitled
track killer* → *Run workflow*, eliges una acción:

| Acción | Qué hace |
|---|---|
| `selftest` | Prueba Gemini (romanizar/traducir japonés y coreano) y Discogs. No toca MEGA. Resultado en `selftest.json` |
| `inventory` | Cuenta los archivos de la fuente por extensión y por carpeta. No descarga ni mueve nada. Resultado en `inventory.json` (acepta `mega_source`, p. ej. `/` para ver dónde está todo) |
| `audit` | Solo lectura: nombres que Windows/FAT/Android no aceptan (`: " < > \| ? *`), punto o espacio final, rutas largas, choques por mayúsculas. Resultado en `names-audit.json` |
| `diagnose` | Cuenta, cuota y descarga de prueba de MEGA. Resultado en `diagnose.json` |
| `start` | Inicia el trabajo (o ajusta `batch_size` si ya hay uno activo) y corre un lote ya. Primer arranque recomendado: `batch_size=5` |
| `run` | Corre un lote (es lo que hace el cron) |
| `stop` | Detiene el trabajo |

Con el trabajo activo, el cron (`0 */5 * * *`) corre un lote cada 5 h. Si no
hay trabajo activo sale en segundos. Cuando no queda audio en la fuente, el
trabajo rescata los acompañantes huérfanos, barre las carpetas vacías y pasa
solo a `finished` (o `finished_with_leftovers` si quedan archivos no audio
sin canción). Para rehacerlo, otro `start`.

## Estado e informe (rama `job-state`)

Todo vive en la rama `job-state`, sin tocar `main`:

- `summary.md` — estado, conteos y fallos de Gemini. **Empieza por acá.**
- `report.jsonl` — una línea por archivo: tags **originales** antes de tocarlo, tags finales, fuente del dato (existente / discogs / itunes / nombre / romanizado), destino, acompañantes y motivo si no se procesó.
- `state.json` — estado del trabajo, intentos por archivo.
- `leftovers.json`, `inventory.json`, `selftest.json`.

## Reglas de protección de tags

- Un tag con **valor real** nunca se pisa. Vacío o placeholder (`Unknown Artist`, `Unknown Disc`, `Track 01`, `Untitled album`, …) cuenta como ausente: se completa si hay match confiable, o se deja **vacío** (nunca se escribe "Unknown").
- Un match de Discogs/iTunes solo se acepta si su artista coincide con el artista real (se ignora el sufijo `(2)` de Discogs).
- Una carátula incrustada existente nunca se reemplaza.
- **Excepción deliberada — script no latino:** si artista/álbum/título están en **cualquier escritura no latina** (japonés, chino, coreano, ruso, mongol, griego, árabe, hebreo, armenio, georgiano, hindi, tailandés, etc.; se detecta por el nombre Unicode del carácter, no por una lista de rangos), se **romanizan** vía Gemini para poder encontrarlos al buscar, **aunque el archivo ya tenga todos los tags**. Estándar fijo y **sin ningún diacrítico** (se quitan además por código): japonés Hepburn sin macrones, chino pinyin sin tonos, coreano romanización revisada, ruso/mongol cirílico tipo BGN/PCGN (`Кино` → `Kino`). El valor original queda en el comentario del archivo (`Original -- …`) y en `report.jsonl`; el comentario también lleva la traducción al español de título y álbum (nunca del artista). Si Gemini falla, se reintenta una vez y después se sube con los tags originales intactos (queda contado en `no_latin_kept`).
- Se guardan en el informe los tags originales de cada archivo.

## Dónde termina cada archivo

- **Procesado** → `/untitledless/<Artista>/<Álbum>/`; sin álbum → `Untitled album`.
- **No procesado** → `/untitledless-nonprocessed/<ruta relativa>`, con el motivo en el informe: `sin_artista`, `sin_titulo`, `incompleto`, `error_descarga`, `error_lectura`, `error_tageo`, `error_subida` (tras 2 intentos), `formato_no_soportado` (flac, ogg, wav, etc.).
- **Acompañantes** (no audio con el mismo nombre base que la canción: `.lrc`, `.jpg`, `.cue`…) se mueven con ella, renombrados con el nombre final. Si dos audios comparten nombre base, ninguno se lleva el acompañante.
- **Imagen de carpeta** (`album`, `albumart`, `cover`, `folder`, `front`, `art`, `artwork`): se mueve solo si todas las canciones de esa carpeta de origen terminaron en el mismo álbum.
- **Huérfanos**: al final, un acompañante cuya canción ya está en el destino (o en no procesados) se mueve junto a ella.
- Nombre repetido en el destino → se renombra `Nombre (2).mp3`, nunca se pisa.

## Configuración

**Secrets**: `MEGA_EMAIL`, `MEGA_PASSWORD`, `DISCOGS_TOKEN`, `GEMINI_API_KEY` (`ACOUSTID_API_KEY` ya no se usa pero se conserva a propósito).
**Variables**: `MEGA_SOURCE_PATH` (ruta *interna* de MEGA, ej. `/MEGA/musica`, no un link público) y, opcional, `GEMINI_MODEL`.

Gemini: modelo principal `gemini-3.1-flash-lite`, con reintentos ante 429/5xx/timeouts y respaldo en `gemini-flash-latest` y `gemini-3.5-flash` (`gemini-2.0-flash` fue retirado).

## Robustez

`timeout-minutes: 50` en el job, `concurrency` (una corrida a la vez), timeout por comando `mega-*` (3 seguidos abortan la corrida), presupuesto de 40 min por corrida, runner fijo en `ubuntu-24.04` (el `.deb` de MEGAcmd es de 24.04; `ubuntu-latest` migra a 26.04 el 19-oct-2026). El estado se guarda cada 20 archivos.

## Advertencia: esto borra tus originales

El borrado solo ocurre tras confirmar la subida, pero es real y permanente en MEGA. Prueba siempre con `batch_size=5` y revisa `report.jsonl` antes de subirlo.

## Correrlo localmente

```bash
pip install -r requirements.txt
export MEGA_EMAIL=… MEGA_PASSWORD=… DISCOGS_TOKEN=… GEMINI_API_KEY=…
export MEGA_SOURCE=/musica STATE_DIR=./state ACTION=start BATCH_SIZE=5
bash scripts/run.sh     # requiere megacmd instalado
```
