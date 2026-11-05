# Configuración

Toda la configuración está en `/etc/estanteria/estanteria.env`, dentro del contenedor. Después de cambiar algo:

```bash
systemctl restart estanteria estanteria-bot
```

| Variable | Para qué sirve | Por defecto |
|---|---|---|
| `ESTANTERIA_URL` | Dirección pública, con `https://`. Se usa para los enlaces de invitación y el botón «Abrir mi cuenta» de Telegram. | vacía |
| `TELEGRAM_BOT_TOKEN` | Token del bot de Telegram ([cómo crearlo](telegram.md)). Vacío = sin avisos. | vacío |
| `GEMINI_API_KEY` | Clave de Google AI Studio. Activa la lectura de fotos de lomos y los resúmenes automáticos. | vacía |
| `GEMINI_MODEL` | Modelo de Gemini que se prueba primero. Si falla, se prueban otros solos. | vacío |
| `GOOGLE_BOOKS_KEY` | Clave de la *Books API* de Google Cloud. Sin ella también funciona, pero con un límite diario pequeño (errores 429). | vacía |
| `ESTANTERIA_PLAZO_DIAS` | Días que dura un préstamo. | `21` |
| `ESTANTERIA_COOKIE_SECURE` | Déjalo en `0`. La cookie se marca como segura sola cuando la visita llega por HTTPS; con `1` no podrías entrar a `/admin` por `http://IP`. | `0` |
| `ESTANTERIA_REPO` | Repositorio de GitHub del que se comprueban e instalan las actualizaciones (`usuario/repo`). | `hombrelobo2/biblioteca` |
| `ESTANTERIA_DATA` | Carpeta de la base de datos y las portadas. | `/var/lib/estanteria` |
| `ESTANTERIA_SECRET_KEY` | Clave para firmar las sesiones. La inventa el instalador; si la cambias, se cierran todas las sesiones. | aleatoria |
| `ESTANTERIA_ADMIN_HASH` | Huella de la contraseña de administración inicial. Si la cambias desde la web, manda la de la web. | la del instalador |

## Contraseña de administración

- **Cambiarla:** en `/admin` → Familias → «Contraseña de administración». Al cambiarla se cierran las sesiones abiertas en otros aparatos.
- **La has olvidado:** dentro del contenedor ejecuta `estanteria-cambiar-clave`.

## Ajustes guardados en la base de datos

Algunas cosas se cambian desde la web y se guardan en la tabla `ajustes`: el Telegram de la administración, la contraseña cambiada desde la web y el nombre del bot. No hace falta tocarlas a mano.
