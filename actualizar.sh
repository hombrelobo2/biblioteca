#!/bin/bash
# Actualiza Nuestra Estantería a la última versión publicada en GitHub.
# Uso (como root, dentro del contenedor):  estanteria-actualizar
# Conserva la base de datos, las portadas, las contraseñas y la configuración
# (y antes de tocar nada guarda una copia de la base de datos en /var/backups/estanteria).
set -euo pipefail
REPO="${ESTANTERIA_REPO:-hombrelobo2/biblioteca}"
RAMA="${ESTANTERIA_RAMA:-main}"
[ "$(id -u)" -eq 0 ] || { echo "Ejecuta como root."; exit 1; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "==> Descargando $REPO ($RAMA)"
curl -fsSL "https://github.com/$REPO/archive/refs/heads/$RAMA.tar.gz" | tar -xz -C "$TMP" --strip-components=1
bash "$TMP/install.sh"
