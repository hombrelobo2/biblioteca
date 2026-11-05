"""Tareas diarias de Nuestra Estantería (las lanza estanteria-tareas.timer cada mañana).

  python3 tareas.py diario         recordatorios + (los domingos) copia de la base de datos por Telegram
  python3 tareas.py recordatorios  solo los recordatorios
  python3 tareas.py copia          manda ahora la copia de la base de datos al Telegram de la administración

Recordatorios:
  · a quien tiene el libro: 3 días antes y el día que vence
  · si se pasa la fecha: un aviso a las dos familias cada semana
  · peticiones o confirmaciones sin contestar desde hace 2 días: un aviso cada 3 días a quien le toca
"""
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta

import prestamos as P
import telegram
from basedatos import ajuste, conectar, iniciar_db

telegram.SINCRONO = True


def log(*a):
    print(*a, flush=True)


def ya_enviado(con, pid, tipo, dias=1):
    """True si este aviso ya salió en los últimos `dias` días. Si no, lo apunta."""
    desde = (date.today() - timedelta(days=dias - 1)).isoformat()
    if con.execute("SELECT 1 FROM avisos_enviados WHERE prestamo_id = ? AND tipo = ? AND fecha >= ?",
                   (pid, tipo, desde)).fetchone():
        return True
    con.execute("INSERT OR IGNORE INTO avisos_enviados (prestamo_id, tipo, fecha) VALUES (?, ?, ?)", (pid, tipo, P.hoy()))
    con.commit()
    return False


def botones(p, familia_id):
    filas = []
    acciones = [a for a in P.acciones_posibles(p, familia_id) if a not in P.DELICADAS]
    b = [(P.ETIQUETAS[a], "p:%s:%s" % (p["id"], a)) for a in acciones]
    for i in range(0, len(b), 2):
        filas.append(b[i:i + 2])
    return filas


