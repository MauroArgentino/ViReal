# ViReal — render en vivo para IngeTrazo

**ViReal** es una extensión para **IngeTrazo** que abre una **ventana flotante
e independiente** con tu modelo convertido en una **escena iluminada y
navegable**, con estética de motor de juego: texturas reales, sol con sombras
proyectadas, materiales con brillo y un cielo fotográfico con exposición.

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
  (latitud, longitud, fecha y hora del documento). **Sombras proyectadas** con
  PCF 3×3 sobre un mapa de sombras empaquetado de 2048×2048.
- **Cielo fotográfico** con degradado y halo solar (no el fondo gris del CAD),
  **tone mapping ACES** y corrección gamma.
- **Cámara libre tipo juego** (ratón + WASD) y **reencuadre automático** al
  abrir la ventana.
- **Auto-actualización**: si cambias el modelo, la ventana lo reconstruye sola.
- **Ajustes persistentes**: sombras, exposición y ambiente se recuerdan entre
  sesiones.

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
| `Esc`              | Cerrar la ventana                        |

En la barra superior de la ventana tienes botones para **Actualizar**,
**Reencuadrar**, alternar **Sombras** y ajustar **Exposición** y **Ambiente**.

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
   parámetros PBR de cada run.
4. **Luz y sombras**: se construye una matriz ortográfica de luz a partir de la
   dirección del sol; se renderiza un mapa de profundidad de 2048² y en el pase
   principal se compara con PCF 3×3 (con sesgo por pendiente y corrección del
   plano receptor para evitar acné).
5. **Presentación**: cielo con degradado y disco solar, *tone mapping* ACES,
   gamma 2.2 y un HUD dibujado con `QPainter` sobre el widget.

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
./build_zip.ps1 -Version 0.1.0
```

Genera `dist/ingetrazo_vireal-0.1.0.zip` con `vireal.py`, `README.md`,
`LICENSE` y `CHANGELOG.md`. El usuario final sólo necesita copiar `vireal.py`
a su carpeta de plugins. Recuerda subir también `dist/` a la *release* de
GitHub (o adjuntar el `.zip`).

---

## Limitaciones conocidas y hoja de ruta

- El núcleo de IngeTrazo **no expone *material maps* PBR** (normal, roughness,
  AO), así que el realismo es «aproximado»: color, rugosidad, metalicidad y
  transmisión, pero no relieve. Es el máximo que permite el modelo de datos.
- Sin **reflejos de entorno reales**, **SSAO** ni **anti-aliasing** más allá
  del MSAA del *surface*.
- Ideas para próximas versiones: reflejos por entorno, *bloom* suave,
  césped/terreno, sombras suaves (PCSS) y una paleta de cielos elegible.

---

## Licencia

[GNU General Public License v3.0 o posterior](LICENSE)
(`GPL-3.0-or-later`).

## Autor

**Mauro Javier Montenegro** — mauro.montenegro@gmail.com
