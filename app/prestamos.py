"""Préstamos entre familias, con doble confirmación.

Cómo va un préstamo (estados):

  solicitado ──aceptar──▶ aceptado ──entregar──▶ entregado ──recibido──▶ activo
      │ rechazar / cancelar     │ cancelar          │ no_recibido ▶ disputa
      ▼                         ▼                   │ cancelar (dueña)
  rechazado / cancelado     cancelado               ▼ cancelado

  activo ──devuelto (la que lo tiene)──▶ devolucion ──confirmar_devolucion (dueña)──▶ devuelto
     │                                       │ no_llegado ▶ disputa
     ├─ confirmar_devolucion (dueña): devuelto directamente
     ├─ pedir_prorroga / aceptar_prorroga / rechazar_prorroga
     └─ perdido (dueña)

La regla: quien afirma algo que le conviene necesita la confirmación de la otra familia.
«Te lo he dado» lo confirma quien lo recibe; «te lo he devuelto» lo confirma la dueña.
Las disputas las resuelve la administración.

Este módulo lo usan la web (/cuenta y /admin), el bot de Telegram y las tareas diarias.
"""
import os
from datetime import date, datetime, timedelta

import telegram
from basedatos import ajuste, nuevo_id

ABIERTOS = ("solicitado", "aceptado", "entregado", "activo", "devolucion", "disputa")
CERRADOS = ("devuelto", "rechazado", "cancelado", "perdido")
PLAZO_DIAS = int(os.environ.get("ESTANTERIA_PLAZO_DIAS", "21") or 21)
PRORROGA_DIAS = 14

NOMBRES_ESTADO = {
    "solicitado": "Pedido, falta que la dueña acepte",
    "aceptado": "Aceptado, falta entregarlo",
    "entregado": "Entregado, falta que lo confirme quien lo recibe",
    "activo": "Prestado",
    "devolucion": "Devuelto, falta que la dueña lo confirme",
    "disputa": "En desacuerdo: lo revisa la administración",
    "devuelto": "Devuelto",
    "rechazado": "No aceptado",
    "cancelado": "Cancelado",
    "perdido": "Perdido",
}


# Texto de cada botón (web y Telegram)
ETIQUETAS = {
    "aceptar": "✅ Aceptar",
    "rechazar": "❌ Ahora no puedo",
    "cancelar": "✖️ Cancelar",
    "entregar": "📦 Ya se lo he dado",
    "recibido": "✅ Lo tenemos",
    "no_recibido": "⚠️ No lo hemos recibido",
    "devuelto": "↩️ Ya lo hemos devuelto",
    "confirmar_devolucion": "✅ Ya lo tengo de vuelta",
    "no_llegado": "⚠️ No me ha llegado",
    "pedir_prorroga": "⏳ Pedir %d días más" % PRORROGA_DIAS,
    "aceptar_prorroga": "✅ Dar más tiempo",
    "rechazar_prorroga": "❌ No dar más tiempo",
    "perdido": "📕 Se ha perdido",
}
# Acciones serias: en la web piden una nota y van escondidas en un desplegable
DELICADAS = ("no_recibido", "no_llegado", "perdido")


class ErrorPrestamo(Exception):
    pass


# ---------------- Utilidades ----------------

def hoy():
    return date.today().isoformat()


def fecha_bonita(iso):
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return "%d/%02d" % (d.day, d.month) if d.year == date.today().year else "%d/%02d/%d" % (d.day, d.month, d.year)


def dias_hasta(iso):
    try:
        return (date.fromisoformat(iso[:10]) - date.today()).days
    except (ValueError, TypeError):
        return None


def familia(con, fid):
    return con.execute("SELECT * FROM familias WHERE id = ?", (fid,)).fetchone() if fid else None


def libro_info(con, lid):
    return con.execute("""SELECT l.*, b.nombre AS biblioteca, b.familia_id AS duena_id, b.presta AS presta
                          FROM libros l JOIN bibliotecas b ON b.id = l.biblioteca_id WHERE l.id = ?""", (lid,)).fetchone()


