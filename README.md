# ViReal — render en vivo para IngeTrazo

**ViReal** es una extensión para **IngeTrazo** que abre una **ventana flotante
e independiente** con tu modelo convertido en una **escena iluminada y
navegable**, con estética de motor de juego: texturas reales, **sol y cielo
físicos** con sombras suaves, materiales con brillo, **reflejos en tiempo
real**, **relieve en las piedras**, **césped 3D instanciado** que se mece con el
viento, oclusión ambiental, *bloom* y exposición fotográfica.

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
- **Sol y cielo físicos**: la dirección de la luz sale de `core.sun` (latitud,
  longitud, fecha y hora del documento) y su **color e intensidad** de un modelo
  atmosférico (dispersión **Rayleigh + Mie**): blanco al mediodía, cálido y
  tenue al atardecer, apagado de noche. El **cielo** es ese mismo modelo, con
  disco solar y aureola. **Sombras suaves** (PCSS) sobre un mapa de sombras
  empaquetado de 2048×2048, con un desenfoque que crece con la distancia del
  receptor.
- **Ambiente e IBL del cielo**: el relleno de sombra y los reflejos de entorno
  se integran muestreando el propio cielo físico, así que cambian con la hora y
  con la orientación de cada cara. Las paredes verticales reciben un relleno
  hemisférico diurno reforzado y rebote del lado iluminado, para que el lado en
  sombra no quede antinaturalmente negro. El brillo especular se ensancha según
  el tamaño angular del sol, para reflejos suaves en vez de un punto duro. El
  reflejo de entorno usa la **sombra dura** del mapa y, además, recorre el rayo
  reflejado contra el G-buffer: si un muro o techo aparece en ese camino, el
  disco solar, su aureola Mie y el cielo quedan ocultos, aunque la luz directa
  conserve sombras suaves. Así un sol al este no se refleja en un piso protegido
  por planos a su alrededor.
- **Motor HDR con *post-proceso***: la escena se dibuja en *float* (RGBA16F) y
  se procesa en pasos separados — **SSAO** (oclusión ambiental, suavizado antes
  de aplicarse), ***bloom*** (halo del sol y luces) y **composición** con
  *tone mapping* ACES y gamma.
- **Reflejos en tiempo real (SSR)**: los pisos brillantes, el vidrio y el metal
  reflejan lo que tienen alrededor, mezclado con Fresnel por ángulo de visión y
  por rugosidad. El *shader* de geometría refleja el **mismo cielo** que se ve
  de fondo.
- **Texturas con relieve**: a partir de la propia textura difusa se genera un
  *normal map* (Sobel sobre la luminancia) que da **relieve a piedras,
  ladrillos y revoques**; el césped recibe uno tenue (para que el terreno de
  impostor se lea como hierba) y las superficies lisas quedan sin relieve.
  Tanto las texturas como los mapas usan **anisotropía 16×** para conservar el
  detalle en ángulos rasantes.
- **Materiales con brillo**: lee `core.materials`, `core.group` y
  `core.finish` (roughness, metalness, transmisión, IOR) y aplica un sombreado
  **PBR aproximado** (Cook-Torrance GGX + difusa Lambert + ambiente del cielo).
  Los pisos con nombre de material brillante
  (porcelanato, mármol, granito, cerámica, pulido…) reciben además un acabado
  **glossy** ajustable.
- **Paletas de cielo**: día, atardecer, nublado y noche; sólo ajustan el *look*
  (tinte del sol y del cielo, turbidez, brillo base) sobre la base física.
- **Cielo físico** con degradado atmosférico, horizonte y disco solar (no el
  fondo gris del CAD), **tone mapping ACES** y corrección gamma. Si el archivo
  `grasslands_sunset_2k.hdr` está junto al plugin, se mezcla sólo en el
  horizonte como fondo de gran angular.
