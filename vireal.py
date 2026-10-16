# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 — extensión «ViReal» para IngeTrazo.
"""ViReal — ventana flotante de render en vivo, estilo motor de juego.

Abre una **ventana flotante independiente** con su propio contexto OpenGL y
su propio renderizado: coge la geometría visible del modelo, la triangula, le
aplica las texturas reales y los *finishes* (core.finish: roughness,
metalness, transmisión) y la ilumina con el **sol real** del documento, sombras
proyectadas, ambiente hemisférico (cielo/suelo del estilo), *tone mapping* ACES
y corrección gamma. El resultado es un «ViReal» navegable a pie, con cámara
libre tipo juego (ratón + WASD), no el aspecto de línea de un CAD.

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
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QCheckBox,
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
__version__ = "0.2.0"

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
GL_CLAMP_TO_EDGE = 0x812F
GL_MULTISAMPLE = 0x809D

#: Nombres de los uniformes de cada programa (PySide6 exige ``bytes`` en
#: ``uniformLocation`` y la localización para valores float/int).
_MAIN_UNIFORMS = (
    "u_mvp", "u_cam", "u_sun_dir", "u_sun_col", "u_sky_col", "u_gnd_col",
    "u_ambient", "u_exposure", "u_shadow_strength", "u_light_vp", "u_reflect",
    "u_shadow_enable", "u_shadow", "u_tex", "u_color", "u_opacity",
    "u_use_texture",
)
_SKY_UNIFORMS = (
    "u_inv_vp", "u_cam", "u_sun_dir", "u_sun_col", "u_sky_col", "u_gnd_col",
    "u_exposure",
)
_DEPTH_UNIFORMS = ("u_light_vp",)
_GRASS_UNIFORMS = (
    "u_mvp", "u_cam", "u_sun_dir", "u_sun_col", "u_sky_col", "u_gnd_col",
    "u_ambient", "u_exposure", "u_shadow_enable", "u_shadow",
    "u_light_vp", "u_shadow_strength", "u_time", "u_wind_dir",
    "u_wind_strength",
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


# ---------------------------------------------------------------------------
# Shaders
# ---------------------------------------------------------------------------
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
uniform vec3 u_cam;
uniform vec3 u_sun_dir;      // unitaria, hacia el sol
uniform vec3 u_sun_col;
uniform vec3 u_sky_col;
uniform vec3 u_gnd_col;
uniform float u_ambient;
uniform vec3 u_color;        // tinte extra (normalmente blanco)
uniform int u_use_texture;
uniform sampler2D u_tex;
uniform int u_shadow_enable;
uniform sampler2D u_shadow;
uniform mat4 u_light_vp;
uniform float u_shadow_strength;
uniform float u_exposure;
uniform float u_opacity;
uniform vec3 u_reflect;      // color del reflejo (cielo) para vidrio/agua
out vec4 fragColor;

float unpackDepth(vec3 e) {
    return dot(e, vec3(1.0, 1.0 / 255.0, 1.0 / 65025.0));
}

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
    // profundidad guardada con el plano del receptor evaluado EN esa
    // muestra, no contra el fragmento central; así las caras rasantes al
    // sol no se llenan de acné.
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

vec3 aces(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
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
    vec3 L = normalize(u_sun_dir);
    float ndl = max(dot(N, L), 0.0);
    float ndv = max(dot(N, V), 1e-3);
    vec3 H = normalize(L + V);
    float ndh = max(dot(N, H), 0.0);

    float sh = shadowFactor(v_world, ndl);

    // Cook-Torrance GGX (aproximación difusa Lambert de kd = 1 - F).
    float a = max(rough * rough, 1e-3);
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

    vec3 direct = (kd * albedo / 3.141593 + spec) * u_sun_col * ndl * sh;

    float hemi = N.z * 0.5 + 0.5;
    vec3 amb = albedo * mix(u_gnd_col, u_sky_col, hemi) * u_ambient;

    vec3 col = direct + amb;

    if (trans > 0.0) {
        float fres = pow(1.0 - ndv, 3.0);
        vec3 refl = u_reflect * (0.25 + 0.75 * fres);
        col = mix(col, refl + amb * 0.6, clamp(trans * 0.9, 0.0, 1.0));
    }

    col *= u_exposure;
    col = aces(col);
    col = pow(col, vec3(1.0 / 2.2));
    fragColor = vec4(col, u_opacity * base.a);
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
uniform vec3 u_sun_dir;
uniform vec3 u_sun_col;
uniform vec3 u_sky_col;
uniform vec3 u_gnd_col;
uniform float u_exposure;
out vec4 fragColor;

vec3 aces(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

void main() {
    vec4 far = u_inv_vp * vec4(v_ndc, 1.0, 1.0);
    vec3 dir = normalize(far.xyz / far.w - u_cam);
    float up = clamp(dir.z, -1.0, 1.0);
    vec3 sky = mix(u_gnd_col * 0.75, u_sky_col,
                   smoothstep(-0.04, 0.55, up));
    // halo y disco solar
    float sd = max(dot(dir, normalize(u_sun_dir)), 0.0);
    sky += u_sun_col * (pow(sd, 900.0) * 6.0 + pow(sd, 24.0) * 0.18);
    sky *= u_exposure;
    sky = aces(sky);
    fragColor = vec4(pow(sky, vec3(1.0 / 2.2)), 1.0);
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
uniform vec3 u_sun_dir;
uniform vec3 u_sun_col;
uniform vec3 u_sky_col;
uniform vec3 u_gnd_col;
uniform float u_ambient;
uniform float u_exposure;
uniform int u_shadow_enable;
uniform sampler2D u_shadow;
uniform mat4 u_light_vp;
uniform float u_shadow_strength;
out vec4 fragColor;

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

vec3 aces(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

void main() {
    vec3 albedo = v_col;
    vec3 N = normalize(v_nrm);
    vec3 V = normalize(u_cam - v_world);
    if (dot(N, V) < 0.0) N = -N;         // sombreado a dos caras
    vec3 L = normalize(u_sun_dir);
    float ndl = max(dot(N, L), 0.0);
    float sh = grassShadow(v_world);

    vec3 direct = albedo * u_sun_col * ndl * sh / 3.141593 * 1.25;
    float hemi = N.z * 0.5 + 0.5;
    vec3 amb = albedo * mix(u_gnd_col, u_sky_col, hemi) * u_ambient;
    vec3 col = direct + amb;
    // Translucencia aproximada: las puntas dejan pasar algo de sol.
    col += albedo * u_sun_col * 0.07 * v_t * sh;

    col *= u_exposure;
    col = aces(col);
    col = pow(col, vec3(1.0 / 2.2));
    fragColor = vec4(col, 1.0);
}
"""


