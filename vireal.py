# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 — extensión «ViReal» para IngeTrazo.
"""ViReal — ventana flotante de render en vivo, estilo motor de juego.

Abre una **ventana flotante independiente** con su propio contexto OpenGL y
su propio renderizado: coge la geometría visible del modelo, la triangula, le
aplica las texturas reales y los *finishes* (core.finish: roughness,
metalness, transmisión) y la ilumina con el **sol real** del documento, un
cielo y un ambiente atmosféricos físicos, sombras proyectadas, *tone mapping*
ACES y corrección gamma. El resultado es un «ViReal» navegable a pie, con
cámara libre tipo juego (ratón + WASD), no el aspecto de línea de un CAD.

No pretende ser Unreal Engine (IngeTrazo no expone shaders PBR ni material
maps), pero sí da el salto de «dibujo» a «escena iluminada»: superficies con
material, luz direccional con penumbra, cielo con halo solar y exposición
fotográfica.

Es una extensión de sólo ``setup(app)`` (API v2): añade una entrada en
Extensiones (Ctrl+Alt+M), un panel «ViReal» con el botón de abrir y la ventana
en sí. La ventana se refresca sola: si cambias el modelo, la reconstruye.

Las superficies cuya textura o material se llame «césped»/«grass» (pasto,
hierba, lawn, turf…) reciben **césped 3D**: miles de hojas sembradas sobre la
cara que se mecen con el viento y reciben las sombras del sol.

Desde 0.3.0 el motor es un *pipeline* HDR con post-proceso:

* **Reflejos en tiempo real** (SSR, *screen-space reflections*) sobre las
  superficies glossy — pisos, vidrio, metal — más un reflejo analítico del
  cielo (IBL aproximado) que siempre está ahí aunque el SSR falle.
* **Relieve** por *normal-map* derivado de la propia textura (bump): las
  piedras, ladrillos y revestimientos se ven con volumen.
* **Pisos brillantes** (glossy): un control baja la rugosidad de las caras
  que miran hacia arriba para que reflejen como porcelanato pulido.
* **Sombras suaves** (PCSS) con penumbra que crece con la distancia.
* **Sombra ambiental** (SSAO) que asienta los objetos sobre el suelo.
* **Bloom** solar y paletas de cielo (día, atardecer, nublado, noche).

Desde 0.4.0 la iluminación es **física**: un modelo atmosférico (dispersión
Rayleigh + Mie) genera el cielo real y de él salen el color y la intensidad del
sol según su elevación real (blanco al mediodía, naranja y tenue al atardecer,
apagado de noche), el ambiente por muestreo del propio cielo (IBL aproximado) y
un brillo especular suave acorde al tamaño angular del sol. El **mismo cielo**
que se ve de fondo es el que se refleja en las superficies. Las paletas (día,
atardecer, nublado, noche) sólo ajustan el *look* sobre esa base física.

Instalar: copiar este archivo en la carpeta de plugins
(Extensiones ▸ Abrir carpeta de plugins) y reiniciar IngeTrazo.
"""
from __future__ import annotations

import logging
import math
import os
import random
from array import array
from bisect import bisect_left

from PySide6.QtCore import QElapsedTimer, QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QMatrix4x4,
    QOpenGLFunctions,
    QPainter,
    QSurfaceFormat,
    QVector2D,
    QVector3D,
)
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
    QOpenGLVersionFunctionsFactory,
    QOpenGLVersionProfile,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger("ingetrazo.plugins.vireal")

#: Versión de la extensión (coincide con el CHANGELOG y el paquete .zip).
__version__ = "0.4.0"

_SETTINGS = "vireal/"

#: Color de cara por defecto del núcleo (views/viewport.py).
DEFAULT_FACE_COLOR = (0.96, 0.95, 0.925)


def _lerp3(a: QVector3D, b: QVector3D, t: float) -> QVector3D:
    """Interpolación lineal entre dos vectores (QVector3D no trae ``lerp``)."""
    t = max(0.0, min(1.0, t))
    return QVector3D(a.x() + (b.x() - a.x()) * t,
                     a.y() + (b.y() - a.y()) * t,
                     a.z() + (b.z() - a.z()) * t)

# ---------------------------------------------------------------------------
# Constantes GL (el núcleo las declara a mano; QOpenGLFunctions las acepta).
# ---------------------------------------------------------------------------
GL_TRIANGLES = 0x0004
GL_FLOAT = 0x1406
GL_DEPTH_TEST = 0x0B71
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_LEQUAL = 0x0203
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_CULL_FACE = 0x0B44
GL_TEXTURE_2D = 0x0DE1
GL_TEXTURE0 = 0x84C0
GL_TEXTURE1 = 0x84C1
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_LINEAR = 0x2601
GL_RGBA8 = 0x8058
GL_RGBA16F = 0x881A
GL_CLAMP_TO_EDGE = 0x812F
GL_MULTISAMPLE = 0x809D
GL_TEXTURE2 = 0x84C2
GL_TEXTURE3 = 0x84C3
GL_TEXTURE4 = 0x84C4
GL_FRAMEBUFFER = 0x8D40
GL_COLOR_ATTACHMENT0 = 0x8CE0
GL_COLOR_ATTACHMENT1 = 0x8CE1
GL_COLOR_ATTACHMENT2 = 0x8CE2

#: Nombres de los uniformes de cada programa (PySide6 exige ``bytes`` en
#: ``uniformLocation`` y la localización para valores float/int).
_ATMO_UNIFORMS = (
    "u_sun_dir", "u_sun_tint", "u_sky_tint", "u_gnd_col", "u_sky_floor",
    "u_mie", "u_ambient",
)
_MAIN_UNIFORMS = _ATMO_UNIFORMS + (
    "u_mvp", "u_cam", "u_shadow_strength", "u_light_vp",
    "u_shadow_enable", "u_shadow", "u_tex", "u_color", "u_opacity",
    "u_use_texture", "u_use_bump", "u_bump", "u_bump_strength",
    "u_env", "u_floor_gloss",
)
_SKY_UNIFORMS = _ATMO_UNIFORMS + ("u_inv_vp", "u_cam")
_DEPTH_UNIFORMS = ("u_light_vp",)
_GRASS_UNIFORMS = _ATMO_UNIFORMS + (
    "u_mvp", "u_cam", "u_shadow_enable", "u_shadow",
    "u_light_vp", "u_shadow_strength", "u_time", "u_wind_dir",
    "u_wind_strength",
)
_SSAO_UNIFORMS = (
    "u_pos", "u_mat", "u_view", "u_proj", "u_texel", "u_radius", "u_bias",
    "u_intensity",
)
_BRIGHT_UNIFORMS = ("u_tex", "u_threshold", "u_knee")
_BLUR_UNIFORMS = ("u_tex", "u_dir", "u_texel")
_COMPOSITE_UNIFORMS = (
    "u_color", "u_ao", "u_bloom", "u_pos", "u_mat", "u_cam", "u_view",
    "u_proj", "u_exposure", "u_ao_strength", "u_bloom_amount", "u_env",
    "u_ssr_enable", "u_ssr_strength", "u_vignette",
)

#: Tamaño del mapa de sombras (px).
SHADOW_SIZE = 2048

#: stride (bytes) e offsets del vértice interleaved:
#: pos(3) @0, nrm(3) @12, uv(2) @24, col(3) @32, pbr(3) @44  → 56 bytes.
V_STRIDE = 56
V_POS = 0
V_NRM = 12
V_UV = 24
V_COL = 32
V_PBR = 44

#: Vértice del césped (otro programa): pos(3) @0, nrm(3) @12, col(3) @24,
#: datos(4) @36 (t 0..1, fase, flexibilidad, altura) → 52 bytes.
G_STRIDE = 52
G_POS = 0
G_NRM = 12
G_COL = 24
G_DATA = 36

#: Palabras que marcan una textura o material de césped (se buscan sin
#: acentos, sin extensión y en el nombre del archivo + su carpeta padre).
_GRASS_WORDS = ("grass", "cesped", "pasto", "hierba", "lawn", "turf",
                "grama", "tepe", "sod", "chacra")

#: Materiales pisos que deben verse pulidos/glossy (porcelanato, mármol…).
_GLOSS_WORDS = ("piso", "floor", "porcelanato", "porcelanic", "ceramic",
                "ceramica", "ceramico", "marmol", "marble", "pulido",
                "brillante", "glossy", "baldosa", "tile", "granito",
                "granite", "microcemento", "laca", "terrazo", "vinilico")

#: Materiales con relieve (piedra, ladrillo, adoquín, revestimiento…).
_STONE_WORDS = ("piedra", "stone", "rock", "roca", "ladrillo", "brick",
                "adoquin", "paving", "pavimento", "revestimiento", "muro",
                "mamposteria", "block", "hormigon visto", "texturado",
                "travertino", "laja", "canto", "aspero", "rugoso")

#: Paletas de cielo. Cada preset ajusta el modelo atmosférico:
#: (etiqueta, tinte del sol, tinte del cielo, turbidez Mie, brillo base del
#: cielo, color del suelo/rebote). El color y la intensidad reales del sol
#: salen de la física (elevación solar); esto sólo es el «look» de la paleta.
_SKY_PRESETS = (
    ("Día", (1.00, 1.00, 1.00), (1.00, 1.00, 1.00), 1.0, (0.0, 0.0, 0.0),
     (0.10, 0.10, 0.10)),
    ("Atardecer", (1.05, 0.80, 0.58), (1.00, 0.95, 0.95), 1.7,
     (0.004, 0.004, 0.007), (0.07, 0.05, 0.04)),
    ("Nublado", (0.32, 0.33, 0.36), (0.72, 0.74, 0.78), 3.2,
     (0.030, 0.031, 0.036), (0.16, 0.16, 0.16)),
    ("Noche", (0.05, 0.06, 0.11), (0.035, 0.050, 0.100), 0.6,
     (0.17, 0.22, 0.30), (0.015, 0.02, 0.035)),
)

#: Color de la hoja cuando no se puede leer la textura del suelo.
_GRASS_FALLBACK = (0.30, 0.45, 0.17)

#: Altura (m) y ancho (m) base de cada hoja de césped.
GRASS_HEIGHT = 0.11
GRASS_WIDTH = 0.014

#: Tope de hojas por reconstrucción (una escena enorme no debe colgar la app).
GRASS_MAX_BLADES = 20000

#: Velocidad/amplitud del viento por defecto.
WIND_DIR = (0.80, 0.60)
WIND_STRENGTH = 0.05

#: Caché del color medio de las texturas (para teñir las hojas).
_AVG_COLOR_CACHE: dict = {}

#: Caché de los normal-map generados a partir de la textura (relieve).
_NORMAL_CACHE: dict = {}


# ---------------------------------------------------------------------------
# Shaders
# ---------------------------------------------------------------------------

#: Modelo atmosférico analítico (dispersión Rayleigh + Mie) compartido por los
#: shaders. Genera el color del cielo, el color/intensidad del sol según su
#: elevación real y un ambiente aproximado por muestreo del cielo (IBL).
#: Se incluye tal cual en cada fragment shader (los uniforms que usa ya están
#: declarados aquí, así que los shaders que lo incluyen no deben repetirlos).
_ATMO_GLSL = """
uniform vec3 u_sun_dir;      // unitaria, hacia el sol
uniform vec3 u_sun_tint;     // tinte/intensidad del sol (paleta)
uniform vec3 u_sky_tint;     // tinte del cielo (paleta)
uniform vec3 u_gnd_col;      // rebote del suelo / color bajo el horizonte
uniform vec3 u_sky_floor;    // brillo base del cielo (noche, nublado)
uniform float u_mie;         // turbidez (Mie): bruma / nubes
uniform float u_ambient;     // multiplicador global de ambiente

const float ATMO_PI = 3.14159265359;
// Profundidades ópticas cenitales (Rayleigh por canal, Mie acromática).
const vec3  ATMO_OD_R = vec3(0.045, 0.105, 0.180);
const float ATMO_OD_M = 0.021;
const float ATMO_G    = 0.760;   // asimetría Mie (hacia delante)
const float ATMO_SKY  = 105.0;   // escala de la dispersión del cielo
const float ATMO_SUN  = 3.4;     // radiancia del sol directo
const float ATMO_DISC = 34.0;    // radiancia del disco solar
const float ATMO_ANG  = 0.00465; // radio angular del sol (rad ≈ 0,27°)

// Masa de aire (Kasten-Young simplificada): ~1 en el cenit, crece mucho en el
// horizonte. Es lo que hace que el sol se enrojezca y se apague al atardecer.
float atmoAirMass(float cz) {
    cz = max(cz, -0.12);
    return 1.0 / max(cz + 0.025 * exp(-11.0 * cz), 0.0015);
}

// Transmitancia de la atmósfera para una dirección con coseno cenital cz.
vec3 atmoTrans(float cz) {
    float am = atmoAirMass(cz);
    return exp(-(ATMO_OD_R + vec3(ATMO_OD_M * 1.1 * u_mie)) * am);
}

float atmoPhaseR(float mu) {
    return 3.0 / (16.0 * ATMO_PI) * (1.0 + mu * mu);
}

float atmoPhaseM(float mu) {
    float g = ATMO_G;
    float gg = g * g;
    return (1.0 - gg)
         / (4.0 * ATMO_PI * pow(max(1.0 + gg - 2.0 * g * mu, 1e-4), 1.5));
}

// Dispersión del cielo en la dirección (unitaria) *dir*, SIN el disco solar.
vec3 skyScatter(vec3 dir) {
    float am = atmoAirMass(dir.z);
    vec3 attn = exp(-(ATMO_OD_R + vec3(ATMO_OD_M * 1.1 * u_mie)) * am);
    float mu = dot(dir, u_sun_dir);
    // La raíz deja que el cenit conserve azul al atardecer (la luz que lo
    // ilumina viene de capas altas, menos extinguidas que el sol rasante).
    vec3 sunT = sqrt(max(atmoTrans(u_sun_dir.z), vec3(0.0)));
    vec3 insc = (ATMO_OD_R * atmoPhaseR(mu)
                 + vec3(ATMO_OD_M * u_mie) * atmoPhaseM(mu)) * (1.0 - attn);
    vec3 col = ATMO_SKY * u_sky_tint * sunT * insc + u_sky_floor * u_sky_tint;
    // Bajo el horizonte: rebote del suelo.
    float below = smoothstep(0.0, -0.12, dir.z);
    return mix(col, u_gnd_col, below);
}

// Cielo completo (dispersión + disco solar con borde suave).
vec3 skyRadiance(vec3 dir) {
    vec3 col = skyScatter(dir);
    float mu = dot(dir, u_sun_dir);
    float ang = acos(clamp(mu, -1.0, 1.0));
    float disc = 1.0 - smoothstep(ATMO_ANG * 0.7, ATMO_ANG * 1.3, ang);
    col += ATMO_DISC * u_sun_tint * atmoTrans(u_sun_dir.z) * disc;
    return col;
}

// Luz solar directa (color HDR × intensidad), con extinción y apagado de noche.
vec3 sunLight() {
    float day = smoothstep(-0.04, 0.10, u_sun_dir.z);
    return ATMO_SUN * u_sun_tint * atmoTrans(u_sun_dir.z) * day;
}

// Ambiente aproximado (IBL): tres muestras del cielo alrededor de la normal.
// Para una normal hacia abajo las muestras caen bajo el horizonte y toma el
// color de rebote del suelo, así el relleno de sombra cambia según la cara.
vec3 skyAmbient(vec3 n) {
    vec3 up = vec3(0.0, 0.0, 1.0);
    return skyScatter(normalize(n + up * 0.5)) * 0.50
         + skyScatter(up) * 0.25
         + skyScatter(normalize(n * 0.6 - up * 0.4)) * 0.25;
}
"""