def prestamo(con, pid):
    return con.execute("""SELECT p.*, l.titulo, l.portada, b.nombre AS biblioteca,
                                 fd.nombre AS duena, fd.telegram_chat AS duena_chat,
                                 fp.nombre AS prestataria, fp.telegram_chat AS prestataria_chat
                          FROM prestamos p
                          JOIN libros l ON l.id = p.libro_id
                          JOIN bibliotecas b ON b.id = l.biblioteca_id
                          JOIN familias fd ON fd.id = p.duena_id
                          JOIN familias fp ON fp.id = p.prestataria_id
                          WHERE p.id = ?""", (pid,)).fetchone()


def abierto_de_libro(con, lid):
    return con.execute("SELECT * FROM prestamos WHERE libro_id = ? AND estado IN (%s) ORDER BY creado DESC LIMIT 1"
                       % ",".join("?" * len(ABIERTOS)), (lid,) + ABIERTOS).fetchone()


def estados_publicos(con):
    """Para la estantería pública: qué libros están prestados (sin nombres) y hasta cuándo."""
    salida = {}
    for r in con.execute("SELECT libro_id, estado, vence FROM prestamos WHERE estado IN (%s)"
                         % ",".join("?" * len(ABIERTOS)), ABIERTOS):
        if r["estado"] in ("activo", "devolucion", "disputa", "entregado"):
            salida[r["libro_id"]] = {"prestado": True, "vuelve": r["vence"]}
        else:
            salida.setdefault(r["libro_id"], {"prestado": False, "pedido": True, "vuelve": ""})
    return salida


def _evento(con, pid, quien, accion, detalle=""):
    con.execute("INSERT INTO prestamo_eventos (prestamo_id, quien, accion, detalle) VALUES (?, ?, ?, ?)",
                (pid, quien, accion, detalle[:500]))


def _actualizar(con, pid, **campos):
    campos["actualizado"] = datetime.now().isoformat(" ", "seconds")
    con.execute("UPDATE prestamos SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in campos),
                list(campos.values()) + [pid])


# ---------------- Avisos ----------------

def _t(p):
    return "«%s»" % telegram.esc(p["titulo"])


def _boton_cuenta():
    url = telegram.enlace_cuenta()
    return [("Abrir mi cuenta", url)] if url else []


def avisar(chat, texto, botones=None):
    filas = [f for f in (botones or []) if f]
    extra = _boton_cuenta()
    if extra:
        filas.append(extra)
    telegram.enviar(chat, texto, filas)


def avisar_admin(con, texto):
    chat = ajuste(con, "telegram_admin_chat")
    if chat:
        telegram.enviar(chat, "🛠 " + texto)


def avisar_espera(con, lid, motivo="libre"):
    """«Avísame cuando vuelva»: avisa a quien esperaba este libro y vacía la cola."""
    l = libro_info(con, lid)
    if not l:
        return
    for r in con.execute("""SELECT f.id, f.telegram_chat FROM espera e JOIN familias f ON f.id = e.familia_id
                            WHERE e.libro_id = ? AND f.activa = 1""", (lid,)).fetchall():
        if motivo == "libre":
            avisar(r["telegram_chat"], "🔔 «%s» ya está libre en la biblioteca de %s. ¿Lo pides?"
                   % (telegram.esc(l["titulo"]), telegram.esc(l["biblioteca"])), [[("📚 Pedirlo", "l:%s:pedir" % lid)]])
        else:
            avisar(r["telegram_chat"], "😕 «%s» se ha perdido, así que ya no se puede pedir." % telegram.esc(l["titulo"]))
    con.execute("DELETE FROM espera WHERE libro_id = ?", (lid,))
    con.commit()


# ---------------- Empezar un préstamo ----------------

