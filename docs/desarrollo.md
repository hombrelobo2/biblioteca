# Desarrollo

## Cómo está hecho

```
public/index.html        Estantería pública: una sola página, sin dependencias. Lee libros.json y resumenes.json.
app/
  estanteria.py          Aplicación Flask: JSON público, /admin (bibliotecas, libros, nuevas entradas, completar, colecciones, copia)
  cuenta.py              /cuenta: zona de las familias y API para la página pública (/cuenta/api/…)
  admin_prestamos.py     /admin/familias y /admin/prestamos
  prestamos.py           Lógica de los préstamos (estados, permisos, avisos). La usan la web, el bot y las tareas.
  basedatos.py           Esquema SQLite, migraciones, ajustes y enlaces de un solo uso
  busqueda.py            Búsqueda en Todostuslibros, Elkar, Google Books y Open Library
  completar.py           «Completar datos que faltan»
  ia.py                  Gemini: leer fotos de lomos y escribir resúmenes
  telegram.py            Envío de mensajes a Telegram
  bot.py                 Bot de Telegram (long polling), servicio estanteria-bot
  tareas.py              Recordatorios diarios, copia semanal y aviso de versión nueva, estanteria-tareas.timer
  sistema.py             /admin/actualizar: versión instalada, versión en GitHub e instalación
  importar.py            Importa una estantería antigua (libros.json + portadas)
  templates/             Plantillas Jinja de /admin y /cuenta
deploy/                  nginx, systemd y copia diaria
docs/guia/               Fuente de la guía para niños (HTML) y generar.js, que crea docs/guia-para-ninos.pdf
docs/manual/             Fuente del manual de administración y generar.js, que crea docs/manual-administracion.pdf
install.sh               Instalación o actualización en Debian 13
actualizar.sh            Descarga la última versión de GitHub y ejecuta install.sh
proxmox/estanteria.sh    Crea el LXC en Proxmox y lo instala
```

Decisiones:

- **SQLite** en modo WAL: un solo archivo, sin servidor aparte. Sobra para cientos de familias.
- **La página pública es estática.** nginx la sirve directamente con las portadas, y los datos llegan en dos JSON con ETag. Los resúmenes se cargan aparte, solo al abrir un libro.
- **Sin dependencias de JavaScript** ni paso de compilación.
- Seguridad:
  - Token CSRF en todos los formularios y llamadas POST.
  - Límite de intentos al entrar.
  - Cookies `HttpOnly`/`SameSite=Lax`, marcadas como `Secure` cuando la visita llega por HTTPS (`ProxyFix` + `X-Forwarded-Proto`).
  - Contraseñas con hash de werkzeug.
  - Enlaces de un solo uso guardados como sha256.

## Estados de un préstamo

```
solicitado ─aceptar─▶ aceptado ─entregar─▶ entregado ─recibido─▶ activo ─devuelto─▶ devolucion ─confirmar_devolucion─▶ devuelto
    │rechazar/cancelar     │cancelar          │no_recibido ▶ disputa  │                   │no_llegado ▶ disputa
    ▼                      ▼                  │cancelar (dueña)       ├ confirmar_devolucion (dueña) ▶ devuelto
 rechazado/cancelado    cancelado             ▼                       ├ pedir_prorroga / aceptar_prorroga / rechazar_prorroga
                                           cancelado                  └ perdido (dueña)
```

Las disputas las resuelve la administración (`prestamos.resolver`). Quién puede hacer cada acción y desde qué estado está en `prestamos.ACCIONES`.

## Probarlo en tu ordenador

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
mkdir -p /tmp/estanteria
export ESTANTERIA_DATA=/tmp/estanteria
export ESTANTERIA_ADMIN_HASH="$(python -c 'from werkzeug.security import generate_password_hash as g; print(g("prueba123"))')"
cd app && flask --app estanteria run --port 8000
```

- Administración en <http://127.0.0.1:8000/admin/> (contraseña `prueba123`).
- La página pública la sirve nginx en producción. Para verla en local, ábrela con cualquier servidor estático que pase `/libros.json`, `/resumenes.json`, `/portadas/` y `/cuenta` al puerto 8000.
- Sin `TELEGRAM_BOT_TOKEN` los avisos simplemente no se envían.

## Publicar una versión

1. Sube el número en `VERSION` (por ejemplo `1.2.0`).
2. Añade arriba en `CHANGELOG.md` una sección `## 1.2.0 — AAAA-MM-DD` con una línea por novedad que empiece por `- `. Es lo que ven los administradores antes de actualizar, así que escríbelas para ellos.
3. Súbelo a `main`. Las instalaciones lo verán en `/admin` → Actualizar (como mucho 6 horas después, o al pulsar «Buscar ahora»).

## Contribuir

Los textos de la interfaz están en español y van dirigidos a familias. Mantén ese tono: frases cortas y claras, sin tecnicismos. Si añades algo que guarda datos, añade la columna en `basedatos.iniciar_db()` para que las instalaciones antiguas se actualicen solas.