_VERT = """
#version 330 core
layout(location = 0) in vec3 a_pos;
layout(location = 1) in vec3 a_nrm;
layout(location = 2) in vec2 a_uv;
layout(location = 3) in vec3 a_col;
layout(location = 4) in vec3 a_pbr;   // roughness, metallic, transmission
uniform mat4 u_mvp;
out vec3 v_world;
out vec3 v_nrm;
out vec2 v_uv;
out vec3 v_col;
out vec3 v_pbr;
void main() {
    v_world = a_pos;
    v_nrm = a_nrm;
    v_uv = a_uv;
    v_col = a_col;
    v_pbr = a_pbr;
    gl_Position = u_mvp * vec4(a_pos, 1.0);
}
"""

_FRAG = """
#version 330 core
in vec3 v_world;
in vec3 v_nrm;
in vec2 v_uv;
in vec3 v_col;
in vec3 v_pbr;
""" + _ATMO_GLSL + """
uniform vec3 u_cam;
uniform vec3 u_color;        // tinte extra (normalmente blanco)
uniform int u_use_texture;
uniform sampler2D u_tex;
uniform int u_use_bump;
uniform sampler2D u_bump;    // normal-map derivado de la textura (relieve)
uniform float u_bump_strength;
uniform float u_env;         // intensidad de los reflejos de entorno (IBL)
uniform float u_floor_gloss; // 0..1: vuelve las caras hacia arriba pulidas
uniform int u_shadow_enable;
uniform sampler2D u_shadow;
uniform mat4 u_light_vp;
uniform float u_shadow_strength;
uniform float u_opacity;
layout(location = 0) out vec4 out_color;  // radiancia HDR (sin tonemap)
layout(location = 1) out vec4 out_mat;    // normal.xyz + rugosidad
layout(location = 2) out vec4 out_pos;    // posición mundo + reflectividad

const vec2 POISSON[12] = vec2[12](
    vec2(-0.326, -0.406), vec2(-0.840, -0.074), vec2(-0.696, 0.457),
    vec2(-0.203, 0.621), vec2(0.962, -0.195), vec2(0.473, -0.480),
    vec2(0.519, 0.767), vec2(0.185, -0.893), vec2(0.507, 0.064),
    vec2(0.896, 0.412), vec2(-0.322, -0.933), vec2(-0.792, -0.598)
);

float unpackDepth(vec3 e) {
    return dot(e, vec3(1.0, 1.0 / 255.0, 1.0 / 65025.0));
}

// Sombras suaves (PCSS): se busca la profundidad media de los bloqueadores y
// el radio del filtro crece con la penumbra, así las sombras lejanas quedan
// más difusas que las que tocan el objeto.
float shadowFactor(vec3 wpos, float ndl) {
    if (u_shadow_enable == 0) return 1.0;
    vec4 lp = u_light_vp * vec4(wpos, 1.0);
    vec3 uvz = lp.xyz / lp.w * 0.5 + 0.5;
    if (uvz.x <= 0.0 || uvz.x >= 1.0 || uvz.y <= 0.0 || uvz.y >= 1.0
            || uvz.z >= 1.0) {
        return 1.0;
    }
    vec2 texel = 1.0 / vec2(textureSize(u_shadow, 0));
    // Corrección del plano receptor (Isidoro): cada muestra compara la
    // profundidad guardada con el plano del receptor evaluado EN esa muestra.
    vec3 ddx = dFdx(uvz);
    vec3 ddy = dFdy(uvz);
    float det = ddx.x * ddy.y - ddx.y * ddy.x;
    vec2 grad = (abs(det) > 1e-12)
        ? vec2(ddy.y * ddx.z - ddx.y * ddy.z,
               ddx.x * ddy.z - ddy.x * ddx.z) / det
        : vec2(0.0);
    float glen = length(grad);
    if (glen > 8.0) {
        grad *= 8.0 / glen;
        glen = 8.0;
    }
    float bias = 0.0006 + glen * texel.x * 1.5;

    float blockerSum = 0.0;
    float blockerCnt = 0.0;
    for (int i = 0; i < 12; i++) {
        vec2 off = POISSON[i] * texel * 3.0;
        float d = unpackDepth(texture(u_shadow, uvz.xy + off).rgb);
        if (uvz.z + dot(grad, off) - bias > d) {
            blockerSum += d;
            blockerCnt += 1.0;
        }
    }
    if (blockerCnt < 0.5) return 1.0;
    float avgB = blockerSum / blockerCnt;
    float penumbra = clamp((uvz.z - avgB) / max(avgB, 1e-4), 0.0, 1.0);
    float radius = mix(1.0, 24.0, penumbra);
    float lit = 0.0;
    for (int i = 0; i < 12; i++) {
        vec2 off = POISSON[i] * texel * radius;
        float d = unpackDepth(texture(u_shadow, uvz.xy + off).rgb);
        lit += (uvz.z + dot(grad, off) - bias <= d) ? 1.0 : 0.0;
    }
    return mix(1.0, lit / 12.0, u_shadow_strength);
}

void main() {
    vec4 base = vec4(u_color, 1.0);
    if (u_use_texture == 1) {
        base *= texture(u_tex, v_uv);
    }
    base.rgb *= v_col;
    float rough = clamp(v_pbr.x, 0.04, 1.0);
    float metal = v_pbr.y;
    float trans = v_pbr.z;
    vec3 albedo = base.rgb;

    vec3 N = normalize(v_nrm);
    vec3 V = normalize(u_cam - v_world);
    if (dot(N, V) < 0.0) N = -N;          // sombreado a dos caras

    // Pisos brillantes: las caras que miran hacia arriba se pulen.
    float upFacing = smoothstep(0.55, 0.95, N.z);
    rough = mix(rough, 0.06, upFacing * u_floor_gloss);
    metal = max(metal, upFacing * u_floor_gloss * 0.20);

    // Relieve: normal-map derivado de la textura. La base tangente se obtiene
    // de las derivadas (marco cotangente de Christian Schüler).
    if (u_use_bump == 1 && u_bump_strength > 0.001) {
        vec3 nm = texture(u_bump, v_uv).xyz * 2.0 - 1.0;
        nm.xy *= u_bump_strength;
        vec3 dp1 = dFdx(v_world);
        vec3 dp2 = dFdy(v_world);
        vec2 duv1 = dFdx(v_uv);
        vec2 duv2 = dFdy(v_uv);
        vec3 dp2perp = cross(dp2, N);
        vec3 dp1perp = cross(N, dp1);
        vec3 T = dp2perp * duv1.x + dp1perp * duv2.x;
        vec3 B = dp2perp * duv1.y + dp1perp * duv2.y;
        float im = inversesqrt(max(dot(T, T), dot(B, B)) + 1e-8);
        mat3 TBN = mat3(T * im, B * im, N);
        N = normalize(TBN * normalize(nm));
    }

    vec3 L = normalize(u_sun_dir);
    float ndl = max(dot(N, L), 0.0);
    float ndv = max(dot(N, V), 1e-3);
    vec3 H = normalize(L + V);
    float ndh = max(dot(N, H), 0.0);

    float sh = shadowFactor(v_world, ndl);

    // Cook-Torrance GGX. El sol tiene un tamaño angular finito y la bruma lo
    // agranda: se ensancha el lóbulo especular para que el brillo sea suave
    // (un reflejo realista) en vez de un punto duro.
    float sunSoft = 0.015 + 0.028 * min(u_mie, 3.0);
    float rr = min(rough + sunSoft, 1.0);
    float a = max(rr * rr, 1e-3);
    float a2 = a * a;
    float denom = ndh * ndh * (a2 - 1.0) + 1.0;
    float D = a2 / (3.141593 * denom * denom);
    float k = a * 0.5;
    float G = (ndl / (ndl * (1.0 - k) + k))
            * (ndv / (ndv * (1.0 - k) + k));
    vec3 F0 = mix(vec3(0.04), albedo, metal);
    vec3 F = F0 + (1.0 - F0) * pow(1.0 - max(dot(H, V), 0.0), 5.0);
    vec3 spec = D * G * F / (4.0 * ndl * ndv + 1e-4);
    vec3 kd = (1.0 - F) * (1.0 - metal);

    // Luz solar directa: color e intensidad salen del modelo atmosférico
    // (blanco al mediodía, naranja y tenue al atardecer, apagada de noche).
    vec3 sun = sunLight();
    vec3 direct = (kd * albedo / 3.141593 + spec) * sun * ndl * sh;

    // Ambiente: se integra el propio cielo muestreado alrededor de la normal
    // (IBL aproximado), así el relleno de sombra toma el color real del cielo.
    vec3 amb = albedo * skyAmbient(N) * u_ambient;

    // Reflejo del entorno: el mismo cielo que se ve de fondo, difuminado con
    // la rugosidad (se mezcla con el ambiente difuso).
    vec3 R = reflect(-V, N);
    vec3 env = skyRadiance(R);
    env = mix(skyAmbient(N), env, clamp(1.0 - rough * 1.6, 0.0, 1.0));
    float fres = pow(1.0 - ndv, 5.0);
    vec3 Fenv = F0 + (1.0 - F0) * fres;
    vec3 ambientSpec = env * Fenv * (1.0 - rough) * u_env;

    vec3 col = direct + amb + ambientSpec;

    if (trans > 0.0) {
        float fres2 = pow(1.0 - ndv, 3.0);
        vec3 refl = mix(skyAmbient(N), env, 0.7) * (0.25 + 0.75 * fres2);
        col = mix(col, refl + amb * 0.6, clamp(trans * 0.9, 0.0, 1.0));
    }

    out_color = vec4(col, u_opacity * base.a);
    out_mat = vec4(N, rough);
    out_pos = vec4(v_world, 1.0 - rough);
}
"""

_SKY_VERT = """
#version 330 core
out vec2 v_ndc;
void main() {
    vec2 p = vec2((gl_VertexID == 1) ? 3.0 : -1.0,
                  (gl_VertexID == 2) ? 3.0 : -1.0);
    v_ndc = p;
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

_SKY_FRAG = """
#version 330 core
in vec2 v_ndc;
uniform mat4 u_inv_vp;
uniform vec3 u_cam;
""" + _ATMO_GLSL + """
layout(location = 0) out vec4 out_color;
layout(location = 1) out vec4 out_mat;
layout(location = 2) out vec4 out_pos;