def pedir(con, familia_id, libro_id):
    """Una familia pide un libro desde la estantería."""
    l = libro_info(con, libro_id)
    f = familia(con, familia_id)
    if not l or not f or not f["activa"]:
        raise ErrorPrestamo("No se encuentra ese libro.")
    if not l["duena_id"]:
        raise ErrorPrestamo("Esta biblioteca todavía no tiene una familia que gestione sus préstamos.")
    if l["duena_id"] == familia_id:
        raise ErrorPrestamo("Este libro es de tu biblioteca.")
    if not l["presta"]:
        raise ErrorPrestamo("Esta biblioteca es solo para mirar: no presta libros.")
    if l["perdido"]:
        raise ErrorPrestamo("Este libro está marcado como perdido.")
    duena = familia(con, l["duena_id"])
    if not duena or not duena["activa"]:
        raise ErrorPrestamo("La familia de esta biblioteca no está activa ahora.")
    if abierto_de_libro(con, libro_id):
        raise ErrorPrestamo("Ese libro ya está prestado o pedido. Puedes pulsar «Avísame cuando vuelva».")
    pid = nuevo_id("p")
    con.execute("INSERT INTO prestamos (id, libro_id, duena_id, prestataria_id, estado) VALUES (?, ?, ?, ?, 'solicitado')",
                (pid, libro_id, l["duena_id"], familia_id))
    _evento(con, pid, "prestataria", "pedir")
    con.execute("DELETE FROM espera WHERE libro_id = ? AND familia_id = ?", (libro_id, familia_id))
    con.commit()
    avisar(duena["telegram_chat"], "📚 <b>%s</b> te pide prestado «%s» (biblioteca de %s)."
           % (telegram.esc(f["nombre"]), telegram.esc(l["titulo"]), telegram.esc(l["biblioteca"])),
           [[("✅ Aceptar", "p:%s:aceptar" % pid), ("❌ No puedo", "p:%s:rechazar" % pid)]])
    return pid


def prestar(con, duena_id, libro_id, prestataria_id):
    """La dueña apunta directamente que ha prestado un libro. La otra familia tiene que confirmarlo."""
    l = libro_info(con, libro_id)
    if not l or l["duena_id"] != duena_id:
        raise ErrorPrestamo("Ese libro no es de tu biblioteca.")
    if prestataria_id == duena_id:
        raise ErrorPrestamo("Elige otra familia.")
    p_fam = familia(con, prestataria_id)
    if not p_fam or not p_fam["activa"]:
        raise ErrorPrestamo("Esa familia no existe o no está activa.")
    if l["perdido"]:
        raise ErrorPrestamo("Este libro está marcado como perdido.")
    if abierto_de_libro(con, libro_id):
        raise ErrorPrestamo("Ese libro ya tiene un préstamo en marcha.")
    duena = familia(con, duena_id)
    pid = nuevo_id("p")
    con.execute("INSERT INTO prestamos (id, libro_id, duena_id, prestataria_id, estado) VALUES (?, ?, ?, ?, 'entregado')",
                (pid, libro_id, duena_id, prestataria_id))
    _evento(con, pid, "duena", "prestar")
    con.execute("DELETE FROM espera WHERE libro_id = ? AND familia_id = ?", (libro_id, prestataria_id))
    con.commit()
    avisar(p_fam["telegram_chat"], "📦 <b>%s</b> dice que os ha prestado «%s». ¿Lo tenéis?"
           % (telegram.esc(duena["nombre"]), telegram.esc(l["titulo"])),
           [[("✅ Lo tenemos", "p:%s:recibido" % pid), ("⚠️ No lo hemos recibido", "p:%s:no_recibido" % pid)]])
    return pid


def esperar(con, familia_id, libro_id):
    l = libro_info(con, libro_id)
    if not l:
        raise ErrorPrestamo("No se encuentra ese libro.")
    if l["duena_id"] == familia_id:
        raise ErrorPrestamo("Este libro es de tu biblioteca.")
    con.execute("INSERT OR IGNORE INTO espera (libro_id, familia_id) VALUES (?, ?)", (libro_id, familia_id))
    con.commit()


def no_esperar(con, familia_id, libro_id):
    con.execute("DELETE FROM espera WHERE libro_id = ? AND familia_id = ?", (libro_id, familia_id))
    con.commit()


# ---------------- Acciones sobre un préstamo ----------------

# accion: (quién puede hacerla, estados desde los que se puede)
ACCIONES = {
    "aceptar": ("duena", ("solicitado",)),
    "rechazar": ("duena", ("solicitado",)),
    "cancelar": ("cualquiera", ("solicitado", "aceptado", "entregado")),
    "entregar": ("duena", ("aceptado",)),
    "recibido": ("prestataria", ("entregado",)),
    "no_recibido": ("prestataria", ("entregado",)),
    "devuelto": ("prestataria", ("activo",)),
    "confirmar_devolucion": ("duena", ("activo", "devolucion")),
    "no_llegado": ("duena", ("devolucion",)),
    "pedir_prorroga": ("prestataria", ("activo",)),
    "aceptar_prorroga": ("duena", ("activo",)),
    "rechazar_prorroga": ("duena", ("activo",)),
    "perdido": ("duena", ("activo", "devolucion")),
}


