# Changelog

Todas las fechas son en formato AAAA-MM-DD.

## [0.3.0] — 2026-10-15

Reescritura del motor de render: pasa de un pase directo a un **pipeline HDR con
*post-proceso*** (G-buffer MRT → SSAO → SSR → *bloom* → composición).

### Añadido
- **Reflejos en tiempo real (SSR)**: los pisos brillantes, el vidrio y el metal
  reflejan la escena que los rodea, mezclado con Fresnel por ángulo de visión y
  por rugosidad. Sobre los bordes de pantalla cae a un reflejo analítico del
  cielo. Tecla `F` y casilla **Reflejos**.
- **Reflejos de entorno** en el *shader* principal (aproximación IBL que
  muestrea el cielo analítico) con un control de intensidad **reflejos de
  entorno**.
- **Pisos glossy**: los materiales cuyo nombre suena a piso pulido
  (porcelanato, mármol, granito, cerámica, terrazo, laca, vinílico…) se vuelven
  reflectantes, más un control global **Brillo de pisos** que baja la rugosidad
  de las caras que miran hacia arriba.
- **Relieve en las texturas**: se genera un *normal map* desde la propia
  textura difusa (Sobel sobre la luminancia, cacheado) y se aplica con un marco
  tangente por fragmento. Las **piedras, ladrillos y revestimientos** reciben un
  relieve marcado; el resto, uno suave; el césped, ninguno. Control **Relieve**.
- **Sombras suaves (PCSS)**: estimación del tamaño de la *penumbra* según la
  distancia del receptor y filtrado PCF, en lugar del PCF 3×3 duro.
- **SSAO**: oclusión ambiental *screen-space* sobre la posición y la normal.
  Tecla `G` y casilla **Oclusión**.
- ***Bloom***: *bright pass* + desenfoque separable (dos pasadas), que da halo
  al sol y a las luces. Tecla `B` y casilla ***Bloom***.
- **Paletas de cielo**: **Día**, **Atardecer**, **Nublado** y **Noche**, con el
  sol teñido por la elevación real. Tecla `N` y desplegable en la barra.
- **G-buffer MRT** (RGBA16F, 3 salidas) creado vía
  `QOpenGLVersionFunctionsFactory`; si la GPU no lo soporta, el render cae
  automáticamente a un camino de una sola salida (sin SSR/SSAO).

### Cambiado
- El *tone mapping* ACES y la gamma ahora se aplican en la **composición**, no
  en cada *shader* de geometría (que pasan a emitir HDR lineal).
- La barra superior pasa a **dos filas** para acomodar los controles nuevos.
- El HUD informa del estado de reflejos, oclusión, *bloom* y paleta.

### Notas
- Los ajustes nuevos (reflejos, oclusión, *bloom*, entorno, brillo de pisos,
  relieve, paleta) también se guardan entre sesiones.
- La orientación del *normal map* derivado se ajusta al mismo espejado que la
  textura difusa.

## [0.2.0] — 2026-10-15

### Añadido
- **Césped 3D**: las caras cuya textura o material se detecta como césped
  (`grass`, `cesped`, `césped`, `pasto`, `hierba`, `lawn`, `turf`, `grama`,
  `sod`, sin acentos y mirando también la carpeta) reciben miles de hojas
  sembradas sobre la superficie, con altura, ancho, inclinación, color y fase
  propios. El color base sale del **color medio real de la textura** del suelo.
- **Viento**: un *vertex shader* propio mece las hojas con tres senos
  desfasados ponderados por la altura (ráfagas), orientados por una dirección
  de viento global. Activación/desactivación instantánea.
- Las hojas **reciben las sombras** del sol (mismo mapa de sombras).
- Controles nuevos en la barra: **Césped 3D**, **Viento** y **densidad de
  césped** (hojas por m²), más las teclas `C` y `V`. Todo se guarda entre
  sesiones.
- El **mapa de sombras** ahora se cachea y sólo se rehace cuando cambia la
  escena (antes se rehacía en cada fotograma); necesario al repintar de
  continuo para animar el viento.

### Notas
- La siembra del césped es determinista (semilla fija): el césped no «salta»
  al reconstruir por cambios que no lo afectan.
- Tope de 20 000 hojas por reconstrucción para no bloquear la interfaz.

## [0.1.0] — 2026-10-15

Primera versión publicable.

### Añadido
- Ventana flotante (`QDialog`) con su propio contexto OpenGL 3.3, shaders,
  VBOs y texturas, totalmente independiente del viewport del CAD.
- Extracción de la geometría visible del documento (`scene.iter_world_faces`)
  con matrices de mundo, triangulación y proyección UV por cara
  (`core.texture.face_uv_axes`).
- Materiales desde `core.materials` / `core.group` / `core.finish`
  (roughness, metalness, transmisión, IOR) y sombreado PBR aproximado
  (Cook-Torrance GGX + Lambert + ambiente hemisférico).
- **Sol real** del documento (`core.sun`) y **sombras proyectadas** con PCF
  3×3 sobre un mapa de sombras empaquetado de 2048×2048.
- Cielo fotográfico con degradado y halo solar, tone mapping ACES y
  corrección gamma.
- Cámara libre tipo juego (ratón + WASD + Q/E + Shift), reencuadre, control
  de sombras y exposición.
- Reconstrucción automática al cambiar el modelo.
- Panel «ViReal», entrada en el menú de extensiones (`Ctrl+Alt+M`) y ajustes
  persistentes (sombras, exposición, ambiente).
