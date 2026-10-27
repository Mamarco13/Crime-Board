# -*- coding: utf-8 -*-
"""Genera el diagrama de arquitectura (SVG) del proyecto Crime Board Cloud-Native."""
import html
import os

W, H = 1640, 1010
FONT = "Segoe UI, Segoe UI Emoji, Arial, Helvetica, sans-serif"

parts = []
def add(s): parts.append(s)

# ---------- paleta ----------
CLIENTS = ("#E8F1FC", "#1565C0")
EDGE    = ("#FFF4E5", "#E65100")
SERVICE = ("#E7F6EC", "#2E7D32")
DATA    = ("#F6EAF8", "#6A1B9A")
MSG     = ("#FDECF2", "#AD1457")
OBS     = ("#ECEFF1", "#455A64")
INK     = "#22313F"
MUTED   = "#5A6B7B"

def esc(s): return html.escape(s, quote=True)

def box(x, y, w, h, title, sublines, palette, r=10, tsize=17, ssize=12.5):
    fill, stroke = palette
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
    cx = x + w / 2
    n = len(sublines)
    block = tsize + (n * (ssize + 5) if n else 0)
    ty = y + h / 2 - block / 2 + tsize * 0.85
    add(f'<text x="{cx}" y="{ty:.1f}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="{tsize}" font-weight="700" fill="{INK}">{esc(title)}</text>')
    for i, ln in enumerate(sublines):
        ly = ty + tsize * 0.55 + (i + 1) * (ssize + 5)
        add(f'<text x="{cx}" y="{ly:.1f}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="{ssize}" fill="{MUTED}">{esc(ln)}</text>')

def band(x, y, w, h, label, num):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" ry="14" '
        f'fill="#FFFFFF" stroke="#D6DEE6" stroke-width="1.4" stroke-dasharray="7 5"/>')
    add(f'<text x="{x+16}" y="{y+23}" font-family="{FONT}" font-size="12.5" '
        f'font-weight="700" fill="#8A9AA8" letter-spacing="1.4">{esc(num)} · {esc(label.upper())}</text>')

def label(x, y, text, color=INK, size=12.5, anchor="middle", weight="600"):
    # caja blanca de fondo para legibilidad
    tw = len(text) * size * 0.56
    if anchor == "middle":
        bx = x - tw / 2 - 5
    elif anchor == "start":
        bx = x - 5
    else:
        bx = x - tw - 5
    add(f'<rect x="{bx:.1f}" y="{y-size*0.95:.1f}" width="{tw+10:.1f}" '
        f'height="{size*1.5:.1f}" rx="4" fill="#FFFFFF" fill-opacity="0.92"/>')
    add(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{FONT}" '
        f'font-size="{size}" font-weight="{weight}" fill="{color}">{esc(text)}</text>')

def edge(x1, y1, x2, y2, text="", dashed=False, color="#455A64", ty=None, tx=None, anchor="middle"):
    dash = ' stroke-dasharray="8 6"' if dashed else ''
    marker = "arrowD" if dashed else "arrowS"
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
        f'stroke-width="2"{dash} marker-end="url(#{marker})"/>')
    if text:
        mx = tx if tx is not None else (x1 + x2) / 2
        my = ty if ty is not None else (y1 + y2) / 2 + 4
        label(mx, my, text, color=color, anchor=anchor)

# ---------- SVG ----------
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
    f'viewBox="0 0 {W} {H}" font-family="{FONT}">')
add('<defs>'
    '<marker id="arrowS" markerWidth="11" markerHeight="11" refX="9" refY="3.5" '
    'orient="auto" markerUnits="strokeWidth">'
    '<path d="M0,0 L9,3.5 L0,7 z" fill="#455A64"/></marker>'
    '<marker id="arrowD" markerWidth="11" markerHeight="11" refX="9" refY="3.5" '
    'orient="auto" markerUnits="strokeWidth">'
    '<path d="M0,0 L9,3.5 L0,7 z" fill="#AD1457"/></marker>'
    '</defs>')
add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')

# título
add(f'<text x="90" y="58" font-family="{FONT}" font-size="30" font-weight="800" '
    f'fill="{INK}">Crime Board Cloud-Native</text>')
add(f'<text x="90" y="90" font-family="{FONT}" font-size="15.5" fill="{MUTED}">'
    f'Arquitectura de Microservicios — Componentes, Protocolos de Comunicación y Almacenamiento</text>')

