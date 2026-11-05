"""Administración de familias y préstamos: /admin/familias y /admin/prestamos"""
import re
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

import prestamos as P
import telegram
from basedatos import ajuste, crear_token, nuevo_id, poner_ajuste

bp = Blueprint("adminp", __name__, url_prefix="/admin")
DIAS_INVITACION = 7


def db():
    from estanteria import db as _db
    return _db()


def _requiere_admin(f):
    from estanteria import requiere_admin
    return requiere_admin(f)


@bp.before_app_request
def _contar_disputas():
    if request.path.startswith("/admin") and session.get("admin"):
        try:
            g.disputas = db().execute("SELECT COUNT(*) FROM prestamos WHERE estado = 'disputa'").fetchone()[0]
        except Exception:  # noqa: BLE001  (nunca romper la página por el contador)
            g.disputas = 0


@bp.app_context_processor
def _ctx():
    return {"P": P, "fecha_bonita": P.fecha_bonita}


def _base_url():
    return telegram.url_base() or request.host_url.rstrip("/")


def _invitacion(fid):
    token = crear_token(db(), "invitacion", fid, DIAS_INVITACION * 24)
    session["invitacion_url"] = "%s/cuenta/invitacion/%s" % (_base_url(), token)
    session["invitacion_fid"] = fid


USUARIO_OK = re.compile(r"^[a-z0-9][a-z0-9._-]{1,29}$")


def _usuario_desde(nombre):
    import unicodedata
    s = unicodedata.normalize("NFD", nombre.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "", s)[:20] or "familia"
    base, n = s, 2
    while db().execute("SELECT 1 FROM familias WHERE usuario = ?", (s,)).fetchone():
        s = "%s%d" % (base, n)
        n += 1
    return s


def _asignar_bibliotecas(fid, ids):
    con = db()
    con.execute("UPDATE bibliotecas SET familia_id = '' WHERE familia_id = ?", (fid,))
    for bid in ids:
        con.execute("UPDATE bibliotecas SET familia_id = ? WHERE id = ?", (fid, bid))


# ---------------- Familias ----------------

@bp.get("/familias")
@_requiere_admin
def familias():
    con = db()
    filas = con.execute("""SELECT f.*,
          (SELECT GROUP_CONCAT(nombre, ', ') FROM bibliotecas WHERE familia_id = f.id) AS bibs,
          (SELECT COUNT(*) FROM prestamos p WHERE (p.duena_id = f.id OR p.prestataria_id = f.id)
                                            AND p.estado IN ('activo','devolucion','disputa','entregado')) AS abiertos
          FROM familias f WHERE f.usuario NOT LIKE 'borrada-%' ORDER BY f.activa DESC, f.nombre COLLATE NOCASE""").fetchall()
    bibs = con.execute("""SELECT b.id, b.nombre, b.familia_id, f.nombre AS familia FROM bibliotecas b
                          LEFT JOIN familias f ON f.id = b.familia_id ORDER BY b.nombre COLLATE NOCASE""").fetchall()
    return render_template("admin_familias.html", familias=filas, bibs=bibs,
                           telegram_ok=telegram.configurado(), admin_chat=ajuste(con, "telegram_admin_chat"),
                           bot=ajuste(con, "telegram_bot_usuario"))


@bp.post("/familias/nueva")
@_requiere_admin
def familia_nueva():
    nombre = request.form.get("nombre", "").strip()[:60]
    if not nombre:
        flash("Escribe un nombre para la familia.")
        return redirect(url_for("adminp.familias"))
    usuario = request.form.get("usuario", "").strip().lower() or _usuario_desde(nombre)
    if not USUARIO_OK.match(usuario):
        flash("El usuario solo puede tener letras sin tilde, números, puntos o guiones (2 a 30).")
        return redirect(url_for("adminp.familias"))
    if db().execute("SELECT 1 FROM familias WHERE usuario = ?", (usuario,)).fetchone():
        flash("Ya hay una familia con el usuario «%s»." % usuario)
        return redirect(url_for("adminp.familias"))
    fid = nuevo_id("f")
    db().execute("INSERT INTO familias (id, nombre, usuario) VALUES (?, ?, ?)", (fid, nombre, usuario))
    _asignar_bibliotecas(fid, request.form.getlist("bibliotecas"))
    db().commit()
    _invitacion(fid)
    flash("Familia «%s» creada. Mándales el enlace de invitación." % nombre)
    return redirect(url_for("adminp.familia", fid=fid))


@bp.get("/familia/<fid>")
@_requiere_admin
def familia(fid):
    con = db()
    f = con.execute("SELECT * FROM familias WHERE id = ?", (fid,)).fetchone() or abort(404)
    bibs = con.execute("""SELECT b.id, b.nombre, b.familia_id, f.nombre AS familia FROM bibliotecas b
                          LEFT JOIN familias f ON f.id = b.familia_id ORDER BY b.nombre COLLATE NOCASE""").fetchall()
    invitacion = session.pop("invitacion_url", "") if session.get("invitacion_fid") == fid else ""
    session.pop("invitacion_fid", None)
    lista = con.execute(P.SELECT_LISTA + " WHERE p.duena_id = ? OR p.prestataria_id = ? ORDER BY p.actualizado DESC LIMIT 200",
                        (fid, fid)).fetchall()
    return render_template("admin_familia.html", f=f, bibs=bibs, invitacion=invitacion, prestamos=lista,
                           dias=DIAS_INVITACION)


