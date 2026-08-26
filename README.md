# Untitled track killer

Bot incremental que lee canciones desde una carpeta de MEGA, les completa
metadata (artista, álbum, título, carátula) buscando por **texto** en
Discogs — con respaldo en iTunes cuando Discogs no tiene el release — y
sube el resultado tageado a `/untitledless` en tu MEGA. **Reemplaza** el
original: una vez confirmada la subida del archivo tageado, borra el
crudo de la fuente.

Corre solo, cada 5 horas, procesando hasta 100 canciones por corrida (para
no agotar cuota/espacio de MEGA de una sola vez en bibliotecas grandes). También
se puede disparar a mano en cualquier momento.

No usa huella de audio ni decodifica el audio en ningún momento: todo el
matching es por nombre de archivo + búsqueda de texto. Pensado para
colecciones de nicho (industrial/EBM/underground) donde Discogs suele tener
mejor cobertura que MusicBrainz.

## Cómo sabe qué procesar en cada corrida

No hay manifest ni base de estado separada. La fuente misma es el estado:
en cada corrida, lista (sin descargar) lo que hay en `MEGA_SOURCE_PATH`,
toma los primeros `BATCH_SIZE` (100 por defecto) archivos que encuentra, y
por cada uno:

1. Descarga solo ese archivo (no toda la biblioteca).
2. Si ya tiene ID3 completo, no llama a ninguna API — se sube tal cual.
3. Si no, tagea por texto vía Discogs (con respaldo de iTunes).
4. Verifica con `mutagen` que el resultado quedó completo.
5. Si quedó completo: sube a `/untitledless` y **recién ahí** borra el
   original de la fuente.
6. Si quedó incompleto, o algo falló en el camino: el original **no se
   toca**, queda en la fuente para reintentarse en una corrida futura.

Como el borrado solo ocurre tras confirmar la subida, un archivo que
falla nunca desaparece sin haberse reemplazado — y lo que queda en la
fuente en cualquier momento es exactamente lo que falta procesar,
sin necesidad de llevar un registro aparte.

## Antes de correrlo

1. Sube esta carpeta a un repo de GitHub.
2. Sacá un token gratuito de Discogs: `discogs.com` → tu cuenta → **Settings
   → Developers → Generate new token**.
3. En el repo, **Settings → Secrets and variables → Actions → pestaña
   "Secrets"**, agregá:
   - `MEGA_EMAIL` — el correo de tu cuenta MEGA
   - `MEGA_PASSWORD` — la contraseña
   - `DISCOGS_TOKEN` — el token del paso 2
4. En la misma sección pero pestaña **"Variables"** (aparte de Secrets),
   agregá `MEGA_SOURCE_PATH` con la **ruta interna** de tu cuenta MEGA
   donde están los crudos sin tagear (ej. `/musica`). Tiene que ser la ruta
   real dentro de tu cuenta — **no** un link público de `mega.nz/folder/...`,
   porque el bot necesita poder listar y borrar dentro de esa carpeta, y
   eso requiere estar logueado en la cuenta dueña, no acceder por link
   compartido. Esta variable es la que usan las corridas automáticas
   (schedule); una corrida manual puede pisarla completando el input.
5. Listo — el workflow ya corre solo cada 5 horas. Para dispararlo a mano:
   pestaña **Actions** → *Untitled track killer* → **Run workflow**.

## Advertencia: esto borra tus archivos originales

El diseño reemplaza los crudos por la versión tageada. El borrado solo pasa
después de confirmar que la subida a `/untitledless` fue exitosa (nunca
antes), pero sigue siendo un borrado real y permanente en MEGA. Antes de
dejarlo corriendo solo sobre tu biblioteca completa:

- Probá primero con una carpeta chica.
- Confirmá en `/untitledless` que el resultado de una corrida de prueba
  está bien, antes de confiar en corridas automáticas sin supervisión.

## Cosas a tener en cuenta

- **Sin manifest, la fuente es la cola**: si agregás canciones nuevas a
  `MEGA_SOURCE_PATH`, la próxima corrida las va a encontrar automáticamente
  (aparecen en el listado, no están en `/untitledless` todavía).
- **Nombres de archivo ambiguos**: si no hay un separador `Artista -
  Título` claro en el nombre, no hay forma confiable de parsear el
  artista — ese archivo queda incompleto (y por lo tanto sin borrar de la
  fuente) a propósito, no se inventa nada.
- **Rate limits**: Discogs ~60 req/min, iTunes ~20 req/min (el bot
  respeta ambos con pausas). Con 100 archivos por corrida esto no debería
  acercarse al límite de tiempo de un job de Actions, pero en bibliotecas
  con muchos casos de respaldo-por-iTunes puede tardar más.
- **Versión de Ubuntu**: el instalador de MEGAcmd asume `xUbuntu_24.04`.
  Si GitHub cambia la versión por defecto de `ubuntu-latest` y la
  instalación falla, ajustá ese número en
  `.github/workflows/tag-music.yml`.
- **Ajustar el intervalo o el tamaño del lote**: el cron (`0 */5 * * *`,
  cada 5 horas) está en `.github/workflows/tag-music.yml`. El tamaño de
  lote es el input `batch_size` (default 100) para corridas manuales, o la
  variable de entorno `BATCH_SIZE` en el workflow para cambiar el default
  de las corridas automáticas.

## Correrlo localmente (sin GitHub Actions)

```bash
pip install -r requirements.txt
export MEGA_EMAIL="tu correo"
export MEGA_PASSWORD="tu contraseña"
export MEGA_SOURCE="/musica"    # ruta interna, no link publico
export MEGA_DEST="/untitledless"
export DISCOGS_TOKEN="tu token"
export BATCH_SIZE=100
bash scripts/run.sh
```

Necesitás `megacmd` instalado en el sistema.