- **Césped 3D instanciado**: las superficies cuya textura o material se llame
  «césped»/«grass» (también pasto, hierba, lawn, turf, grama, sod…) reciben
  **cientos de miles de hojas instanciadas en la GPU** —con el verde medio de tu
  textura— que **se mecen con el viento** y reciben las sombras del sol. Un
  **LOD por distancia** pliega las hojas lejanas al suelo y el terreno texturizado
  hace de *impostor*, de modo que el césped se lee como un **manto continuo**
  (estilo Enscape) sin castigar el rendimiento.
- **Cámara libre tipo juego** (ratón + WASD) y **reencuadre automático** al
  abrir la ventana.
- **Auto-actualización**: si cambias el modelo, la ventana lo reconstruye sola.
- **Audio ambiente día/noche**: pájaros con reverberación exterior durante el
  día y grillos de noche al oscurecer, con volumen opcional y persistente.
- **Ajustes persistentes**: sombras, exposición, ambiente, césped, viento,
  densidad de césped, reflejos, oclusión, *bloom*, relieve, brillo de pisos,
  paleta de cielo y audio se recuerdan entre sesiones.

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
| Arrastrar (ratón)  | Mirar alrededor (dirección configurable en la barra: Normal / Invertir X / Invertir Y / Invertir X e Y) |
| `W` / `A` / `S` / `D` | Moverse adelante / izquierda / atrás / derecha |
| `Q` / `E`          | Bajar / subir                            |
| `Shift`            | Correr                                   |
| `Espacio`          | Activar / desactivar gravedad y colisiones (modo caminar; sube escalones de hasta 30 cm) |
| Rueda del ratón    | Acercar / alejar                         |
| Rueda **presionada** + arrastrar | Recorrer la hora del día       |
| `Shift` + botón derecho + arrastrar | Recorrer la hora (como en Enscape) |
| `U` / `I`          | Adelantar / retrasar la hora             |
| `Home`             | Restaurar el sol del documento           |
| `C` (mantener)     | Agacharse con transición suave           |
| `R`                | Reencuadrar todo el modelo               |
| `T`                | Activar / desactivar sombras             |
| `H`                | Activar / desactivar el césped 3D        |
| `V`                | Activar / desactivar el viento           |
| `F`                | Activar / desactivar reflejos (SSR)      |
| `G`                | Activar / desactivar oclusión (SSAO)     |
| `B`                | Activar / desactivar *bloom*             |
| `N`                | Cambiar de paleta de cielo               |
| `Esc`              | Cerrar la ventana                        |