def acciones_posibles(p, familia_id):
    """Qué botones ve una familia para un préstamo."""
    rol = "duena" if familia_id == p["duena_id"] else "prestataria" if familia_id == p["prestataria_id"] else None
    salida = []
    for nombre, (quien, estados) in ACCIONES.items():
        if p["estado"] not in estados or (quien != "cualquiera" and quien != rol) or rol is None:
            continue
        if nombre == "cancelar" and ((rol == "prestataria" and p["estado"] == "entregado")
                                     or (rol == "duena" and p["estado"] == "solicitado")):
            continue
        if nombre == "pedir_prorroga" and p["prorroga_dias"]:
            continue
        if nombre in ("aceptar_prorroga", "rechazar_prorroga") and not p["prorroga_dias"]:
            continue
        salida.append(nombre)
    return salida


def accion(con, pid, nombre, familia_id=None, nota=""):
    """Ejecuta una acción de una familia. Devuelve un texto para mostrar. Lanza ErrorPrestamo si no se puede."""
    p = prestamo(con, pid)
    if not p:
        raise ErrorPrestamo("Ese préstamo ya no existe.")
    if nombre not in ACCIONES:
        raise ErrorPrestamo("Acción desconocida.")
    if nombre not in acciones_posibles(p, familia_id):
        if familia_id not in (p["duena_id"], p["prestataria_id"]):
            raise ErrorPrestamo("Ese préstamo no es vuestro.")
        quien, estados = ACCIONES[nombre]
        if p["estado"] in estados:
            raise ErrorPrestamo("Eso le toca hacerlo a la otra familia.")
        raise ErrorPrestamo("Eso ya no se puede hacer: el préstamo está «%s»." % NOMBRES_ESTADO[p["estado"]].lower())
    rol = "duena" if familia_id == p["duena_id"] else "prestataria"
    nota = (nota or "").strip()[:300]
    t, dc, pc = _t(p), p["duena_chat"], p["prestataria_chat"]
    duena, prest = telegram.esc(p["duena"]), telegram.esc(p["prestataria"])

    if nombre == "aceptar":
        _actualizar(con, pid, estado="aceptado")
        avisar(pc, "👍 <b>%s</b> acepta prestaros %s. Cuando os lo dé, confirmad que lo tenéis." % (duena, t))
        msg = "Aceptado. Cuando se lo des, pulsa «Ya se lo he dado»."
    elif nombre == "rechazar":
        _actualizar(con, pid, estado="rechazado", fin=hoy())
        avisar(pc, "😕 <b>%s</b> no puede prestaros %s ahora." % (duena, t))
        msg = "Petición rechazada."
    elif nombre == "cancelar":
        _actualizar(con, pid, estado="cancelado", fin=hoy())
        otro = pc if rol == "duena" else dc
        avisar(otro, "✖️ <b>%s</b> ha cancelado el préstamo de %s." % (duena if rol == "duena" else prest, t))
        msg = "Préstamo cancelado."
    elif nombre == "entregar":
        _actualizar(con, pid, estado="entregado")
        avisar(pc, "📦 <b>%s</b> dice que os ha dado %s. ¿Lo tenéis?" % (duena, t),
               [[("✅ Lo tenemos", "p:%s:recibido" % pid), ("⚠️ No lo hemos recibido", "p:%s:no_recibido" % pid)]])
        msg = "Apuntado. Falta que la otra familia confirme que lo tiene."
    elif nombre == "recibido":
        vence = (date.today() + timedelta(days=PLAZO_DIAS)).isoformat()
        _actualizar(con, pid, estado="activo", inicio=hoy(), vence=vence)
        avisar(dc, "✅ <b>%s</b> confirma que tiene %s. Tiene que devolverlo antes del %s."
               % (prest, t, fecha_bonita(vence)))
        msg = "¡Perfecto! Tenéis que devolverlo antes del %s." % fecha_bonita(vence)
    elif nombre == "no_recibido":
        _actualizar(con, pid, estado="disputa", disputa="%s dice que no ha recibido el libro. %s" % (p["prestataria"], nota))
        avisar(dc, "⚠️ <b>%s</b> dice que no ha recibido %s. Lo revisará la administración." % (prest, t))
        avisar_admin(con, "Desacuerdo: %s dice que no ha recibido %s de %s." % (prest, t, duena))
        msg = "Apuntado. La administración lo revisará."
    elif nombre == "devuelto":
        _actualizar(con, pid, estado="devolucion")
        avisar(dc, "↩️ <b>%s</b> dice que os ha devuelto %s. ¿Lo tenéis?" % (prest, t),
               [[("✅ Recibido", "p:%s:confirmar_devolucion" % pid), ("⚠️ No me ha llegado", "p:%s:no_llegado" % pid)]])
        msg = "Apuntado. Falta que la dueña confirme que lo ha recibido."
    elif nombre == "confirmar_devolucion":
        _actualizar(con, pid, estado="devuelto", fin=hoy(), prorroga_dias=0)
        avisar(pc, "📚 <b>%s</b> confirma que ha recibido %s. ¡Gracias!" % (duena, t))
        msg = "Devolución confirmada. ¡Libro en casa!"
    elif nombre == "no_llegado":
        _actualizar(con, pid, estado="disputa", disputa="%s dice que no le ha llegado el libro devuelto. %s" % (p["duena"], nota))
        avisar(pc, "⚠️ <b>%s</b> dice que no le ha llegado %s. Lo revisará la administración." % (duena, t))
        avisar_admin(con, "Desacuerdo: %s dice que %s no le ha devuelto %s." % (duena, prest, t))
        msg = "Apuntado. La administración lo revisará."
    elif nombre == "pedir_prorroga":
        _actualizar(con, pid, prorroga_dias=PRORROGA_DIAS)
        avisar(dc, "⏳ <b>%s</b> pide %d días más para %s (ahora tiene que devolverlo el %s)."
               % (prest, PRORROGA_DIAS, t, fecha_bonita(p["vence"])),
               [[("✅ Vale", "p:%s:aceptar_prorroga" % pid), ("❌ No", "p:%s:rechazar_prorroga" % pid)]])
        msg = "Has pedido %d días más. Te avisaremos cuando conteste." % PRORROGA_DIAS
    elif nombre == "aceptar_prorroga":
        base = max(date.fromisoformat(p["vence"]), date.today()) if p["vence"] else date.today()
        vence = (base + timedelta(days=p["prorroga_dias"])).isoformat()
        _actualizar(con, pid, vence=vence, prorroga_dias=0, prorrogas=p["prorrogas"] + 1)
        avisar(pc, "✅ <b>%s</b> os da más tiempo para %s. Nueva fecha: %s." % (duena, t, fecha_bonita(vence)))
        msg = "Hecho. Nueva fecha de devolución: %s." % fecha_bonita(vence)
    elif nombre == "rechazar_prorroga":
        _actualizar(con, pid, prorroga_dias=0)
        avisar(pc, "😕 <b>%s</b> necesita %s de vuelta el %s." % (duena, t, fecha_bonita(p["vence"])))
        msg = "Apuntado: se mantiene la fecha del %s." % fecha_bonita(p["vence"])
    elif nombre == "perdido":
        _actualizar(con, pid, estado="perdido", fin=hoy(), nota=nota)
        con.execute("UPDATE libros SET perdido = 1 WHERE id = ?", (p["libro_id"],))
        avisar(pc, "📕 <b>%s</b> ha marcado %s como perdido.%s" % (duena, t, (" Nota: " + telegram.esc(nota)) if nota else ""))
        msg = "Libro marcado como perdido."
    _evento(con, pid, rol, nombre, nota)
    con.commit()
    if nombre in ("rechazar", "cancelar", "confirmar_devolucion"):
        avisar_espera(con, p["libro_id"], "libre")
    elif nombre == "perdido":
        avisar_espera(con, p["libro_id"], "perdido")
    return msg