void main() {
    vec4 far = u_inv_vp * vec4(v_ndc, 1.0, 1.0);
    vec3 dir = normalize(far.xyz / far.w - u_cam);
    // Cielo físico (dispersión + disco solar) en HDR: el tonemap y el bloom
    // se aplican en el post-proceso.
    out_color = vec4(skyRadiance(dir), 1.0);
    out_mat = vec4(0.0);   // el cielo no tiene normal: se salta SSR/SSAO
    out_pos = vec4(0.0);
}
"""

_DEPTH_VERT = """
#version 330 core
layout(location = 0) in vec3 a_pos;
uniform mat4 u_light_vp;
void main() {
    gl_Position = u_light_vp * vec4(a_pos, 1.0);
}
"""

_DEPTH_FRAG = """
#version 330 core
out vec4 fragColor;
void main() {
    // La proyección es ortográfica, así que gl_FragCoord.z es lineal.
    float d = gl_FragCoord.z;
    // Sesgo dependiente de la pendiente en el EMISOR: una cara casi
    // paralela al sol cambia de profundidad muy rápido dentro de un texel
    // y produce acné con cualquier sesgo constante.
    float slope = max(abs(dFdx(d)), abs(dFdy(d)));
    d = min(d + min(slope * 2.0, 0.01) + 0.0004, 0.999999);
    vec3 e = fract(vec3(1.0, 255.0, 65025.0) * d);
    e.xy -= e.yz * (1.0 / 255.0);
    fragColor = vec4(e, 1.0);
}
"""

_GRASS_VERT = """
#version 330 core
layout(location = 0) in vec3 a_pos;   // posición en reposo (mundo)
layout(location = 1) in vec3 a_nrm;
layout(location = 2) in vec3 a_col;
layout(location = 3) in vec4 a_data;  // t (0..1), fase, flexibilidad, altura
uniform mat4 u_mvp;
uniform float u_time;
uniform vec2 u_wind_dir;
uniform float u_wind_strength;
out vec3 v_world;
out vec3 v_nrm;
out vec3 v_col;
out float v_t;
void main() {
    float t = a_data.x;
    float phase = a_data.y;
    float flex = a_data.z;
    // Peso cuadrático: la base no se mueve y la punta sí. Tres senos
    // desfasados dan ráfagas (no un vaivén uniforme).
    float w = t * t;
    float s = sin(u_time * 1.9 + phase) * 0.6
            + sin(u_time * 3.7 + phase * 1.7) * 0.3
            + sin(u_time * 0.9 + phase * 0.5) * 0.5;
    float gust = 0.55 + 0.45 * s;
    vec3 dir = normalize(vec3(u_wind_dir, 0.18));
    float amp = u_wind_strength * flex * w * gust;
    // vibración rápida en la punta (detalle)
    amp += u_wind_strength * 0.12 * w * sin(u_time * 6.5 + phase * 3.1);
    vec3 wp = a_pos + dir * amp;
    v_world = wp;
    v_nrm = a_nrm;
    v_col = a_col;
    v_t = t;
    gl_Position = u_mvp * vec4(wp, 1.0);
}
"""

_GRASS_FRAG = """
#version 330 core
in vec3 v_world;
in vec3 v_nrm;
in vec3 v_col;
in float v_t;
uniform vec3 u_cam;
""" + _ATMO_GLSL + """
uniform int u_shadow_enable;
uniform sampler2D u_shadow;
uniform mat4 u_light_vp;
uniform float u_shadow_strength;
layout(location = 0) out vec4 out_color;
layout(location = 1) out vec4 out_mat;
layout(location = 2) out vec4 out_pos;

float unpackDepth(vec3 e) {
    return dot(e, vec3(1.0, 1.0 / 255.0, 1.0 / 65025.0));
}

float grassShadow(vec3 wpos) {
    if (u_shadow_enable == 0) return 1.0;
    vec4 lp = u_light_vp * vec4(wpos, 1.0);
    vec3 uvz = lp.xyz / lp.w * 0.5 + 0.5;
    if (uvz.x <= 0.0 || uvz.x >= 1.0 || uvz.y <= 0.0 || uvz.y >= 1.0
            || uvz.z >= 1.0) {
        return 1.0;
    }
    vec2 texel = 1.0 / vec2(textureSize(u_shadow, 0));
    vec3 ddx = dFdx(uvz);
    vec3 ddy = dFdy(uvz);
    float det = ddx.x * ddy.y - ddx.y * ddy.x;
    vec2 grad = (abs(det) > 1e-12)
        ? vec2(ddy.y * ddx.z - ddx.y * ddy.z,
               ddx.x * ddy.z - ddy.x * ddx.z) / det
        : vec2(0.0);
    float glen = length(grad);
    if (glen > 8.0) { grad *= 8.0 / glen; glen = 8.0; }
    float bias = 0.0006 + glen * texel.x * 1.5;
    float lit = 0.0;
    for (int dx = -1; dx <= 1; dx++) {
        for (int dy = -1; dy <= 1; dy++) {
            vec2 off = vec2(dx, dy) * texel;
            float d = unpackDepth(texture(u_shadow, uvz.xy + off).rgb);
            lit += (uvz.z + dot(grad, off) - bias <= d) ? 1.0 : 0.0;
        }
    }
    return mix(1.0, lit / 9.0, u_shadow_strength);
}

void main() {
    vec3 albedo = v_col;
    vec3 N = normalize(v_nrm);
    vec3 V = normalize(u_cam - v_world);
    if (dot(N, V) < 0.0) N = -N;         // sombreado a dos caras
    vec3 L = normalize(u_sun_dir);
    float ndl = max(dot(N, L), 0.0);
    float sh = grassShadow(v_world);

    vec3 direct = albedo * sunLight() * ndl * sh / 3.141593 * 1.25;
    vec3 amb = albedo * skyAmbient(N) * u_ambient;
    vec3 col = direct + amb;
    // Translucencia aproximada: las puntas dejan pasar algo de sol.
    col += albedo * sunLight() * 0.07 * v_t * sh;

    out_color = vec4(col, 1.0);
    out_mat = vec4(N, 0.95);   // el césped es difuso: sin SSR
    out_pos = vec4(v_world, 0.05);
}
"""

# --- Post-proceso ---------------------------------------------------------
_FS_VERT = """
#version 330 core
out vec2 v_uv;
void main() {
    vec2 p = vec2((gl_VertexID == 1) ? 3.0 : -1.0,
                  (gl_VertexID == 2) ? 3.0 : -1.0);
    v_uv = p * 0.5 + 0.5;
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

_SSAO_FRAG = """
#version 330 core
in vec2 v_uv;
uniform sampler2D u_pos;
uniform sampler2D u_mat;
uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec2 u_texel;
uniform float u_radius;
uniform float u_bias;
uniform float u_intensity;
out vec4 fragColor;

const vec3 KERNEL[16] = vec3[16](
    vec3(0.52, 0.13, 0.30), vec3(-0.41, 0.58, 0.22),
    vec3(0.17, -0.62, 0.35), vec3(-0.55, -0.20, 0.41),
    vec3(0.68, 0.42, 0.18), vec3(-0.28, 0.71, 0.29),
    vec3(0.34, -0.44, 0.52), vec3(-0.62, 0.11, 0.33),
    vec3(0.11, 0.25, 0.72), vec3(-0.19, -0.33, 0.58),
    vec3(0.45, 0.55, 0.44), vec3(-0.47, 0.40, 0.50),
    vec3(0.60, -0.15, 0.28), vec3(-0.08, 0.52, 0.63),
    vec3(0.24, -0.66, 0.30), vec3(-0.33, -0.50, 0.46)
);

float viewDepth(vec3 w) {
    return -(u_view * vec4(w, 1.0)).z;
}

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

void main() {
    vec4 mp = texture(u_pos, v_uv);
    vec3 P = mp.xyz;
    vec3 N = texture(u_mat, v_uv).xyz;
    if (length(N) < 0.2 || length(P) < 1e-4) {
        fragColor = vec4(1.0);
        return;
    }
    N = normalize(N);

    // Base aleatoria -> TBN.
    float a = hash(v_uv * 1024.0) * 6.2831853;
    vec3 rnd = vec3(cos(a), sin(a), 0.0);
    vec3 T = normalize(rnd - N * dot(rnd, N));
    vec3 B = cross(N, T);
    mat3 TBN = mat3(T, B, N);

    float pz = viewDepth(P);
    float occl = 0.0;
    for (int i = 0; i < 16; i++) {
        vec3 sp = P + TBN * (KERNEL[i] * u_radius);
        vec4 clip = u_proj * u_view * vec4(sp, 1.0);
        if (clip.w <= 0.0) continue;
        vec2 suv = clip.xy / clip.w * 0.5 + 0.5;
        if (suv.x < 0.0 || suv.x > 1.0 || suv.y < 0.0 || suv.y > 1.0) {
            continue;
        }
        vec4 smp = texture(u_pos, suv);
        if (length(smp.xyz) < 1e-4) continue;
        float sz = viewDepth(smp.xyz);
        float sz2 = viewDepth(sp);
        float rangeCheck = smoothstep(0.0, 1.0, u_radius / max(abs(pz - sz), 1e-4));
        occl += (sz <= sz2 - u_bias) ? rangeCheck : 0.0;
    }
    float ao = clamp(1.0 - occl / 16.0 * u_intensity, 0.0, 1.0);
    fragColor = vec4(ao, ao, ao, 1.0);
}
"""

_BRIGHT_FRAG = """
#version 330 core
in vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_threshold;
uniform float u_knee;
out vec4 fragColor;
void main() {
    vec3 c = texture(u_tex, v_uv).rgb;
    float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
    float k = clamp((l - u_threshold) / max(u_knee, 1e-3), 0.0, 1.0);
    fragColor = vec4(c * k * k, 1.0);
}
"""

_BLUR_FRAG = """
#version 330 core
in vec2 v_uv;
uniform sampler2D u_tex;
uniform vec2 u_dir;      // desplazamiento por muestra (en uv)
out vec4 fragColor;
void main() {
    float w[5] = float[5](0.227027, 0.194594, 0.121621, 0.054054, 0.016216);
    vec3 sum = texture(u_tex, v_uv).rgb * w[0];
    for (int i = 1; i < 5; i++) {
        sum += texture(u_tex, v_uv + u_dir * float(i)).rgb * w[i];
        sum += texture(u_tex, v_uv - u_dir * float(i)).rgb * w[i];
    }
    fragColor = vec4(sum, 1.0);
}
"""

_COMPOSITE_FRAG = """
#version 330 core
in vec2 v_uv;
uniform sampler2D u_color;
uniform sampler2D u_ao;
uniform sampler2D u_bloom;
uniform sampler2D u_pos;
uniform sampler2D u_mat;
uniform vec3 u_cam;
uniform mat4 u_view;
uniform mat4 u_proj;
uniform float u_exposure;
uniform float u_ao_strength;
uniform float u_bloom_amount;
uniform float u_env;
uniform int u_ssr_enable;
uniform float u_ssr_strength;
uniform float u_vignette;
out vec4 fragColor;

vec3 aces(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

void main() {
    vec3 col = texture(u_color, v_uv).rgb;

    // Sombra ambiental.
    float ao = texture(u_ao, v_uv).r;
    col *= mix(1.0, ao, u_ao_strength);

    // Reflejos en pantalla (SSR) sobre lo glossy.
    if (u_ssr_enable == 1) {
        vec3 N = texture(u_mat, v_uv).xyz;
        float rough = texture(u_mat, v_uv).a;
        vec3 P = texture(u_pos, v_uv).xyz;
        if (length(N) > 0.2 && rough < 0.45 && length(P) > 1e-4) {
            N = normalize(N);
            vec3 V = normalize(u_cam - P);
            float ndv = max(dot(N, V), 1e-3);
            vec3 R = reflect(-V, N);
            vec3 ray = P + N * 0.03;
            float step = max(0.05, length(P - u_cam) * 0.01);
            float conf = 0.0;
            vec3 hitc = vec3(0.0);
            vec2 lastUv = v_uv;
            for (int i = 0; i < 28; i++) {
                ray += R * step;
                vec4 clip = u_proj * u_view * vec4(ray, 1.0);
                if (clip.w <= 0.0) break;
                vec2 suv = clip.xy / clip.w * 0.5 + 0.5;
                if (suv.x < 0.0 || suv.x > 1.0 || suv.y < 0.0 || suv.y > 1.0) {
                    break;
                }
                lastUv = suv;
                vec3 swp = texture(u_pos, suv).xyz;
                if (length(swp) > 1e-4) {
                    float d = length(swp - ray);
                    if (d < 0.10 * (1.0 + float(i) * 0.12)) {
                        conf = 1.0 - float(i) / 28.0;
                        hitc = texture(u_color, suv).rgb;
                        break;
                    }
                }
                step *= 1.08;
            }
            // Fundido cerca de los bordes (la info de pantalla se acaba ahí).
            float edge = smoothstep(0.0, 0.08, lastUv.x)
                       * smoothstep(1.0, 0.92, lastUv.x)
                       * smoothstep(0.0, 0.08, lastUv.y)
                       * smoothstep(1.0, 0.92, lastUv.y);
            conf *= edge;
            // Sólo se aplica donde hay impacto real: el reflejo del cielo ya
            // lo puso el shader principal, así no se cuenta dos veces.
            float fres = pow(1.0 - ndv, 4.0) * 0.8 + 0.2;
            float gloss = 1.0 - rough;
            float w = clamp(u_ssr_strength * gloss * fres * u_env * 2.0,
                            0.0, 1.0);
            col = mix(col, hitc, w * conf);
        }
    }

    // Bloom.
    col += texture(u_bloom, v_uv).rgb * u_bloom_amount;

    // Exposición + tonemap + gamma.
    col *= u_exposure;
    col = aces(col);
    col = pow(col, vec3(1.0 / 2.2));

    // Viñeta suave.
    vec2 q = v_uv - 0.5;
    float vig = 1.0 - u_vignette * dot(q, q) * 2.0;
    col *= clamp(vig, 0.0, 1.0);

    fragColor = vec4(col, 1.0);
}
"""


# ---------------------------------------------------------------------------
# Extracción de geometría del documento
# ---------------------------------------------------------------------------
class _DrawRun:
    __slots__ = ("texture", "rough", "metal", "trans", "opacity",
                 "bump", "first", "count")

    def __init__(self, texture, rough, metal, trans, opacity, bump=0.0):
        self.texture = texture
        self.rough = rough
        self.metal = metal
        self.trans = trans
        self.opacity = opacity
        self.bump = bump
        self.first = 0
        self.count = 0


class WorldGeometry:
    """Triángulos del modelo visibles, listos para subir a la GPU.

    ``data`` es un ``bytes`` interleaved (ver ``V_*``) y ``runs`` agrupa los
    triángulos por material para dibujarlos con pocas llamadas. ``bounds`` da
    el volumen del modelo (para encuadre y encuadre de la luz) y ``faces``
    cuántas caras entraron.
    """

    def __init__(self):
        self.data = b""
        self.runs: list[_DrawRun] = []
        self.bounds = None          # (lo, hi) QVector3D
        self.faces = 0
        self.tris = 0
        #: Caras marcadas como césped: (lista de triángulos en mundo, ruta de
        #: textura, color de cara). Se usan para sembrar las hojas 3D.
        self.grass_faces: list = []

    @property
    def empty(self) -> bool:
        return not self.runs


def _norm_words(text: str) -> str:
    """Minúsculas y sin acentos, para comparar con las palabras clave."""
    text = str(text).lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"),
                 ("ú", "u"), ("ü", "u"), ("ñ", "n"), ("à", "a"),
                 ("è", "e"), ("ì", "i"), ("ò", "o"), ("ù", "u")):
        text = text.replace(a, b)
    return text


