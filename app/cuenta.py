"""Zona de familias: /cuenta

Cada familia tiene una cuenta (la crea la administración y manda un enlace de invitación).
Desde aquí ven lo que tienen prestado, lo que han prestado, lo que les toca confirmar y su historial.
"""
import time
from datetime import datetime
from functools import wraps

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import prestamos as P
import telegram
from basedatos import ajuste, crear_token, gastar_token, poner_ajuste, ver_token

bp = Blueprint("cuenta", __name__, url_prefix="/cuenta")


def db():
    from estanteria import db as _db
    return _db()


def familia_actual():
    if "familia_obj" in g:
        return g.familia_obj
    f = None
    fid = session.get("familia")
    if fid:
        f = db().execute("SELECT * FROM familias WHERE id = ? AND activa = 1", (fid,)).fetchone()
        if not f:
            session.pop("familia", None)
    g.familia_obj = f
    return f


def requiere_familia(f):
    @wraps(f)
    def envoltura(*a, **kw):
        if not familia_actual():
            if request.path.startswith("/cuenta/api/"):
                return jsonify(ok=False, error="Tienes que entrar con tu cuenta."), 401
            return redirect(url_for("cuenta.entrar"))
        return f(*a, **kw)
    return envoltura


@bp.app_context_processor
def _contexto():
    return {"familia": familia_actual() if request.path.startswith("/cuenta") else None,
            "P": P, "fecha_bonita": P.fecha_bonita}


def nombre_bot():
    """El @usuario del bot (se pregunta una vez a Telegram y se guarda)."""
    con = db()
    u = ajuste(con, "telegram_bot_usuario")
    if not u and telegram.configurado():
        yo = telegram.llamar("getMe")
        if yo and yo.get("username"):
            u = yo["username"]
            poner_ajuste(con, "telegram_bot_usuario", u)
    return u


def enlace_telegram(familia_id):
    bot = nombre_bot()
    if not bot:
        return ""
    return "https://t.me/%s?start=%s" % (bot, crear_token(db(), "telegram", familia_id, 24))


# ---------------- Entrar y salir ----------------

_fallos = {}


@bp.route("/entrar", methods=["GET", "POST"])
def entrar():
    if familia_actual() and request.method == "GET":
        return redirect(url_for("cuenta.inicio"))
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        # Detrás de Nginx Proxy Manager todas las visitas llegan con la misma IP: se cuenta por IP y usuario
        ip = "%s|%s" % (request.headers.get("X-Real-IP") or request.remote_addr or "?", usuario.lower())
        n, desde = _fallos.get(ip, (0, time.time()))
        if n >= 10 and time.time() - desde < 900:
            abort(429, "Demasiados intentos. Espera 15 minutos.")
        clave = request.form.get("clave", "")
        f = db().execute("SELECT * FROM familias WHERE usuario = ? AND activa = 1", (usuario,)).fetchone()
        if f and f["clave_hash"] and check_password_hash(f["clave_hash"], clave):
            _fallos.pop(ip, None)
            _iniciar_sesion(f["id"])
            sig = request.args.get("siguiente", "")
            return redirect(sig if sig.startswith("/") and not sig.startswith("//") else url_for("cuenta.inicio"))
        _fallos[ip] = (n + 1, desde if n else time.time())
        time.sleep(1)
        flash("Usuario o contraseña incorrectos.")
    return render_template("cuenta_entrar.html")


def _iniciar_sesion(fid):
    admin = {k: session[k] for k in ("admin", "admin_desde") if k in session}
    session.clear()
    session.update(admin)
    session.permanent = True
    session["familia"] = fid
    db().execute("UPDATE familias SET ultimo_acceso = ? WHERE id = ?", (datetime.now().isoformat(" ", "seconds"), fid))
    db().commit()


@bp.post("/salir")
def salir():
    session.pop("familia", None)
    return redirect("/")


