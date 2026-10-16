# ViReal — render en vivo para IngeTrazo

**ViReal** es una extensión para **IngeTrazo** que abre una **ventana flotante
e independiente** con tu modelo convertido en una **escena iluminada y
navegable**, con estética de motor de juego: texturas reales, sol con sombras
suaves, materiales con brillo, **reflejos en tiempo real**, **relieve en las
piedras**, oclusión ambiental, *bloom* y un cielo fotográfico con exposición.

No es un visor de líneas más: es un pequeño «mundo» que puedes **recorrer a
pie** con la cámara libre, mientras sigues modelando.

> Extensión de un solo archivo (`vireal.py`), sin dependencias extra: sólo
> IngeTrazo + PySide6 (ya incluidos).

---

## Características

- **Ventana flotante propia**: su propio contexto OpenGL, shaders, VBOs y
  texturas. No comparte ni deforma el viewport del CAD, así que puedes ver el
  modelo en el CAD y en ViReal a la vez.
- **Texturas reales** del modelo, con la misma proyección UV por cara que usa
  el núcleo (`core.texture.face_uv_axes`), incluida la rotación de textura.
- **Materiales con brillo**: lee `core.materials`, `core.group` y
  `core.finish` (roughness, metalness, transmisión, IOR) y aplica un sombreado
  **PBR aproximado** (Cook-Torrance GGX + difusa Lambert + ambiente
  hemisférico).
- **Sol real del documento**: la dirección de la luz sale de `core.sun`
  (latitud, longitud, fecha y hora del documento). **Sombras suaves** (PCSS)
  sobre un mapa de sombras empaquetado de 2048×2048, con un desenfoque que
  crece con la distancia del receptor.
- **Motor HDR con *post-proceso***: la escena se dibuja en *float* (RGBA16F) y
  se procesa en pasos separados — **SSAO** (oclusión ambiental),
  ***bloom*** (halo del sol y luces) y **composición** con *tone mapping* ACES
  y gamma.
- **Reflejos en tiempo real (SSR)**: los pisos brillantes, el vidrio y el metal
  reflejan lo que tienen alrededor, mezclado con Fresnel por ángulo de visión y
  por rugosidad. Se complementa con un reflejo analítico del cielo.
- **Texturas con relieve**: a partir de la propia textura difusa se genera un
  *normal map* (Sobel sobre la luminancia) que da **relieve a piedras,
  ladrillos y revoques**; el césped y las superficies lisas quedan sin relieve.
- **Materiales con brillo**: lee `core.materials`, `core.group` y
  `core.finish` (roughness, metalness, transmisión, IOR) y aplica un sombreado
  **PBR aproximado** (Cook-Torrance GGX + difusa Lambert + ambiente
  hemisférico). Los pisos con nombre de material brillante
  (porcelanato, mármol, granito, cerámica, pulido…) reciben además un acabado
  **glossy** ajustable.
- **Paletas de cielo**: día, atardecer, nublado y noche.
- **Cielo fotográfico** con degradado y halo solar (no el fondo gris del CAD),
  **tone mapping ACES** y corrección gamma.
- **Césped 3D**: las superficies cuya textura o material se llame
  «césped»/«grass» (también pasto, hierba, lawn, turf, grama, sod…) reciben
  miles de hojas sembradas sobre la propia cara —con el verde medio de tu
  textura— que **se mecen con el viento** y reciben las sombras del sol.
- **Cámara libre tipo juego** (ratón + WASD) y **reencuadre automático** al
  abrir la ventana.
- **Auto-actualización**: si cambias el modelo, la ventana lo reconstruye sola.
- **Ajustes persistentes**: sombras, exposición, ambiente, césped, viento,
  densidad de césped, reflejos, oclusión, *bloom*, relieve, brillo de pisos y
  paleta de cielo se recuerdan entre sesiones.

---

## Requisitos

- **IngeTrazo 0.5.x** (API de extensiones v2).
- **GPU con OpenGL 3.3** (cualquier equipo moderno; se ha probado en una
  NVIDIA RTX 3060).
- Windows / macOS / Linux donde corra IngeTrazo.

---

## Instalación

1. Copia el archivo **`vireal.py`** a la carpeta de plugins de IngeTrazo.
   - En IngeTrazo: **Extensiones ▸ Abrir carpeta de plugins**.
   - Ruta típica en Windows: `%APPDATA%\IngeTrazo\plugins`.
2. **Reinicia IngeTrazo.**
3. Abre la ventana (ver *Uso*).

> Si venías de la versión anterior llamada «Mundo», borra `mundo.py` de la
> carpeta de plugins para no registrar el panel dos veces.

### Instalación rápida desde este repositorio

```powershell
./build_zip.ps1 -Install
```

Empaqueta el `.zip` y además copia `vireal.py` a tu carpeta de plugins.

---

## Uso

Puedes abrir la ventana de dos formas:

- Panel lateral **ViReal** → botón **▶ Abrir ventana ViReal**.
- Menú de extensiones → **ViReal — ventana de render 3D en vivo**
  (atajo **`Ctrl+Alt+M`**).

### Controles

| Entrada            | Acción                                   |
| ------------------ | ---------------------------------------- |
| Arrastrar (ratón)  | Mirar alrededor                          |
| `W` / `A` / `S` / `D` | Moverse adelante / izquierda / atrás / derecha |
| `Q` / `E`          | Bajar / subir                            |
| `Shift`            | Correr                                   |
| Rueda del ratón    | Acercar / alejar                         |
| `R`                | Reencuadrar todo el modelo               |
| `T`                | Activar / desactivar sombras             |
| `C`                | Activar / desactivar el césped 3D        |
| `V`                | Activar / desactivar el viento           |
| `F`                | Activar / desactivar reflejos (SSR)      |
| `G`                | Activar / desactivar oclusión (SSAO)     |
| `B`                | Activar / desactivar *bloom*             |
| `N`                | Cambiar de paleta de cielo               |
| `Esc`              | Cerrar la ventana                        |