def _haystack(path=None, matname=None) -> str:
    """Texto de búsqueda (ruta + material) sin acentos ni separadores."""
    hay = ""
    if path:
        hay += " " + _norm_words(str(path).replace("\\", "/"))
    if matname:
        hay += " " + _norm_words(matname)
    return hay


def _is_grass(path=None, matname=None) -> bool:
    """¿La textura (por nombre de archivo y carpeta) o el material es césped?"""
    return any(w in _haystack(path, matname) for w in _GRASS_WORDS)


def _is_glossy(path=None, matname=None) -> bool:
    """¿Es un piso/material que debería verse pulido (glossy)?"""
    return any(w in _haystack(path, matname) for w in _GLOSS_WORDS)


def _is_stone(path=None, matname=None) -> bool:
    """¿Es un material con relieve (piedra, ladrillo, adoquín…)?

    Las superficies de césped no cuentan: su detalle lo da el césped 3D."""
    hay = _haystack(path, matname)
    if any(w in hay for w in _GRASS_WORDS):
        return False
    return any(w in hay for w in _STONE_WORDS)


def _average_color(path):
    """Color medio (0..1) de una textura, para teñir las hojas. Cacheado."""
    if path in _AVG_COLOR_CACHE:
        return _AVG_COLOR_CACHE[path]
    col = None
    try:
        from PySide6.QtGui import QImage
        img = QImage(path)
        if not img.isNull():
            img = img.scaled(24, 24).convertToFormat(QImage.Format_RGB32)
            r = g = b = n = 0
            for y in range(img.height()):
                for x in range(img.width()):
                    c = img.pixel(x, y)
                    r += (c >> 16) & 255
                    g += (c >> 8) & 255
                    b += c & 255
                    n += 1
            if n:
                col = (r / n / 255.0, g / n / 255.0, b / n / 255.0)
    except Exception:  # noqa: BLE001
        col = None
    _AVG_COLOR_CACHE[path] = col
    return col


def _build_normal_map(path, size: int = 256):
    """Genera un normal-map (relieve) a partir de la luminancia de la textura.

    Se calcula una vez por textura y se cachea. El resultado es una ``QImage``
    RGBA8888 con la normal en (x, y, 1) mapeada a 0..255, o ``None`` si la
    textura no se puede leer. Así las piedras/ladrillos ganan volumen sin que
    el modelo tenga un mapa de normales propio."""
    if path in _NORMAL_CACHE:
        return _NORMAL_CACHE[path]
    out = None
    try:
        from PySide6.QtGui import QImage
        img = QImage(path)
        if not img.isNull():
            img = img.scaled(size, size, Qt.IgnoreAspectRatio,
                             Qt.SmoothTransformation)
            img = img.convertToFormat(QImage.Format_RGB32)
            w, h = img.width(), img.height()
            # Luminancia en una matriz de float.
            lum = [[0.0] * w for _ in range(h)]
            for y in range(h):
                row = lum[y]
                for x in range(w):
                    c = img.pixel(x, y)
                    row[x] = (((c >> 16) & 255) * 0.299
                              + ((c >> 8) & 255) * 0.587
                              + (c & 255) * 0.114) / 255.0
            nm = QImage(w, h, QImage.Format_RGBA8888)
            for y in range(h):
                ym = (y - 1) % h
                yp = (y + 1) % h
                for x in range(w):
                    xm = (x - 1) % w
                    xp = (x + 1) % w
                    # Sobel.
                    gx = (lum[ym][xm] + 2.0 * lum[y][xm] + lum[yp][xm]) \
                        - (lum[ym][xp] + 2.0 * lum[y][xp] + lum[yp][xp])
                    gy = (lum[ym][xm] + 2.0 * lum[ym][x] + lum[ym][xp]) \
                        - (lum[yp][xm] + 2.0 * lum[yp][x] + lum[yp][xp])
                    # Normal = normalize(-gx * k, -gy * k, 1).
                    k = 2.5
                    nx, ny, nz = -gx * k, -gy * k, 1.0
                    ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
                    nx, ny, nz = nx / ln, ny / ln, nz / ln
                    r = int((nx * 0.5 + 0.5) * 255.0 + 0.5)
                    g = int((ny * 0.5 + 0.5) * 255.0 + 0.5)
                    b = int((nz * 0.5 + 0.5) * 255.0 + 0.5)
                    nm.setPixel(x, y, (0xFF << 24) | (r << 16) | (g << 8) | b)
            out = nm
    except Exception:  # noqa: BLE001
        out = None
    _NORMAL_CACHE[path] = out
    return out


def _emit_blade(buf, root: QVector3D, nrm: QVector3D, tint, rng) -> None:
    """Añade una hoja de césped (5 vértices → 3 triángulos) al buffer.

    Se hace todo con ``float`` sueltos (sin objetos ``QVector3D`` ni closures)
    porque esto corre cientos de miles de veces y el coste se nota."""
    ux, uy, uz = nrm.x(), nrm.y(), nrm.z()
    ul = math.sqrt(ux * ux + uy * uy + uz * uz)
    if ul < 1e-9:
        ux, uy, uz = 0.0, 0.0, 1.0
    else:
        ux, uy, uz = ux / ul, uy / ul, uz / ul

    if abs(uz) < 0.9:
        rx, ry, rz = 0.0, 0.0, 1.0
    else:
        rx, ry, rz = 1.0, 0.0, 0.0

    # side0 = ref × up
    sx = ry * uz - rz * uy
    sy = rz * ux - rx * uz
    sz = rx * uy - ry * ux
    sl = math.sqrt(sx * sx + sy * sy + sz * sz)
    if sl < 1e-9:
        sx, sy, sz = 1.0, 0.0, 0.0
    else:
        sx, sy, sz = sx / sl, sy / sl, sz / sl
    # fwd0 = up × side0
    fx = uy * sz - uz * sy
    fy = uz * sx - ux * sz
    fz = ux * sy - uy * sx

    ang = rng.random() * 6.2831853
    ca = math.cos(ang)
    sa = math.sin(ang)
    # side y fwd son side0/fwd0 girados en su plano → siguen siendo unitarios
    wx = sx * ca + fx * sa
    wy = sy * ca + fy * sa
    wz = sz * ca + fz * sa
    dx = fx * ca - sx * sa
    dy = fy * ca - sy * sa
    dz = fz * ca - sz * sa

    h = GRASS_HEIGHT * (0.6 + 0.8 * rng.random())
    wid = GRASS_WIDTH * (0.7 + 0.6 * rng.random())
    lean = h * (0.15 + 0.55 * rng.random())

    bx = root.x() + ux * 0.004        # separación del suelo (evita z-fight)
    by = root.y() + uy * 0.004
    bz = root.z() + uz * 0.004

    # face_n = fwd × up ; n_v = normalize(face_n*0.6 + up*0.5)
    fnx = dy * uz - dz * uy
    fny = dz * ux - dx * uz
    fnz = dx * uy - dy * ux
    nvx = fnx * 0.6 + ux * 0.5
    nvy = fny * 0.6 + uy * 0.5
    nvz = fnz * 0.6 + uz * 0.5
    nl = math.sqrt(nvx * nvx + nvy * nvy + nvz * nvz)
    if nl > 1e-9:
        nvx, nvy, nvz = nvx / nl, nvy / nl, nvz / nl

    shade = 0.80 + 0.40 * rng.random()
    t0, t1, t2 = tint
    c0r, c0g, c0b = t0 * 0.50 * shade, t1 * 0.52 * shade, t2 * 0.50 * shade
    c1r, c1g, c1b = t0 * 0.90 * shade, t1 * 0.92 * shade, t2 * 0.85 * shade
    c2r = min(1.0, t0 * 1.12) * shade
    c2g = min(1.0, t1 * 1.16) * shade
    c2b = min(1.0, t2 * 0.95) * shade
    flex = 0.60 + 0.50 * rng.random()
    phase = rng.random() * 6.2831853 + (root.x() + root.y()) * 0.6

    hl = wid * 0.5
    blx, bly, blz = bx - wx * hl, by - wy * hl, bz - wz * hl
    brx, bry, brz = bx + wx * hl, by + wy * hl, bz + wz * hl
    mh = h * 0.55
    ml = lean * 0.30
    mwx, mwy, mwz = bx + ux * mh + dx * ml, by + uy * mh + dy * ml, bz + uz * mh + dz * ml
    m2 = wid * 0.30
    mlx, mly, mlz = mwx - wx * m2, mwy - wy * m2, mwz - wz * m2
    mrx, mry, mrz = mwx + wx * m2, mwy + wy * m2, mwz + wz * m2
    tipx, tipy, tipz = bx + ux * h + dx * lean, by + uy * h + dy * lean, bz + uz * h + dz * lean

    nx, ny, nz = nvx, nvy, nvz
    buf.extend((
        blx, bly, blz, nx, ny, nz, c0r, c0g, c0b, 0.0, phase, flex, h,
        brx, bry, brz, nx, ny, nz, c0r, c0g, c0b, 0.0, phase, flex, h,
        mrx, mry, mrz, nx, ny, nz, c1r, c1g, c1b, 0.55, phase, flex, h,
        blx, bly, blz, nx, ny, nz, c0r, c0g, c0b, 0.0, phase, flex, h,
        mrx, mry, mrz, nx, ny, nz, c1r, c1g, c1b, 0.55, phase, flex, h,
        mlx, mly, mlz, nx, ny, nz, c1r, c1g, c1b, 0.55, phase, flex, h,
        mlx, mly, mlz, nx, ny, nz, c1r, c1g, c1b, 0.55, phase, flex, h,
        mrx, mry, mrz, nx, ny, nz, c1r, c1g, c1b, 0.55, phase, flex, h,
        tipx, tipy, tipz, nx, ny, nz, c2r, c2g, c2b, 1.0, phase, flex, h,
    ))


def build_grass_geometry(faces, density: float, max_blades: int,
                         seed: int = 12345):
    """Siembra hojas 3D sobre las caras de césped.

    ``faces`` es la lista que arma ``build_world_geometry`` (triángulos en
    mundo + textura + color). Devuelve ``(bytes, vcount)`` listos para subir
    a la GPU. Determinista por ``seed`` para que el césped no cambie de sitio
    al reconstruir la escena por cambios ajenos."""
    data = array("f")
    if not faces or density <= 0.0:
        return data, 0

    # Reúne todos los triángulos con su color de hoja y calcula áreas.
    tris = []          # (p0, p1, p2, tint)
    cum = []
    total = 0.0
    for tlist, texpath, facecol in faces:
        tint = _average_color(texpath) if texpath else None
        if tint is None:
            tint = _GRASS_FALLBACK
        if facecol is not None:
            tint = (tint[0] * facecol[0], tint[1] * facecol[1],
                    tint[2] * facecol[2])
        for tri in tlist:
            p0, p1, p2 = tri
            area = 0.5 * QVector3D.crossProduct(p1 - p0, p2 - p0).length()
            if area <= 1e-9:
                continue
            total += area
            tris.append((p0, p1, p2, tint))
            cum.append(total)
    if total <= 0.0 or not tris:
        return data, 0

    n_blades = int(min(float(max_blades), density * total))
    if n_blades <= 0:
        return data, 0

    rng = random.Random(seed)
    last = len(tris) - 1
    for _ in range(n_blades):
        r = rng.random() * total
        j = bisect_left(cum, r)
        if j < 0:
            j = 0
        elif j > last:
            j = last
        p0, p1, p2, tint = tris[j]
        u = rng.random()
        v = rng.random()
        if u + v > 1.0:
            u = 1.0 - u
            v = 1.0 - v
        root = p0 + (p1 - p0) * u + (p2 - p0) * v
        n = QVector3D.crossProduct(p1 - p0, p2 - p0)
        ln = n.length()
        n = (n / ln) if ln > 1e-9 else QVector3D(0.0, 0.0, 1.0)
        _emit_blade(data, root, n, tint, rng)

    vcount = len(data) // (G_STRIDE // 4)
    return data, vcount


