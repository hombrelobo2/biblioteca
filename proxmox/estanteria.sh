#!/usr/bin/env bash
# Nuestra Estantería — crea un contenedor LXC en Proxmox VE y lo deja funcionando.
#
# Ejecútalo en la consola del host de Proxmox (como root):
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/hombrelobo2/biblioteca/main/proxmox/estanteria.sh)"
#
# Para actualizar un contenedor ya creado:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/hombrelobo2/biblioteca/main/proxmox/estanteria.sh)" -- actualizar 112
#
# Sin preguntas (todo con variables):
#   CT_ID, CT_HOSTNAME, CT_CORES, CT_RAM, CT_SWAP, CT_DISCO (GB), CT_ALMACEN, CT_PUENTE, CT_IP (dhcp o 192.168.1.50/24),
#   CT_GW, CT_DNS, CT_VLAN, CT_CLAVE_ROOT, ESTANTERIA_ADMIN_PASS, ESTANTERIA_URL, TELEGRAM_BOT_TOKEN, GEMINI_API_KEY,
#   y SIN_PREGUNTAS=1
set -Eeuo pipefail

REPO="${ESTANTERIA_REPO:-hombrelobo2/biblioteca}"
RAMA="${ESTANTERIA_RAMA:-main}"

# ---------- Presentación ----------
if [ -t 1 ]; then
  V=$'\e[32m'; A=$'\e[33m'; R=$'\e[31m'; N=$'\e[1m'; X=$'\e[0m'
else
  V=""; A=""; R=""; N=""; X=""
fi
ok()   { echo "${V}✔${X} $*"; }
info() { echo "${A}•${X} $*"; }
fallo(){ echo "${R}✘ $*${X}" >&2; exit 1; }
trap 'fallo "Algo ha fallado en la línea $LINENO. Revisa los mensajes de arriba."' ERR

preguntar() {   # preguntar VARIABLE "Texto" "por defecto"
  local var=$1 texto=$2 defecto=${3:-} resp
  if [ -n "${!var:-}" ]; then return; fi
  if [ "${SIN_PREGUNTAS:-0}" = "1" ]; then printf -v "$var" '%s' "$defecto"; return; fi
  read -r -p "  $texto [${defecto}]: " resp </dev/tty || true
  printf -v "$var" '%s' "${resp:-$defecto}"
}

