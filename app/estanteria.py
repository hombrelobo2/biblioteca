"""Nuestra Estantería — aplicación para servidor propio (LXC + nginx + SQLite).

Parte pública: nginx sirve index.html y las portadas; esta aplicación genera
libros.json y resumenes.json desde la base de datos (mismo formato que antes).
Parte privada (/admin): bibliotecas, libros y la página de nuevas entradas.
"""
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from functools import wraps

import requests
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for, Response)
from PIL import Image
from werkzeug.security import check_password_hash, generate_password_hash

import busqueda
import completar
import ia
import prestamos

from basedatos import DATA, DB_PATH, PORTADAS, ajuste, conectar, iniciar_db, nuevo_id, poner_ajuste  # noqa: F401
MAX_PORTADA = 8 * 1024 * 1024

app = Flask(__name__)
# Detrás de nginx (y de Nginx Proxy Manager): fiarse de X-Forwarded-Proto para saber si la visita llega por HTTPS
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0)


class SesionSegura(SecureCookieSessionInterface):
    """La cookie lleva la marca «Secure» cuando la visita llega por HTTPS (la web pública),
    y no cuando entras desde casa por http://IP (la administración)."""
    def get_cookie_secure(self, app_):
        return app_.config["SESSION_COOKIE_SECURE"] or request.is_secure


