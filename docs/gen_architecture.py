# -*- coding: utf-8 -*-
"""Genera el diagrama de arquitectura (SVG) del proyecto OpenCrime Cloud-Native."""
import html
import os

W, H = 1800, 1120
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
    f'fill="{INK}">OpenCrime Cloud-Native</text>')
add(f'<text x="90" y="90" font-family="{FONT}" font-size="15.5" fill="{MUTED}">'
    f'Topología actual — SPA, cinco microservicios, PostgreSQL y Redis (Docker Compose)</text>')

# leyenda
add('<rect x="1280" y="26" width="440" height="82" rx="10" fill="#F8FAFC" stroke="#D6DEE6"/>')
add(f'<line x1="1304" y1="54" x2="1362" y2="54" stroke="#455A64" stroke-width="2.4" marker-end="url(#arrowS)"/>')
add(f'<text x="1374" y="58" font-family="{FONT}" font-size="13" fill="{INK}">Comunicación HTTP / REST / SQL / RESP</text>')
add(f'<text x="1304" y="91" font-family="{FONT}" font-size="12.5" fill="{MUTED}">Solo componentes desplegados actualmente</text>')

# ---------- bandas de despliegue ----------
band(80, 125, 1640, 145, "Cliente", "1")
band(80, 295, 1640, 405, "Servicios de aplicación · Docker Compose", "2")
band(80, 725, 1640, 250, "Persistencia", "3")

# ---------- componentes actuales ----------
box(570, 155, 660, 92, "Frontend web (SPA)",
    ["frontend/index.html · contenido estático", "REST/JSON directo a los puertos publicados; sin API Gateway"],
    CLIENTS, tsize=18, ssize=13)

card_y, card_w = 375, 288
cards = [
    (100, "Level Management", ["Catálogo y publicación", "PostgreSQL · host :8000"]),
    (428, "Playground Engine", ["Validación de acusaciones", "Redis · host :8001"]),
    (756, "Progress Service", ["Libretas, sesiones e intentos", "PostgreSQL · host :8004"]),
    (1084, "User Service", ["Registro, login y JWT", "PostgreSQL · host :8002"]),
    (1412, "Rating Service", ["Votos y medias", "PostgreSQL · host :8003"]),
]
for x, title, details in cards:
    box(x, card_y, card_w, 138, title, details, SERVICE, tsize=15, ssize=12)

# La SPA llama directamente a cada servicio; estas flechas no representan un gateway.
for center_x in (244, 572, 900, 1228, 1556):
    edge(900, 247, center_x, card_y)
label(900, 325, "HTTP / REST · JSON · puertos 8000–8004", color="#455A64")

label(900, 555,
      "Llamadas internas HTTP/REST: Playground → Level Management y Progress · User → Progress",
      color="#455A64", size=12)

box(220, 800, 750, 130, "PostgreSQL · TCP 5432",
    ["Datos lógicos de Level, User, Rating y Progress", "Volumen persistente: postgres_data · red db-net (internal)"],
    DATA, tsize=17, ssize=12.5)
box(1110, 800, 500, 130, "Redis · RESP 6379",
    ["Caché / estado de Playground Engine", "Volumen redis_data · AOF · red cache-net (internal)"],
    DATA, tsize=17, ssize=12.5)

# Conexiones de datos: la base relacional la comparten cuatro servicios lógicos.
edge(244, 513, 430, 800)
edge(900, 513, 760, 800)
edge(1228, 513, 850, 800)
edge(1556, 513, 900, 800)
edge(572, 513, 1250, 800)
label(470, 695, "SQL · cuatro servicios", color="#6A1B9A", size=12)
label(1270, 695, "Playground Engine", color="#6A1B9A", size=12)

label(900, 1010,
      "Red backend-net: servicios · db-net: PostgreSQL y clientes DB · cache-net: Redis y Playground Engine",
      color="#455A64", size=12)
label(900, 1050,
      "No incluidos: API Gateway, Kafka, S3/Object Storage, Prometheus/Grafana y cliente móvil",
      color="#8A4B08", size=12)

add('</svg>')

out_dir = os.path.dirname(os.path.abspath(__file__))
svg_path = os.path.join(out_dir, "architecture.svg")
with open(svg_path, "w", encoding="utf-8") as f:
    f.write("\n".join(parts))
print("SVG escrito en:", svg_path)