preguntar_secreto() {   # preguntar_secreto VARIABLE "Texto" (vacío = se inventa)
  local var=$1 texto=$2 c1 c2
  if [ -n "${!var:-}" ] || [ "${SIN_PREGUNTAS:-0}" = "1" ]; then return; fi
  while true; do
    read -r -s -p "  $texto (Enter = inventar una): " c1 </dev/tty || true; echo
    [ -z "$c1" ] && return
    read -r -s -p "  Repítela: " c2 </dev/tty || true; echo
    if [ "$c1" = "$c2" ] && [ ${#c1} -ge 8 ]; then printf -v "$var" '%s' "$c1"; return; fi
    echo "  No coinciden o tiene menos de 8 caracteres."
  done
}

inventar_clave() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16 || true; }

# ---------- Comprobaciones ----------
[ "$(id -u)" -eq 0 ] || fallo "Ejecuta este script como root en el host de Proxmox."
command -v pct >/dev/null && command -v pveam >/dev/null || fallo "Este script se ejecuta en el host de Proxmox VE (no se encuentra 'pct')."

# ---------- Modo actualizar ----------
if [ "${1:-}" = "actualizar" ]; then
  ID="${2:-}"
  [ -n "$ID" ] || fallo "Indica el número del contenedor: … actualizar 112"
  pct status "$ID" >/dev/null 2>&1 || fallo "No existe el contenedor $ID."
  info "Actualizando Nuestra Estantería en el contenedor $ID"
  if pct exec "$ID" -- test -x /usr/local/sbin/estanteria-actualizar; then
    pct exec "$ID" -- env ESTANTERIA_REPO="$REPO" ESTANTERIA_RAMA="$RAMA" /usr/local/sbin/estanteria-actualizar
  else
    # Instalaciones antiguas (sin el comando): se descarga y se instala igual
    pct exec "$ID" -- bash -c "set -e; apt-get install -y -qq curl ca-certificates >/dev/null; T=\$(mktemp -d); \
      curl -fsSL https://github.com/$REPO/archive/refs/heads/$RAMA.tar.gz | tar -xz -C \$T --strip-components=1; \
      bash \$T/install.sh; rm -rf \$T"
  fi
  ok "Actualizado."
  exit 0
fi

echo
echo "${N}📚 Nuestra Estantería — nuevo contenedor LXC para Proxmox${X}"
echo "   Debian 13 · nginx · Python · SQLite. Pulsa Enter para aceptar lo que va entre corchetes."
echo

# ---------- Valores ----------
SIG_ID=$(pvesh get /cluster/nextid 2>/dev/null || echo 200)
ALMACEN_DEF=$(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')
ALMACEN_DEF=${ALMACEN_DEF:-local-lvm}
PLANTILLAS=$(pvesm status -content vztmpl 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')
PLANTILLAS=${PLANTILLAS:-local}

echo "${N}Contenedor${X}"
preguntar CT_ID       "Número del contenedor" "$SIG_ID"
preguntar CT_HOSTNAME "Nombre" "estanteria"
preguntar CT_ALMACEN  "Almacenamiento del disco" "$ALMACEN_DEF"
preguntar CT_DISCO    "Disco en GB (las portadas ocupan ~50 KB cada una)" "4"
preguntar CT_CORES    "Núcleos" "1"
preguntar CT_RAM      "Memoria en MB" "512"
preguntar CT_SWAP     "Swap en MB" "512"
echo "${N}Red${X}"
preguntar CT_PUENTE   "Puente de red" "vmbr0"
preguntar CT_IP       "IP: 'dhcp' o fija con máscara, p. ej. 192.168.1.50/24" "dhcp"
if [ "$CT_IP" != "dhcp" ]; then
  GW_DEF=$(ip route 2>/dev/null | awk '/^default/ {print $3; exit}' || true)
  preguntar CT_GW     "Puerta de enlace" "${GW_DEF:-192.168.1.1}"
fi
preguntar CT_DNS      "Servidor DNS (vacío = el del host)" ""
preguntar CT_VLAN     "VLAN (vacío = ninguna)" ""
echo "${N}Contraseñas${X}"
preguntar_secreto CT_CLAVE_ROOT "Contraseña de root del contenedor"
preguntar_secreto ESTANTERIA_ADMIN_PASS "Contraseña de administración de la estantería"
echo "${N}Opcional (se puede rellenar después en /etc/estanteria/estanteria.env)${X}"
preguntar ESTANTERIA_URL     "Dirección pública con https:// (para invitaciones y Telegram)" ""
preguntar TELEGRAM_BOT_TOKEN "Token del bot de Telegram (de @BotFather)" ""
preguntar GEMINI_API_KEY     "Clave de Gemini para leer fotos de lomos" ""

CT_CLAVE_ROOT=${CT_CLAVE_ROOT:-$(inventar_clave)}
ESTANTERIA_ADMIN_PASS=${ESTANTERIA_ADMIN_PASS:-$(inventar_clave)}
CT_GW=${CT_GW:-}

pct status "$CT_ID" >/dev/null 2>&1 && fallo "Ya existe un contenedor con el número $CT_ID."
[[ "$CT_ID" =~ ^[0-9]+$ ]] || fallo "El número del contenedor tiene que ser un número."

# ---------- Plantilla Debian 13 ----------
info "Buscando la plantilla de Debian 13"
pveam update >/dev/null 2>&1 || info "No se ha podido actualizar la lista de plantillas; sigo con la que haya."
PLANTILLA=$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep -E '^debian-13-standard_.*_amd64\.tar\.(zst|gz|xz)$' | sort -V | tail -n1 || true)
if [ -z "$PLANTILLA" ]; then
  PLANTILLA=$(pveam list "$PLANTILLAS" 2>/dev/null | awk '{print $1}' | sed 's|.*/||' | grep -E '^debian-13-standard' | sort -V | tail -n1 || true)
fi
[ -n "$PLANTILLA" ] || fallo "No encuentro la plantilla debian-13-standard. Descárgala en la web de Proxmox (local → Plantillas CT)."
if ! pveam list "$PLANTILLAS" 2>/dev/null | grep -q "$PLANTILLA"; then
  info "Descargando $PLANTILLA"
  pveam download "$PLANTILLAS" "$PLANTILLA" >/dev/null
fi
ok "Plantilla: $PLANTILLA"

# ---------- Crear el contenedor ----------
RED="name=eth0,bridge=$CT_PUENTE,ip=$CT_IP"
[ -n "$CT_GW" ] && RED="$RED,gw=$CT_GW"
[ -n "$CT_VLAN" ] && RED="$RED,tag=$CT_VLAN"
OPC=()
[ -n "$CT_DNS" ] && OPC+=(--nameserver "$CT_DNS")

info "Creando el contenedor $CT_ID ($CT_HOSTNAME)"
pct create "$CT_ID" "$PLANTILLAS:vztmpl/$PLANTILLA" \
  --hostname "$CT_HOSTNAME" --cores "$CT_CORES" --memory "$CT_RAM" --swap "$CT_SWAP" \
  --rootfs "$CT_ALMACEN:$CT_DISCO" --net0 "$RED" "${OPC[@]}" \
  --unprivileged 1 --features nesting=1 --onboot 1 --ostype debian \
  --password "$CT_CLAVE_ROOT" --tags estanteria \
  --description "Nuestra Estantería — https://github.com/$REPO" >/dev/null
ok "Contenedor creado"

pct start "$CT_ID"
info "Esperando a que tenga red"
for _ in $(seq 1 60); do
  if pct exec "$CT_ID" -- getent hosts deb.debian.org >/dev/null 2>&1; then break; fi
  sleep 2
done
pct exec "$CT_ID" -- getent hosts deb.debian.org >/dev/null 2>&1 \
  || fallo "El contenedor no llega a internet. Revisa la IP, la puerta de enlace y el DNS (pct config $CT_ID)."
ok "Red lista"

# Misma zona horaria que el host (los recordatorios salen a las 10:00 de esa hora)
ZONA=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo "Etc/UTC")
pct exec "$CT_ID" -- bash -c "ln -sf /usr/share/zoneinfo/$ZONA /etc/localtime && echo $ZONA > /etc/timezone" || true

# ---------- Instalar la estantería ----------
info "Instalando Nuestra Estantería (tarda unos minutos)"
TMPENV=$(mktemp)
chmod 600 "$TMPENV"
{
  printf 'export ESTANTERIA_ADMIN_PASS=%q\n' "$ESTANTERIA_ADMIN_PASS"
  printf 'export ESTANTERIA_URL=%q\n' "$ESTANTERIA_URL"
  printf 'export TELEGRAM_BOT_TOKEN=%q\n' "$TELEGRAM_BOT_TOKEN"
  printf 'export GEMINI_API_KEY=%q\n' "$GEMINI_API_KEY"
} > "$TMPENV"
pct push "$CT_ID" "$TMPENV" /root/.estanteria-instalar.env --perms 600
rm -f "$TMPENV"
pct exec "$CT_ID" -- bash -c "set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq curl ca-certificates locales >/dev/null
  sed -i 's/^# *es_ES.UTF-8/es_ES.UTF-8/' /etc/locale.gen && locale-gen >/dev/null 2>&1 || true
  T=\$(mktemp -d)
  curl -fsSL https://github.com/$REPO/archive/refs/heads/$RAMA.tar.gz | tar -xz -C \$T --strip-components=1
  . /root/.estanteria-instalar.env
  rm -f /root/.estanteria-instalar.env
  bash \$T/install.sh </dev/null
  rm -rf \$T"
ok "Instalada"

IP=$(pct exec "$CT_ID" -- ip -4 -o addr show dev eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
echo
echo "${N}🎉 Listo${X}"
echo "   Estantería:       http://$IP/"
echo "   Administración:   http://$IP/admin/"
echo "   Contraseña admin: $ESTANTERIA_ADMIN_PASS"
echo "   Root del LXC:     $CT_CLAVE_ROOT   (entra con: pct enter $CT_ID)"
echo
echo "   Apunta las contraseñas: no se vuelven a mostrar."
echo "   Siguiente paso: publícala con HTTPS (Nginx Proxy Manager, Cloudflare Tunnel…) y mira la guía:"
echo "   https://github.com/$REPO#después-de-instalar"