@bp.route("/invitacion/<token>", methods=["GET", "POST"])
def invitacion(token):
    """Enlace que manda la administración: la familia elige su contraseña (también sirve para cambiarla si se olvida)."""
    fid = ver_token(db(), token, "invitacion")
    f = db().execute("SELECT * FROM familias WHERE id = ? AND activa = 1", (fid,)).fetchone() if fid else None
    if not f:
        return render_template("error.html", e=type("E", (), {"code": 404, "description":
                               "Este enlace ya se ha usado o ha caducado. Pide uno nuevo a quien lleva la estantería."})()), 404
    if request.method == "POST":
        c1, c2 = request.form.get("clave", ""), request.form.get("clave2", "")
        if len(c1) < 8:
            flash("La contraseña tiene que tener al menos 8 caracteres.")
        elif c1 != c2:
            flash("Las dos contraseñas no coinciden.")
        else:
            gastar_token(db(), token, "invitacion")
            db().execute("UPDATE familias SET clave_hash = ? WHERE id = ?", (generate_password_hash(c1), f["id"]))
            db().commit()
            _iniciar_sesion(f["id"])
            flash("¡Listo! Ya tenéis cuenta. Para entrar otra vez usad el usuario «%s»." % f["usuario"])
            return redirect(url_for("cuenta.inicio"))
    return render_template("cuenta_invitacion.html", f=f)


# ---------------- Mi cuenta ----------------

@bp.get("/")
@requiere_familia
def inicio():
    f = familia_actual()
    con = db()
    listas = P.de_familia(con, f["id"])
    mis_bibs = con.execute("SELECT * FROM bibliotecas WHERE familia_id = ? ORDER BY nombre COLLATE NOCASE", (f["id"],)).fetchall()
    ocupados = {r["libro_id"] for r in con.execute(
        "SELECT libro_id FROM prestamos WHERE estado IN (%s)" % ",".join("?" * len(P.ABIERTOS)), P.ABIERTOS)}
    mis_libros = [l for l in con.execute(
        """SELECT l.id, l.titulo, b.nombre AS biblioteca FROM libros l JOIN bibliotecas b ON b.id = l.biblioteca_id
           WHERE b.familia_id = ? AND l.perdido = 0 ORDER BY l.titulo COLLATE NOCASE""", (f["id"],)) if l["id"] not in ocupados]
    otras = con.execute("SELECT id, nombre FROM familias WHERE activa = 1 AND id <> ? ORDER BY nombre COLLATE NOCASE",
                        (f["id"],)).fetchall()
    espera = con.execute("""SELECT l.id, l.titulo, l.portada, b.nombre AS biblioteca FROM espera e
                            JOIN libros l ON l.id = e.libro_id JOIN bibliotecas b ON b.id = l.biblioteca_id
                            WHERE e.familia_id = ? ORDER BY e.creado""", (f["id"],)).fetchall()
    return render_template("cuenta.html", listas=listas, mis_bibs=mis_bibs, mis_libros=mis_libros, otras=otras,
                           espera=espera, telegram_ok=telegram.configurado(), plazo=P.PLAZO_DIAS)


def _volver():
    return redirect(url_for("cuenta.inicio") + (request.form.get("ancla") or ""))


@bp.post("/prestamo/<pid>/<nombre>")
@requiere_familia
def accion(pid, nombre):
    try:
        flash(P.accion(db(), pid, nombre, familia_actual()["id"], request.form.get("nota", "")))
    except P.ErrorPrestamo as e:
        flash(str(e))
    return _volver()


@bp.post("/prestar")
@requiere_familia
def prestar():
    try:
        P.prestar(db(), familia_actual()["id"], request.form.get("libro_id", ""), request.form.get("familia_id", ""))
        flash("Apuntado. Falta que la otra familia confirme que lo tiene.")
    except P.ErrorPrestamo as e:
        flash(str(e))
    return _volver()


