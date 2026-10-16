# Changelog

Todas las fechas son en formato AAAA-MM-DD.

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
