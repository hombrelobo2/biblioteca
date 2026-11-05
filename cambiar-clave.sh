#!/bin/bash
# Cambia la contraseña de administración de Nuestra Estantería.
# Uso (como root):  estanteria-cambiar-clave
set -euo pipefail
ENV=/etc/estanteria/estanteria.env
[ "$(id -u)" -eq 0 ] || { echo "Ejecuta como root."; exit 1; }
while true; do
  read -r -s -p "Nueva contraseña: " C1; echo
  read -r -s -p "Repite la contraseña: " C2; echo
  [ "$C1" = "$C2" ] && [ ${#C1} -ge 8 ] && break
  echo "No coinciden o tiene menos de 8 caracteres."
done
HASH=$(C="$C1" /opt/estanteria/venv/bin/python -c 'import os;from werkzeug.security import generate_password_hash as g;print(g(os.environ["C"]))')
grep -v '^ESTANTERIA_ADMIN_HASH=' "$ENV" > "$ENV.tmp"
echo "ESTANTERIA_ADMIN_HASH='$HASH'" >> "$ENV.tmp"
chown root:estanteria "$ENV.tmp"; chmod 640 "$ENV.tmp"; mv "$ENV.tmp" "$ENV"
# Si se cambió desde la web, esa manda: se borra para que valga esta (y se cierran las sesiones abiertas)
sqlite3 /var/lib/estanteria/estanteria.db "DELETE FROM ajustes WHERE clave = 'admin_hash'; INSERT OR REPLACE INTO ajustes (clave, valor) VALUES ('admin_ver', hex(randomblob(8)));" 2>/dev/null || true
systemctl restart estanteria
echo "Contraseña cambiada."
