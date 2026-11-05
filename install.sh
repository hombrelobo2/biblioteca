#!/bin/bash
# Nuestra Estantería — instalación o actualización dentro de un LXC Debian 13 (Proxmox 9).
# Uso (como root, dentro del contenedor):   bash install.sh
# Se puede volver a ejecutar para actualizar: conserva la base de datos, las portadas y la contraseña.
#
# Instalación sin preguntas (la usa el script de Proxmox). Solo se leen la primera vez:
#   ESTANTERIA_ADMIN_PASS   contraseña de administración (si falta y no hay terminal, se inventa una y se muestra)
#   ESTANTERIA_URL          dirección pública, con https://
#   TELEGRAM_BOT_TOKEN      token del bot de Telegram (@BotFather)
#   GEMINI_API_KEY          clave de Gemini para leer fotos de lomos
#   GOOGLE_BOOKS_KEY        clave de Google Books (opcional)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "Ejecuta este script como root."; exit 1; fi
ORIGEN="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/estanteria
DATA=/var/lib/estanteria
ETC=/etc/estanteria

echo "==> Instalando paquetes del sistema"
export DEBIAN_FRONTEND=noninteractive
# Evita los avisos de «locale» cuando el idioma de quien conecta no está instalado en el contenedor
locale -a 2>/dev/null | grep -qix "${LANG:-C.UTF-8}" || export LANG=C.UTF-8 LC_ALL=C.UTF-8
apt-get update -qq
apt-get install -y -qq python3 python3-venv nginx sqlite3 curl ca-certificates >/dev/null

if [ -f "$DATA/estanteria.db" ]; then
  echo "==> Copia de la base de datos antes de actualizar"
  mkdir -p /var/backups/estanteria
  COPIA="/var/backups/estanteria/antes-de-actualizar-$(date +%F-%H%M).db"
  sqlite3 "$DATA/estanteria.db" ".backup '$COPIA'" && echo "    Guardada en $COPIA"
  # Solo se guardan las 10 últimas de este tipo
  ls -1t /var/backups/estanteria/antes-de-actualizar-*.db 2>/dev/null | tail -n +11 | xargs -r rm -f
fi

echo "==> Usuario y carpetas"
id estanteria >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin estanteria
mkdir -p "$APP" "$DATA/portadas" "$ETC" /var/backups/estanteria
rm -rf "$APP/app" "$APP/public"
cp -r "$ORIGEN/app" "$ORIGEN/public" "$APP/"
cp "$ORIGEN/requirements.txt" "$APP/"
chown -R root:root "$APP"
chown -R estanteria:estanteria "$DATA"
chmod 750 "$DATA"
chmod 755 "$DATA/portadas"
# nginx (www-data) tiene que poder leer las portadas
usermod -a -G estanteria www-data