def build_world_geometry(scene) -> WorldGeometry:
    """Lee ``scene`` y construye la malla iluminable (ViReal) sin tocar la GPU."""
    geo = WorldGeometry()
    try:
        from core.group import effective_material
        from core.materials import effective_attrs
        from core.texture import face_uv_axes
        from core.finish import PBR, resolve as resolve_finish
    except Exception:  # noqa: BLE001
        log.exception("ViReal: no pude importar el modelo de materiales")
        return geo

    materials = getattr(scene, "materials", None) or {}
    ctx_mat = None
    try:
        ctx_mat = effective_material(scene.edit_group)
    except Exception:  # noqa: BLE001
        ctx_mat = None

    lo = hi = None

    def _grow(p: QVector3D) -> None:
        nonlocal lo, hi
        if lo is None:
            lo = QVector3D(p)
            hi = QVector3D(p)
            return
        lo.setX(min(lo.x(), p.x())); hi.setX(max(hi.x(), p.x()))
        lo.setY(min(lo.y(), p.y())); hi.setY(max(hi.y(), p.y()))
        lo.setZ(min(lo.z(), p.z())); hi.setZ(max(hi.z(), p.z()))

    # buf acumula los flotantes; runs_buf agrupa por material.
    buf = array("f")
    order: list[tuple] = []
    by_key: dict[tuple, array] = {}

    def _bucket(key, texture):
        bucket = by_key.get(key)
        if bucket is None:
            bucket = by_key[key] = array("f")
            order.append((key, texture, bucket))
        return bucket

    try:
        world_faces = list(scene.iter_world_faces())
    except Exception:  # noqa: BLE001
        log.exception("ViReal: fallo iterando las caras del modelo")
        world_faces = []

    count = 0
    for face, matrix in world_faces:
        try:
            attrs = effective_attrs(face.attrs, ctx_mat)
            n_local = face.normal()
            if matrix is not None:
                n = matrix.mapVector(n_local)
                nl = n.length()
                n = (n / nl) if nl > 1e-9 else n_local
            else:
                n = n_local
            tris = face.triangulate(n_local)
            if not tris:
                continue
            tex = attrs.get("texture")
            texpath = tex.get("path") if (tex and tex.get("path")) else None
            if texpath:
                gu, cu, gv, cv = face_uv_axes(tex, n)
                col = attrs.get("color")
                color = (float(col[0]), float(col[1]), float(col[2])) \
                    if col is not None else (1.0, 1.0, 1.0)
            else:
                gu = cu = gv = cv = None
                col = attrs.get("color")
                color = (float(col[0]), float(col[1]), float(col[2])) \
                    if col is not None else DEFAULT_FACE_COLOR
            opacity = float(attrs.get("opacity", 1.0) or 1.0)
            matname = attrs.get("mat")
            chosen = None
            entry = materials.get(matname) if matname else None
            if entry is not None:
                chosen = getattr(entry, "finish", None)
            finish = resolve_finish(chosen, matname, texpath, opacity)
            rough, metal, trans, _ior = PBR.get(finish, (0.9, 0.0, 0.0, 1.5))
            # Pisos pulidos: baja la rugosidad para que reflejen como glossy.
            if _is_glossy(texpath, matname):
                rough = min(rough, 0.12)
                metal = max(metal, 0.10)
            # Relieve: fuerza del normal-map derivado de la textura.
            if _is_stone(texpath, matname):
                bump = 0.9
            elif texpath:
                bump = 0.30
            else:
                bump = 0.0
            grass = _is_grass(texpath, matname)
            if grass:
                bump = 0.0        # el césped 3D ya da el detalle
            key = (texpath, round(rough, 3), round(metal, 3),
                   round(trans, 3), round(opacity, 3), round(bump, 2))
            bucket = _bucket(key, texpath)
            glist = [] if grass else None
        except Exception:  # noqa: BLE001 — una cara mala no tira la escena
            continue

        for tri in tris:
            wps = [] if glist is not None else None
            for p in tri:
                wp = matrix.map(p) if matrix is not None else p
                if wps is not None:
                    wps.append(wp)
                _grow(wp)
                if gu is not None:
                    u = QVector3D.dotProduct(gu, wp) + cu
                    v = QVector3D.dotProduct(gv, wp) + cv
                else:
                    u = v = 0.0
                bucket.extend((wp.x(), wp.y(), wp.z(),
                               n.x(), n.y(), n.z(),
                               u, v,
                               color[0], color[1], color[2],
                               rough, metal, trans))
            if wps is not None and len(wps) >= 3:
                glist.append((wps[0], wps[1], wps[2]))
        if glist:
            geo.grass_faces.append((glist, texpath, color))
        count += 1

    geo.faces = count
    geo.bounds = (lo, hi) if lo is not None else None

    runs = []
    first = 0
    for key, texture, bucket in order:
        if not bucket:
            continue
        buf.extend(bucket)
        n_floats = len(bucket)
        run = _DrawRun(texture, key[1], key[2], key[3], key[4], key[5])
        run.first = first
        run.count = n_floats // (V_STRIDE // 4)
        first += run.count
        runs.append(run)
    geo.data = buf.tobytes()
    geo.tris = first
    geo.runs = runs
    return geo


# ---------------------------------------------------------------------------
# Cámara libre (vuelo tipo juego)
# ---------------------------------------------------------------------------
class FlyCamera:
    def __init__(self) -> None:
        self.pos = QVector3D(0.0, -5.0, 2.0)
        self.yaw = math.radians(90.0)     # 0 = +Y, sentido antihorario
        self.pitch = math.radians(-8.0)
        self.speed = 3.0                  # m/s
        self.fov = 62.0
        self.near = 0.05
        self.far = 4000.0

    def forward(self) -> QVector3D:
        cp = math.cos(self.pitch)
        return QVector3D(cp * math.cos(self.yaw),
                         cp * math.sin(self.yaw),
                         math.sin(self.pitch))

    def right(self) -> QVector3D:
        f = self.forward()
        r = QVector3D.crossProduct(f, QVector3D(0.0, 0.0, 1.0))
        ln = r.length()
        return r / ln if ln > 1e-9 else QVector3D(1.0, 0.0, 0.0)

    def up(self) -> QVector3D:
        return QVector3D.crossProduct(self.right(), self.forward())

    def view_matrix(self) -> QMatrix4x4:
        m = QMatrix4x4()
        m.lookAt(self.pos, self.pos + self.forward(), self.up())
        return m

    def projection_matrix(self, aspect: float) -> QMatrix4x4:
        m = QMatrix4x4()
        m.perspective(self.fov, max(aspect, 1e-3), self.near, self.far)
        return m

    def set_from_vectors(self, pos: QVector3D, direction: QVector3D) -> None:
        self.pos = QVector3D(pos)
        d = QVector3D(direction)
        if d.lengthSquared() < 1e-9:
            d = QVector3D(0.0, 1.0, 0.0)
        d = d.normalized()
        self.yaw = math.atan2(d.y(), d.x())
        self.pitch = math.asin(max(-1.0, min(1.0, d.z())))


