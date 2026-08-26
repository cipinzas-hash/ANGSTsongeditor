# Untitled track killer

Baja canciones desde una carpeta de MEGA (solo lectura, nunca se toca el
origen), les completa metadata (artista, álbum, título, número de pista,
carátula) buscando por **texto** en Discogs — a partir del artista/título que
se extraen del nombre del archivo — y sube el resultado ya tageado a
`/untitledless` en tu MEGA.

No usa huella de audio ni decodifica el audio en ningún momento: todo el
matching es por nombre de archivo + búsqueda de texto. Pensado para
colecciones de nicho (industrial/EBM/underground) donde Discogs suele tener
mejor cobertura que MusicBrainz.

## Antes de correrlo

1. Sube esta carpeta a un repo de GitHub (público o privado).
2. Sacá un token gratuito de Discogs: `discogs.com` → tu cuenta → **Settings
   → Developers → Generate new token**. No hace falta app ni OAuth completo.
3. En el repo: **Settings → Secrets and variables → Actions → New repository
   secret**, y agregá:
   - `MEGA_EMAIL` — el correo de tu cuenta MEGA
   - `MEGA_PASSWORD` — la contraseña
   - `DISCOGS_TOKEN` — el token del paso 2
4. Pestaña **Actions** → *Untitled track killer* → **Run workflow**. Ahí
   podés cambiar la carpeta/link de origen sin tocar código. El destino es
   fijo: `/untitledless`.

## Cómo decide qué tag ponerle a cada canción

- Si el archivo ya tiene ID3 completo (artista+álbum+título), se deja tal
  cual, sin tocarlo.
- Si no, `scripts/tag_with_discogs.py` extrae artista/título del nombre del
  archivo (soporta patrones tipo `NN.Artista - Título.mp3`, `Artista -
  Título.mp3`) y usa el nombre de la carpeta padre (`Artista - Álbum`) como
  pista extra si está disponible.
- Con eso arma una búsqueda de texto contra `/database/search` de Discogs,
  toma el mejor resultado, y si encuentra el tracklist del release intenta
  matchear el título exacto de la pista para sacar el número correcto.
- `scripts/verify_tags.py` corre después y confirma con `mutagen` (lectura
  directa del ID3, no confía en que el paso anterior haya funcionado) que
  cada archivo quedó con artista, álbum y título. **Si algo queda
  incompleto, el workflow corta ahí y no sube nada a MEGA** — falla en
  rojo en vez de publicar basura en verde.

## Cosas a tener en cuenta

- **Rate limit de Discogs**: 60 req/min autenticado. El script duerme ~1.1s
  entre requests, así que colecciones grandes tardan proporcionalmente.
- **Nombres de archivo ambiguos**: si no hay un separador `Artista - Título`
  claro en el nombre (ej. `Kill-Instinct-Control.mp3`), no hay forma
  confiable de parsear el artista — ese archivo queda marcado incompleto a
  propósito, no se inventa nada.
- **Ancho de banda**: MEGA aplica límites de transferencia, y los runners de
  GitHub Actions comparten IP con muchísimos otros workflows. Si tu
  colección es grande y el proceso se corta a medio camino, la opción más
  confiable es correr `scripts/run.sh` directo en tu propio servidor
  (exportando las mismas variables de entorno que usa el workflow), o
  registrarlo como [runner self-hosted](https://docs.github.com/actions/hosting-your-own-runners)
  y cambiar `runs-on: ubuntu-latest` por `runs-on: self-hosted` en el
  workflow.
- **Versión de Ubuntu**: el instalador de MEGAcmd asume `xUbuntu_24.04` (lo
  que usa `ubuntu-latest` al momento de escribir esto). Si GitHub cambia la
  versión por defecto y la instalación falla, ajustá ese número en
  `.github/workflows/tag-music.yml`.
- Antes de tirarle toda la colección, probá con una carpeta chica.

## Correrlo localmente (sin GitHub Actions)

```bash
pip install -r requirements.txt
export MEGA_EMAIL="tu correo"
export MEGA_PASSWORD="tu contraseña"
export MEGA_SOURCE="carpeta o link de MEGA"
export DISCOGS_TOKEN="tu token"
bash scripts/run.sh
```

Necesitás `megacmd` instalado en el sistema.
