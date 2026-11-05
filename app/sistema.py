"""Versión y actualizaciones: /admin/actualizar

La web corre con un usuario sin permisos, así que no instala nada ella misma: deja un aviso
(el archivo «actualizar-ahora») y systemd lanza estanteria-actualizar como root
(estanteria-actualizar.path → estanteria-actualizar.service). El resultado queda en actualizacion.log.
"""
import os
import re
import time

import requests
from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for

from basedatos import DATA, ajuste, poner_ajuste

bp = Blueprint("sistema", __name__, url_prefix="/admin")

REPO = os.environ.get("ESTANTERIA_REPO", "hombrelobo2/biblioteca").strip()
RAMA = os.environ.get("ESTANTERIA_RAMA", "main").strip()
AVISO = os.path.join(DATA, "actualizar-ahora")
LOG = os.path.join(DATA, "actualizacion.log")
CADA = 6 * 3600   # comprobar como mucho cada 6 horas


def db():
    from estanteria import db as _db
    return _db()


def _requiere_admin(f):
    """Como estanteria.requiere_admin, pero sin importar estanteria al cargar este módulo
    (así lo pueden usar también tareas.py y el bot)."""
    from functools import wraps

    @wraps(f)
    def envoltura(*a, **kw):
        from estanteria import requiere_admin
        return requiere_admin(f)(*a, **kw)
    return envoltura


def version_instalada():
    for ruta in (os.path.join(os.path.dirname(__file__), "..", "VERSION"),):
        try:
            return open(ruta).read().strip()
        except OSError:
            pass
    return "0"


def _tupla(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:3]) or (0,)


def es_mas_nueva(remota, local=None):
    return _tupla(remota) > _tupla(local or version_instalada())


def _raw(ruta):
    r = requests.get("https://raw.githubusercontent.com/%s/%s/%s" % (REPO, RAMA, ruta),
                     timeout=10, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    return r.text


def novedades(changelog, desde):
    """Secciones del CHANGELOG más nuevas que la versión instalada: [(versión, texto)]."""
    salida = []
    for m in re.finditer(r"^## \[?(\d+\.\d+(?:\.\d+)?)\]?[^\n]*\n(.*?)(?=^## |\Z)", changelog, re.M | re.S):
        if es_mas_nueva(m.group(1), desde):
            salida.append((m.group(1), m.group(2).strip()))
    return salida


def comprobar(con, forzar=False):
    """Pregunta a GitHub cuál es la última versión (con caché). Devuelve (versión, error)."""
    ultima = ajuste(con, "version_ultima")
    cuando = float(ajuste(con, "version_comprobada", "0") or 0)
    if not forzar and ultima and time.time() - cuando < CADA:
        return ultima, ""
    try:
        ultima = _raw("VERSION").strip()
        poner_ajuste(con, "version_ultima", ultima)
        poner_ajuste(con, "version_comprobada", time.time())
        try:
            poner_ajuste(con, "version_novedades", _raw("CHANGELOG.md")[:20000])
        except requests.RequestException:
            pass
        return ultima, ""
    except requests.RequestException as e:
        return ultima, "No se ha podido consultar GitHub (%s)." % e.__class__.__name__


def atascada():
    """El aviso lleva más de 2 minutos sin que nadie lo recoja: falta estanteria-actualizar.path (versión antigua)."""
    try:
        return time.time() - os.path.getmtime(AVISO) > 120
    except OSError:
        return False


def en_marcha():
    if os.path.exists(AVISO):
        return not atascada()
    try:   # el servicio ya ha recogido el aviso y está trabajando: el registro aún no tiene «FIN»
        return (time.time() - os.path.getmtime(LOG) < 900) and not re.search(r"^FIN \d+", leer_log(), re.M)
    except OSError:
        return False


def leer_log():
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return f.read()[-15000:]
    except OSError:
        return ""


@bp.before_app_request
def _aviso_version():
    # Solo mira lo guardado (no llama a GitHub en cada página)
    if request.path.startswith("/admin") and session.get("admin"):
        try:
            g.version_nueva = ajuste(db(), "version_ultima") if es_mas_nueva(ajuste(db(), "version_ultima")) else ""
        except Exception:  # noqa: BLE001
            g.version_nueva = ""


@bp.get("/actualizar")
@_requiere_admin
def actualizar():
    con = db()
    ultima, error = comprobar(con, forzar=bool(request.args.get("buscar")))
    if error:
        flash(error)
    instalada = version_instalada()
    return render_template("actualizar.html", instalada=instalada, ultima=ultima, nueva=es_mas_nueva(ultima, instalada),
                           novedades=novedades(ajuste(con, "version_novedades"), instalada), repo=REPO,
                           en_marcha=en_marcha(), atascada=atascada(), log=leer_log(),
                           comprobada=time.strftime("%d/%m %H:%M", time.localtime(float(ajuste(con, "version_comprobada", "0") or 0))))


@bp.post("/actualizar")
@_requiere_admin
def actualizar_ya():
    if en_marcha():
        flash("Ya hay una actualización en marcha.")
        return redirect(url_for("sistema.actualizar"))
    if os.path.exists(AVISO):
        os.remove(AVISO)
    with open(AVISO, "w") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        with open(LOG, "w") as f:
            f.write("Esperando a que empiece la actualización…\n")
    except OSError:
        pass
    return redirect(url_for("sistema.actualizar"))


@bp.get("/api/actualizacion")
@_requiere_admin
def estado():
    log = leer_log()
    return jsonify(en_marcha=en_marcha(), atascada=atascada(), log=log, version=version_instalada(),
                   terminado=bool(re.search(r"^FIN \d+", log, re.M)),
                   ok=bool(re.search(r"^FIN 0$", log, re.M)))