# ---------------------------------------------------------------------------
# Vista OpenGL
# ---------------------------------------------------------------------------
class WorldView(QOpenGLWidget):
    """Contexto OpenGL propio: cielo, geometría PBR y sombras del sol."""

    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self.app = app
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        fmt.setDepthBufferSize(24)
        fmt.setStencilBufferSize(8)
        fmt.setSamples(4)
        self.setFormat(fmt)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.camera = FlyCamera()
        self._gl = None
        self._mrt = False
        self._prog = None
        self._sky = None
        self._depth = None
        self._gprog = None
        self._ssao = None
        self._bright = None
        self._blur = None
        self._comp = None
        self._vao = None
        self._empty_vao = None
        self._vbo = None
        self._gvao = None
        self._gvbo = None
        self._shadow_fbo = None
        self._scene_fbo = None
        self._ao_fbo = None
        self._bloom_a = None
        self._bloom_b = None
        self._white_tex = None
        self._black_tex = None
        self._fb_size = None
        self._textures: dict = {}
        self._normals: dict = {}
        self._lm: dict = {}
        self._ls: dict = {}
        self._ld: dict = {}
        self._lg: dict = {}
        self._lo: dict = {}
        self._lb: dict = {}
        self._lc: dict = {}
        self._blocs: dict = {}
        self._runs: list[_DrawRun] = []
        self._vcount = 0
        self._gcount = 0
        self._time = 0.0
        self._built_version = None
        self._dirty = True
        self._want_frame = True
        self._bounds = None
        self._light_vp = QMatrix4x4()
        self._sun_dir = QVector3D(0.35, 0.45, 0.82)
        self._scene_radius = 10.0

        self.shadows = True
        self.ambient = 0.55
        self.exposure = 1.05
        self.shadow_strength = 0.62
        self.grass = True
        self.wind = True
        self.grass_density = 45.0
        self.env = 0.40           # intensidad de reflejos de entorno
        self.floor_gloss = 0.35   # pisos hacia arriba pulidos
        self.bump = 1.0           # multiplicador global de relieve
        self.ssr = True           # reflejos en pantalla
        self.ssao = True          # oclusión ambiental
        self.bloom = True         # resplandor solar
        self.bloom_amount = 0.55
        self.ao_strength = 0.55
        self.sky_preset = 0
        self.vignette = 0.30
        self._shadow_dirty = True

        self._keys: set = set()
        self._looking = False
        self._last_mouse = QPoint()
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_t = 0.0
        self._stats = ""
        self._error = ""

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---- ciclo ------------------------------------------------------------
    def mark_dirty(self) -> None:
        self._dirty = True

    def _tick(self) -> None:
        if not self.isVisible():
            return
        self._time = self._clock.elapsed() / 1000.0
        moved = bool(self._keys) or self._looking
        if moved:
            self._apply_movement()
        animate = self.wind and self._gcount > 0
        if moved or self._dirty or animate:
            self.update()

    def _apply_movement(self) -> None:
        now = self._clock.elapsed() / 1000.0
        dt = min(0.1, max(0.0, now - self._last_t))
        self._last_t = now
        if dt <= 0.0:
            return
        cam = self.camera
        sprint = 3.2 if (self._mods & Qt.ShiftModifier) else 1.0
        step = cam.speed * dt * sprint
        f = cam.forward()
        r = cam.right()
        if Qt.Key_W in self._keys:
            cam.pos = cam.pos + f * step
        if Qt.Key_S in self._keys:
            cam.pos = cam.pos - f * step
        if Qt.Key_D in self._keys:
            cam.pos = cam.pos + r * step
        if Qt.Key_A in self._keys:
            cam.pos = cam.pos - r * step
        if Qt.Key_E in self._keys:
            cam.pos = cam.pos + QVector3D(0.0, 0.0, step)
        if Qt.Key_Q in self._keys:
            cam.pos = cam.pos - QVector3D(0.0, 0.0, step)

    _mods = Qt.NoModifier

    # ---- GL ---------------------------------------------------------------
    def initializeGL(self) -> None:
        self._gl = None
        self._mrt = False
        try:
            prof = QOpenGLVersionProfile()
            prof.setVersion(3, 3)
            prof.setProfile(QSurfaceFormat.CoreProfile)
            gl = QOpenGLVersionFunctionsFactory.get(prof, self.context())
            if gl is not None:
                gl.initializeOpenGLFunctions()
                self._gl = gl
                self._mrt = hasattr(gl, "glDrawBuffers")
        except Exception:  # noqa: BLE001
            log.exception("ViReal: no pude obtener las funciones GL 3.3")
        if self._gl is None:
            self._gl = QOpenGLFunctions(self.context())
            self._gl.initializeOpenGLFunctions()
        self._prog = self._compile(_VERT, _FRAG, "principal")
        self._sky = self._compile(_SKY_VERT, _SKY_FRAG, "cielo")
        self._depth = self._compile(_DEPTH_VERT, _DEPTH_FRAG, "sombras")
        self._gprog = self._compile(_GRASS_VERT, _GRASS_FRAG, "césped")
        self._ssao = self._compile(_FS_VERT, _SSAO_FRAG, "ssao")
        self._bright = self._compile(_FS_VERT, _BRIGHT_FRAG, "bloom-brillo")
        self._blur = self._compile(_FS_VERT, _BLUR_FRAG, "bloom-desenfoque")
        self._comp = self._compile(_FS_VERT, _COMPOSITE_FRAG, "compuesto")
        self._lm = self._locs_for(self._prog, _MAIN_UNIFORMS) if self._prog else {}
        self._ls = self._locs_for(self._sky, _SKY_UNIFORMS) if self._sky else {}
        self._ld = self._locs_for(self._depth, _DEPTH_UNIFORMS) if self._depth else {}
        self._lg = self._locs_for(self._gprog, _GRASS_UNIFORMS) if self._gprog else {}
        self._lo = self._locs_for(self._ssao, _SSAO_UNIFORMS) if self._ssao else {}
        self._lb = self._locs_for(self._bright, _BRIGHT_UNIFORMS) if self._bright else {}
        self._lc = self._locs_for(self._comp, _COMPOSITE_UNIFORMS) if self._comp else {}
        self._blocs = self._locs_for(self._blur, _BLUR_UNIFORMS) \
            if self._blur else {}
        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._empty_vao = QOpenGLVertexArrayObject(self)
        self._empty_vao.create()
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._vbo.create()
        self._gvao = QOpenGLVertexArrayObject(self)
        self._gvao.create()
        self._gvbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._gvbo.create()
        self._gl.glEnable(GL_DEPTH_TEST)
        self._gl.glDepthFunc(GL_LEQUAL)
        self._gl.glEnable(GL_MULTISAMPLE)
        self._set_camera_from_core()
        self._dirty = True

    def _compile(self, vs: str, fs: str, name: str):
        prog = QOpenGLShaderProgram(self)
        ok_v = prog.addShaderFromSourceCode(QOpenGLShader.Vertex, vs)
        ok_f = prog.addShaderFromSourceCode(QOpenGLShader.Fragment, fs)
        if not (ok_v and ok_f) or not prog.link():
            log.error("ViReal: shader %s no compila:\n%s", name, prog.log())
            self._error = f"shader {name}: {prog.log().strip()[:400]}"
            return None
        return prog

    @staticmethod
    def _locs_for(prog, names) -> dict:
        """Localizaciones de uniformes (PySide6 exige ``bytes`` y usar la
        localización para float/int)."""
        out = {}
        for n in names:
            out[n] = prog.uniformLocation(n.encode())
        return out

    @staticmethod
    def _sv(prog, locs: dict, name: str, value) -> None:
        """Sube un uniforme usando su localización.

        Ojo: en este PySide6 la sobrecarga ``setUniformValue(loc, valor)``
        solo funciona con tipos vector/matriz; los escalares *float*/*int*
        hay que enviarlos con ``setUniformValue1f`` / ``setUniformValue1i``
        (si no, se ignoran en silencio y el uniforme queda a 0)."""
        loc = locs.get(name)
        if loc is None or loc < 0:
            return
        if isinstance(value, bool):
            prog.setUniformValue1i(loc, 1 if value else 0)
        elif isinstance(value, int):
            prog.setUniformValue1i(loc, value)
        elif isinstance(value, float):
            prog.setUniformValue1f(loc, value)
        else:
            prog.setUniformValue(loc, value)

    def resizeGL(self, w: int, h: int) -> None:
        pass

    def _set_camera_from_core(self) -> None:
        try:
            core_cam = self.app.viewport.camera
            eye = core_cam.eye()
            fwd = core_cam.forward()
            self.camera.set_from_vectors(eye, fwd)
        except Exception:  # noqa: BLE001
            pass

    def frame_scene(self) -> None:
        """Coloca la cámara para ver todo el modelo de un vistazo."""
        if self._bounds is None:
            return
        lo, hi = self._bounds
        center = (lo + hi) * 0.5
        radius = max(1.0, (hi - lo).length() * 0.5)
        cam = self.camera
        cam.far = max(cam.far, radius * 20.0)
        cam.pos = center + QVector3D(1.0, -1.4, 0.9).normalized() * radius * 2.4
        direction = (center - cam.pos)
        cam.set_from_vectors(cam.pos, direction)
        cam.speed = max(2.0, radius * 0.35)
        self.update()

    # ---- construcción del buffer ------------------------------------------
    def _scene_version(self):
        try:
            return getattr(self.app.viewport.scene, "version", 0)
        except Exception:  # noqa: BLE001
            return 0

    def _rebuild(self) -> None:
        scene = getattr(self.app.viewport, "scene", None)
        if scene is None:
            return
        geo = build_world_geometry(scene)
        self._bounds = geo.bounds
        if geo.bounds is not None:
            self._scene_radius = max(2.0, (geo.bounds[1] - geo.bounds[0]).length() * 0.5)
        self._runs = geo.runs
        raw = geo.data
        self._vcount = geo.tris
        count = len(raw)
        self._vbo.bind()
        if count:
            try:
                self._vbo.allocate(raw, count)
            except TypeError:
                self._vbo.allocate(raw)
        else:
            try:
                self._vbo.allocate(4)
            except Exception:  # noqa: BLE001
                pass
        # atributos (el VAO recuerda el binding con este mismo buffer)
        self._vao.bind()
        if self._prog is not None:
            self._prog.bind()
            for loc, offset, size in ((0, V_POS, 3), (1, V_NRM, 3),
                                      (2, V_UV, 2), (3, V_COL, 3),
                                      (4, V_PBR, 3)):
                self._prog.enableAttributeArray(loc)
                self._prog.setAttributeBuffer(loc, GL_FLOAT, offset, size,
                                              V_STRIDE)
            self._prog.release()
        self._vao.release()
        self._vbo.release()
        self._upload_grass(geo)
        n_tex = len({r.texture for r in self._runs if r.texture})
        blades = self._gcount // 9
        extra = f" · {blades} hojas" if blades else ""
        self._stats = (f"{geo.faces} caras · {self._vcount // 3} triángulos · "
                       f"{len(self._runs)} materiales · {n_tex} texturas{extra}")
        self._built_version = self._scene_version()
        self._dirty = False
        self._update_light()
        if self._want_frame and self._bounds is not None:
            self._want_frame = False
            self.frame_scene()

    def _upload_grass(self, geo) -> None:
        """Siembra y sube el césped 3D (0 hojas si la densidad es 0).

        Se construye siempre (aunque el césped esté oculto) para que el
        interruptor sea instantáneo, sin rehacer toda la geometría."""
        density = max(0.0, float(self.grass_density))
        data, gcount = build_grass_geometry(geo.grass_faces, density,
                                            GRASS_MAX_BLADES)
        self._gcount = gcount
        blob = data.tobytes()
        self._gvbo.bind()
        if blob:
            try:
                self._gvbo.allocate(blob, len(blob))
            except TypeError:
                self._gvbo.allocate(blob)
        else:
            try:
                self._gvbo.allocate(4)
            except Exception:  # noqa: BLE001
                pass
        self._gvao.bind()
        if self._gprog is not None:
            self._gprog.bind()
            for loc, offset, size in ((0, G_POS, 3), (1, G_NRM, 3),
                                      (2, G_COL, 3), (3, G_DATA, 4)):
                self._gprog.enableAttributeArray(loc)
                self._gprog.setAttributeBuffer(loc, GL_FLOAT, offset, size,
                                               G_STRIDE)
            self._gprog.release()
        self._gvao.release()
        self._gvbo.release()

    def _update_light(self) -> None:
        scene = getattr(self.app.viewport, "scene", None)
        d = QVector3D(0.35, 0.45, 0.82)
        try:
            from core.sun import DEFAULT_LAT, DEFAULT_LON, sun_direction
            sh = getattr(scene, "shadows", None)
            datum = getattr(scene, "georef", None)
            lat = getattr(datum, "lat", None)
            lon = getattr(datum, "lon", None)
            if lat is None or lon is None:
                lat, lon = DEFAULT_LAT, DEFAULT_LON
            if sh is not None:
                vec = sun_direction(lat, lon, sh.when_utc(lon))
                if vec is not None:
                    d = QVector3D(vec[0], vec[1], vec[2])
                    north = getattr(datum, "north", 0.0)
                    if datum is not None and north:
                        # El sol llega en ejes de cuadrícula; el modelo puede
                        # estar girado por el ángulo norte del datum.
                        gx, gy = datum.grid_to_local_xy(d.x(), d.y())
                        d = QVector3D(gx, gy, d.z())
        except Exception:  # noqa: BLE001
            pass
        if d.lengthSquared() < 1e-9:
            d = QVector3D(0.35, 0.45, 0.82)
        self._sun_dir = d.normalized()

        # Matriz luz (ortográfica) ajustada al modelo y al suelo z=0 que
        # recibe. El ojo se sitúa EN LA DIRECCIÓN DEL SOL (center + d): con
        # el signo contrario la luz quedaría bajo tierra y el mapa solo
        # guardaría el suelo — sin sombras.
        if self._bounds is not None:
            lo, hi = self._bounds
            cx = (lo.x() + hi.x()) * 0.5
            cy = (lo.y() + hi.y()) * 0.5
            loz = min(lo.z(), 0.0)
            cz = (loz + hi.z()) * 0.5
            center = QVector3D(cx, cy, cz)
            r = max(hi.x() - lo.x(), hi.y() - lo.y(),
                    hi.z() - loz, 2.0) * 0.87
        else:
            center = QVector3D(0.0, 0.0, 0.0)
            r = 10.0
        r *= 1.25
        up = (QVector3D(0.0, 0.0, 1.0) if abs(self._sun_dir.z()) < 0.95
              else QVector3D(0.0, 1.0, 0.0))
        eye = center + self._sun_dir * (r * 2.0)
        view = QMatrix4x4()
        view.lookAt(eye, center, up)
        proj = QMatrix4x4()
        proj.ortho(-r, r, -r, r, r * 0.5, r * 3.5)
        self._light_vp = proj * view
        self._shadow_dirty = True

    def _ensure_shadow_fbo(self):
        if self._shadow_fbo is not None:
            return self._shadow_fbo
        try:
            fmt = QOpenGLFramebufferObjectFormat()
            fmt.setAttachment(QOpenGLFramebufferObject.NoAttachment)
            fmt.setInternalTextureFormat(GL_RGBA8)
            fbo = QOpenGLFramebufferObject(SHADOW_SIZE, SHADOW_SIZE, fmt)
            if not fbo.isValid():
                self._error = "FBO de sombras no válido"
                return None
            self._shadow_fbo = fbo
        except Exception as ex:  # noqa: BLE001
            log.exception("ViReal: no pude crear el FBO de sombras")
            self._error = f"sombras: {ex}"
            return None
        return self._shadow_fbo

    def _get_texture(self, path: str):
        tex = self._textures.get(path)
        if tex is not None:
            return tex
        from PySide6.QtGui import QImage
        img = QImage(path)
        if img.isNull():
            self._textures[path] = None
            return None
        tex = QOpenGLTexture(img.mirrored())
        tex.setWrapMode(QOpenGLTexture.Repeat)
        tex.setMinificationFilter(QOpenGLTexture.LinearMipMapLinear)
        tex.setMagnificationFilter(QOpenGLTexture.Linear)
        self._textures[path] = tex
        return tex

    def _get_normal(self, path: str):
        """Textura de relieve (normal-map) derivada de la textura difusa."""
        if path in self._normals:
            return self._normals[path]
        tex = None
        img = _build_normal_map(path)
        if img is not None:
            try:
                tex = QOpenGLTexture(img.mirrored())
                tex.setWrapMode(QOpenGLTexture.Repeat)
                tex.setMinificationFilter(QOpenGLTexture.LinearMipMapLinear)
                tex.setMagnificationFilter(QOpenGLTexture.Linear)
            except Exception:  # noqa: BLE001
                tex = None
        self._normals[path] = tex
        return tex

    def _solid_tex(self, value: int):
        """Textura 1×1 constante (blanca/negra) para pasar por sampler."""
        from PySide6.QtGui import QImage
        img = QImage(1, 1, QImage.Format_RGBA8888)
        img.fill(value)
        tex = QOpenGLTexture(img)
        tex.setMinificationFilter(QOpenGLTexture.Linear)
        tex.setMagnificationFilter(QOpenGLTexture.Linear)
        return tex

    def _white(self):
        if self._white_tex is None:
            self._white_tex = self._solid_tex(0xFFFFFFFF)
        return self._white_tex

    def _black(self):
        if self._black_tex is None:
            self._black_tex = self._solid_tex(0xFF000000)
        return self._black_tex

    # ---- infraestructura GL ----------------------------------------------
    def _dpr(self) -> float:
        try:
            return max(1.0, min(2.0, float(self.devicePixelRatioF())))
        except Exception:  # noqa: BLE001
            return 1.0

    @staticmethod
    def _invert(m: QMatrix4x4) -> QMatrix4x4:
        try:
            res = m.inverted()
            if isinstance(res, tuple):
                return res[0] if res[1] else QMatrix4x4()
            return res
        except Exception:  # noqa: BLE001
            return QMatrix4x4()

    def _draw_buffers(self, bufs) -> None:
        if self._mrt:
            try:
                self._gl.glDrawBuffers(len(bufs), list(bufs))
            except Exception:  # noqa: BLE001
                pass

    def _bind_tex(self, unit: int, gl_id: int) -> None:
        gl = self._gl
        gl.glActiveTexture(GL_TEXTURE0 + unit)
        gl.glBindTexture(GL_TEXTURE_2D, int(gl_id))
        gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        gl.glActiveTexture(GL_TEXTURE0)

    def _ensure_scene_fbo(self, w: int, h: int):
        if self._scene_fbo is not None and self._fb_size == (w, h):
            return self._scene_fbo
        self._delete_targets()
        try:
            fmt = QOpenGLFramebufferObjectFormat()
            fmt.setAttachment(QOpenGLFramebufferObject.Depth)
            fmt.setInternalTextureFormat(GL_RGBA16F)
            fbo = QOpenGLFramebufferObject(w, h, fmt)
            if not fbo.isValid():
                self._error = "FBO de escena no válido"
                return None
            if self._mrt:
                fbo.addColorAttachment(w, h)
                fbo.addColorAttachment(w, h)
            self._scene_fbo = fbo
            self._fb_size = (w, h)
        except Exception as ex:  # noqa: BLE001
            log.exception("ViReal: no pude crear el FBO de escena")
            self._error = f"escena: {ex}"
            self._scene_fbo = None
            return None
        # auxiliares (AO y bloom)
        try:
            afmt = QOpenGLFramebufferObjectFormat()
            afmt.setAttachment(QOpenGLFramebufferObject.NoAttachment)
            self._ao_fbo = QOpenGLFramebufferObject(w, h, afmt)
            hw, hh = max(1, w // 2), max(1, h // 2)
            bfmt = QOpenGLFramebufferObjectFormat()
            bfmt.setAttachment(QOpenGLFramebufferObject.NoAttachment)
            bfmt.setInternalTextureFormat(GL_RGBA16F)
            self._bloom_a = QOpenGLFramebufferObject(hw, hh, bfmt)
            self._bloom_b = QOpenGLFramebufferObject(hw, hh, bfmt)
        except Exception:  # noqa: BLE001
            log.exception("ViReal: no pude crear los FBO auxiliares")
        return self._scene_fbo

    def _delete_targets(self) -> None:
        for name in ("_scene_fbo", "_ao_fbo", "_bloom_a", "_bloom_b"):
            obj = getattr(self, name, None)
            if obj is not None:
                try:
                    del obj
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, name, None)
        self._fb_size = None

    # ---- pasadas de render -------------------------------------------------
    def _render_shadow(self) -> int:
        gl = self._gl
        if not (self.shadows and self._depth is not None and self._vcount):
            return 0
        fbo = self._ensure_shadow_fbo()
        if fbo is None:
            return 0
        if self._shadow_dirty:
            fbo.bind()
            gl.glViewport(0, 0, SHADOW_SIZE, SHADOW_SIZE)
            gl.glClearColor(1.0, 1.0, 1.0, 1.0)
            gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            self._depth.bind()
            self._sv(self._depth, self._ld, "u_light_vp", self._light_vp)
            self._vao.bind()
            gl.glDrawArrays(GL_TRIANGLES, 0, self._vcount)
            self._vao.release()
            self._depth.release()
            fbo.release()
            self._shadow_dirty = False
        return fbo.texture()

    # ---- pintado ----------------------------------------------------------
    def paintGL(self) -> None:
        if self._gl is None:
            return
        if self._dirty or self._scene_version() != self._built_version:
            self._rebuild()

        gl = self._gl
        dpr = self._dpr()
        w = max(1, int(self.width() * dpr))
        h = max(1, int(self.height() * dpr))
        aspect = w / h

        view = self.camera.view_matrix()
        proj = self.camera.projection_matrix(aspect)
        mvp = proj * view

        shadow_tex = self._render_shadow()

        fbo = self._ensure_scene_fbo(w, h)
        if fbo is None:
            return
        texs = fbo.textures()

        use_shadow = 1 if shadow_tex else 0

        # --- pasada de escena (HDR, MRT) ---
        fbo.bind()
        if self._mrt and len(texs) >= 3:
            self._draw_buffers((GL_COLOR_ATTACHMENT0, GL_COLOR_ATTACHMENT1,
                                GL_COLOR_ATTACHMENT2))
        gl.glViewport(0, 0, w, h)
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        self._draw_sky(mvp)
        self._draw_world(mvp, shadow_tex, use_shadow)
        self._draw_grass(mvp, shadow_tex, use_shadow)
        fbo.release()

        # --- SSAO ---
        ao_tex = self._render_ao(w, h, view, proj, texs)

        # --- bloom ---
        bloom_tex = self._render_bloom(texs)

        # --- composición final al widget ---
        self._composite(w, h, view, proj, texs, ao_tex, bloom_tex)

        self._draw_hud(w, h)

    def _draw_sky(self, mvp) -> None:
        if self._sky is None:
            return
        gl = self._gl
        self._sky.bind()
        self._sv(self._sky, self._ls, "u_inv_vp", self._invert(mvp))
        self._sv(self._sky, self._ls, "u_cam", self.camera.pos)
        self._set_atmo(self._sky, self._ls)
        self._empty_vao.bind()
        gl.glDisable(GL_DEPTH_TEST)
        gl.glDrawArrays(GL_TRIANGLES, 0, 3)
        gl.glEnable(GL_DEPTH_TEST)
        self._empty_vao.release()
        self._sky.release()

    def _draw_world(self, mvp, shadow_tex, use_shadow) -> None:
        if not self._runs or self._prog is None:
            return
        gl = self._gl
        self._prog.bind()
        self._sv(self._prog, self._lm, "u_mvp", mvp)
        self._sv(self._prog, self._lm, "u_cam", self.camera.pos)
        self._set_atmo(self._prog, self._lm)
        self._sv(self._prog, self._lm, "u_env", self.env)
        self._sv(self._prog, self._lm, "u_floor_gloss", self.floor_gloss)
        self._sv(self._prog, self._lm, "u_shadow_strength", self.shadow_strength)
        self._sv(self._prog, self._lm, "u_light_vp", self._light_vp)
        self._sv(self._prog, self._lm, "u_shadow_enable", use_shadow)
        self._sv(self._prog, self._lm, "u_shadow", 1)
        self._sv(self._prog, self._lm, "u_tex", 0)
        self._sv(self._prog, self._lm, "u_bump", 2)
        if shadow_tex:
            self._bind_tex(1, shadow_tex)

        self._vao.bind()
        for run in self._runs:
            self._sv(self._prog, self._lm, "u_color", QVector3D(1.0, 1.0, 1.0))
            self._sv(self._prog, self._lm, "u_opacity", run.opacity)
            if run.texture:
                tex = self._get_texture(run.texture)
                if tex is not None:
                    self._sv(self._prog, self._lm, "u_use_texture", 1)
                    tex.bind(0)
                else:
                    self._sv(self._prog, self._lm, "u_use_texture", 0)
            else:
                self._sv(self._prog, self._lm, "u_use_texture", 0)
            # relieve
            bstr = run.bump * self.bump
            if bstr > 0.001 and run.texture:
                nrm = self._get_normal(run.texture)
                if nrm is not None:
                    self._sv(self._prog, self._lm, "u_use_bump", 1)
                    self._sv(self._prog, self._lm, "u_bump_strength", bstr)
                    nrm.bind(2)
                else:
                    self._sv(self._prog, self._lm, "u_use_bump", 0)
            else:
                self._sv(self._prog, self._lm, "u_use_bump", 0)
            if run.opacity < 0.999:
                gl.glEnable(GL_BLEND)
                gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                gl.glDepthMask(False)
            gl.glDrawArrays(GL_TRIANGLES, run.first, run.count)
            if run.opacity < 0.999:
                gl.glDepthMask(True)
                gl.glDisable(GL_BLEND)
        self._vao.release()
        self._prog.release()

    def _draw_grass(self, mvp, shadow_tex, use_shadow) -> None:
        if not (self.grass and self._gcount and self._gprog is not None):
            return
        gl = self._gl
        gprog = self._gprog
        gprog.bind()
        self._sv(gprog, self._lg, "u_mvp", mvp)
        self._sv(gprog, self._lg, "u_cam", self.camera.pos)
        self._set_atmo(gprog, self._lg)
        self._sv(gprog, self._lg, "u_shadow_strength", self.shadow_strength)
        self._sv(gprog, self._lg, "u_light_vp", self._light_vp)
        self._sv(gprog, self._lg, "u_shadow_enable", use_shadow)
        self._sv(gprog, self._lg, "u_shadow", 1)
        self._sv(gprog, self._lg, "u_time",
                 self._time if self.wind else 0.0)
        wdx, wdy = WIND_DIR
        self._sv(gprog, self._lg, "u_wind_dir", QVector2D(wdx, wdy))
        self._sv(gprog, self._lg, "u_wind_strength",
                 WIND_STRENGTH if self.wind else 0.0)
        if shadow_tex:
            self._bind_tex(1, shadow_tex)
        self._gvao.bind()
        gl.glDrawArrays(GL_TRIANGLES, 0, self._gcount)
        self._gvao.release()
        gprog.release()

    def _render_ao(self, w, h, view, proj, texs):
        """Sombra ambiental (SSAO). Devuelve el id de textura con el AO."""
        gl = self._gl
        if not (self.ssao and self._mrt and self._ssao is not None
                and self._ao_fbo is not None and len(texs) >= 3):
            return 0
        ao = self._ao_fbo
        ao.bind()
        self._draw_buffers((GL_COLOR_ATTACHMENT0,))
        gl.glViewport(0, 0, w, h)
        gl.glDisable(GL_DEPTH_TEST)
        prog = self._ssao
        prog.bind()
        self._bind_tex(0, texs[2])   # posición mundo
        self._bind_tex(1, texs[1])   # normal
        self._sv(prog, self._lo, "u_pos", 0)
        self._sv(prog, self._lo, "u_mat", 1)
        self._sv(prog, self._lo, "u_view", view)
        self._sv(prog, self._lo, "u_proj", proj)
        self._sv(prog, self._lo, "u_texel", QVector2D(1.0 / w, 1.0 / h))
        self._sv(prog, self._lo, "u_radius",
                 max(0.08, self._scene_radius * 0.012))
        self._sv(prog, self._lo, "u_bias", 0.02)
        self._sv(prog, self._lo, "u_intensity", 1.2)
        self._empty_vao.bind()
        gl.glDrawArrays(GL_TRIANGLES, 0, 3)
        self._empty_vao.release()
        prog.release()
        gl.glEnable(GL_DEPTH_TEST)
        ao.release()
        return ao.texture()

    def _render_bloom(self, texs):
        gl = self._gl
        if not (self.bloom and self._bright is not None and self._blur is not None
                and self._bloom_a is not None and self._bloom_b is not None):
            return 0
        a, b = self._bloom_a, self._bloom_b
        bw, bh = a.width(), a.height()
        gl.glDisable(GL_DEPTH_TEST)
        self._empty_vao.bind()
        # brillo
        a.bind()
        self._draw_buffers((GL_COLOR_ATTACHMENT0,))
        gl.glViewport(0, 0, bw, bh)
        self._bright.bind()
        self._bind_tex(0, texs[0])
        self._sv(self._bright, self._lb, "u_tex", 0)
        self._sv(self._bright, self._lb, "u_threshold", 1.05)
        self._sv(self._bright, self._lb, "u_knee", 0.6)
        gl.glDrawArrays(GL_TRIANGLES, 0, 3)
        self._bright.release()
        # desenfoque horizontal y vertical (2 pasadas)
        for src, dst, direction in ((a, b, (1.0, 0.0)), (b, a, (0.0, 1.0)),
                                    (a, b, (1.0, 0.0)), (b, a, (0.0, 1.0))):
            dst.bind()
            self._draw_buffers((GL_COLOR_ATTACHMENT0,))
            gl.glViewport(0, 0, bw, bh)
            self._blur.bind()
            self._bind_tex(0, src.texture())
            self._sv(self._blur, self._blocs, "u_tex", 0)
            self._sv(self._blur, self._blocs, "u_dir",
                     QVector2D(direction[0] / bw, direction[1] / bh))
            self._sv(self._blur, self._blocs, "u_texel",
                     QVector2D(1.0 / bw, 1.0 / bh))
            gl.glDrawArrays(GL_TRIANGLES, 0, 3)
            self._blur.release()
        self._empty_vao.release()
        gl.glEnable(GL_DEPTH_TEST)
        return a.texture()

    def _composite(self, w, h, view, proj, texs, ao_tex, bloom_tex) -> None:
        gl = self._gl
        if self._comp is None:
            return
        gl.glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        gl.glDisable(GL_DEPTH_TEST)
        gl.glViewport(0, 0, w, h)
        prog = self._comp
        prog.bind()
        self._bind_tex(0, texs[0])
        self._bind_tex(1, ao_tex if ao_tex else self._white().textureId())
        self._bind_tex(2, bloom_tex if bloom_tex else self._black().textureId())
        if self._mrt and len(texs) >= 3:
            self._bind_tex(3, texs[2])
            self._bind_tex(4, texs[1])
        else:
            self._bind_tex(3, self._black().textureId())
            self._bind_tex(4, self._black().textureId())
        self._sv(prog, self._lc, "u_color", 0)
        self._sv(prog, self._lc, "u_ao", 1)
        self._sv(prog, self._lc, "u_bloom", 2)
        self._sv(prog, self._lc, "u_pos", 3)
        self._sv(prog, self._lc, "u_mat", 4)
        self._sv(prog, self._lc, "u_cam", self.camera.pos)
        self._sv(prog, self._lc, "u_view", view)
        self._sv(prog, self._lc, "u_proj", proj)
        self._sv(prog, self._lc, "u_exposure", self.exposure)
        self._sv(prog, self._lc, "u_ao_strength",
                 self.ao_strength if (self.ssao and ao_tex) else 0.0)
        self._sv(prog, self._lc, "u_bloom_amount",
                 self.bloom_amount if (self.bloom and bloom_tex) else 0.0)
        self._sv(prog, self._lc, "u_env", self.env)
        self._sv(prog, self._lc, "u_ssr_enable",
                 1 if (self.ssr and self._mrt and len(texs) >= 3) else 0)
        self._sv(prog, self._lc, "u_ssr_strength", 0.85)
        self._sv(prog, self._lc, "u_vignette", self.vignette)
        self._empty_vao.bind()
        gl.glDrawArrays(GL_TRIANGLES, 0, 3)
        self._empty_vao.release()
        prog.release()
        gl.glEnable(GL_DEPTH_TEST)

    def _atmo(self):
        """Parámetros de la paleta atmosférica elegida (sin la elevación del
        sol: la física del sol la resuelven los shaders a partir de u_sun_dir)."""
        idx = max(0, min(len(_SKY_PRESETS) - 1, int(self.sky_preset)))
        _name, sun_t, sky_t, mie, floor, gnd = _SKY_PRESETS[idx]
        return (QVector3D(*sun_t), QVector3D(*sky_t), QVector3D(*gnd),
                float(mie), QVector3D(*floor))

    def _set_atmo(self, prog, locs) -> None:
        """Sube los uniforms del modelo atmosférico a un programa."""
        sun_t, sky_t, gnd, mie, floor = self._atmo()
        self._sv(prog, locs, "u_sun_dir", self._sun_dir)
        self._sv(prog, locs, "u_sun_tint", sun_t)
        self._sv(prog, locs, "u_sky_tint", sky_t)
        self._sv(prog, locs, "u_gnd_col", gnd)
        self._sv(prog, locs, "u_mie", mie)
        self._sv(prog, locs, "u_sky_floor", floor)
        self._sv(prog, locs, "u_ambient", float(self.ambient))

    def _draw_hud(self, w: int, h: int) -> None:
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            font = QFont()
            font.setPointSize(9)
            p.setFont(font)
            lines = [
                "ViReal — ratón: mirar (arrastra) · W/A/S/D: mover · "
                "Q/E: bajar/subir · rueda: acercar",
                "Shift: correr · R: reencuadrar",
                "T: sombras · C: césped · V: viento · F: reflejos · "
                "G: oclusión · B: bloom · N: cielo",
            ]
            if self._stats:
                lines.append(self._stats)
            if self._error:
                lines.append("⚠ " + self._error)
            fm = p.fontMetrics()
            tw = max(fm.horizontalAdvance(s) for s in lines)
            th = fm.height()
            pad = 8
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(12, 14, 18, 170))
            p.drawRoundedRect(10, 10, tw + pad * 2, th * len(lines) + pad * 2,
                              8, 8)
            p.setPen(QColor(232, 236, 242, 235) if not self._error
                     else QColor(255, 190, 120, 255))
            ty = 10 + pad + fm.ascent()
            for s in lines:
                p.drawText(10 + pad, ty, s)
                ty += th
            p.end()
        except Exception:  # noqa: BLE001
            pass

    # ---- entrada ----------------------------------------------------------
    def keyPressEvent(self, ev) -> None:
        k = ev.key()
        if k == Qt.Key_Escape:
            self.window().close()
            return
        if k == Qt.Key_R:
            self.frame_scene()
            return
        if k == Qt.Key_T:
            self.shadows = not self.shadows
            self.update()
            return
        if k == Qt.Key_C:
            self.grass = not self.grass
            self.update()
            return
        if k == Qt.Key_V:
            self.wind = not self.wind
            self.update()
            return
        if k == Qt.Key_F:
            self.ssr = not self.ssr
            self.update()
            return
        if k == Qt.Key_G:
            self.ssao = not self.ssao
            self.update()
            return
        if k == Qt.Key_B:
            self.bloom = not self.bloom
            self.update()
            return
        if k == Qt.Key_N:
            self.sky_preset = (self.sky_preset + 1) % len(_SKY_PRESETS)
            self.update()
            return
        self._keys.add(k)
        self._mods = ev.modifiers()
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev) -> None:
        self._keys.discard(ev.key())
        self._mods = ev.modifiers()
        super().keyReleaseEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if ev.button() in (Qt.LeftButton, Qt.RightButton, Qt.MiddleButton):
            self._looking = True
            self._last_mouse = ev.position().toPoint()
            self.setCursor(Qt.BlankCursor)
        self.setFocus()

    def mouseReleaseEvent(self, ev) -> None:
        self._looking = False
        self.unsetCursor()

    def mouseMoveEvent(self, ev) -> None:
        if not self._looking:
            return
        pos = ev.position().toPoint()
        dx = pos.x() - self._last_mouse.x()
        dy = pos.y() - self._last_mouse.y()
        self._last_mouse = pos
        cam = self.camera
        cam.yaw += dx * 0.0032
        cam.pitch -= dy * 0.0032
        lim = math.radians(89.0)
        cam.pitch = max(-lim, min(lim, cam.pitch))
        self.update()

    def wheelEvent(self, ev) -> None:
        steps = ev.angleDelta().y() / 120.0
        self.camera.pos = self.camera.pos + self.camera.forward() * (steps * 0.8)
        self.update()