# ---------------------------------------------------------------------------
# Extracción de geometría del documento
# ---------------------------------------------------------------------------
class _DrawRun:
    __slots__ = ("texture", "rough", "metal", "trans", "opacity",
                 "first", "count")

    def __init__(self, texture, rough, metal, trans, opacity):
        self.texture = texture
        self.rough = rough
        self.metal = metal
        self.trans = trans
        self.opacity = opacity
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


def _is_grass(path=None, matname=None) -> bool:
    """¿La textura (por nombre de archivo y carpeta) o el material es césped?"""
    hay = ""
    if path:
        hay += " " + _norm_words(str(path).replace("\\", "/"))
    if matname:
        hay += " " + _norm_words(matname)
    return any(w in hay for w in _GRASS_WORDS)


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
            key = (texpath, round(rough, 3), round(metal, 3),
                   round(trans, 3), round(opacity, 3))
            bucket = _bucket(key, texpath)
            grass = _is_grass(texpath, matname)
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
        run = _DrawRun(texture, key[1], key[2], key[3], key[4])
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
        self._gl: QOpenGLFunctions | None = None
        self._prog = None
        self._sky = None
        self._depth = None
        self._gprog = None
        self._vao = None
        self._empty_vao = None
        self._vbo = None
        self._gvao = None
        self._gvbo = None
        self._shadow_fbo = None
        self._textures: dict = {}
        self._lm: dict = {}
        self._ls: dict = {}
        self._ld: dict = {}
        self._lg: dict = {}
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

        self.shadows = True
        self.ambient = 0.55
        self.exposure = 1.05
        self.shadow_strength = 0.62
        self.grass = True
        self.wind = True
        self.grass_density = 45.0
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
        self._gl = QOpenGLFunctions(self.context())
        self._gl.initializeOpenGLFunctions()
        self._prog = self._compile(_VERT, _FRAG, "principal")
        self._sky = self._compile(_SKY_VERT, _SKY_FRAG, "cielo")
        self._depth = self._compile(_DEPTH_VERT, _DEPTH_FRAG, "sombras")
        self._gprog = self._compile(_GRASS_VERT, _GRASS_FRAG, "césped")
        self._lm = self._locs_for(self._prog, _MAIN_UNIFORMS) if self._prog else {}
        self._ls = self._locs_for(self._sky, _SKY_UNIFORMS) if self._sky else {}
        self._ld = self._locs_for(self._depth, _DEPTH_UNIFORMS) if self._depth else {}
        self._lg = self._locs_for(self._gprog, _GRASS_UNIFORMS) if self._gprog else {}
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

    # ---- pintado ----------------------------------------------------------
    def paintGL(self) -> None:
        if self._gl is None:
            return
        if self._dirty or self._scene_version() != self._built_version:
            self._rebuild()

        gl = self._gl
        w = max(1, self.width())
        h = max(1, self.height())
        aspect = w / h

        view = self.camera.view_matrix()
        proj = self.camera.projection_matrix(aspect)
        mvp = proj * view

        sky_col, gnd_col, sun_col, reflect = self._palette()

        # --- mapa de sombras (solo se rehace si cambió la escena: la matriz
        # de luz no depende de la cámara) ---
        shadow_tex = 0
        if self.shadows and self._depth is not None and self._vcount:
            fbo = self._ensure_shadow_fbo()
            if fbo is not None:
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
                shadow_tex = fbo.texture()

        # --- frame por defecto del widget ---
        gl.glBindFramebuffer(0x8D40, self.defaultFramebufferObject())
        gl.glViewport(0, 0, int(w * self.devicePixelRatioF()),
                      int(h * self.devicePixelRatioF()))
        gl.glClearColor(sky_col.x(), sky_col.y(), sky_col.z(), 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        # --- cielo ---
        if self._sky is not None:
            self._sky.bind()
            inv = QMatrix4x4()
            try:
                res = mvp.inverted()
                if isinstance(res, tuple):
                    if res[1]:
                        inv = res[0]
                else:
                    inv = res
            except Exception:  # noqa: BLE001
                inv = QMatrix4x4()
            self._sv(self._sky, self._ls, "u_inv_vp", inv)
            self._sv(self._sky, self._ls, "u_cam", self.camera.pos)
            self._sv(self._sky, self._ls, "u_sun_dir", self._sun_dir)
            self._sv(self._sky, self._ls, "u_sun_col", sun_col)
            self._sv(self._sky, self._ls, "u_sky_col", sky_col)
            self._sv(self._sky, self._ls, "u_gnd_col", gnd_col)
            self._sv(self._sky, self._ls, "u_exposure", self.exposure)
            self._empty_vao.bind()
            self._gl.glDisable(GL_DEPTH_TEST)
            gl.glDrawArrays(GL_TRIANGLES, 0, 3)
            self._gl.glEnable(GL_DEPTH_TEST)
            self._empty_vao.release()
            self._sky.release()

        # --- geometría ---
        if self._runs and self._prog is not None:
            self._prog.bind()
            self._sv(self._prog, self._lm, "u_mvp", mvp)
            self._sv(self._prog, self._lm, "u_cam", self.camera.pos)
            self._sv(self._prog, self._lm, "u_sun_dir", self._sun_dir)
            self._sv(self._prog, self._lm, "u_sun_col", sun_col)
            self._sv(self._prog, self._lm, "u_sky_col", sky_col)
            self._sv(self._prog, self._lm, "u_gnd_col", gnd_col)
            self._sv(self._prog, self._lm, "u_ambient", self.ambient)
            self._sv(self._prog, self._lm, "u_exposure", self.exposure)
            self._sv(self._prog, self._lm, "u_shadow_strength",
                     self.shadow_strength)
            self._sv(self._prog, self._lm, "u_light_vp", self._light_vp)
            self._sv(self._prog, self._lm, "u_reflect", reflect)
            use_shadow = 1 if (self.shadows and shadow_tex) else 0
            self._sv(self._prog, self._lm, "u_shadow_enable", use_shadow)
            self._sv(self._prog, self._lm, "u_shadow", 1)
            self._sv(self._prog, self._lm, "u_tex", 0)

            if shadow_tex:
                gl.glActiveTexture(GL_TEXTURE1)
                gl.glBindTexture(GL_TEXTURE_2D, int(shadow_tex))
                # El FBO no trae mipmaps: sin fijar el filtro la textura queda
                # incompleta y muestrea negro (sombras inertes).
                gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S,
                                   GL_CLAMP_TO_EDGE)
                gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T,
                                   GL_CLAMP_TO_EDGE)
                gl.glActiveTexture(GL_TEXTURE0)

            self._vao.bind()
            for run in self._runs:
                self._sv(self._prog, self._lm, "u_color",
                         QVector3D(1.0, 1.0, 1.0))
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

        # --- césped 3D (opaco, recibe sombras) ---
        if self.grass and self._gcount and self._gprog is not None:
            gprog = self._gprog
            gprog.bind()
            self._sv(gprog, self._lg, "u_mvp", mvp)
            self._sv(gprog, self._lg, "u_cam", self.camera.pos)
            self._sv(gprog, self._lg, "u_sun_dir", self._sun_dir)
            self._sv(gprog, self._lg, "u_sun_col", sun_col)
            self._sv(gprog, self._lg, "u_sky_col", sky_col)
            self._sv(gprog, self._lg, "u_gnd_col", gnd_col)
            self._sv(gprog, self._lg, "u_ambient", self.ambient)
            self._sv(gprog, self._lg, "u_exposure", self.exposure)
            self._sv(gprog, self._lg, "u_shadow_strength", self.shadow_strength)
            self._sv(gprog, self._lg, "u_light_vp", self._light_vp)
            use_shadow = 1 if (self.shadows and shadow_tex) else 0
            self._sv(gprog, self._lg, "u_shadow_enable", use_shadow)
            self._sv(gprog, self._lg, "u_shadow", 1)
            self._sv(gprog, self._lg, "u_time",
                     self._time if self.wind else 0.0)
            wdx, wdy = WIND_DIR
            self._sv(gprog, self._lg, "u_wind_dir", QVector2D(wdx, wdy))
            self._sv(gprog, self._lg, "u_wind_strength",
                     WIND_STRENGTH if self.wind else 0.0)
            if shadow_tex:
                gl.glActiveTexture(GL_TEXTURE1)
                gl.glBindTexture(GL_TEXTURE_2D, int(shadow_tex))
                gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S,
                                   GL_CLAMP_TO_EDGE)
                gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T,
                                   GL_CLAMP_TO_EDGE)
                gl.glActiveTexture(GL_TEXTURE0)
            self._gvao.bind()
            gl.glDrawArrays(GL_TRIANGLES, 0, self._gcount)
            self._gvao.release()
            gprog.release()

        self._draw_hud(w, h)

    def _palette(self):
        # Cielo fotográfico propio (no el fondo gris del CAD): el «ViReal»
        # quiere un horizonte azul, no el papel del viewport.
        sky = QVector3D(0.46, 0.64, 0.92)
        gnd = QVector3D(0.30, 0.29, 0.27)
        sun = QVector3D(1.00, 0.96, 0.88)
        # El sol bajo tiñe más cálido.
        elev = max(0.0, self._sun_dir.z())
        warm = 1.0 - min(1.0, elev * 1.6)
        sun = _lerp3(sun, QVector3D(1.0, 0.62, 0.34), warm * 0.8)
        reflect = _lerp3(sky, QVector3D(1.0, 1.0, 1.0), 0.25)
        return sky, gnd, sun, reflect

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
                "Shift: correr · R: reencuadrar · T: sombras · "
                "C: césped · V: viento",
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
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)

        btn_refresh = QPushButton("⟳  Actualizar", bar)
        btn_refresh.clicked.connect(self.view.mark_dirty)
        btn_frame = QPushButton("⛶  Reencuadrar", bar)
        btn_frame.clicked.connect(self.view.frame_scene)
        lay.addWidget(btn_refresh)
        lay.addWidget(btn_frame)

        self.chk_shadows = QCheckBox("Sombras", bar)
        self.chk_shadows.setChecked(True)
        self.chk_shadows.toggled.connect(self._on_shadows)
        lay.addWidget(self.chk_shadows)

        self.chk_grass = QCheckBox("Césped 3D", bar)
        self.chk_grass.setChecked(True)
        self.chk_grass.setToolTip(
            "Hojas 3D que se mecen con el viento sobre las superficies con "
            "textura de césped/grass (tecla C)")
        self.chk_grass.toggled.connect(self._on_grass)
        lay.addWidget(self.chk_grass)

        self.chk_wind = QCheckBox("Viento", bar)
        self.chk_wind.setChecked(True)
        self.chk_wind.setToolTip("Anima el césped (tecla V)")
        self.chk_wind.toggled.connect(self._on_wind)
        lay.addWidget(self.chk_wind)

        lay.addWidget(QLabel("Exposición", bar))
        self.spin_exp = QDoubleSpinBox(bar)
        self.spin_exp.setRange(0.2, 3.0)
        self.spin_exp.setSingleStep(0.05)
        self.spin_exp.setDecimals(2)
        self.spin_exp.setValue(1.05)
        self.spin_exp.valueChanged.connect(self._on_exposure)
        lay.addWidget(self.spin_exp)

        lay.addWidget(QLabel("Ambiente", bar))
        self.spin_amb = QDoubleSpinBox(bar)
        self.spin_amb.setRange(0.0, 2.0)
        self.spin_amb.setSingleStep(0.05)
        self.spin_amb.setDecimals(2)
        self.spin_amb.setValue(0.55)
        self.spin_amb.valueChanged.connect(self._on_ambient)
        lay.addWidget(self.spin_amb)

        lay.addWidget(QLabel("Césped/m²", bar))
        self.spin_grass = QDoubleSpinBox(bar)
        self.spin_grass.setRange(0.0, 200.0)
        self.spin_grass.setSingleStep(5.0)
        self.spin_grass.setDecimals(0)
        self.spin_grass.setValue(45.0)
        self.spin_grass.setToolTip("Densidad de hojas por metro cuadrado de "
                                   "césped (0 = sin césped)")
        self.spin_grass.valueChanged.connect(self._on_density)
        lay.addWidget(self.spin_grass)

        lay.addStretch(1)
        hint = QLabel("arrastra para mirar · W/A/S/D mover · Esc cerrar", bar)
        hint.setEnabled(False)
        lay.addWidget(hint)
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

    def _load_view_state(self) -> None:
        s = self.settings
        try:
            self.view.shadows = str(s.value(_SETTINGS + "shadows",
                                            "true")).lower() in ("1", "true")
            self.view.exposure = float(s.value(_SETTINGS + "exposure", 1.05))
            self.view.ambient = float(s.value(_SETTINGS + "ambient", 0.55))
            self.view.grass = str(s.value(_SETTINGS + "grass",
                                          "true")).lower() in ("1", "true")
            self.view.wind = str(s.value(_SETTINGS + "wind",
                                         "true")).lower() in ("1", "true")
            self.view.grass_density = float(
                s.value(_SETTINGS + "grass_density", 45.0))
            self.chk_shadows.setChecked(self.view.shadows)
            self.spin_exp.setValue(self.view.exposure)
            self.spin_amb.setValue(self.view.ambient)
            self.chk_grass.setChecked(self.view.grass)
            self.chk_wind.setChecked(self.view.wind)
            self.spin_grass.setValue(self.view.grass_density)
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
            "reales, sol y sombras, materiales con brillo (core.finish) y "
            "cielo. Césped 3D que se mece con el viento en las superficies "
            "con textura de césped/grass. Cámara libre tipo juego "
            "(W/A/S/D + ratón).", box)
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