@bp.post("/familia/<fid>")
@_requiere_admin
def familia_guardar(fid):
    con = db()
    f = con.execute("SELECT * FROM familias WHERE id = ?", (fid,)).fetchone() or abort(404)
    nombre = request.form.get("nombre", "").strip()[:60] or f["nombre"]
    usuario = request.form.get("usuario", "").strip().lower() or f["usuario"]
    if not USUARIO_OK.match(usuario):
        flash("El usuario solo puede tener letras sin tilde, números, puntos o guiones (2 a 30).")
        return redirect(url_for("adminp.familia", fid=fid))
    if con.execute("SELECT 1 FROM familias WHERE usuario = ? AND id <> ?", (usuario, fid)).fetchone():
        flash("Ya hay otra familia con el usuario «%s»." % usuario)
        return redirect(url_for("adminp.familia", fid=fid))
    activa = 1 if request.form.get("activa") else 0
    con.execute("UPDATE familias SET nombre = ?, usuario = ?, activa = ? WHERE id = ?", (nombre, usuario, activa, fid))
    _asignar_bibliotecas(fid, request.form.getlist("bibliotecas"))
    if not activa:
        con.execute("DELETE FROM tokens WHERE familia_id = ?", (fid,))
    con.commit()
    flash("Guardado." + ("" if activa else " La familia está desactivada: no puede entrar ni pedir libros."))
    return redirect(url_for("adminp.familia", fid=fid))


@bp.post("/familia/<fid>/borrar")
@_requiere_admin
def familia_borrar(fid):
    con = db()
    f = con.execute("SELECT * FROM familias WHERE id = ?", (fid,)).fetchone() or abort(404)
    if request.form.get("confirmar", "").strip() != f["nombre"]:
        flash("Para borrar, escribe el nombre exacto de la familia.")
        return redirect(url_for("adminp.familia", fid=fid))
    abiertos = con.execute("SELECT COUNT(*) FROM prestamos WHERE (duena_id = ? OR prestataria_id = ?) AND estado IN (%s)"
                           % ",".join("?" * len(P.ABIERTOS)), (fid, fid) + P.ABIERTOS).fetchone()[0]
    if abiertos:
        flash("No se puede borrar: tiene %d %s en marcha. Termínalos o decide cómo quedan en «Préstamos»."
              % (abiertos, "préstamo" if abiertos == 1 else "préstamos"))
        return redirect(url_for("adminp.familia", fid=fid))
    tenia_bibs = con.execute("UPDATE bibliotecas SET familia_id = '' WHERE familia_id = ?", (fid,)).rowcount
    con.execute("DELETE FROM tokens WHERE familia_id = ?", (fid,))
    con.execute("DELETE FROM espera WHERE familia_id = ?", (fid,))
    historial = con.execute("SELECT COUNT(*) FROM prestamos WHERE duena_id = ? OR prestataria_id = ?", (fid, fid)).fetchone()[0]
    if historial:
        # Tiene préstamos antiguos: se borran sus datos pero se deja un hueco anónimo para que el historial
        # de las demás familias siga cuadrando
        con.execute("""UPDATE familias SET nombre = 'Familia borrada', usuario = ?, clave_hash = '', telegram_chat = '',
                       activa = 0, ultimo_acceso = '' WHERE id = ?""", ("borrada-" + fid, fid))
    else:
        con.execute("DELETE FROM familias WHERE id = ?", (fid,))
    con.commit()
    flash("Familia «%s» borrada.%s" % (f["nombre"], " Sus bibliotecas se quedan sin familia dueña (no se pueden pedir hasta que asignes otra)."
                                       if tenia_bibs else ""))
    return redirect(url_for("adminp.familias"))


@bp.post("/familia/<fid>/invitacion")
@_requiere_admin
def familia_invitacion(fid):
    db().execute("SELECT 1 FROM familias WHERE id = ?", (fid,)).fetchone() or abort(404)
    _invitacion(fid)
    flash("Enlace nuevo creado. Sirve también para poner una contraseña nueva si la han olvidado.")
    return redirect(url_for("adminp.familia", fid=fid))


@bp.post("/familia/<fid>/telegram/quitar")
@_requiere_admin
def familia_telegram_quitar(fid):
    db().execute("UPDATE familias SET telegram_chat = '' WHERE id = ?", (fid,))
    db().commit()
    flash("Telegram desvinculado.")
    return redirect(url_for("adminp.familia", fid=fid))