def resolver(con, pid, estado, nota=""):
    """La administración decide (disputas o cualquier corrección)."""
    p = prestamo(con, pid)
    if not p:
        raise ErrorPrestamo("Ese préstamo ya no existe.")
    if estado not in ("activo", "devuelto", "cancelado", "perdido"):
        raise ErrorPrestamo("Estado no válido.")
    campos = {"estado": estado, "disputa": "" if estado != p["estado"] else p["disputa"]}
    if estado == "activo":
        campos["inicio"] = p["inicio"] or hoy()
        campos["vence"] = p["vence"] or (date.today() + timedelta(days=PLAZO_DIAS)).isoformat()
        campos["fin"] = ""
    else:
        campos["fin"] = p["fin"] or hoy()
        campos["prorroga_dias"] = 0
    _actualizar(con, pid, **campos)
    if estado == "perdido":
        con.execute("UPDATE libros SET perdido = 1 WHERE id = ?", (p["libro_id"],))
    _evento(con, pid, "admin", "resolver:" + estado, nota)
    con.commit()
    texto = {"activo": "sigue prestado%s" % (" hasta el " + fecha_bonita(campos.get("vence", "")) if campos.get("vence") else ""),
             "devuelto": "se da por devuelto", "cancelado": "se cancela", "perdido": "se da por perdido"}[estado]
    for chat in (p["duena_chat"], p["prestataria_chat"]):
        avisar(chat, "🛠 La administración ha revisado el préstamo de %s: %s.%s"
               % (_t(p), texto, (" Nota: " + telegram.esc(nota)) if nota else ""))
    if estado in ("devuelto", "cancelado"):
        avisar_espera(con, p["libro_id"], "libre")
    elif estado == "perdido":
        avisar_espera(con, p["libro_id"], "perdido")
    return "Préstamo actualizado: %s." % texto


