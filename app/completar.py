"""«Completar datos que faltan»: busca en las librerías los libros antiguos sin resumen,
sin número de colección, sin páginas o sin portada, y propone los datos para revisarlos.

Nunca sobrescribe lo que ya está escrito: solo rellena campos vacíos, y solo cuando
el libro encontrado coincide de verdad (mismo título y, si lo hay, mismo autor).
"""
import re
import time

import busqueda
import ia

_norm = busqueda._norm
EUSKERA = "En euskera"


def faltas(libro):
    """Qué le falta a un libro (fila de la base de datos)."""
    f = []
    if not libro["resumen"]:
        f.append("resumen")
    if libro["coleccion"] and libro["coleccion"] != EUSKERA and not libro["numero"]:
        f.append("numero")
    if not libro["paginas"]:
        f.append("paginas")
    if not libro["portada"]:
        f.append("portada")
    return f


def _variantes_libro(libro):
    t = libro["titulo"]
    v = {_norm(t)}
    # Libros en euskera: "Biga 4: Biga endreda-makila" -> también "Biga endreda-makila"
    if ":" in t:
        v.add(_norm(t.split(":", 1)[1]))
    if libro["coleccion"] and libro["coleccion"] != EUSKERA:
        v.add(_norm("%s %s" % (libro["coleccion"], t)))
        v.add(_norm("%s %s %s" % (libro["coleccion"], libro["numero"], t)))
    return {x for x in v if x}


def _variantes_cand(c):
    v = {_norm(c["titulo"])}
    if ":" in c["titulo"]:
        v.add(_norm(c["titulo"].split(":", 1)[1]))
    if c.get("coleccion") and c["coleccion"] != EUSKERA:
        v.add(_norm("%s %s" % (c["coleccion"], c["titulo"])))
        v.add(_norm("%s %s %s" % (c["coleccion"], c.get("numero", ""), c["titulo"])))
    return {x for x in v if x}


def _parecido(a, b):
    pa, pb = set(a.split()), set(b.split())
    if len(pa) < 2 or len(pb) < 2:
        return 0
    return len(pa & pb) / len(pa | pb)


def _apellidos(autor):
    return {w for w in _norm(autor).split() if len(w) > 3}


def _mismo_autor(a, b):
    """Compara apellidos admitiendo erratas pequeñas (Elkar escribe a veces «Rowlling»)."""
    pa, pb = _apellidos(a), _apellidos(b)
    if pa & pb:
        return True
    return any(abs(len(x) - len(y)) <= 1 and sum(1 for u, v in zip(sorted(x), sorted(y)) if u != v) <= 1
               and x[:3] == y[:3] for x in pa for y in pb)


def coincide(libro, c):
    """True si el candidato es, con bastante seguridad, el mismo libro."""
    vl, vc = _variantes_libro(libro), _variantes_cand(c)
    exacto = bool(vl & vc)
    if libro["autor"] and c.get("autor"):
        if not _mismo_autor(libro["autor"], c["autor"]):
            return False
        if exacto or max((_parecido(a, b) for a in vl for b in vc), default=0) >= 0.8:
            return True
        # "Los peores profes del mundo" ~ "Los peores profes del mundo. Edición especial"
        return any((b.startswith(a + " ") or a.startswith(b + " ")) and min(len(a.split()), len(b.split())) >= 3
                   for a in vl for b in vc)
    # Sin autor con el que comprobar: solo vale el título idéntico
    return exacto


def _mejor(libro, candidatos):
    buenos = [c for c in candidatos if coincide(libro, c)]
    if not buenos:
        return None

    def nota(c):
        return ((2 if c.get("descripcion") else 0) + (1 if c.get("isbn", "").startswith("97884") else 0)
                + (1 if c.get("portada_url") else 0) + (1 if c.get("paginas") else 0)
                + (1 if c.get("numero") else 0))
    return max(buenos, key=nota)


def _numero(libro, c):
    if not libro["coleccion"] or libro["coleccion"] == EUSKERA:
        return ""
    if c.get("numero") and c.get("coleccion"):
        a, b = _norm(libro["coleccion"]), _norm(c["coleccion"])
        if a == b or a in b or b in a:
            return c["numero"]
    # El título puede traerlo: "Las Ratitas 7. Cupcakes…"
    titulo, serie, numero = busqueda.separar_serie(c["titulo"])
    if numero and _norm(serie) == _norm(libro["coleccion"]):
        return numero
    return ""


def proponer(libros, que=("resumen", "numero", "paginas", "portada")):
    """libros: filas de la base de datos. Devuelve una propuesta por libro."""
    propuestas, para_resumir = [], []
    for i, libro in enumerate(libros):
        falta = [f for f in faltas(libro) if f in que]
        p = {"id": libro["id"], "titulo": libro["titulo"], "autor": libro["autor"],
             "coleccion": libro["coleccion"], "numero_actual": libro["numero"], "falta": falta,
             "portada_actual": libro["portada"],
             "encontrado": False, "fuente": "", "isbn": "", "cambios": {}, "aviso": ""}
        propuestas.append(p)
        if not falta:
            continue
        if i:
            time.sleep(0.5)   # con calma, para que las librerías no nos bloqueen
        titulo = libro["titulo"]
        if libro["coleccion"] == EUSKERA and ":" in titulo:
            titulo = titulo.split(":", 1)[1].strip()   # "Biga 4: Biga endreda-makila" -> "Biga endreda-makila"
        linea = titulo + (" | " + libro["autor"] if libro["autor"] else "")
        try:
            res = busqueda.buscar(linea)
        except Exception as e:
            p["aviso"] = "Error al buscar (%s)" % e.__class__.__name__
            continue
        c = _mejor(libro, res["candidatos"])
        if not c:
            p["aviso"] = "No se ha encontrado este libro con seguridad."
            continue
        p.update(encontrado=True, fuente=c["fuente"], isbn=c.get("isbn", ""))
        if "numero" in falta:
            n = _numero(libro, c)
            if n:
                p["cambios"]["numero"] = n
        if "paginas" in falta and c.get("paginas"):
            p["cambios"]["paginas"] = str(c["paginas"])
        if "portada" in falta and c.get("portada_url"):
            p["cambios"]["portada_url"] = c["portada_url"]
        if "resumen" in falta and c.get("descripcion"):
            para_resumir.append((p, c))
    if para_resumir and ia.configurada():
        try:
            resumenes = ia.resumir([{"id": p["id"], "titulo": c["titulo"], "descripcion": c["descripcion"]}
                                    for p, c in para_resumir])
        except ia.ErrorIA as e:
            resumenes = {}
            for p, _c in para_resumir:
                p["aviso"] = "No se ha podido escribir el resumen: %s" % e
        for p, _c in para_resumir:
            if resumenes.get(p["id"]):
                p["cambios"]["resumen"] = resumenes[p["id"]]
            elif not p["aviso"]:
                p["aviso"] = "La sinopsis no servía para un resumen fiable."
    for p in propuestas:
        if p["encontrado"] and not p["cambios"] and not p["aviso"]:
            p["aviso"] = "Encontrado, pero las librerías no tienen los datos que faltan."
    return propuestas