app.session_interface = SesionSegura()
ADMIN_HORAS = 12
app.config.update(
    SECRET_KEY=os.environ.get("ESTANTERIA_SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("ESTANTERIA_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,   # familias: 30 días; administración: 12 horas (ver abajo)
    MAX_CONTENT_LENGTH=12 * 1024 * 1024,
)

# ---------------- Base de datos ----------------

def db():
    if "db" not in g:
        g.db = conectar()
    return g.db


@app.teardown_appcontext
def cerrar(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


# ---------------- Seguridad ----------------

def requiere_admin(f):
    @wraps(f)
    def envoltura(*a, **kw):
        if (not session.get("admin") or time.time() - session.get("admin_desde", 0) > ADMIN_HORAS * 3600
                or session.get("admin_ver", "") != ajuste(db(), "admin_ver")):   # al cambiar la contraseña se cierran las demás sesiones
            session.pop("admin", None)
            return redirect(url_for("login", siguiente=request.path))
        return f(*a, **kw)
    return envoltura


def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


app.jinja_env.globals["csrf"] = token_csrf


MAX_RESTAURAR = 500 * 1024 * 1024


@app.before_request
def comprobar_csrf():
    if request.endpoint == "copia_restaurar":
        request.max_content_length = MAX_RESTAURAR   # la copia .zip lleva todas las portadas
    if request.method == "POST" and request.endpoint != "login":
        enviado = request.form.get("csrf") or request.headers.get("X-CSRF") or ""
        if not session.get("csrf") or not hmac.compare_digest(enviado, session["csrf"]):
            abort(400, "Formulario caducado. Vuelve a cargar la página.")


@app.after_request
def cabeceras(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    if request.path.startswith(("/admin", "/cuenta")):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


# ---------------- Parte pública (JSON) ----------------

def _json_publico(datos):
    cuerpo = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
    etag = '"%s"' % hashlib.md5(cuerpo.encode()).hexdigest()[:16]
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})
    return Response(cuerpo, mimetype="application/json", headers={"ETag": etag, "Cache-Control": "no-cache"})


@app.get("/libros.json")
def libros_json():
    c = db()
    bibs = [{"id": r["id"], "nombre": r["nombre"], "presta": bool(r["presta"] and r["familia_id"])}
            for r in c.execute("SELECT id, nombre, presta, familia_id FROM bibliotecas ORDER BY orden, creada")]
    estados = prestamos.estados_publicos(c)
    libros = []
    for r in c.execute("SELECT * FROM libros WHERE perdido = 0 ORDER BY creado, id"):
        d = {"id": r["id"], "biblioteca": r["biblioteca_id"], "titulo": r["titulo"], "autor": r["autor"],
             "coleccion": r["coleccion"], "numero": r["numero"], "paginas": r["paginas"],
             "portada": ("portadas/" + r["portada"]) if r["portada"] else ""}
        e = estados.get(r["id"])
        if e:   # sin nombres: solo si está prestado y hacia cuándo vuelve
            d["prestado"] = 1 if e["prestado"] else 0
            d["pedido"] = 0 if e["prestado"] else 1
            if e["vuelve"]:
                d["vuelve"] = e["vuelve"]
        libros.append(d)
    return _json_publico({"bibliotecas": bibs, "libros": libros})


@app.get("/resumenes.json")
def resumenes_json():
    filas = db().execute("SELECT id, resumen FROM libros WHERE resumen <> ''")
    return _json_publico({r["id"]: r["resumen"] for r in filas})


# ---------------- Acceso ----------------

_fallos = {}


def hash_admin():
    """La contraseña cambiada desde la web se guarda en la base de datos y manda sobre la del .env.
    (estanteria-cambiar-clave la borra de la base de datos: sirve para recuperar el acceso.)"""
    return ajuste(db(), "admin_hash") or os.environ.get("ESTANTERIA_ADMIN_HASH", "")


@app.post("/admin/clave")
@requiere_admin
def cambiar_clave_admin():
    actual, c1, c2 = request.form.get("actual", ""), request.form.get("clave", ""), request.form.get("clave2", "")
    if not check_password_hash(hash_admin(), actual):
        time.sleep(1)
        flash("La contraseña actual no es correcta.")
    elif len(c1) < 8 or c1 != c2:
        flash("La nueva contraseña tiene que tener al menos 8 caracteres y escribirse igual dos veces.")
    else:
        poner_ajuste(db(), "admin_hash", generate_password_hash(c1))
        ver = secrets.token_hex(8)
        poner_ajuste(db(), "admin_ver", ver)
        session["admin_ver"] = ver
        flash("Contraseña de administración cambiada. Se han cerrado las sesiones abiertas en otros aparatos.")
    return redirect(url_for("adminp.familias") + "#clave")


@app.route("/admin/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.headers.get("X-Real-IP") or request.remote_addr or "?"
        n, desde = _fallos.get(ip, (0, time.time()))
        if n >= 8 and time.time() - desde < 900:
            abort(429, "Demasiados intentos. Espera 15 minutos.")
        clave = request.form.get("clave", "")
        hash_ok = hash_admin()
        if hash_ok and check_password_hash(hash_ok, clave):
            _fallos.pop(ip, None)
            familia_sesion = session.get("familia")
            session.clear()
            session.permanent = True
            session["admin"] = True
            session["admin_desde"] = time.time()
            session["admin_ver"] = ajuste(db(), "admin_ver")
            if familia_sesion:
                session["familia"] = familia_sesion
            sig = request.args.get("siguiente", "")
            return redirect(sig if sig.startswith("/admin") else url_for("panel"))
        _fallos[ip] = (n + 1, desde if n else time.time())
        time.sleep(1)
        flash("Contraseña incorrecta.")
    return render_template("entrar.html")


@app.post("/admin/salir")
def salir():
    session.pop("admin", None)
    session.pop("admin_desde", None)
    return redirect(url_for("login"))


# ---------------- Panel ----------------

@app.get("/admin/")
@requiere_admin
def panel():
    filas = db().execute("""SELECT b.id, b.nombre, COUNT(l.id) AS n,
                            SUM(CASE WHEN l.resumen <> '' THEN 1 ELSE 0 END) AS con_resumen,
                            SUM(CASE WHEN l.resumen_origen = 'ia' THEN 1 ELSE 0 END) AS ia_sin_revisar,
                            SUM(CASE WHEN l.portada <> '' THEN 1 ELSE 0 END) AS con_portada
                            FROM bibliotecas b LEFT JOIN libros l ON l.biblioteca_id = b.id
                            GROUP BY b.id ORDER BY b.nombre COLLATE NOCASE""").fetchall()
    return render_template("panel.html", bibliotecas=filas)


@app.post("/admin/biblioteca/nueva")
@requiere_admin
def biblioteca_nueva():
    nombre = request.form.get("nombre", "").strip()[:60]
    if not nombre:
        flash("Escribe el nombre de la biblioteca.")
        return redirect(url_for("panel"))
    bid = crear_biblioteca(nombre)
    flash("Biblioteca «%s» creada." % nombre)
    return redirect(url_for("biblioteca", bid=bid))


def crear_biblioteca(nombre):
    c = db()
    orden = (c.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM bibliotecas").fetchone()[0])
    bid = nuevo_id("l")
    c.execute("INSERT INTO bibliotecas (id, nombre, orden) VALUES (?, ?, ?)", (bid, nombre, orden))
    c.commit()
    return bid


@app.get("/admin/biblioteca/<bid>")
@requiere_admin
def biblioteca(bid):
    b = db().execute("SELECT * FROM bibliotecas WHERE id = ?", (bid,)).fetchone() or abort(404)
    libros = db().execute("""SELECT * FROM libros WHERE biblioteca_id = ?
                             ORDER BY coleccion = '', coleccion COLLATE NOCASE, CAST(numero AS INTEGER), titulo COLLATE NOCASE""",
                          (bid,)).fetchall()
    familias = db().execute("SELECT id, nombre, activa FROM familias ORDER BY nombre COLLATE NOCASE").fetchall()
    abiertos = {r["libro_id"]: r for r in db().execute(
        "SELECT p.libro_id, p.estado, p.vence, f.nombre AS prestataria FROM prestamos p JOIN familias f ON f.id = p.prestataria_id "
        "WHERE p.estado IN (%s)" % ",".join("'%s'" % e for e in prestamos.ABIERTOS))}
    return render_template("biblioteca.html", b=b, libros=libros, familias=familias, abiertos=abiertos,
                           nombres_estado=prestamos.NOMBRES_ESTADO)


@app.post("/admin/biblioteca/<bid>/familia")
@requiere_admin
def biblioteca_familia(bid):
    db().execute("SELECT id FROM bibliotecas WHERE id = ?", (bid,)).fetchone() or abort(404)
    fid = request.form.get("familia_id", "")
    if fid and not db().execute("SELECT id FROM familias WHERE id = ?", (fid,)).fetchone():
        abort(400)
    db().execute("UPDATE bibliotecas SET familia_id = ?, presta = ? WHERE id = ?",
                 (fid, 1 if request.form.get("presta") else 0, bid))
    db().commit()
    flash("Guardado.")
    return redirect(url_for("biblioteca", bid=bid))


@app.post("/admin/biblioteca/<bid>/renombrar")
@requiere_admin
def biblioteca_renombrar(bid):
    nombre = request.form.get("nombre", "").strip()[:60]
    if nombre:
        db().execute("UPDATE bibliotecas SET nombre = ? WHERE id = ?", (nombre, bid))
        db().commit()
        flash("Nombre cambiado.")
    return redirect(url_for("biblioteca", bid=bid))


@app.post("/admin/biblioteca/<bid>/borrar")
@requiere_admin
def biblioteca_borrar(bid):
    b = db().execute("SELECT nombre FROM bibliotecas WHERE id = ?", (bid,)).fetchone() or abort(404)
    if request.form.get("confirmar", "").strip() != b["nombre"]:
        flash("Para borrar, escribe el nombre exacto de la biblioteca.")
        return redirect(url_for("biblioteca", bid=bid))
    for r in db().execute("SELECT portada FROM libros WHERE biblioteca_id = ?", (bid,)).fetchall():
        borrar_portada(r["portada"])
    db().execute("DELETE FROM bibliotecas WHERE id = ?", (bid,))
    db().commit()
    flash("Biblioteca «%s» borrada." % b["nombre"])
    return redirect(url_for("panel"))


# ---------------- Libros ----------------

CAMPOS = ("titulo", "autor", "coleccion", "numero", "paginas", "resumen", "isbn")
LIMITES = {"titulo": 200, "autor": 150, "coleccion": 100, "numero": 6, "paginas": 5, "resumen": 600, "isbn": 13}


def limpiar_campos(d):
    out = {}
    for k in CAMPOS:
        v = re.sub(r"\s+", " ", str(d.get(k) or "")).strip()
        if k == "paginas":
            v = v if v.isdigit() and 0 < int(v) < 5000 else ""
        if k == "isbn":
            v = busqueda.limpiar_isbn(v)
        v = v[:LIMITES[k]]
        out[k] = v
    return out


@app.route("/admin/libro/<lid>", methods=["GET", "POST"])
@requiere_admin
def libro(lid):
    c = db()
    l = c.execute("SELECT * FROM libros WHERE id = ?", (lid,)).fetchone() or abort(404)
    if request.method == "POST":
        d = limpiar_campos(request.form)
        if not d["titulo"]:
            flash("El título es obligatorio.")
            return redirect(url_for("libro", lid=lid))
        portada = l["portada"]
        try:
            if request.files.get("portada_archivo") and request.files["portada_archivo"].filename:
                nueva = guardar_portada(lid, request.files["portada_archivo"].read())
                borrar_portada(portada if portada != nueva else "")
                portada = nueva
            elif request.form.get("portada_url", "").strip():
                nueva = descargar_portada(lid, request.form["portada_url"].strip())
                borrar_portada(portada if portada != nueva else "")
                portada = nueva
            elif request.form.get("quitar_portada"):
                borrar_portada(portada)
                portada = ""
        except ValueError as e:
            flash(str(e))
        bib = request.form.get("biblioteca_id") or l["biblioteca_id"]
        if not c.execute("SELECT 1 FROM bibliotecas WHERE id = ?", (bib,)).fetchone():
            bib = l["biblioteca_id"]
        origen = l["resumen_origen"]
        if not d["resumen"]:
            origen = ""
        elif d["resumen"] != l["resumen"] or request.form.get("revisado"):
            origen = "revisado" if origen == "ia" else origen   # cambiado o marcado a mano: ya no es «IA sin revisar»
        c.execute("""UPDATE libros SET titulo=?, autor=?, coleccion=?, numero=?, paginas=?, resumen=?, isbn=?,
                     portada=?, biblioteca_id=?, resumen_origen=?, perdido=? WHERE id=?""",
                  (d["titulo"], d["autor"], d["coleccion"], d["numero"], d["paginas"], d["resumen"], d["isbn"],
                   portada, bib, origen, 1 if request.form.get("perdido") else 0, lid))
        c.commit()
        flash("Libro guardado.")
        return redirect(url_for("biblioteca", bid=bib))
    bibs = c.execute("SELECT id, nombre FROM bibliotecas ORDER BY nombre COLLATE NOCASE").fetchall()
    return render_template("libro.html", l=l, bibliotecas=bibs)


@app.post("/admin/libro/<lid>/borrar")
@requiere_admin
def libro_borrar(lid):
    l = db().execute("SELECT * FROM libros WHERE id = ?", (lid,)).fetchone() or abort(404)
    borrar_portada(l["portada"])
    db().execute("DELETE FROM libros WHERE id = ?", (lid,))
    db().commit()
    flash("«%s» borrado." % l["titulo"])
    return redirect(url_for("biblioteca", bid=l["biblioteca_id"]))


# ---------------- Portadas ----------------

def guardar_portada(lid, datos):
    """Guarda la imagen tal cual si es JPEG (sin recomprimir); si no, la pasa a JPEG."""
    if len(datos) > MAX_PORTADA:
        raise ValueError("La imagen es demasiado grande (máx. 8 MB).")
    try:
        im = Image.open(io.BytesIO(datos))
        im.verify()
        im = Image.open(io.BytesIO(datos))
    except Exception:
        raise ValueError("Ese archivo no es una imagen válida.")
    if im.width < 40 or im.height < 40:
        raise ValueError("La imagen es demasiado pequeña para ser una portada.")
    nombre = "%s-%s.jpg" % (lid, uuid.uuid4().hex[:6])
    ruta = os.path.join(PORTADAS, nombre)
    if im.format == "JPEG":
        with open(ruta, "wb") as f:
            f.write(datos)
    else:
        im.convert("RGB").save(ruta, "JPEG", quality=92)
    return nombre


def descargar_portada(lid, url):
    if not re.match(r"^https://", url):
        raise ValueError("La dirección de la portada tiene que empezar por https://")
    try:
        r = requests.get(url, headers=busqueda.UA, timeout=15, stream=True)
        r.raise_for_status()
        datos = r.raw.read(MAX_PORTADA + 1, decode_content=True)
    except requests.RequestException:
        raise ValueError("No se ha podido descargar la portada.")
    if "elkarbanaketa" in url and len(datos) < 6000:
        raise ValueError("Elkar no tiene la portada de este libro.")
    return guardar_portada(lid, datos)


def borrar_portada(nombre):
    if nombre and re.fullmatch(r"[A-Za-z0-9_.\-]+", nombre):
        try:
            os.remove(os.path.join(PORTADAS, nombre))
        except FileNotFoundError:
            pass


# ---------------- Nuevas entradas ----------------

@app.get("/admin/nuevas")
@requiere_admin
def nuevas():
    bibs = db().execute("SELECT id, nombre FROM bibliotecas ORDER BY nombre COLLATE NOCASE").fetchall()
    titulos = {}
    for r in db().execute("SELECT biblioteca_id, titulo FROM libros"):
        titulos.setdefault(r["biblioteca_id"], []).append(r["titulo"])
    return render_template("nuevas.html", bibliotecas=bibs, elegida=request.args.get("b", ""),
                           ia_lista=ia.configurada(), colecciones=colecciones_existentes(), titulos=titulos)


def colecciones_existentes():
    """Nombres de colección ya usados, con la grafía más repetida (para unificar 'las ratitas' -> 'Las Ratitas')."""
    filas = db().execute("""SELECT coleccion, COUNT(*) AS n FROM libros WHERE coleccion <> ''
                            GROUP BY coleccion ORDER BY n DESC""").fetchall()
    vistas, salida = set(), []
    for f in filas:
        clave = busqueda._norm(f["coleccion"])
        if clave and clave not in vistas:
            vistas.add(clave)
            salida.append(f["coleccion"])
    return sorted(salida, key=str.lower)


HOSTS_IMAGEN = ("web.elkarbanaketa.eus", "static.cegal.es", "covers.openlibrary.org", "books.google.com")


@app.get("/admin/api/imagen")
@requiere_admin
def api_imagen():
    """Muestra en la página de nuevas entradas portadas de webs que no dejan enlazarlas directamente."""
    url = request.args.get("u", "")
    m = re.match(r"^https://([^/]+)/", url)
    if not m or m.group(1) not in HOSTS_IMAGEN:
        abort(404)
    try:
        r = requests.get(url, headers=busqueda.UA, timeout=15, stream=True)
        datos = r.raw.read(MAX_PORTADA, decode_content=True)
    except requests.RequestException:
        abort(404)
    if r.status_code != 200 or not r.headers.get("Content-Type", "").startswith("image/"):
        abort(404)
    return Response(datos, mimetype=r.headers["Content-Type"], headers={"Cache-Control": "private, max-age=86400"})


@app.post("/admin/api/foto")
@requiere_admin
def api_foto():
    foto = request.files.get("foto")
    if not foto or not foto.filename:
        return jsonify({"ok": False, "error": "No has elegido ninguna foto."}), 400
    try:
        libros = ia.leer_lomos(foto.read(), colecciones_existentes())
    except ia.ErrorIA as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    return jsonify({"ok": True, "libros": libros})


@app.post("/admin/api/resumir")
@requiere_admin
def api_resumir():
    libros = (request.get_json(silent=True) or {}).get("libros") or []
    if not ia.configurada():
        return jsonify({"ok": False, "error": "Sin clave de Gemini."}), 400
    try:
        return jsonify({"ok": True, "resumenes": ia.resumir(libros)})
    except ia.ErrorIA as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.get("/admin/api/buscar")
@requiere_admin
def api_buscar():
    linea = request.args.get("q", "").strip()[:200]
    if not linea:
        return jsonify({"candidatos": [], "errores": []})
    return jsonify(busqueda.buscar(linea))


@app.post("/admin/api/guardar")
@requiere_admin
def api_guardar():
    datos = request.get_json(silent=True) or {}
    c = db()
    bid = datos.get("biblioteca_id", "")
    nombre_nueva = str(datos.get("biblioteca_nueva", "")).strip()[:60]
    if nombre_nueva:
        bid = crear_biblioteca(nombre_nueva)
    elif not c.execute("SELECT 1 FROM bibliotecas WHERE id = ?", (bid,)).fetchone():
        return jsonify({"ok": False, "error": "Elige una biblioteca."}), 400
    guardados, avisos = [], []
    for item in (datos.get("libros") or [])[:100]:
        d = limpiar_campos(item)
        if not d["titulo"]:
            continue
        lid = nuevo_id("n")
        portada = ""
        url = str(item.get("portada_url") or "").strip()
        if url:
            try:
                portada = descargar_portada(lid, url)
            except ValueError as e:
                avisos.append("%s: %s" % (d["titulo"], e))
        origen = "ia" if d["resumen"] and item.get("resumen_origen") == "ia" else ""
        c.execute("""INSERT INTO libros (id, biblioteca_id, titulo, autor, coleccion, numero, paginas, resumen, isbn, portada,
                     fuente, resumen_origen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (lid, bid, d["titulo"], d["autor"], d["coleccion"], d["numero"], d["paginas"], d["resumen"],
                   d["isbn"], portada, str(item.get("fuente") or "")[:40], origen))
        guardados.append(lid)
    c.commit()
    flash("Guardados %d libros." % len(guardados) + (" Avisos: " + " · ".join(avisos) if avisos else ""))
    return jsonify({"ok": True, "biblioteca_id": bid, "guardados": len(guardados), "avisos": avisos,
                    "url": url_for("biblioteca", bid=bid)})


# ---------------- Completar datos que faltan ----------------

def _libros_ambito(bid, todos=False):
    sql = "SELECT * FROM libros WHERE 1=1"
    args = []
    if bid:
        sql += " AND biblioteca_id = ?"
        args.append(bid)
    if not todos:
        sql += " AND completado = ''"
    sql += " ORDER BY biblioteca_id, coleccion COLLATE NOCASE, CAST(numero AS INTEGER), titulo COLLATE NOCASE"
    return [l for l in db().execute(sql, args).fetchall() if completar.faltas(l)]


@app.get("/admin/completar")
@requiere_admin
def completar_pagina():
    bid = request.args.get("b", "")
    b = db().execute("SELECT * FROM bibliotecas WHERE id = ?", (bid,)).fetchone() if bid else None
    if bid and not b:
        abort(404)
    cuenta = {"resumen": 0, "numero": 0, "paginas": 0, "portada": 0}
    pendientes = _libros_ambito(bid)
    todos = _libros_ambito(bid, todos=True)
    for l in todos:                      # lo que falta en total (revisado antes o no)
        for f in completar.faltas(l):
            cuenta[f] += 1
    ya = len(todos) - len(pendientes)
    return render_template("completar.html", b=b, cuenta=cuenta, total=len(pendientes), ya_revisados=ya,
                           ia_lista=ia.configurada())


@app.get("/admin/api/completar/pendientes")
@requiere_admin
def api_completar_pendientes():
    que = set(request.args.get("que", "resumen,numero,paginas,portada").split(","))
    libros = _libros_ambito(request.args.get("b", ""), todos=request.args.get("todos") == "1")
    return jsonify({"ids": [l["id"] for l in libros if set(completar.faltas(l)) & que]})


@app.post("/admin/api/completar/propuestas")
@requiere_admin
def api_completar_propuestas():
    datos = request.get_json(silent=True) or {}
    ids = [str(i) for i in (datos.get("ids") or [])][:10]
    que = tuple(q for q in (datos.get("que") or []) if q in ("resumen", "numero", "paginas", "portada"))
    if not ids:
        return jsonify({"propuestas": []})
    marcas = ",".join("?" * len(ids))
    filas = {l["id"]: l for l in db().execute("SELECT * FROM libros WHERE id IN (%s)" % marcas, ids)}
    libros = [filas[i] for i in ids if i in filas]
    return jsonify({"propuestas": completar.proponer(libros, que or ("resumen", "numero", "paginas", "portada"))})


@app.post("/admin/api/completar/guardar")
@requiere_admin
def api_completar_guardar():
    datos = request.get_json(silent=True) or {}
    c = db()
    hechos, avisos = 0, []
    for cambio in (datos.get("cambios") or [])[:20]:
        l = c.execute("SELECT * FROM libros WHERE id = ?", (str(cambio.get("id", "")),)).fetchone()
        if not l:
            continue
        d = limpiar_campos({k: cambio.get(k, "") for k in ("titulo", "resumen", "numero", "paginas", "isbn")})
        nuevos = {}
        # Solo se rellenan campos vacíos: lo que ya estaba escrito no se toca
        for campo in ("resumen", "numero", "paginas", "isbn"):
            if d[campo] and not l[campo]:
                nuevos[campo] = d[campo]
        if "resumen" in nuevos:
            nuevos["resumen_origen"] = "revisado" if cambio.get("resumen_editado") else "ia"
        url = str(cambio.get("portada_url") or "").strip()
        if url and not l["portada"]:
            try:
                nuevos["portada"] = descargar_portada(l["id"], url)
            except ValueError as e:
                avisos.append("%s: %s" % (l["titulo"], e))
        if nuevos:
            c.execute("UPDATE libros SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in nuevos),
                      list(nuevos.values()) + [l["id"]])
            hechos += 1
    revisados = [str(i) for i in (datos.get("revisados") or [])][:20]
    if revisados:
        c.execute("UPDATE libros SET completado = datetime('now') WHERE id IN (%s)" % ",".join("?" * len(revisados)),
                  revisados)
    c.commit()
    return jsonify({"ok": True, "actualizados": hechos, "avisos": avisos})


# ---------------- Colecciones ----------------

@app.get("/admin/colecciones")
@requiere_admin
def colecciones():
    filas = db().execute("""SELECT coleccion, COUNT(*) AS n, COUNT(DISTINCT biblioteca_id) AS bibs,
                            SUM(CASE WHEN numero = '' THEN 1 ELSE 0 END) AS sin_numero
                            FROM libros WHERE coleccion <> '' GROUP BY coleccion
                            ORDER BY coleccion COLLATE NOCASE""").fetchall()
    grupos = {}
    for f in filas:
        grupos.setdefault(busqueda._norm(f["coleccion"]), []).append(f)
    # Colecciones escritas de varias maneras («Las Ratitas» y «las ratitas»): se proponen para unificar
    repetidas = [sorted(g, key=lambda f: -f["n"]) for g in grupos.values() if len(g) > 1]
    return render_template("colecciones.html", colecciones=filas, repetidas=repetidas)


@app.post("/admin/colecciones/renombrar")
@requiere_admin
def colecciones_renombrar():
    nuevo = re.sub(r"\s+", " ", request.form.get("a", "")).strip()[:100]
    viejos = [v for v in request.form.getlist("de") if v]
    if not nuevo or not viejos:
        flash("Falta el nombre de la colección.")
        return redirect(url_for("colecciones"))
    marcas = ",".join("?" * len(viejos))
    n = db().execute("UPDATE libros SET coleccion = ? WHERE coleccion IN (%s)" % marcas, [nuevo] + viejos).rowcount
    db().commit()
    flash("Colección «%s»: %d libros actualizados." % (nuevo, n))
    return redirect(url_for("colecciones"))


# ---------------- Copia de seguridad ----------------

@app.get("/admin/copia")
@requiere_admin
def copia():
    import tempfile
    import zipfile
    tmp = tempfile.TemporaryDirectory()
    ruta_db = os.path.join(tmp.name, "estanteria.db")
    destino = sqlite3.connect(ruta_db)
    db().backup(destino)          # copia coherente aunque la aplicación esté en uso
    destino.close()
    ruta_zip = os.path.join(tmp.name, "copia.zip")
    with zipfile.ZipFile(ruta_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ruta_db, "estanteria.db")
        for nombre in sorted(os.listdir(PORTADAS)):
            z.write(os.path.join(PORTADAS, nombre), "portadas/" + nombre, compress_type=zipfile.ZIP_STORED)
        z.writestr("LEEME.txt", "Copia de Nuestra Estantería (%s).\n\nPara restaurarla en el contenedor:\n"
                   "  systemctl stop estanteria\n"
                   "  cp estanteria.db /var/lib/estanteria/estanteria.db\n"
                   "  cp portadas/* /var/lib/estanteria/portadas/\n"
                   "  chown -R estanteria:estanteria /var/lib/estanteria\n"
                   "  systemctl start estanteria\n" % time.strftime("%Y-%m-%d %H:%M"))
    with open(ruta_zip, "rb") as f:
        datos = f.read()
    tmp.cleanup()
    nombre = "estanteria-copia-%s.zip" % time.strftime("%Y-%m-%d")
    return Response(datos, mimetype="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % nombre})


def _es_sqlite(ruta):
    with open(ruta, "rb") as f:
        return f.read(16) == b"SQLite format 3\x00"


@app.post("/admin/copia/restaurar")
@requiere_admin
def copia_restaurar():
    """Importa una copia: el .zip del botón «Descargar copia», o la base de datos sola (.db o .db.gz, la de Telegram)."""
    import gzip
    import shutil
    import tempfile
    import zipfile
    archivo = request.files.get("copia")
    if not archivo or not archivo.filename:
        flash("Elige el archivo de la copia.")
        return redirect(url_for("panel") + "#copia")
    if not request.form.get("confirmar"):
        flash("Marca la casilla para confirmar que quieres sustituir los datos actuales.")
        return redirect(url_for("panel") + "#copia")
    tmp = tempfile.mkdtemp()
    try:
        subida = os.path.join(tmp, "subida")
        archivo.save(subida)
        ruta_db, portadas = None, []
        nombre = archivo.filename.lower()
        if zipfile.is_zipfile(subida):
            with zipfile.ZipFile(subida) as z:
                nombres = z.namelist()
                if "estanteria.db" not in nombres:
                    raise ValueError("El .zip no tiene estanteria.db. ¿Es una copia de Nuestra Estantería?")
                ruta_db = os.path.join(tmp, "estanteria.db")
                with z.open("estanteria.db") as a, open(ruta_db, "wb") as b:
                    shutil.copyfileobj(a, b)
                for n in nombres:
                    base = os.path.basename(n)
                    if (n.startswith("portadas/") and base and not base.startswith(".")
                            and re.fullmatch(r"[\w.-]+\.(jpe?g|png|webp|gif)", base, re.I)):
                        portadas.append((n, base))
                if portadas:
                    carpeta = os.path.join(tmp, "portadas")
                    os.makedirs(carpeta)
                    for n, base in portadas:
                        with z.open(n) as a, open(os.path.join(carpeta, base), "wb") as b:
                            shutil.copyfileobj(a, b)
        elif nombre.endswith(".gz"):
            ruta_db = os.path.join(tmp, "estanteria.db")
            with gzip.open(subida) as a, open(ruta_db, "wb") as b:
                shutil.copyfileobj(a, b)
        else:
            ruta_db = subida
        if not _es_sqlite(ruta_db):
            raise ValueError("El archivo no es una copia válida (se espera .zip, .db o .db.gz).")
        origen = sqlite3.connect(ruta_db)
        try:
            tablas = {r[0] for r in origen.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"bibliotecas", "libros"} <= tablas:
                raise ValueError("Esa base de datos no es de Nuestra Estantería.")
            if origen.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("La copia está dañada (falla la comprobación de SQLite).")
            n_libros = origen.execute("SELECT COUNT(*) FROM libros").fetchone()[0]
            # Antes de sustituir nada: copia de lo que hay ahora
            seguridad = os.path.join(DATA, "copias")
            os.makedirs(seguridad, exist_ok=True)
            antes = os.path.join(seguridad, "antes-de-restaurar-%s.db" % time.strftime("%Y-%m-%d-%H%M%S"))
            destino = sqlite3.connect(antes)
            db().backup(destino)
            destino.close()
            for viejo in sorted(f for f in os.listdir(seguridad) if f.startswith("antes-de-restaurar-"))[:-5]:
                os.remove(os.path.join(seguridad, viejo))
            # Sustituir la base de datos en uso (copia página a página, segura aunque haya otras conexiones)
            origen.backup(db())
        finally:
            origen.close()
        iniciar_db()   # por si la copia es de una versión anterior: añade tablas y columnas nuevas
        copiadas = 0
        for _, base in portadas:
            shutil.copyfile(os.path.join(tmp, "portadas", base), os.path.join(PORTADAS, base))
            copiadas += 1
        session["admin_ver"] = ajuste(db(), "admin_ver")   # seguir dentro aunque la copia traiga otra contraseña
        flash("Copia importada: %d libros%s. Lo que había antes está guardado en %s."
              % (n_libros, (" y %d portadas" % copiadas) if copiadas else " (sin portadas: solo la base de datos)", antes))
    except (ValueError, zipfile.BadZipFile, OSError, EOFError, sqlite3.DatabaseError) as e:
        flash("No se ha podido importar: %s" % e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return redirect(url_for("panel") + "#copia")


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(429)
def error(e):
    return render_template("error.html", e=e), e.code


iniciar_db()

# Zona de familias (/cuenta) y administración de familias y préstamos (/admin/familias, /admin/prestamos)
import cuenta  # noqa: E402
import admin_prestamos  # noqa: E402
import sistema  # noqa: E402

app.register_blueprint(cuenta.bp)
app.register_blueprint(admin_prestamos.bp)
app.register_blueprint(sistema.bp)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