echo "==> Entorno de Python"
[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

if [ ! -f "$ETC/estanteria.env" ]; then
  echo "==> Contraseña de administración (solo para ti; las familias no la necesitan)"
  CLAVE_INVENTADA=""
  if [ -n "${ESTANTERIA_ADMIN_PASS:-}" ]; then
    C1="$ESTANTERIA_ADMIN_PASS"
  elif [ -t 0 ]; then
    while true; do
      read -r -s -p "Contraseña: " C1; echo
      read -r -s -p "Repite la contraseña: " C2; echo
      [ "$C1" = "$C2" ] && [ ${#C1} -ge 8 ] && break
      echo "No coinciden o tiene menos de 8 caracteres. Prueba otra vez."
    done
  else
    C1=$("$APP/venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(12))')
    CLAVE_INVENTADA="$C1"
  fi
  HASH=$(C="$C1" "$APP/venv/bin/python" -c 'import os;from werkzeug.security import generate_password_hash as g;print(g(os.environ["C"]))')
  SECRET=$("$APP/venv/bin/python" -c 'import secrets;print(secrets.token_hex(32))')
  cat > "$ETC/estanteria.env" <<EOF
ESTANTERIA_DATA=$DATA
ESTANTERIA_SECRET_KEY=$SECRET
ESTANTERIA_ADMIN_HASH='$HASH'
# Déjalo en 0: la cookie se marca como segura sola cuando la visita llega por HTTPS
ESTANTERIA_COOKIE_SECURE=0
# Opcional: clave de la API de Google Books (sin ella también funciona, con un límite diario)
GOOGLE_BOOKS_KEY=${GOOGLE_BOOKS_KEY:-}
# Clave de Gemini (Google AI Studio) para leer fotos de lomos. Vacía = botón desactivado
GEMINI_API_KEY=${GEMINI_API_KEY:-}
# Opcional: modelo de Gemini (por defecto prueba gemini-flash-latest, gemini-3.8-flash y gemini-flash-lite-latest)
GEMINI_MODEL=
EOF
  chown root:estanteria "$ETC/estanteria.env"
  chmod 640 "$ETC/estanteria.env"
fi
# Ajustes nuevos de los préstamos (se añaden a instalaciones antiguas sin tocar lo demás)
if ! grep -q '^TELEGRAM_BOT_TOKEN=' "$ETC/estanteria.env"; then
  printf '%s\n' "# Préstamos: token del bot de Telegram (te lo da @BotFather). Vacío = sin avisos por Telegram" \
    "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}" >> "$ETC/estanteria.env"
fi
if ! grep -q '^ESTANTERIA_URL=' "$ETC/estanteria.env"; then
  printf '%s\n' "# Dirección pública de la estantería, con https:// (para los enlaces de invitación y de Telegram)" \
    "ESTANTERIA_URL=${ESTANTERIA_URL:-}" >> "$ETC/estanteria.env"
fi

echo "==> Base de datos"
runuser -u estanteria -- env ESTANTERIA_DATA="$DATA" "$APP/venv/bin/python" -c 'import sys;sys.path.insert(0,"'"$APP"'/app");import estanteria'
LIBROS=$(sqlite3 "$DATA/estanteria.db" "SELECT COUNT(*) FROM libros;")
if [ "$LIBROS" = "0" ] && [ -f "$ORIGEN/datos-iniciales/libros.json" ]; then
  # El usuario de la aplicación no puede leer /root: copiamos los datos a un sitio legible
  rm -rf "$APP/datos-iniciales"
  cp -r "$ORIGEN/datos-iniciales" "$APP/datos-iniciales"
  chmod -R a+rX "$APP/datos-iniciales"
  runuser -u estanteria -- env ESTANTERIA_DATA="$DATA" "$APP/venv/bin/python" "$APP/app/importar.py" "$APP/datos-iniciales"
elif [ "$LIBROS" = "0" ]; then
  echo "    Base de datos nueva y vacía: añade bibliotecas y libros desde /admin."
else
  echo "    La base de datos ya tiene $LIBROS libros: se conserva tal cual."
fi

echo "==> Servicios"
cp "$ORIGEN/deploy/estanteria.service" "$ORIGEN/deploy/estanteria-bot.service" \
   "$ORIGEN/deploy/estanteria-tareas.service" "$ORIGEN/deploy/estanteria-tareas.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q estanteria estanteria-bot estanteria-tareas.timer
systemctl restart estanteria estanteria-bot
systemctl start estanteria-tareas.timer

echo "==> nginx"
cp "$ORIGEN/deploy/estanteria-proxy.conf" /etc/nginx/estanteria-proxy.conf
cp "$ORIGEN/deploy/estanteria-map.conf" /etc/nginx/conf.d/estanteria-map.conf
if [ ! -f /etc/nginx/sites-available/estanteria ]; then
  cp "$ORIGEN/deploy/nginx-estanteria.conf" /etc/nginx/sites-available/estanteria
else
  echo "    /etc/nginx/sites-available/estanteria ya existe: se conserva (por si lo has cambiado)."
  if ! grep -q 'location /cuenta' /etc/nginx/sites-available/estanteria; then
    # Versión nueva: añadir la zona de familias (/cuenta) justo antes de /admin
    cp /etc/nginx/sites-available/estanteria /etc/nginx/sites-available/estanteria.antes-de-prestamos
    python3 "$ORIGEN/deploy/nginx-anadir-cuenta.py" /etc/nginx/sites-available/estanteria
    echo "    Añadida la zona de familias (/cuenta) a la configuración de nginx."
  fi
fi
ln -sf /etc/nginx/sites-available/estanteria /etc/nginx/sites-enabled/estanteria
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl reload nginx || systemctl restart nginx

echo "==> Copias de seguridad diarias"
install -m 755 "$ORIGEN/deploy/estanteria-copia" /etc/cron.daily/estanteria-copia
install -m 755 "$ORIGEN/cambiar-clave.sh" /usr/local/sbin/estanteria-cambiar-clave
install -m 755 "$ORIGEN/actualizar.sh" /usr/local/sbin/estanteria-actualizar

sleep 1
if curl -fsS -o /dev/null http://127.0.0.1/libros.json 2>/dev/null || wget -q -O /dev/null http://127.0.0.1/libros.json 2>/dev/null; then
  OK="funcionando"
else
  OK="instalada, pero no responde todavía: mira 'journalctl -u estanteria -n 50'"
fi
IP=$(hostname -I | awk '{print $1}')
echo
echo "Nuestra Estantería está $OK."
echo "  Estantería para las familias:  http://$IP/"
echo "  Administración (solo tú):      http://$IP/admin/"
echo "  Cuentas de familia:            http://$IP/cuenta/"
if ! grep -q '^TELEGRAM_BOT_TOKEN=..*' "$ETC/estanteria.env"; then
  echo
  echo "Para los avisos por Telegram: rellena TELEGRAM_BOT_TOKEN y ESTANTERIA_URL en $ETC/estanteria.env"
  echo "y después ejecuta:  systemctl restart estanteria estanteria-bot"
fi
if [ -n "${CLAVE_INVENTADA:-}" ]; then
  echo
  echo "  Contraseña de administración: $CLAVE_INVENTADA"
  echo "  (apúntala; puedes cambiarla en /admin → Familias o con 'estanteria-cambiar-clave')"
fi
