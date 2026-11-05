# Instalación y mantenimiento

## Qué se instala y dónde

| Pieza | Qué hace | Dónde |
|---|---|---|
| nginx | Sirve la estantería pública y las portadas y pasa el resto a la aplicación | `/etc/nginx/sites-available/estanteria` |
| Aplicación (Flask + gunicorn) | Datos para la página pública, `/cuenta` (familias) y `/admin` | `/opt/estanteria/app`, servicio `estanteria` |
| Bot de Telegram | Avisos con botones y vinculación de cuentas | servicio `estanteria-bot` |
| Tareas diarias | Recordatorios a las 10:00 y copia semanal (domingo) por Telegram | `estanteria-tareas.timer` |
| Base de datos SQLite | Bibliotecas, libros, familias y préstamos | `/var/lib/estanteria/estanteria.db` |
| Portadas | Una imagen por libro, tal como llega de la librería | `/var/lib/estanteria/portadas/` |
| Configuración | Claves y opciones ([ver](configuracion.md)) | `/etc/estanteria/estanteria.env` |
| Copias | Diarias (14 días) y antes de cada actualización (10 últimas) | `/var/backups/estanteria/` |
| Actualizador | Instala la versión nueva cuando lo pides desde `/admin` → Actualizar | `estanteria-actualizar.path` y `.service` |

Todo funciona con un usuario sin privilegios (`estanteria`). Con 512 MB de RAM y 4 GB de disco va sobrado: 250 libros con portada ocupan unos 15 MB.

## Crear el contenedor a mano

Si prefieres no usar el script de [`proxmox/estanteria.sh`](../proxmox/estanteria.sh), en el host de Proxmox:

```bash
pveam update
pveam available | grep debian-13                       # nombre exacto de la plantilla
pveam download local debian-13-standard_13.1-2_amd64.tar.zst

pct create 120 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --hostname estanteria --cores 1 --memory 512 --swap 512 \
  --rootfs local-lvm:4 --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 --features nesting=1 --onboot 1
pct start 120
pct enter 120
```