# leyenda
add('<rect x="1170" y="26" width="392" height="82" rx="10" fill="#F8FAFC" stroke="#D6DEE6"/>')
add(f'<line x1="1194" y1="54" x2="1252" y2="54" stroke="#455A64" stroke-width="2.4" marker-end="url(#arrowS)"/>')
add(f'<text x="1264" y="58" font-family="{FONT}" font-size="13" fill="{INK}">Síncrono (REST · SQL · RESP)</text>')
add(f'<line x1="1194" y1="88" x2="1252" y2="88" stroke="#AD1457" stroke-width="2.4" stroke-dasharray="8 6" marker-end="url(#arrowD)"/>')
add(f'<text x="1264" y="92" font-family="{FONT}" font-size="13" fill="{INK}">Asíncrono (eventos · mensajería)</text>')

# ---------- bandas ----------
band(90, 122, 1470, 152, "Consumidores", "1")
band(90, 300, 1470, 122, "Borde / Entrada (Edge)", "2")
band(90, 448, 1470, 178, "Microservicios", "3")
band(90, 648, 1470, 152, "Persistencia y Mensajería", "4")
band(90, 820, 1470, 158, "Operación y Servicio Medido", "5")

# ---------- componentes ----------
box(430, 158, 340, 86, "Cliente Web (SPA)",
    ["Tablero interactivo", "HTML / JavaScript"], CLIENTS)
box(870, 158, 340, 86, "Cliente Móvil",
    ["Navegador / dispositivo", "con acceso a la red"], CLIENTS)

box(430, 342, 780, 66, "API Gateway  /  Ingress  +  Balanceador de carga",
    ["Punto único de entrada  ·  HTTPS / TLS 1.3  ·  REST + JSON"], EDGE, tsize=17, ssize=12.5)

box(230, 490, 520, 116, "Level Management Service",
    ["Catálogo y publicación de misterios", "POST /api/levels/  ·  GET /api/levels/{id}"], SERVICE)
box(890, 490, 520, 116, "Playground Engine Service",
    ["Validación del tablero del jugador", "POST /api/engine/validate"], SERVICE)

box(110, 690, 330, 96, "Base de datos relacional",
    ["PostgreSQL", "Niveles, pistas y usuarios"], DATA, tsize=15.5, ssize=12)
box(480, 690, 330, 96, "Object Storage",
    ["Assets / imágenes", "de los misterios"], DATA, tsize=15.5, ssize=12)
box(850, 690, 330, 96, "Bus de Eventos",
    ["Apache Kafka", "level.published · attempt.validated"], MSG, tsize=15.5, ssize=12)
box(1220, 690, 320, 96, "Caché en memoria",
    ["Redis", "Sesiones y estado de partida"], DATA, tsize=15.5, ssize=12)

box(430, 876, 1090, 84, "Observabilidad y Servicio Medido",
    ["Prometheus · Grafana · Logs/Trazas  —  métricas de uso, facturación y autoescalado"], OBS, tsize=17, ssize=12.5)

# ---------- conexiones ----------
edge(600, 244, 600, 342)          # web -> gateway
edge(1040, 244, 1040, 342)        # movil -> gateway
label(820, 300, "HTTPS · REST (JSON)", color="#455A64")

edge(600, 408, 490, 490, ty=452, tx=505)     # gateway -> level
edge(1040, 408, 1150, 490, ty=452, tx=1130)  # gateway -> engine
label(820, 452, "HTTP / REST (JSON) interno", color="#455A64")

edge(400, 606, 275, 690)                                     # level -> postgres
label(285, 650, "SQL · TCP:5432", color="#6A1B9A")
edge(645, 606, 645, 690)                                     # level -> object storage
label(645, 650, "HTTPS · S3 API", color="#6A1B9A")

edge(1300, 606, 1380, 690)                                   # engine -> redis
label(1360, 650, "RESP · 6379", color="#6A1B9A")

edge(700, 606, 940, 690, text="publicar eventos", dashed=True, color="#AD1457",
     ty=632, tx=815)
edge(1150, 606, 1090, 690, text="eventos de validación", dashed=True, color="#AD1457",
     ty=648, tx=1120)
edge(1015, 786, 1015, 876, text="métricas · eventos", dashed=True, color="#AD1457",
     ty=836, tx=1042, anchor="start")

add('</svg>')

out_dir = os.path.dirname(os.path.abspath(__file__))
svg_path = os.path.join(out_dir, "architecture.svg")
with open(svg_path, "w", encoding="utf-8") as f:
    f.write("\n".join(parts))
print("SVG escrito en:", svg_path)
