"""Añade la zona de familias (/cuenta) a una configuración de nginx de una versión anterior."""
import sys

ruta = sys.argv[1]
s = open(ruta).read()
if "location /cuenta" not in s:
    bloque = ("    location /cuenta {\n"
              "        # Zona de las familias (entrar, «Mi cuenta», pedir libros). Tiene que verse desde fuera.\n"
              "        proxy_pass http://127.0.0.1:8000;\n"
              "        include /etc/nginx/estanteria-proxy.conf;\n"
              "    }\n\n")
    i = s.find("    location /admin")
    s = s[:i] + bloque + s[i:] if i >= 0 else s.replace("    location / {", bloque + "    location / {", 1)
    open(ruta, "w").write(s)
