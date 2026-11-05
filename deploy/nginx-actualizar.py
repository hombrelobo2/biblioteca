"""Pone al día una configuración de nginx de una versión anterior (sin tocar lo que hayas cambiado tú).

- Añade la zona de familias (/cuenta).
- Permite subir copias de seguridad grandes en /admin (client_max_body_size 600m).
Antes de cambiar nada guarda el archivo original con la extensión .antes-AAAAMMDD.
"""
import shutil
import sys
import time

ruta = sys.argv[1]
s = original = open(ruta).read()

if "location /cuenta" not in s:
    bloque = ("    location /cuenta {\n"
              "        # Zona de las familias (entrar, «Mi cuenta», pedir libros). Tiene que verse desde fuera.\n"
              "        proxy_pass http://127.0.0.1:8000;\n"
              "        include /etc/nginx/estanteria-proxy.conf;\n"
              "    }\n\n")
    i = s.find("    location /admin")
    s = s[:i] + bloque + s[i:] if i >= 0 else s.replace("    location / {", bloque + "    location / {", 1)
    print("    nginx: añadida la zona de familias (/cuenta)")

i = s.find("location /admin {")
if i >= 0:
    fin = s.find("}", i)
    if "client_max_body_size" not in s[i:fin]:
        s = s.replace("location /admin {", "location /admin {\n        # Para poder importar copias de seguridad (.zip con todas las portadas)\n"
                                           "        client_max_body_size 600m;", 1)
        print("    nginx: /admin admite subir copias de seguridad grandes")

if s != original:
    shutil.copy(ruta, ruta + ".antes-" + time.strftime("%Y%m%d%H%M"))
    open(ruta, "w").write(s)
