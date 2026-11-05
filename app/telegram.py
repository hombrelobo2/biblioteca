"""Envío de mensajes por Telegram (avisos de préstamos y copias de seguridad).

Configuración (en /etc/estanteria/estanteria.env):
  TELEGRAM_BOT_TOKEN=123456:ABC...      (el que te da @BotFather)
Sin token, los avisos simplemente no se envían.
"""
import json
import os
import sys
import threading

import requests

API = "https://api.telegram.org/bot%s/%s"
# El bot y las tareas diarias ponen esto a True: así los mensajes salen antes de que el programa termine
SINCRONO = False


def token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def configurado():
    return bool(token())


def llamar(metodo, espera=15, files=None, **datos):
    """Llama a la API de Telegram. Devuelve el 'result' o None si falla (los avisos nunca rompen la web)."""
    if not token():
        return None
    try:
        if files:
            r = requests.post(API % (token(), metodo), data=datos, files=files, timeout=espera)
        else:
            r = requests.post(API % (token(), metodo), json=datos, timeout=espera)
        j = r.json()
        if not j.get("ok"):
            print("Telegram %s: %s" % (metodo, j.get("description")), file=sys.stderr, flush=True)
            return None
        return j.get("result")
    except (requests.RequestException, ValueError) as e:
        print("Telegram %s: %r" % (metodo, e), file=sys.stderr, flush=True)
        return None


def teclado(botones):
    """botones: [[(texto, dato), ...], ...] -> inline_keyboard. Si dato empieza por http, es un enlace."""
    filas = []
    for fila in botones or []:
        filas.append([{"text": t, "url": d} if d.startswith("http") else {"text": t, "callback_data": d[:64]}
                      for t, d in fila])
    return {"inline_keyboard": filas} if filas else None


def enviar(chat, texto, botones=None, en_segundo_plano=True):
    """Envía un mensaje (HTML sencillo). En la web se manda en segundo plano para no hacer esperar."""
    if not chat or not token():
        return None
    datos = {"chat_id": chat, "text": texto[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
    t = teclado(botones)
    if t:
        datos["reply_markup"] = t
    if en_segundo_plano and not SINCRONO:
        threading.Thread(target=llamar, args=("sendMessage",), kwargs=datos, daemon=True).start()
        return True
    return llamar("sendMessage", **datos)


def enviar_documento(chat, ruta, nombre, texto=""):
    if not chat or not token():
        return None
    with open(ruta, "rb") as f:
        return llamar("sendDocument", espera=120, files={"document": (nombre, f)},
                      chat_id=chat, caption=texto[:1000])


def esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def url_base():
    return os.environ.get("ESTANTERIA_URL", "").rstrip("/")


def enlace_cuenta():
    u = url_base()
    return (u + "/cuenta/") if u.startswith("https://") else ""