En la barra superior de la ventana tienes botones para **Actualizar**,
**Reencuadrar**, alternar **Sombras**, **Césped 3D**, **Viento**, **Reflejos**,
**Oclusión** y ***Bloom***, elegir la **paleta de cielo** y ajustar
**Exposición**, **Ambiente**, **densidad de césped** (hojas por m²; 0 lo
desactiva), **reflejos de entorno**, **brillo de pisos** y **relieve**.

---

## Cómo funciona (resumen técnico)

ViReal no refleja el viewport: construye su propia escena leyendo el modelo.

1. **Geometría**: recorre las caras visibles con `scene.iter_world_faces()`
   (aplicando las matrices de mundo), triangula los polígonos con agujeros y
   calcula por vértice posición, normal, UV, color y parámetros de material.
   Los triángulos se agrupan en *runs* por material/textura para dibujar pocas
   llamadas.
2. **Texturas**: carga cada textura con `QOpenGLTexture` y mapea con los ejes
   UV que da `core.texture.face_uv_axes`.
3. **Materiales**: `core.materials.effective_attrs`,
   `core.group.effective_material` y `core.finish.PBR`/`resolve` alimentan los
   parámetros PBR de cada run. Un *normal map* se **deriva de la propia textura
   difusa** (Sobel sobre la luminancia, cacheado) para dar relieve a piedras y
   ladrillos; los nombres de material se inspeccionan para decidir qué es
   glossy y qué tiene relieve.
4. **Luz y sombras**: se construye una matriz ortográfica de luz a partir de la
   dirección del sol; se renderiza un mapa de profundidad de 2048² y en el pase
   principal se compara con **PCSS** (sombras suaves: estimación del tamaño del
   *penumbra* + PCF). El mapa se cachea y sólo se rehace cuando cambia la escena
   (no depende de la cámara).
5. **Césped 3D**: las caras detectadas como césped se triangulan y sobre ellas
   se siembra un número de hojas proporcional a su área (hasta un tope). Cada
   hoja es una cinta afilada de 3 triángulos, con altura, ancho, inclinación,
   tono y color propios (teñidos con el color medio real de la textura). Un
   programa aparte las dibuja y las deforma en el *vertex shader* con tres senos
   desfasados (ráfagas de viento) ponderados por la altura; reciben las sombras
   del sol. La siembra es determinista, así que el césped no «salta» al
   reconstruir por cambios ajenos.
6. **Motor HDR y *post-proceso***: la escena se dibuja a un
   `QOpenGLFramebufferObject` RGBA16F con **tres salidas** (color, normal +
   rugosidad, posición) mediante MRT. Sobre ese *G-buffer* corren, en pasos
   separados, **SSAO** (oclusión ambiental a partir de la posición y la
   normal), **SSR** (reflejos en pantalla que marchan el rayo reflejado por el
   *buffer* de posición), ***bright pass* + *blur*** (*bloom*) y la
   **composición** final, que aplica ACES, gamma, viñeta y el HUD con
   `QPainter`.
7. **Presentación**: cielo con degradado y disco solar (elegible por paleta),
   *tone mapping* ACES, gamma 2.2 y un HUD dibujado con `QPainter` sobre el
   widget.

---

## Estructura del repositorio

```
ingetrazo_vireal/
├── vireal.py        # la extensión (esto es lo que se instala)
├── README.md
├── LICENSE          # GPL-3.0-or-later
├── CHANGELOG.md
├── .gitignore
└── build_zip.ps1    # empaqueta dist/ingetrazo_vireal-<version>.zip
```

---

## Empaquetado para publicar

```powershell
./build_zip.ps1 -Version 0.3.0
```

Genera `dist/ingetrazo_vireal-0.3.0.zip` con `vireal.py`, `README.md`,
`LICENSE` y `CHANGELOG.md`. El usuario final sólo necesita copiar `vireal.py`
a su carpeta de plugins. Recuerda subir también `dist/` a la *release* de
GitHub (o adjuntar el `.zip`).

> Si omites `-Version`, el script la lee automáticamente de `__version__` en
> `vireal.py`.

---

## Limitaciones conocidas y hoja de ruta

- El núcleo de IngeTrazo **no expone *material maps* PBR** (normal, roughness,
  AO), así que el realismo es «aproximado». El **relieve** se deriva de la
  textura difusa (Sobel), no de un *normal map* real; cuando el modelo de datos
  los exponga, se usarán directamente.
- **Reflejos (SSR)** y **oclusión (SSAO)** son *screen-space*: sólo ven lo que
  está en pantalla; lo que queda fuera cae al reflejo analítico del cielo. En
  GPUs sin OpenGL 3.3 con MRT se desactivan solos y el render sigue funcionando.
- El **césped 3D** es geométrico (hojas sueltas, no *tufts* con textura
  alfa), no se auto-sombra entre hojas y se siembra al reconstruir la escena:
  en céspedes muy grandes, más densidad = más hojas = más trabajo por
  reconstrucción. La densidad es ajustable desde la barra.
- Ideas para próximas versiones: más hojas *instanced* (GPU) para densidades
  muy altas, *water/refraction* real, niebla volumétrica, *depth of field* y
  terreno de césped continuo.

---

## Licencia

[GNU General Public License v3.0 o posterior](LICENSE)
(`GPL-3.0-or-later`).

## Autor

**Mauro Javier Montenegro** — mauro.montenegro@gmail.com