# ---------------------------------------------------------------------------
# Ventana flotante
# ---------------------------------------------------------------------------
class WorldWindow(QDialog):
    def __init__(self, app) -> None:
        super().__init__(app.window)
        self.setWindowTitle("ViReal — render en vivo")
        self.setWindowFlag(Qt.Window, True)
        self.setModal(False)
        self.resize(1120, 720)
        self.setSizeGripEnabled(True)

        self.view = WorldView(app, self)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_bar(app), 0)
        root.addWidget(self.view, 1)

        self.settings = QSettings()
        self._load_view_state()

    def _build_bar(self, app) -> QWidget:
        bar = QWidget(self)
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        # ---- fila 1: escena ----
        row1 = QWidget(bar)
        lay = QHBoxLayout(row1)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        btn_refresh = QPushButton("⟳  Actualizar", row1)
        btn_refresh.clicked.connect(self.view.mark_dirty)
        btn_frame = QPushButton("⛶  Reencuadrar", row1)
        btn_frame.clicked.connect(self.view.frame_scene)
        lay.addWidget(btn_refresh)
        lay.addWidget(btn_frame)

        self.chk_shadows = QCheckBox("Sombras", row1)
        self.chk_shadows.setChecked(True)
        self.chk_shadows.toggled.connect(self._on_shadows)
        lay.addWidget(self.chk_shadows)

        self.chk_grass = QCheckBox("Césped 3D", row1)
        self.chk_grass.setChecked(True)
        self.chk_grass.setToolTip(
            "Hojas 3D que se mecen con el viento sobre las superficies con "
            "textura de césped/grass (tecla C)")
        self.chk_grass.toggled.connect(self._on_grass)
        lay.addWidget(self.chk_grass)

        self.chk_wind = QCheckBox("Viento", row1)
        self.chk_wind.setChecked(True)
        self.chk_wind.setToolTip("Anima el césped (tecla V)")
        self.chk_wind.toggled.connect(self._on_wind)
        lay.addWidget(self.chk_wind)

        lay.addWidget(QLabel("Exposición", row1))
        self.spin_exp = QDoubleSpinBox(row1)
        self.spin_exp.setRange(0.2, 3.0)
        self.spin_exp.setSingleStep(0.05)
        self.spin_exp.setDecimals(2)
        self.spin_exp.setValue(1.05)
        self.spin_exp.valueChanged.connect(self._on_exposure)
        lay.addWidget(self.spin_exp)

        lay.addWidget(QLabel("Ambiente", row1))
        self.spin_amb = QDoubleSpinBox(row1)
        self.spin_amb.setRange(0.0, 2.0)
        self.spin_amb.setSingleStep(0.05)
        self.spin_amb.setDecimals(2)
        self.spin_amb.setValue(0.55)
        self.spin_amb.valueChanged.connect(self._on_ambient)
        lay.addWidget(self.spin_amb)

        lay.addStretch(1)
        hint = QLabel("arrastra para mirar · W/A/S/D mover · Esc cerrar", row1)
        hint.setEnabled(False)
        lay.addWidget(hint)
        outer.addWidget(row1)

        # ---- fila 2: efectos ----
        row2 = QWidget(bar)
        fx = QHBoxLayout(row2)
        fx.setContentsMargins(0, 0, 0, 0)
        fx.setSpacing(8)

        self.chk_ssr = QCheckBox("Reflejos", row2)
        self.chk_ssr.setChecked(True)
        self.chk_ssr.setToolTip("Reflejos en tiempo real sobre pisos glossy, "
                                "vidrio y metal (tecla F)")
        self.chk_ssr.toggled.connect(self._on_ssr)
        fx.addWidget(self.chk_ssr)

        self.chk_ssao = QCheckBox("Oclusión", row2)
        self.chk_ssao.setChecked(True)
        self.chk_ssao.setToolTip("Sombra ambiental: asienta los objetos sobre "
                                 "el suelo (tecla G)")
        self.chk_ssao.toggled.connect(self._on_ssao)
        fx.addWidget(self.chk_ssao)

        self.chk_bloom = QCheckBox("Bloom", row2)
        self.chk_bloom.setChecked(True)
        self.chk_bloom.setToolTip("Resplandor solar (tecla B)")
        self.chk_bloom.toggled.connect(self._on_bloom)
        fx.addWidget(self.chk_bloom)

        fx.addWidget(QLabel("Cielo", row2))
        self.cmb_sky = QComboBox(row2)
        for preset in _SKY_PRESETS:
            self.cmb_sky.addItem(preset[0])
        self.cmb_sky.currentIndexChanged.connect(self._on_sky)
        fx.addWidget(self.cmb_sky)

        fx.addWidget(QLabel("Reflejo", row2))
        self.spin_env = QDoubleSpinBox(row2)
        self.spin_env.setRange(0.0, 1.5)
        self.spin_env.setSingleStep(0.05)
        self.spin_env.setDecimals(2)
        self.spin_env.setValue(0.40)
        self.spin_env.setToolTip("Intensidad de los reflejos del entorno")
        self.spin_env.valueChanged.connect(self._on_env)
        fx.addWidget(self.spin_env)

        fx.addWidget(QLabel("Brillo pisos", row2))
        self.spin_floor = QDoubleSpinBox(row2)
        self.spin_floor.setRange(0.0, 1.0)
        self.spin_floor.setSingleStep(0.05)
        self.spin_floor.setDecimals(2)
        self.spin_floor.setValue(0.35)
        self.spin_floor.setToolTip("Vuelve glossy las caras que miran hacia "
                                   "arriba (pisos)")
        self.spin_floor.valueChanged.connect(self._on_floor)
        fx.addWidget(self.spin_floor)

        fx.addWidget(QLabel("Relieve", row2))
        self.spin_bump = QDoubleSpinBox(row2)
        self.spin_bump.setRange(0.0, 2.0)
        self.spin_bump.setSingleStep(0.1)
        self.spin_bump.setDecimals(2)
        self.spin_bump.setValue(1.0)
        self.spin_bump.setToolTip("Fuerza del relieve (normal-map) de "
                                  "piedras, ladrillos y revestimientos")
        self.spin_bump.valueChanged.connect(self._on_bump)
        fx.addWidget(self.spin_bump)

        fx.addWidget(QLabel("Césped/m²", row2))
        self.spin_grass = QDoubleSpinBox(row2)
        self.spin_grass.setRange(0.0, 200.0)
        self.spin_grass.setSingleStep(5.0)
        self.spin_grass.setDecimals(0)
        self.spin_grass.setValue(45.0)
        self.spin_grass.setToolTip("Densidad de hojas por metro cuadrado de "
                                   "césped (0 = sin césped)")
        self.spin_grass.valueChanged.connect(self._on_density)
        fx.addWidget(self.spin_grass)

        fx.addStretch(1)
        outer.addWidget(row2)
        return bar

    def _on_shadows(self, on: bool) -> None:
        self.view.shadows = bool(on)
        self.view.update()

    def _on_exposure(self, v: float) -> None:
        self.view.exposure = float(v)
        self.view.update()

    def _on_ambient(self, v: float) -> None:
        self.view.ambient = float(v)
        self.view.update()

    def _on_grass(self, on: bool) -> None:
        self.view.grass = bool(on)
        self.view.update()

    def _on_wind(self, on: bool) -> None:
        self.view.wind = bool(on)
        self.view.update()

    def _on_density(self, v: float) -> None:
        self.view.grass_density = float(v)
        self.view.mark_dirty()
        self.view.update()

    def _on_ssr(self, on: bool) -> None:
        self.view.ssr = bool(on)
        self.view.update()

    def _on_ssao(self, on: bool) -> None:
        self.view.ssao = bool(on)
        self.view.update()

    def _on_bloom(self, on: bool) -> None:
        self.view.bloom = bool(on)
        self.view.update()

    def _on_sky(self, idx: int) -> None:
        self.view.sky_preset = int(idx)
        self.view.update()

    def _on_env(self, v: float) -> None:
        self.view.env = float(v)
        self.view.update()

    def _on_floor(self, v: float) -> None:
        self.view.floor_gloss = float(v)
        self.view.update()

    def _on_bump(self, v: float) -> None:
        self.view.bump = float(v)
        self.view.update()

    def _load_view_state(self) -> None:
        s = self.settings

        def _flag(key, default=True):
            return str(s.value(_SETTINGS + key,
                               "true" if default else "false")).lower() \
                in ("1", "true")

        try:
            self.view.shadows = _flag("shadows")
            self.view.exposure = float(s.value(_SETTINGS + "exposure", 1.05))
            self.view.ambient = float(s.value(_SETTINGS + "ambient", 0.55))
            self.view.grass = _flag("grass")
            self.view.wind = _flag("wind")
            self.view.grass_density = float(
                s.value(_SETTINGS + "grass_density", 45.0))
            self.view.ssr = _flag("ssr")
            self.view.ssao = _flag("ssao")
            self.view.bloom = _flag("bloom")
            self.view.env = float(s.value(_SETTINGS + "env", 0.40))
            self.view.floor_gloss = float(s.value(_SETTINGS + "floor", 0.35))
            self.view.bump = float(s.value(_SETTINGS + "bump", 1.0))
            self.view.sky_preset = int(s.value(_SETTINGS + "sky", 0))
            self.chk_shadows.setChecked(self.view.shadows)
            self.spin_exp.setValue(self.view.exposure)
            self.spin_amb.setValue(self.view.ambient)
            self.chk_grass.setChecked(self.view.grass)
            self.chk_wind.setChecked(self.view.wind)
            self.spin_grass.setValue(self.view.grass_density)
            self.chk_ssr.setChecked(self.view.ssr)
            self.chk_ssao.setChecked(self.view.ssao)
            self.chk_bloom.setChecked(self.view.bloom)
            self.cmb_sky.setCurrentIndex(
                max(0, min(len(_SKY_PRESETS) - 1, self.view.sky_preset)))
            self.spin_env.setValue(self.view.env)
            self.spin_floor.setValue(self.view.floor_gloss)
            self.spin_bump.setValue(self.view.bump)
        except (TypeError, ValueError):
            pass

    def save_view_state(self) -> None:
        s = self.settings
        s.setValue(_SETTINGS + "shadows", self.view.shadows)
        s.setValue(_SETTINGS + "exposure", self.view.exposure)
        s.setValue(_SETTINGS + "ambient", self.view.ambient)
        s.setValue(_SETTINGS + "grass", self.view.grass)
        s.setValue(_SETTINGS + "wind", self.view.wind)
        s.setValue(_SETTINGS + "grass_density", self.view.grass_density)
        s.setValue(_SETTINGS + "ssr", self.view.ssr)
        s.setValue(_SETTINGS + "ssao", self.view.ssao)
        s.setValue(_SETTINGS + "bloom", self.view.bloom)
        s.setValue(_SETTINGS + "env", self.view.env)
        s.setValue(_SETTINGS + "floor", self.view.floor_gloss)
        s.setValue(_SETTINGS + "bump", self.view.bump)
        s.setValue(_SETTINGS + "sky", self.view.sky_preset)
        s.sync()

    def closeEvent(self, ev) -> None:
        self.save_view_state()
        self.view.mark_dirty()
        super().closeEvent(ev)