En la barra superior de la ventana tienes botones para **Actualizar**,
**Reencuadrar**, alternar **Sombras**, **Césped 3D**, **Viento**, **Reflejos**,
**Oclusión** y ***Bloom***, elegir la **paleta de cielo**, invertir la
**dirección del arrastre** del ratón (eje X, eje Y o ambos), activar la
**Gravedad**, ajustar la **Altura** de la persona (los ojos quedan al 93%) y
regular **Exposición**, **Ambiente**, **densidad de césped** (hojas por m²; 0 lo
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
4. **Atmósfera, luz y sombras**: un modelo analítico de **masa de aire**
   (Rayleigh + Mie) da el color del cielo en cada dirección y la transmitancia
   del sol, de la que salen su color e intensidad según la elevación real. Con la
   dirección del sol se construye una matriz ortográfica de luz; se renderiza un
   mapa de profundidad de 2048² y en el pase principal se compara con **PCSS**
   (sombras suaves: estimación del tamaño del *penumbra* + PCF). El mapa se
   cachea y sólo se rehace cuando cambia la escena (no depende de la cámara).
5. **Césped 3D instanciado**: las caras detectadas como césped se triangulan y
   sobre ellas se siembra un número de hojas proporcional a su área (hasta un
   tope de 200 000). Cada hoja no es geometría propia: es una **instancia** de
   una **hoja plantilla** (9 vértices) con sus datos en atributos por instancia
   (raíz, marco de la superficie, alto, ancho, tinte, fase). Se dibujan con
   `glDrawArraysInstanced` en **una sola llamada**, deformadas en el *vertex
   shader* con tres senos desfasados (ráfagas de viento). Un **LOD por
   distancia** pliega las hojas lejanas al suelo (el terreno texturizado hace de
   impostor). La siembra es determinista, así que el césped no «salta» al
   reconstruir por cambios ajenos.
6. **Motor HDR y *post-proceso***: la escena se dibuja a un
   `QOpenGLFramebufferObject` RGBA16F con **tres salidas** (color, normal +
   rugosidad, posición) mediante MRT. Sobre ese *G-buffer* corren, en pasos
   separados, **SSAO** (oclusión ambiental a partir de la posición y la
   normal), **SSR** (reflejos en pantalla que marchan el rayo reflejado por el
   *buffer* de posición), ***bright pass* + *blur*** (*bloom*) y la
   **composición** final, que aplica ACES, gamma, viñeta y el HUD con
   `QPainter`.
7. **Presentación**: cielo atmosférico con disco solar y aureola (el *look* lo
   ajusta la paleta, la física la pone la hora), *tone mapping* ACES, gamma 2.2
   y un HUD dibujado con `QPainter` sobre el widget.

---

## Estructura del repositorio

```
ingetrazo_vireal/
├── vireal.py        # la extensión (esto es lo que se instala)
├── babylon/         # visor Babylon.js (beta)
│   ├── index.html
│   └── viewer.js
├── audio/           # sonido ambiente CC0
│   └── birds_cc0.wav
├── grasslands_sunset_2k.hdr
├── README.md
├── LICENSE          # GPL-3.0-or-later
├── CHANGELOG.md
├── .gitignore
└── build_zip.ps1    # empaqueta dist/ingetrazo_vireal-<version>.zip
```

---

## Empaquetado para publicar

```powershell
./build_zip.ps1 -Version 0.5.0
```

Genera `dist/ingetrazo_vireal-0.8.0.zip` con `vireal.py`, `README.md`,
`LICENSE`, `CHANGELOG.md`, el HDR y las carpetas `babylon/` y `audio/`. El
usuario final necesita copiar `vireal.py`, el HDR, `babylon/` y `audio/` a su
carpeta de plugins.
Recuerda subir también `dist/` a la *release* de GitHub (o adjuntar el `.zip`).

> Si omites `-Version`, el script la lee automáticamente de `__version__` en
> `vireal.py`.

---

## Visor Babylon.js (beta)

El botón **Babylon (beta)** abre una segunda ventana con un canvas Babylon.js.
La vista OpenGL sigue siendo el render principal; esta ventana exporta una
copia comprimida de la geometría y las texturas visibles (hasta 24 000
vértices) y las inyecta en la página mediante `QWebEngineView.runJavaScript`.
Los PNG temporales se crean en `%TEMP%\vireal_babylon_*` y se borran al cerrar
la ventana. Cerrarla también descarta el motor Babylon para liberar RAM.

Requiere que IngeTrazo incluya `PySide6.QtWebEngineWidgets`. El motor Babylon
y el mapa de entorno IBL se descargan desde sus URLs oficiales, así que esta
ventana necesita conexión a internet. Si no está disponible, ilumina igual con
luz hemisférica y el render OpenGL sigue funcionando igual.

---

## Sonido ambiente

La casilla **Ambiente** reproduce un loop estéreo **sin sonido directo**: sólo
la reverberación exterior, con doble filtrado de agudos (suena lejano, no
pegado a la cámara): `audio/birds_ambient.wav` de día y
`audio/crickets_ambient.wav` de noche (el cambio sigue a la hora del sol, así
que también responde al recorrido con rueda/`U`/`I`). El deslizador adyacente
ajusta el volumen, se apaga al cerrar la ventana y recuerda ambos valores entre
sesiones. Si la instalación no trae `PySide6.QtMultimedia` o `QSoundEffect`
falla, se usa el reproductor de Windows (`winmm`): el sonido sigue funcionando
y la barra ajusta el volumen de la salida de Windows (se restaura al cerrar).

---

## Recorrido del día

Con la **rueda presionada** y arrastrando a izquierda/derecha se recorre la
hora del día: alba, mediodía, ocaso, noche y vuelta al alba. También funcionan
**Shift + botón derecho + arrastrar** y las teclas **U/I**; **Home** vuelve al
sol que viene del documento. De noche aparecen la **luna** (con halo y luz
difusa) y un campo de **estrellas** procedural.

La documentación oficial de Enscape confirma el ajuste de hora con
**Shift + botón derecho** o **U/I**
(https://documentation.chaos.com/space/ENSCAPE/842203139/Control+and+Input);
allí la rueda se usa para avanzar la cámara, así que aquí se añadió además la
rueda presionada según tu pedido.

La ventana de vista real y la de Babylon se pueden **minimizar y maximizar**
con los botones del título.

---

## Limitaciones conocidas y hoja de ruta

- El núcleo de IngeTrazo **no expone *material maps* PBR** (normal, roughness,
  AO), así que el realismo es «aproximado». El **relieve** se deriva de la
  textura difusa (Sobel), no de un *normal map* real; cuando el modelo de datos
  los exponga, se usarán directamente.
- **Reflejos (SSR)** y **oclusión (SSAO)** son *screen-space*: sólo ven lo que
  está en pantalla; lo que queda fuera cae al reflejo del cielo físico. En
  GPUs sin OpenGL 3.3 con MRT se desactivan solos y el render sigue funcionando.
- La **atmósfera** es un modelo analítico de un solo pase (masa de aire), no un
  trazado de dispersión multicapa: reproduce bien el color del cielo, el
  horizonte y el enrojecimiento del atardecer, pero no nubes volumétricas.
- El **césped 3D** es geométrico (hojas instanciadas, no *tufts* con textura
  alfa) y no se auto-sombra entre hojas. Se siembra al reconstruir la escena:
  más densidad = más hojas, con un tope de 200 000 por reconstrucción (~0,5 s).
  Un **LOD por distancia** pliega las hojas lejanas al suelo, así que sólo las
  hojas **cercanas** cuestan relleno de píxeles. La densidad es ajustable desde
  la barra.
- Ideas para próximas versiones: *tufts* de césped con alfa, siembra
  **relativa a la cámara** (resembrar sólo el entorno cercano para céspedes
  enormes), *water/refraction* real, niebla volumétrica y *depth of field*.

---

## Licencia

[GNU General Public License v3.0 o posterior](LICENSE)
(`GPL-3.0-or-later`).

El HDR `grasslands_sunset_2k.hdr` **no es GPL**: es
[Grasslands Sunset de Poly Haven](https://polyhaven.com/a/grasslands_sunset),
bajo **CC0 1.0** (foto: Dimitrios Savva; procesado: Jarod Guest). Verificado
por MD5 `e90aa885c6237866893a696e650f9cba` contra la API de Poly Haven. CC0
no exige atribución, pero se incluye de cortesía.

Los loops incluidos son versiones procesadas (reverberación, estéreo y
*crossfade*) de grabaciones **CC0 1.0**:

- `audio/birds_ambient.wav` ← `Black-capped Chickadee 201947598.wav` de
  [*ngoomie*](https://commons.wikimedia.org/wiki/File:Black-capped_Chickadee_201947598.wav)
  (grabado en Calgary, 2024; SHA-1 `90830954646a38e35606da2063281be6de309124`).
- `audio/crickets_ambient.wav` ← `cricketsounds090613.wav` de
  [*syncopika*](https://opengameart.org/content/cricket-sounds) (grabado en su
  patio, 2014; SHA-256 `ECC088A3D1AB967202B63A92836DADC47BD08C8AB3F17D8527F620264AA5FAF9`).

## Autor

**Mauro Javier Montenegro** — mauro.montenegro@gmail.com
