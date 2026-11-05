"""Bot de Telegram de Nuestra Estantería.

Lo arranca systemd (estanteria-bot.service). Pregunta a Telegram cada poco si hay mensajes nuevos
(no hace falta abrir puertos ni configurar nada en el router).

Qué hace:
  /start <código>  vincula el Telegram de una familia (o de la administración) con su cuenta
  /prestamos       lista los préstamos en marcha, con botones
  botones          «Aceptar», «Lo tenemos», «Recibido»… de los avisos
"""
import sys
import time

import prestamos as P
import telegram
from basedatos import ajuste, conectar, gastar_token, iniciar_db, poner_ajuste

telegram.SINCRONO = True

AYUDA = ("📚 <b>Nuestra Estantería</b>\n\n"
         "Os avisaré cuando os pidan un libro, cuando os lo devuelvan y unos días antes de que toque devolver uno.\n\n"
         "/prestamos — ver vuestros préstamos en marcha\n"
         "/ayuda — este mensaje")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def familia_de_chat(con, chat):
    return con.execute("SELECT * FROM familias WHERE telegram_chat = ? AND activa = 1", (str(chat),)).fetchone()


def responder(chat, texto, botones=None):
    telegram.enviar(chat, texto, botones)


def cmd_start(con, chat, arg, nombre_tg):
    if not arg:
        f = familia_de_chat(con, chat)
        if f:
            responder(chat, "Hola, %s 👋\n\n%s" % (telegram.esc(f["nombre"]), AYUDA))
        else:
            responder(chat, "Hola 👋 Para recibir avisos, entrad en «Mi cuenta» de la estantería y pulsad «Vincular Telegram».")
        return
    fid = gastar_token(con, arg, "telegram")
    if fid is None:
        responder(chat, "Este enlace ya se ha usado o ha caducado. Volved a pulsar «Vincular Telegram» en la web.")
        return
    if fid == "admin":
        poner_ajuste(con, "telegram_admin_chat", str(chat))
        responder(chat, "🛠 Listo: aquí recibirás los desacuerdos entre familias y, cada domingo, la copia de la base de datos.")
        log("Telegram de administración vinculado")
        return
    # Un Telegram solo puede estar en una familia
    con.execute("UPDATE familias SET telegram_chat = '' WHERE telegram_chat = ?", (str(chat),))
    con.execute("UPDATE familias SET telegram_chat = ? WHERE id = ?", (str(chat), fid))
    con.commit()
    f = con.execute("SELECT nombre FROM familias WHERE id = ?", (fid,)).fetchone()
    responder(chat, "✅ ¡Hecho, %s! Telegram vinculado.\n\n%s" % (telegram.esc(f["nombre"] if f else ""), AYUDA))
    log("Telegram vinculado a la familia", fid)


def cmd_prestamos(con, chat):
    f = familia_de_chat(con, chat)
    if not f:
        responder(chat, "Este Telegram no está vinculado a ninguna familia. Hacedlo desde «Mi cuenta» en la web.")
        return
    listas = P.de_familia(con, f["id"])
    bloques = [("Os piden", listas["nos_piden"]), ("Os han prestado", listas["nos_han_prestado"]),
               ("Habéis prestado", listas["hemos_prestado"]), ("Habéis pedido", listas["hemos_pedido"])]
    if not any(l for _, l in bloques):
        responder(chat, "No tenéis ningún préstamo en marcha. 📚")
        return
    for titulo, lista in bloques:
        if not lista:
            continue
        responder(chat, "<b>%s</b>" % titulo)
        for it in lista:
            p = it["p"]
            otra = p["prestataria"] if it["rol"] == "duena" else p["duena"]
            linea = "%s«%s» · %s\n%s" % ("👉 " if it.get("toca") else "", telegram.esc(p["titulo"]), telegram.esc(otra),
                                         P.NOMBRES_ESTADO[p["estado"]])
            if p["estado"] in ("activo", "devolucion") and p["vence"]:
                linea += " · vuelve el %s" % P.fecha_bonita(p["vence"])
            botones = [(P.ETIQUETAS[a], "p:%s:%s" % (p["id"], a)) for a in it["acciones"] if a not in P.DELICADAS]
            responder(chat, linea, [botones[i:i + 2] for i in range(0, len(botones), 2)])


def boton(con, cq):
    chat = str(cq["message"]["chat"]["id"]) if cq.get("message") else str(cq["from"]["id"])
    datos = cq.get("data", "")
    f = familia_de_chat(con, chat)
    texto = ""
    try:
        if not f:
            raise P.ErrorPrestamo("Este Telegram no está vinculado a ninguna familia.")
        tipo, ident, accion = (datos.split(":") + ["", "", ""])[:3]
        if tipo == "p":
            texto = P.accion(con, ident, accion, f["id"])
        elif tipo == "l" and accion == "pedir":
            P.pedir(con, f["id"], ident)
            texto = "¡Pedido! La familia dueña recibirá un aviso."
        else:
            raise P.ErrorPrestamo("Botón desconocido.")
        ok = True
    except P.ErrorPrestamo as e:
        texto, ok = str(e), False
    telegram.llamar("answerCallbackQuery", callback_query_id=cq["id"], text=texto[:190], show_alert=not ok)
    msg = cq.get("message")
    if msg:
        # Quitar los botones del aviso y apuntar debajo lo que se ha hecho
        original = msg.get("text", "")
        telegram.llamar("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
                        text=(telegram.esc(original) + "\n\n" + ("👉 " if ok else "⚠️ ") + telegram.esc(texto))[:4000],
                        parse_mode="HTML", disable_web_page_preview=True)


def atender(con, u):
    if "callback_query" in u:
        boton(con, u["callback_query"])
        return
    m = u.get("message") or {}
    if not m or m.get("chat", {}).get("type") != "private":
        return
    chat = str(m["chat"]["id"])
    texto = (m.get("text") or "").strip()
    cmd, _, arg = texto.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/start":
        cmd_start(con, chat, arg.strip(), m.get("from", {}).get("first_name", ""))
    elif cmd in ("/prestamos", "/préstamos"):
        cmd_prestamos(con, chat)
    else:
        responder(chat, AYUDA)


def main():
    if not telegram.configurado():
        log("No hay TELEGRAM_BOT_TOKEN en /etc/estanteria/estanteria.env: el bot no arranca (la web funciona igual).")
        return 0
    iniciar_db()
    con = conectar()
    yo = None
    while yo is None:
        yo = telegram.llamar("getMe")
        if yo is None:
            log("No se puede hablar con Telegram (¿token mal copiado o sin internet?). Reintento en 60 s.")
            time.sleep(60)
    poner_ajuste(con, "telegram_bot_usuario", yo.get("username", ""))
    telegram.llamar("setMyCommands", commands=[{"command": "prestamos", "description": "Ver vuestros préstamos"},
                                               {"command": "ayuda", "description": "Qué hace este bot"}])
    log("Bot @%s escuchando" % yo.get("username"))
    offset = int(ajuste(con, "telegram_offset", "0") or 0)
    while True:
        updates = telegram.llamar("getUpdates", espera=65, offset=offset, timeout=50,
                                  allowed_updates=["message", "callback_query"])
        if updates is None:
            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            try:
                atender(con, u)
            except Exception as e:  # noqa: BLE001  (un mensaje raro no debe tumbar el bot)
                log("Error atendiendo un mensaje: %r" % e)
                try:
                    con.rollback()
                except Exception:  # noqa: BLE001
                    pass
        if updates:
            poner_ajuste(con, "telegram_offset", offset)


if __name__ == "__main__":
    sys.exit(main())
