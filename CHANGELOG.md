# Changelog

Todas las fechas son en formato AAAA-MM-DD.

## [0.8.0] — 2026-11-23

### Añadido
- **Audio ambiente día/noche** opcional: casilla **Ambiente** y volumen en la
  barra. Pájaros y grillos procesados sin sonido directo: sólo reverberación
  exterior con doble filtrado de agudos, para que suenen lejanos. El cambio
  sigue a la hora del sol, incluso al arrastrar la hora.
  Usa `QSoundEffect` (si QtMultimedia está disponible) y guarda el estado en
  `QSettings`.
- Loops CC0 `audio/birds_ambient.wav` y `audio/crickets_ambient.wav` incluidos
  en el paquete (ver README).
- **Step-up**: el caminador sube escalones y bordillos de hasta 30 cm. Ahora se
  ignoran las caras verticales bajas que antes bloqueaban el avance.
- **Relleno de paredes**: las caras verticales sin sol directo reciben más luz
  hemisférica diurna y rebote del lado iluminado; dejan de verse negras. El
  azul del cielo se desatura parcialmente para evitar un aspecto surrealista.
  Igual ajuste en el visor Babylon.js beta.
- **Audio sin QtMultimedia**: si `QSoundEffect` no está disponible o falla, se
  usa `winmm.PlaySoundW` de Windows, así la casilla **Ambiente** sigue activa.
- **Agacharse**: mantener **C** baja la cámara con transición suave y soltarla
  la levanta (impidiendo levantarse bajo techo). La tecla **H** reemplaza a C
  para activar/desactivar el césped.
- **Hora arrastrable**: rueda del ratón (o **Shift + botón derecho**, como en
  Enscape) y **U/I** recorren el día; **Home** restaura el sol del documento.
  Aparecen luna, halo y estrellas de noche, con luz lunar difusa.
- La ventana de vista real (y la de Babylon) ahora se pueden **minimizar y
  maximizar**.

### Corregido
- Los reflejos de entorno usan ahora la **sombra dura** del mapa, no la versión
  suavizada de la luz directa, y marchan el rayo reflejado contra el G-buffer:
  ni el disco solar, ni su aureola Mie, ni el cielo difuso atraviesan paredes o
  techos en el reflejo del piso. La iluminación directa mantiene sombras suaves.

### Referencia
- Controles de sol de Enscape:
  https://documentation.chaos.com/space/ENSCAPE/842203139/Control+and+Input
  (Shift + botón derecho o U/I; la rueda se usa para mover la cámara).

## [0.7.1] — 2026-11-19

### Cambiado
- Visor Babylon más liviano: geometría enviada como `Float32Array` en base64,
  texturas como archivos PNG temporales de 512 px (se borran al cerrar) y tope
  de 24 000 vértices por escena.
- Se libera el payload de Python justo después de inyectarlo en la página.
- Iluminación del visor: IBL (`environmentSpecular.env`), hemisférica reforzada,
  rebote desde abajo y sombras más suaves para que las caras sin sol directo
  no queden negras.

## [0.7.0] — 2026-11-19

### Añadido
- Visor **Babylon.js (beta)** dentro del plugin: botón **Babylon (beta)** en la
  barra, ventana `QWebEngineView` y exportación de la geometría/texturas de
  ViReal a la escena Babylon (`babylon/index.html`, `babylon/viewer.js`).
- Requiere que la instalación incluya `PySide6.QtWebEngineWidgets`; la escena
  se inyecta por `runJavaScript` y el motor Babylon se carga desde su CDN.

## [0.6.6] — 2026-11-19

### Corregido
- La dirección del sol se compara cada frame con `shadows.when_utc(lon)`,
  latitud, longitud y norte, así mover la hora en IngeTrazo actualiza
  `_light_vp` y la sombra sin depender de `scene.version`.
- La pasada de sombras fuerza `GL_DEPTH_TEST` y `glDepthMask(True)` antes de
  limpiar y dibujar el mapa.
- Las caras en sombra recuperan rebote: el ambiente difuso usa
  `skyAmbient(N) + u_gnd_col`, con muestra HDRI opcional sobre la normal y un
  factor `0.85`, en vez del `0.25` anterior que dejaba las caras casi negras.

## [0.6.5] — 2026-11-19

### Corregido
- El mapa de sombras ahora tiene un búfer de profundidad real: se reemplazó
  `NoAttachment` por `QOpenGLFramebufferObject.Depth` en `_ensure_shadow_fbo`.
  Así el Z-buffer ordena muro y piso por distancia al sol en vez de por orden
  de dibujo, y el muro vuelve a proyectar sombra sobre el piso.

## [0.6.4] — 2026-11-19

### Corregido
- Relieve de juntas: se invirtió el signo de `gx` en el Sobel y se bajó la
  intensidad de 2,5 a 1,4 para que el cemento claro no genere cordones.
- Costura vertical del horizonte HDRI: el eje horizontal usa `GL_REPEAT` y el
  vertical `GL_CLAMP_TO_EDGE`.
- El horizonte HDRI también se refleja en pisos pulidos y vidrio: `_FRAG`
  mezcla la misma muestra por la dirección reflejada `R`.

## [0.6.3] — 2026-11-19

### Corregido
- El horizonte HDR aparecía invertido: se corrigió la orientación vertical de
  la textura equirectangular.

## [0.6.2] — 2026-11-19

### Añadido
- **Horizonte HDR**: si `grasslands_sunset_2k.hdr` está junto al plugin, se usa
  como fondo cerca y debajo del horizonte; el cielo físico por encima se conserva.