@bp.post("/telegram")
@_requiere_admin
def telegram_admin():
    """Vincula el Telegram de la administración (avisos de desacuerdos y copia semanal de la base de datos)."""
    from cuenta import nombre_bot
    bot = nombre_bot()
    if not bot:
        flash("No se puede hablar con el bot. Revisa TELEGRAM_BOT_TOKEN en /etc/estanteria/estanteria.env.")
        return redirect(url_for("adminp.familias"))
    return redirect("https://t.me/%s?start=%s" % (bot, crear_token(db(), "telegram", "admin", 24)))


@bp.post("/telegram/probar")
@_requiere_admin
def telegram_probar():
    chat = ajuste(db(), "telegram_admin_chat")
    ok = telegram.enviar(chat, "👋 Prueba de Nuestra Estantería: los avisos funcionan.", en_segundo_plano=False)
    flash("Mensaje enviado. Mira Telegram." if ok else "No se ha podido enviar. Mira 'journalctl -u estanteria -n 30'.")
    return redirect(url_for("adminp.familias"))


@bp.post("/telegram/quitar")
@_requiere_admin
def telegram_admin_quitar():
    poner_ajuste(db(), "telegram_admin_chat", "")
    flash("Telegram de la administración desvinculado.")
    return redirect(url_for("adminp.familias"))


# ---------------- Préstamos ----------------

@bp.get("/prestamos")
@_requiere_admin
def prestamos():
    con = db()
    hoy = P.hoy()
    stats = {
        "activos": con.execute("SELECT COUNT(*) FROM prestamos WHERE estado IN ('activo','devolucion','entregado')").fetchone()[0],
        "vencidos": con.execute("SELECT COUNT(*) FROM prestamos WHERE estado IN ('activo','devolucion') AND vence <> '' AND vence < ?",
                                (hoy,)).fetchone()[0],
        "pedidos": con.execute("SELECT COUNT(*) FROM prestamos WHERE estado IN ('solicitado','aceptado')").fetchone()[0],
        "devueltos": con.execute("SELECT COUNT(*) FROM prestamos WHERE estado = 'devuelto'").fetchone()[0],
        "perdidos": con.execute("SELECT COUNT(*) FROM prestamos WHERE estado = 'perdido'").fetchone()[0],
    }
    mas = con.execute("""SELECT l.titulo, COUNT(*) AS n FROM prestamos p JOIN libros l ON l.id = p.libro_id
                         WHERE p.estado IN ('activo','devolucion','devuelto') GROUP BY p.libro_id ORDER BY n DESC, l.titulo LIMIT 5""").fetchall()
    abiertos = P.todos(con, "abiertos")
    cerrados = con.execute(P.SELECT_LISTA + " WHERE p.estado IN ('devuelto','rechazado','cancelado','perdido') "
                           "ORDER BY p.actualizado DESC LIMIT 100").fetchall()
    grupos = {"disputa": [], "vencidos": [], "fuera": [], "pedidos": []}
    for p in abiertos:
        if p["estado"] == "disputa":
            grupos["disputa"].append(p)
        elif p["estado"] in ("activo", "devolucion") and p["vence"] and p["vence"] < hoy:
            grupos["vencidos"].append(p)
        elif p["estado"] in ("activo", "devolucion", "entregado"):
            grupos["fuera"].append(p)
        else:
            grupos["pedidos"].append(p)
    return render_template("admin_prestamos.html", stats=stats, mas=mas, grupos=grupos, cerrados=cerrados,
                           dias_hasta=P.dias_hasta)


@bp.get("/prestamo/<pid>")
@_requiere_admin
def prestamo(pid):
    p = P.prestamo(db(), pid) or abort(404)
    return render_template("admin_prestamo.html", p=p, eventos=P.eventos(db(), pid))


@bp.post("/prestamo/<pid>/resolver")
@_requiere_admin
def resolver(pid):
    try:
        flash(P.resolver(db(), pid, request.form.get("estado", ""), request.form.get("nota", "")))
    except P.ErrorPrestamo as e:
        flash(str(e))
    return redirect(url_for("adminp.prestamo", pid=pid))


@bp.post("/prestamo/<pid>/fecha")
@_requiere_admin
def fecha(pid):
    p = P.prestamo(db(), pid) or abort(404)
    nueva = request.form.get("vence", "")
    try:
        date.fromisoformat(nueva)
    except ValueError:
        flash("Fecha no válida.")
        return redirect(url_for("adminp.prestamo", pid=pid))
    db().execute("UPDATE prestamos SET vence = ? WHERE id = ?", (nueva, pid))
    db().execute("INSERT INTO prestamo_eventos (prestamo_id, quien, accion, detalle) VALUES (?, 'admin', 'cambiar_fecha', ?)",
                 (pid, nueva))
    db().commit()
    for chat in (p["duena_chat"], p["prestataria_chat"]):
        P.avisar(chat, "🛠 Nueva fecha de devolución para «%s»: %s." % (telegram.esc(p["titulo"]), P.fecha_bonita(nueva)))
    flash("Fecha cambiada.")
    return redirect(url_for("adminp.prestamo", pid=pid))