def recordatorios(con):
    n = 0
    hoy = date.today()
    filas = con.execute("""SELECT p.*, l.titulo, fd.nombre AS duena, fd.telegram_chat AS duena_chat,
                                  fp.nombre AS prestataria, fp.telegram_chat AS prestataria_chat
                           FROM prestamos p JOIN libros l ON l.id = p.libro_id
                           JOIN familias fd ON fd.id = p.duena_id JOIN familias fp ON fp.id = p.prestataria_id
                           WHERE p.estado IN (%s)""" % ",".join("?" * len(P.ABIERTOS)), P.ABIERTOS).fetchall()
    for p in filas:
        t = "«%s»" % telegram.esc(p["titulo"])
        # 1) Fechas de devolución
        if p["estado"] == "activo" and p["vence"]:
            d = P.dias_hasta(p["vence"])
            if d == 3 and not ya_enviado(con, p["id"], "faltan3"):
                P.avisar(p["prestataria_chat"], "📅 Dentro de 3 días (el %s) toca devolver %s a %s."
                         % (P.fecha_bonita(p["vence"]), t, telegram.esc(p["duena"])), botones(p, p["prestataria_id"]))
                n += 1
            elif d == 0 and not ya_enviado(con, p["id"], "hoy"):
                P.avisar(p["prestataria_chat"], "📅 Hoy toca devolver %s a %s. Cuando lo deis, pulsad «Ya lo hemos devuelto»."
                         % (t, telegram.esc(p["duena"])), botones(p, p["prestataria_id"]))
                n += 1
            elif d is not None and d < 0 and -d % 7 == 0 and not ya_enviado(con, p["id"], "retraso"):
                semanas = -d // 7
                texto = "⏰ %s tenía que volver el %s (hace %d %s)." % (
                    t, P.fecha_bonita(p["vence"]), semanas, "semana" if semanas == 1 else "semanas")
                P.avisar(p["prestataria_chat"], texto + " ¿Podéis devolverlo o pedir más tiempo?", botones(p, p["prestataria_id"]))
                P.avisar(p["duena_chat"], texto + " Lo tiene %s." % telegram.esc(p["prestataria"]), botones(p, p["duena_id"]))
                n += 2
            continue
        # 2) Cosas sin contestar
        try:
            quieto = (datetime.now() - datetime.fromisoformat(p["actualizado"])).days
        except ValueError:
            quieto = 0
        if quieto < 2:
            continue
        if p["estado"] in ("solicitado", "aceptado", "devolucion"):
            quien, chat = p["duena_id"], p["duena_chat"]
        elif p["estado"] == "entregado":
            quien, chat = p["prestataria_id"], p["prestataria_chat"]
        else:
            continue   # disputa: la lleva la administración
        if ya_enviado(con, p["id"], "pendiente", dias=3):
            continue
        texto = {
            "solicitado": "📚 %s os pidió %s hace %d días. ¿Se lo podéis prestar?" % (telegram.esc(p["prestataria"]), t, quieto),
            "aceptado": "📦 Aceptasteis prestar %s a %s. Cuando se lo deis, pulsad «Ya se lo he dado»." % (t, telegram.esc(p["prestataria"])),
            "devolucion": "↩️ %s dice que os devolvió %s. ¿Lo tenéis?" % (telegram.esc(p["prestataria"]), t),
            "entregado": "📦 %s dice que os dio %s. ¿Lo tenéis?" % (telegram.esc(p["duena"]), t),
        }[p["estado"]]
        P.avisar(chat, texto, botones(p, quien))
        n += 1
    # Prórrogas sin contestar
    for p in filas:
        if p["estado"] == "activo" and p["prorroga_dias"] and not ya_enviado(con, p["id"], "prorroga", dias=3):
            try:
                if (datetime.now() - datetime.fromisoformat(p["actualizado"])).days < 2:
                    continue
            except ValueError:
                continue
            P.avisar(p["duena_chat"], "⏳ %s sigue esperando respuesta: pide %d días más para «%s»."
                     % (telegram.esc(p["prestataria"]), p["prorroga_dias"], telegram.esc(p["titulo"])), botones(p, p["duena_id"]))
            n += 1
    # Limpieza de avisos viejos
    con.execute("DELETE FROM avisos_enviados WHERE fecha < ?", ((hoy - timedelta(days=60)).isoformat(),))
    con.commit()
    log("Recordatorios enviados: %d" % n)
    return n


def copia(con):
    chat = ajuste(con, "telegram_admin_chat")
    if not chat or not telegram.configurado():
        log("Copia por Telegram: no hay Telegram de administración vinculado.")
        return False
    tmp = tempfile.mkdtemp()
    try:
        ruta = os.path.join(tmp, "estanteria.db")
        destino = sqlite3.connect(ruta)
        con.backup(destino)
        destino.close()
        nombre = "estanteria-%s.db.gz" % P.hoy()
        with open(ruta, "rb") as a, gzip.open(os.path.join(tmp, nombre), "wb") as b:
            shutil.copyfileobj(a, b)
        libros = con.execute("SELECT COUNT(*) FROM libros").fetchone()[0]
        fuera = con.execute("SELECT COUNT(*) FROM prestamos WHERE estado IN ('activo','devolucion','entregado')").fetchone()[0]
        disputas = con.execute("SELECT COUNT(*) FROM prestamos WHERE estado = 'disputa'").fetchone()[0]
        texto = "🗄 Copia semanal de la base de datos.\n%d libros · %d prestados ahora%s.\n(Las portadas no van aquí: están en las copias diarias del contenedor.)" % (
            libros, fuera, (" · ⚠️ %d desacuerdos sin resolver" % disputas) if disputas else "")
        ok = telegram.enviar_documento(chat, os.path.join(tmp, nombre), nombre, texto)
        log("Copia por Telegram: %s" % ("enviada" if ok else "ha fallado"))
        return bool(ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    orden = argv[1] if len(argv) > 1 else "diario"
    iniciar_db()
    con = conectar()
    if orden in ("diario", "recordatorios"):
        recordatorios(con)
    if orden == "copia" or (orden == "diario" and date.today().weekday() == 6):
        copia(con)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