### Cambiado
- Equilibrio de luz: el sol directo pasa de 3,4 a 7,0 y el relleno ambiental
  del cielo se reduce al 25%, para evitar el aspecto quemado con exposiciones
  altas. El brillo especular del entorno cae con el cuadrado de la rugosidad.
- El **SSAO** se suaviza con dos pasadas separables antes de la composición.
- Las texturas y los mapas de relieve usan **anisotropía 16×** para que los
  pisos vistos en ángulo rasante conserven el detalle.

## [0.6.1] — 2026-11-19

### Corregido
- Aterrizaje sobre suelos y colisión con figuras: el colisionador ahora lee
  correctamente los tres vértices de cada triángulo del buffer de la escena.

## [0.6.0] — 2026-11-19

### Añadido
- **Modo caminar** con gravedad y colisiones, Activado/desactivado con la
  **barra espaciadora** o la casilla **Gravedad**. El observador cae, se apoya
  sobre las superficies superiores y choca contra muebles y paredes; el
  desplazamiento lateral se desliza sobre los obstáculos.
- **Altura de la persona** ajustable (1,20–2,20 m, por defecto 1,75 m). Los
  ojos se sitúan al **93%** de esa altura, acordes con las proporciones humanas.
- Los escalones de hasta **0,28 m** se suben automáticamente; el cuerpo se
  modela como un cilindro de **0,25 m** de radio.
- Índice espacial de triángulos reconstruido con la escena para que las
  consultas de suelo y colisión no recorran toda la malla.

### Corregido
- El avance continuo de la cámara cuando se perdía el `keyRelease` o el
  `mouseRelease` (cambio de foco, ratón fuera de la vista): al perder el foco o
  ocultarse la ventana se limpian las teclas y el arrastre.

## [0.5.1] — 2026-11-19

### Añadido
- Selector **Arrastre** en la barra de la ventana para invertir la dirección
  del ratón: **Normal**, **Invertir X**, **Invertir Y** o **Invertir X e Y**.
  La elección se guarda en los ajustes de ViReal.

## [0.5.0] — 2026-10-16

Césped **instanciado en la GPU** con **LOD por distancia**: el césped pasa de
un puñado de hojas sueltas a un **manto continuo** estilo Enscape.

### Añadido
- **GPU instancing** para el césped: una **hoja plantilla** (9 vértices) se
  reutiliza para todas las instancias; los datos de cada hoja (raíz, marco de la
  superficie, alto, ancho, tinte y fase de viento) van en **atributos por
  instancia** (`glVertexAttribDivisor` + `glDrawArraysInstanced`). Se pasa de
  ~20 000 a **hasta 200 000 hojas** en una sola llamada de dibujo.
- **LOD por distancia**: las hojas lejanas se pliegan al suelo de forma suave
  (`smoothstep`) y el **terreno texturizado hace de impostor** de césped. El
  radio de LOD crece con el tamaño de la escena. Al reencuadrar todo el modelo
  no se ven «hojas sueltas» a lo lejos; al caminar, el césped real aparece.
- **Relieve suave en el césped**: las caras de césped ya no quedan planas; con
  el terreno de impostor reciben un *normal-map* tenue para que la hierba se lea
  como superficie, no como un piso pintado.

### Cambiado
- **Densidad de césped por defecto**: de 45 a **500 hojas/m²** (el control
  llega ahora a 2000). El motor viejo, además, era 3× más lento sembrando; el
  bucle nuevo evita objetos por instancia y siembra 200 000 hojas en ~0,5 s.
- Cambiar la **densidad** ahora sólo **resiembra el césped**, sin reconstruir
  toda la geometría del modelo (mucho más ágil con el control).
- Migración de ajustes: si venías con la densidad por defecto antigua (45) se
  sube una sola vez a 500; si la habías elegido a mano, se respeta.
- El césped deja de forzar repintados del viento cuando está oculto.

### Notas
- Requiere soporte de *instancing* (OpenGL 3.3 lo incluye). Si el contexto no lo
  diera, el césped 3D se desactiva solo y el resto del render sigue igual.
- La siembra sigue siendo **determinista** (semilla fija): el césped no salta al
  reconstruir por cambios ajenos.

## [0.4.0] — 2026-10-16

Iluminación **física**: el cielo y la luz del sol salen de un modelo atmosférico
real en vez de una paleta de colores fija.

### Añadido
- **Cielo atmosférico** (dispersión **Rayleigh + Mie**): el gradiente del cielo,
  el blanqueo del horizonte, el azul del cenit y el enrojecimiento del atardecer
  se calculan con un modelo de masa de aire. El sol tiene **disco y aureola**
  con borde suave.
- **Sol físico**: el color y la intensidad de la luz directa dependen de la
  **elevación solar real** del documento — blanco al mediodía, cálido y tenue al
  atardecer, apagado de noche — mediante la transmitancia atmosférica.
- **Ambiente por el cielo (IBL aproximado)**: el relleno de sombra se integra
  muestreando el propio modelo de cielo alrededor de la normal, así que cambia de
  color con la hora y con la orientación de cada cara (el suelo rebota distinto
  que el cielo).
- **Reflejos coherentes con el fondo**: el *shader* de geometría refleja el
  **mismo cielo** que se ve de fondo.
- **Brillo especular suave**: el lóbulo especular se ensancha según el tamaño
  angular del sol (y la bruma), de modo que los reflejos en pisos glossy son
  manchas suaves y no un punto duro.

### Cambiado
- Las **paletas** (Día, Atardecer, Nublado, Noche) ya no fijan los colores: sólo
  ajustan el *look* (tinte del sol y del cielo, turbidez y brillo base) sobre la
  base física.
- El *shader* del **césped** usa el mismo modelo de sol y cielo.

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