# ---------------- Listados ----------------

SELECT_LISTA = """SELECT p.*, l.titulo, l.portada, l.autor, b.nombre AS biblioteca,
                         fd.nombre AS duena, fp.nombre AS prestataria
                  FROM prestamos p JOIN libros l ON l.id = p.libro_id JOIN bibliotecas b ON b.id = l.biblioteca_id
                  JOIN familias fd ON fd.id = p.duena_id JOIN familias fp ON fp.id = p.prestataria_id"""


TOCA = ("aceptar", "entregar", "recibido", "confirmar_devolucion", "aceptar_prorroga")


def de_familia(con, fid):
    """Préstamos de una familia, separados para su pantalla.
    Cada préstamo va en su sitio (nos han prestado / hemos prestado / nos piden / hemos pedido);
    los que esperan una respuesta de esta familia llevan item["toca"] y además se listan en "pendientes"."""
    filas = con.execute(SELECT_LISTA + " WHERE (p.duena_id = ? OR p.prestataria_id = ?) ORDER BY p.actualizado DESC",
                        (fid, fid)).fetchall()
    res = {"pendientes": [], "nos_han_prestado": [], "hemos_prestado": [], "nos_piden": [], "hemos_pedido": [],
           "historial": []}
    for p in filas:
        acciones = acciones_posibles(p, fid)
        rol = "duena" if p["duena_id"] == fid else "prestataria"
        item = {"p": p, "acciones": acciones, "rol": rol, "dias": dias_hasta(p["vence"]) if p["vence"] else None}
        if p["estado"] not in ABIERTOS:
            res["historial"].append(item)
            continue
        toca = [a for a in acciones if a in TOCA and (p["estado"] != "activo" or a == "aceptar_prorroga")]
        item["toca"] = bool(toca)
        if toca:
            res["pendientes"].append(item)
        pedido = p["estado"] in ("solicitado", "aceptado")
        if rol == "prestataria":
            res["hemos_pedido" if pedido else "nos_han_prestado"].append(item)
        else:
            res["nos_piden" if pedido else "hemos_prestado"].append(item)
    return res


def todos(con, filtro="abiertos"):
    if filtro == "abiertos":
        where = "WHERE p.estado IN (%s)" % ",".join("'%s'" % e for e in ABIERTOS)
    elif filtro == "disputas":
        where = "WHERE p.estado = 'disputa'"
    elif filtro == "vencidos":
        where = "WHERE p.estado IN ('activo','devolucion') AND p.vence <> '' AND p.vence < '%s'" % hoy()
    else:
        where = ""
    return con.execute(SELECT_LISTA + " " + where + " ORDER BY p.estado = 'disputa' DESC, p.vence = '', p.vence, p.actualizado DESC LIMIT 500").fetchall()


def eventos(con, pid):
    return con.execute("SELECT * FROM prestamo_eventos WHERE prestamo_id = ? ORDER BY id", (pid,)).fetchall()