@bp.post("/biblioteca/<bid>")
@requiere_familia
def biblioteca(bid):
    r = db().execute("UPDATE bibliotecas SET presta = ? WHERE id = ? AND familia_id = ?",
                     (1 if request.form.get("presta") else 0, bid, familia_actual()["id"]))
    db().commit()
    if r.rowcount:
        flash("Guardado.")
    return _volver()


@bp.post("/espera/<lid>/quitar")
@requiere_familia
def quitar_espera(lid):
    P.no_esperar(db(), familia_actual()["id"], lid)
    return _volver()


@bp.post("/telegram")
@requiere_familia
def telegram_vincular():
    url = enlace_telegram(familia_actual()["id"])
    if not url:
        flash("Los avisos por Telegram no están activados en esta estantería.")
        return _volver()
    return redirect(url)


@bp.post("/telegram/quitar")
@requiere_familia
def telegram_quitar():
    db().execute("UPDATE familias SET telegram_chat = '' WHERE id = ?", (familia_actual()["id"],))
    db().commit()
    flash("Telegram desvinculado. Ya no recibiréis avisos.")
    return _volver()


@bp.post("/clave")
@requiere_familia
def clave():
    f = familia_actual()
    actual, c1, c2 = request.form.get("actual", ""), request.form.get("clave", ""), request.form.get("clave2", "")
    if not check_password_hash(f["clave_hash"], actual):
        flash("La contraseña actual no es correcta.")
    elif len(c1) < 8 or c1 != c2:
        flash("La nueva contraseña tiene que tener al menos 8 caracteres y escribirse igual dos veces.")
    else:
        db().execute("UPDATE familias SET clave_hash = ? WHERE id = ?", (generate_password_hash(c1), f["id"]))
        db().commit()
        flash("Contraseña cambiada.")
    return _volver()


# ---------------- API para la estantería pública ----------------

@bp.get("/api/yo")
def api_yo():
    from estanteria import token_csrf
    f = familia_actual()
    if not f:
        return jsonify(ok=True, familia=None)
    con = db()
    propias = [r["id"] for r in con.execute("SELECT id FROM bibliotecas WHERE familia_id = ?", (f["id"],))]
    espera = [r["libro_id"] for r in con.execute("SELECT libro_id FROM espera WHERE familia_id = ?", (f["id"],))]
    mios = {r["libro_id"]: r["estado"] for r in con.execute(
        "SELECT libro_id, estado FROM prestamos WHERE prestataria_id = ? AND estado IN (%s)"
        % ",".join("?" * len(P.ABIERTOS)), (f["id"],) + P.ABIERTOS)}
    pendientes = len(P.de_familia(con, f["id"])["pendientes"])
    return jsonify(ok=True, familia={"nombre": f["nombre"]}, csrf=token_csrf(), propias=propias,
                   espera=espera, mios=mios, pendientes=pendientes)


@bp.post("/api/pedir")
@requiere_familia
def api_pedir():
    lid = (request.get_json(silent=True) or {}).get("libro", "")
    try:
        P.pedir(db(), familia_actual()["id"], str(lid))
        return jsonify(ok=True, mensaje="¡Pedido! La familia dueña recibirá un aviso. Lo verás en «Mi cuenta».")
    except P.ErrorPrestamo as e:
        return jsonify(ok=False, error=str(e)), 400


@bp.post("/api/esperar")
@requiere_familia
def api_esperar():
    datos = request.get_json(silent=True) or {}
    lid = str(datos.get("libro", ""))
    try:
        if datos.get("quitar"):
            P.no_esperar(db(), familia_actual()["id"], lid)
            return jsonify(ok=True, mensaje="Ya no te avisaremos.")
        P.esperar(db(), familia_actual()["id"], lid)
        return jsonify(ok=True, mensaje="Te avisaremos cuando vuelva." + ("" if familia_actual()["telegram_chat"] else
                                         " (Vincula Telegram en «Mi cuenta» para recibir el aviso.)"))
    except P.ErrorPrestamo as e:
        return jsonify(ok=False, error=str(e)), 400