# ---------------------------------------------------------------------------
# Panel lateral + entrada de la extensión
# ---------------------------------------------------------------------------
class ViRealPanel(QWidget):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        box = QGroupBox("ViReal — render en vivo", self)
        bl = QVBoxLayout(box)
        note = QLabel(
            "Abre una ventana flotante con el modelo iluminado: texturas "
            "reales, cielo y luz solar físicos (el sol se enrojece y se apaga "
            "según su elevación real), sombras suaves, reflejos en tiempo real "
            "sobre pisos brillantes, vidrio y metal, relieve en piedras y "
            "ladrillos, sombra ambiental y bloom. Césped 3D que se mece con "
            "el viento en las superficies con textura de césped/grass, y "
            "paletas de cielo (día, atardecer, nublado, noche). Cámara libre "
            "tipo juego (W/A/S/D + ratón).", box)
        note.setWordWrap(True)
        bl.addWidget(note)
        btn = QPushButton("▶  Abrir ventana ViReal", box)
        btn.clicked.connect(lambda: open_world_window(self.app))
        bl.addWidget(btn)
        lay.addWidget(box)
        lay.addStretch(1)


_window: WorldWindow | None = None


def open_world_window(app) -> WorldWindow:
    global _window
    if _window is None:
        _window = WorldWindow(app)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    _window.view.setFocus()
    _window.view.mark_dirty()
    _window.view.update()
    return _window


def setup(app) -> None:
    app.add_panel("ViReal", ViRealPanel(app))

    def open_from_menu() -> None:
        open_world_window(app)

    app.add_menu_action(
        "ViReal — ventana de render 3D en vivo", open_from_menu, "Ctrl+Alt+M",
        tip="Ventana flotante con el modelo iluminado (texturas, sol, "
            "sombras) y cámara libre.")

    def _on_doc() -> None:
        if _window is not None:
            _window.view.mark_dirty()

    app.on_document_changed(_on_doc)
    log.info("ViReal listo.")
