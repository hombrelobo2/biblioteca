"""Importa la estantería actual (libros.json + resumenes.json + portadas/) a la base de datos.

Uso:  python3 importar.py /ruta/a/datos-iniciales
No duplica: si un libro o una biblioteca ya existe (mismo id), lo deja como está.
"""
import json
import os
import shutil
import sys

os.environ.setdefault("ESTANTERIA_DATA", "/var/lib/estanteria")
import estanteria  # noqa: E402  (crea las tablas si no existen)


def main(origen):
    libros = json.load(open(os.path.join(origen, "libros.json"), encoding="utf-8"))
    ruta_res = os.path.join(origen, "resumenes.json")
    resumenes = json.load(open(ruta_res, encoding="utf-8")) if os.path.exists(ruta_res) else {}
    con = estanteria.conectar()
    nb = nl = np = 0
    for orden, b in enumerate(libros.get("bibliotecas", []), 1):
        cur = con.execute("INSERT OR IGNORE INTO bibliotecas (id, nombre, orden) VALUES (?, ?, ?)",
                          (b["id"], b["nombre"], orden))
        nb += cur.rowcount
    for l in libros.get("libros", []):
        portada = ""
        if l.get("portada"):
            nombre = os.path.basename(l["portada"])
            src = os.path.join(origen, l["portada"])
            if os.path.exists(src):
                dst = os.path.join(estanteria.PORTADAS, nombre)
                if not os.path.exists(dst):
                    shutil.copyfile(src, dst)
                    np += 1
                portada = nombre
        cur = con.execute(
            """INSERT OR IGNORE INTO libros (id, biblioteca_id, titulo, autor, coleccion, numero, paginas, resumen, portada, fuente)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'importado')""",
            (l["id"], l["biblioteca"], l["titulo"], l.get("autor", ""), l.get("coleccion", ""), str(l.get("numero", "")),
             str(l.get("paginas", "") or ""), resumenes.get(l["id"], ""), portada))
        nl += cur.rowcount
    con.commit()
    con.close()
    print("Importado: %d bibliotecas, %d libros, %d portadas copiadas." % (nb, nl, np))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
