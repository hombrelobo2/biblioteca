# Avisos por Telegram

Con Telegram, las familias reciben los avisos al momento y pueden confirmar con un botón, sin entrar en la web. Es opcional: sin Telegram todo funciona desde «Mi cuenta».

## 1. Crear el bot

1. En Telegram, abre **@BotFather** y escribe `/newbot`.
2. Ponle un nombre («Estantería del cole») y un usuario que acabe en `bot` (`estanteria_del_cole_bot`).
3. Copia el **token** que te da (algo como `123456789:AAH…`).

Usa un bot **solo para la estantería**: la aplicación le pregunta a Telegram cada poco si hay mensajes nuevos, y no puede compartir el bot con otro programa ni con un webhook.

## 2. Configurarlo

En el contenedor, edita `/etc/estanteria/estanteria.env`:

```
TELEGRAM_BOT_TOKEN=123456789:AAH…
ESTANTERIA_URL=https://estanteria.midominio.com
```

y reinicia:

```bash
systemctl restart estanteria estanteria-bot
```

No hace falta abrir puertos: el bot sale a internet, nadie entra.

## 3. Vincular

- **Tú (administración):** en `/admin` → Familias → «Vincular mi Telegram». Se abre Telegram: pulsa **Iniciar**. Recibirás los desacuerdos y, cada domingo, una copia de la base de datos. Con «Enviar mensaje de prueba» compruebas que funciona.
- **Cada familia:** en «Mi cuenta» → «Vincular Telegram» → **Iniciar** en Telegram.

Los enlaces de vinculación valen 24 horas y una sola vez. Un mismo Telegram puede ser a la vez el de la administración y el de una familia.

## Qué se puede hacer desde el bot

- Pulsar los botones de cada aviso (Aceptar, Lo tenemos, Recibido, Dar más tiempo…). El mensaje se actualiza con lo que has hecho.
- `/prestamos`: lista los préstamos en marcha, con sus botones.
- `/ayuda`.

Lo más delicado (marcar un libro como perdido) solo se hace desde la web.

## Problemas

**No llega nada.** Mira `journalctl -u estanteria-bot -n 50`:

- `No hay TELEGRAM_BOT_TOKEN`: falta el token o no has reiniciado.
- `Unauthorized`: el token está mal copiado.
- `Conflict: terminated by other getUpdates request` o `can't use getUpdates method while webhook is active`: el bot se está usando en otro sitio. Crea uno nuevo para la estantería.

**El botón «Vincular Telegram» no aparece.** Falta `TELEGRAM_BOT_TOKEN` o no se ha reiniciado el servicio `estanteria`.

**El enlace de invitación sale con `http://IP`.** Rellena `ESTANTERIA_URL`.
