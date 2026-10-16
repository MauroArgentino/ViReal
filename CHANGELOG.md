# Changelog

Todas las fechas son en formato AAAA-MM-DD.

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
