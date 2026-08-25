# Bot de metadatos: MEGA → beets

Baja canciones desde una carpeta de MEGA, les completa metadata (artista,
álbum, título, número de pista, carátula) usando huella de audio
(AcoustID/MusicBrainz) con el nombre del archivo como respaldo, y sube el
resultado a otra carpeta en tu MEGA. Los archivos originales no se tocan.

Pensado para tus rips de CD que quedaron sin ningún tag.

## Antes de correrlo

1. Sube esta carpeta a un repo de GitHub (público o privado).
2. Crea una cuenta gratis en https://acoustid.org/ y saca tu API key ahí
   (podés loguearte con MusicBrainz u OpenID).
3. En el repo: **Settings → Secrets and variables → Actions → New repository
   secret**, y agrega:
   - `MEGA_EMAIL` — el correo de tu cuenta MEGA
   - `MEGA_PASSWORD` — la contraseña
   - `ACOUSTID_API_KEY` — la key del paso 2
4. Pestaña **Actions** → *Tag Music from MEGA* → **Run workflow**. Ahí mismo
   podés cambiar la carpeta de origen y destino sin tocar el código.

## Cómo decide qué tag ponerle a cada canción

- `fromfilename` intenta sacar artista/título del nombre del archivo.
- `chroma` (AcoustID) identifica la canción por su huella de audio — esto
  funciona aunque el archivo no tenga ningún tag previo, que es tu caso.
- `fetchart` + `embedart` buscan la carátula y la incrustan en el archivo.
- Si para alguna canción no hay un match confiable, igual se sube con lo
  mínimo que se pudo inferir (no se queda ninguna sin subir), gracias a
  `quiet_fallback: asis` en `beets_config.template.yaml`.

## Cosas a tener en cuenta

- **Ancho de banda**: MEGA aplica límites de transferencia, y los runners de
  GitHub Actions comparten IP con muchísimos otros workflows. Si tu colección
  es grande y el proceso se corta a medio camino, la opción más confiable es
  correr `scripts/run.sh` directo en tu propio servidor (exportando las
  mismas tres variables de entorno que usa el workflow), o registrar ese
  servidor como [runner self-hosted](https://docs.github.com/actions/hosting-your-own-runners)
  y cambiar `runs-on: ubuntu-latest` por `runs-on: self-hosted` en el
  workflow — mismo botón de "Run workflow", sin depender de la nube de
  GitHub para la parte pesada.
- **Versión de Ubuntu**: el instalador de MEGAcmd asume `xUbuntu_24.04`
  (lo que usa `ubuntu-latest` al momento de escribir esto). Si GitHub cambia
  la versión por defecto y la instalación falla, ajusta ese número en
  `.github/workflows/tag-music.yml`.
- Antes de tirarle toda la colección, probá el workflow con una carpeta
  chica para revisar que el matching quede bien — sobre todo si algunos
  discos son raros, compilaciones o tienen nombres de archivo poco
  descriptivos, donde la huella de audio hace todo el trabajo igual.

## Correrlo localmente (sin GitHub Actions)

```bash
pip install -r requirements.txt
export MEGA_EMAIL="tu correo"
export MEGA_PASSWORD="tu contraseña"
export MEGA_SOURCE="carpeta o link de MEGA"
export MEGA_DEST="/ProcesadoTags"
export ACOUSTID_API_KEY="tu key"
envsubst < beets_config.template.yaml > beets_config.yaml
bash scripts/run.sh
```

Necesitás `megacmd` y `fpcalc` (paquete `libchromaprint-tools` en
Debian/Ubuntu) instalados en el sistema.