Y dentro del contenedor sigue la [instalación en cualquier Debian 13](../README.md#instalación-en-cualquier-debian-13).

## Publicarla en internet

La aplicación escucha en el puerto 80 del contenedor. Ponle delante un proxy con HTTPS:

- **Nginx Proxy Manager:** un *Proxy Host* hacia `http://IP-DEL-CONTENEDOR:80`, con certificado de Let's Encrypt y «Force SSL».
- **Cloudflare Tunnel:** un *Public Hostname* hacia `http://IP-DEL-CONTENEDOR:80`.

Después pon la dirección en `ESTANTERIA_URL` ([configuración](configuracion.md)).

**Qué tiene que verse desde fuera:** `/`, `/libros.json`, `/resumenes.json`, `/portadas/` y `/cuenta` (la zona de las familias).

**`/admin` mejor solo desde casa.** Dos maneras de hacerlo:

- En el proxy: en Nginx Proxy Manager, pestaña *Custom Locations*, crea la ubicación `/admin` hacia la misma IP y puerto, pulsa el engranaje ⚙ y escribe `allow 192.168.1.0/24; deny all;` (ajusta tu red).
- En el contenedor: en `/etc/nginx/sites-available/estanteria`, dentro de `location /admin`, quita las almohadillas de `allow 192.168.1.0/24; deny all;`, ajusta la red y ejecuta `systemctl reload nginx`. **Ojo:** si el proxy está en tu misma red, todas las visitas parecen venir de la IP del proxy, así que esta opción solo sirve si el proxy está en otra red. Entonces bloquéalo en el proxy.

## Copias de seguridad

| Copia | Cuándo | Dónde |
|---|---|---|
| Base de datos + portadas | Cada día, se guardan 14 días | `/var/backups/estanteria/` dentro del contenedor |
| Base de datos | Antes de cada instalación o actualización (10 últimas) | `/var/backups/estanteria/antes-de-actualizar-*.db` |
| Base de datos | Cada domingo, por Telegram, si has vinculado el de administración | tu chat con el bot |
| Todo en un .zip | Cuando pulses el botón | `/admin` → «Descargar copia de seguridad» |

Las copias de dentro del contenedor no te salvan si se rompe el disco: haz también copias del contenedor en Proxmox (*Datacenter → Backup*) o descarga el .zip de vez en cuando.

### Restaurar

**Desde la web (lo más fácil):** en el panel de `/admin` → Copia de seguridad → «Importar una copia de seguridad». Sirve el .zip descargado del panel (base de datos y portadas) o la base de datos sola (`.db`, o el `.db.gz` que llega por Telegram). Se sustituyen todos los datos por los de la copia; lo que había se guarda antes en `/var/lib/estanteria/copias/` (las 5 últimas).

**A mano, dentro del contenedor:**

```bash
systemctl stop estanteria estanteria-bot
cp /var/backups/estanteria/estanteria-AAAA-MM-DD.db /var/lib/estanteria/estanteria.db
tar -xzf /var/backups/estanteria/portadas-AAAA-MM-DD.tar.gz -C /var/lib/estanteria   # si quieres también las portadas
chown -R estanteria:estanteria /var/lib/estanteria
systemctl start estanteria estanteria-bot
```

La copia que llega por Telegram viene comprimida: `gunzip estanteria-AAAA-MM-DD.db.gz` antes de copiarla.

## Actualizaciones

En `/admin` → **Actualizar** ves la versión instalada y la última publicada en GitHub, con sus novedades (sale de [`CHANGELOG.md`](../CHANGELOG.md)). Al pulsar «Instalar», la web deja un aviso y el servicio `estanteria-actualizar` (que es el que tiene permisos de root) descarga la versión nueva y ejecuta `install.sh`. En la misma página ves el registro en directo. La web se reinicia al final.

Las tareas diarias miran si hay versión nueva y avisan una vez al Telegram de la administración.

Para usar tu propio fork, pon `ESTANTERIA_REPO=tu-usuario/tu-repo` en el `.env`.

## Comandos útiles (dentro del contenedor)

| Para… | Comando |
|---|---|
| Actualizar a la última versión | `estanteria-actualizar` |
| Cambiar la contraseña de administración | `estanteria-cambiar-clave` |
| Ver errores de la web | `journalctl -u estanteria -n 50` |
| Ver errores del bot | `journalctl -u estanteria-bot -n 50` |
| Ver cuándo tocan los recordatorios | `systemctl list-timers estanteria-tareas.timer` |
| Mandar los recordatorios ahora | `systemctl start estanteria-tareas` |
| Mandar ahora la copia por Telegram | `systemd-run --uid=estanteria -p EnvironmentFile=/etc/estanteria/estanteria.env --working-directory=/opt/estanteria/app --wait /opt/estanteria/venv/bin/python tareas.py copia` |
| Reiniciar todo | `systemctl restart estanteria estanteria-bot nginx` |

## Problemas frecuentes

**La página no carga desde fuera pero sí con la IP.** Revisa que el proxy apunte a la IP del contenedor (no a `localhost`) y al puerto 80.

**«Formulario caducado» al guardar.** La sesión ha caducado o el navegador ha bloqueado la cookie. Recarga la página. Si pasa siempre por HTTPS, revisa que el proxy mande la cabecera `X-Forwarded-Proto` (Nginx Proxy Manager lo hace).

**«Google Books: error 429».** Se ha acabado el cupo diario sin clave. Pon `GOOGLE_BOOKS_KEY` ([configuración](configuracion.md)). Las otras fuentes siguen funcionando.

**El contenedor no tiene internet.** `pct config ID` en el host para revisar la IP, la puerta de enlace y el DNS. Puedes poner un DNS con `pct set ID --nameserver 1.1.1.1`.

**Los avisos de Telegram no llegan.** Mira [Telegram → problemas](telegram.md#problemas).
