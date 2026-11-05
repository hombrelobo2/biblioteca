"""Búsqueda de libros en Todostuslibros, Elkar, Google Books y Open Library.

Cada línea de texto se convierte en una lista de "candidatos" con el mismo formato,
para que el administrador elija el bueno antes de guardarlo.

- Todostuslibros (librerías de barrio): títulos con tildes, serie y número, páginas y sinopsis.
- Elkar: portadas grandes, páginas y sinopsis; la mejor fuente para libros en euskera.
- Google Books y Open Library: libros de fuera y ediciones antiguas.

Elkar y Todostuslibros no tienen API: se leen sus páginas web como lo haría una persona,
con pocas peticiones y de una en una. Si cambian el diseño, hay que ajustar las expresiones
de este archivo (las funciones *_elkar y *_ttl).
"""
import html
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import requests

TIMEOUT = 12
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) NuestraEstanteria/1.0 (biblioteca infantil familiar)",
      "Accept-Language": "es-ES,es;q=0.9,eu;q=0.8"}
GOOGLE_KEY = os.environ.get("GOOGLE_BOOKS_KEY", "").strip()

ELKAR = "https://www.elkar.eus"
ELKAR_IMG = "https://web.elkarbanaketa.eus/irudia.cfm?%s&mota=liburu"
TTL = "https://www.todostuslibros.com"
IDIOMAS_ELKAR = {"gaztelania": "es", "euskara": "eu", "katalana": "ca", "ingelesa": "en", "frantsesa": "fr", "galiziera": "gl"}

# Orden de preferencia para la portada cuando varias fuentes tienen el mismo libro
PRIORIDAD_PORTADA = {"Elkar": 0, "Open Library": 1, "Todostuslibros": 2, "Google Books": 3}
PRIORIDAD_TITULO = {"Todostuslibros": 0, "Google Books": 1, "Open Library": 2, "Elkar": 3}

_cache = {}
_cache_lock = threading.Lock()


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def _texto(s):
    """Quita etiquetas HTML y espacios sobrantes."""
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", s or "", flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = re.sub(r"[ \t]+([,.;:!?»)])", r"\1", s)
    return "\n".join(" ".join(l.split()) for l in s.splitlines() if l.strip())


def limpiar_isbn(s):
    d = re.sub(r"[^0-9Xx]", "", s or "")
    return d.upper() if len(d) in (10, 13) else ""


def interpretar_linea(linea):
    """'Título | Autor' -> (titulo, autor, isbn). Una línea que solo es un ISBN también vale."""
    linea = (linea or "").strip()
    partes = [p.strip() for p in linea.split("|")]
    titulo = partes[0] if partes else ""
    autor = partes[1] if len(partes) > 1 else ""
    isbn = limpiar_isbn(titulo) if re.fullmatch(r"[0-9Xx\- ]{10,17}", titulo) else ""
    return titulo, autor, isbn


def _get(url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("headers", UA)
    return requests.get(url, **kw)


def candidato(fuente, **datos):
    c = {"fuente": fuente, "titulo": "", "autor": "", "isbn": "", "paginas": "", "descripcion": "",
         "portada_url": "", "idioma": "", "editorial": "", "anio": "", "coleccion": "", "numero": ""}
    c.update({k: v for k, v in datos.items() if v is not None})
    return c


def separar_serie(titulo):
    """'Cupcakes con sorpresa (Las Ratitas 7)' -> ('Cupcakes con sorpresa', 'Las Ratitas', '7')."""
    m = re.match(r"^(.*?)\s*\(([^()]*?)\s+(\d{1,3})\)\s*(?:\(.*\))?\s*$", titulo)
    m2 = re.match(r"^([^()\d.-]+?)\s+(\d{1,3})(?:\s+-|\.)\s+(.+?)\s*(?:\((?:ed\.|edici[oó]n)[^)]*\))?\s*$", titulo, re.I)
    if m2 and not (m and m.group(1).strip()) and not re.search(r"\b(de|del|los|las|la|el|y|en)$", m2.group(1).strip(), re.I):
        return m2.group(3).strip(), m2.group(1).strip(), m2.group(2)
    if m and m.group(1).strip():
        serie = re.sub(r"\s*\[[^\]]*\]\s*", " ", m.group(2)).strip()
        return m.group(1).strip(), serie, m.group(3)
    return re.sub(r"\s*\((?:ed\.|edici[oó]n|estuche|pack)[^)]*\)\s*", " ", titulo, flags=re.I).strip(), "", ""


def arreglar_mayusculas(texto, referencias=()):
    """Elkar escribe los títulos EN MAYÚSCULAS y sin tildes. Usa la grafía de la línea escrita
    (por ejemplo 'cámara', 'Harry Potter') cuando coincide; si no, solo la primera letra en mayúscula."""
    mapa = {}
    for ref in referencias:
        for w in re.findall(r"[\wÀ-ÿ'’-]+", ref or ""):
            mapa.setdefault(_norm(w), w)
    palabras = re.split(r"(\W+)", texto.lower())
    salida = []
    for w in palabras:
        salida.append(mapa.get(_norm(w), w) if w and w[0].isalnum() else w)
    t = "".join(salida).strip()
    return t[:1].upper() + t[1:]


def norma_euskera(c):
    """Norma de la estantería: libros en euskera -> colección 'En euskera', con la serie en el título."""
    if c.get("idioma") == "eu" and c.get("coleccion") != "En euskera":
        if c["coleccion"] and _norm(c["coleccion"]) not in _norm(c["titulo"]):
            c["titulo"] = "%s%s: %s" % (c["coleccion"], (" " + c["numero"]) if c["numero"] else "", c["titulo"])
        c["coleccion"], c["numero"] = "En euskera", ""
    return c


# ---------- Google Books ----------

def _google_portada(info):
    links = info.get("imageLinks") or {}
    url = links.get("thumbnail") or links.get("smallThumbnail") or ""
    return url.replace("http://", "https://").replace("&edge=curl", "") if url else ""


def _google_isbn(info):
    ids = {i.get("type"): i.get("identifier") for i in info.get("industryIdentifiers") or []}
    return ids.get("ISBN_13") or ids.get("ISBN_10") or ""


def buscar_google(titulo, autor="", isbn="", maximo=6):
    if isbn:
        q = "isbn:" + isbn
    else:
        q = 'intitle:"%s"' % titulo.replace('"', "")
        if autor:
            q += ' inauthor:"%s"' % autor.replace('"', "")
    params = {"q": q, "maxResults": maximo, "printType": "books"}
    if GOOGLE_KEY:
        params["key"] = GOOGLE_KEY
    for intento in range(2):
        r = _get("https://www.googleapis.com/books/v1/volumes", params=params)
        if r.status_code in (429, 503) and intento == 0:
            time.sleep(2)
            continue
        break
    if r.status_code != 200:
        raise RuntimeError("error %s%s" % (r.status_code, " (límite de consultas: pon una GOOGLE_BOOKS_KEY)" if r.status_code == 429 else ""))
    return [convertir_google(it) for it in (r.json().get("items") or [])]


def convertir_google(item):
    info = item.get("volumeInfo") or {}
    titulo = info.get("title") or ""
    if info.get("subtitle"):
        titulo += ". " + info["subtitle"]
    return candidato("Google Books", titulo=titulo, autor=", ".join(info.get("authors") or []),
                     isbn=_google_isbn(info), paginas=info.get("pageCount") or "",
                     descripcion=_texto(info.get("description") or ""), portada_url=_google_portada(info),
                     idioma=info.get("language") or "", editorial=info.get("publisher") or "",
                     anio=(info.get("publishedDate") or "")[:4])


# ---------- Open Library ----------

def buscar_openlibrary(titulo, autor="", isbn="", maximo=5):
    campos = "key,title,subtitle,author_name,isbn,number_of_pages_median,cover_i,language,publisher,first_publish_year"
    params = {"limit": maximo, "fields": campos}
    if isbn:
        params["isbn"] = isbn
    else:
        params["title"] = titulo
        if autor:
            params["author"] = autor
    r = _get("https://openlibrary.org/search.json", params=params)
    r.raise_for_status()
    return [convertir_openlibrary(d, isbn) for d in (r.json().get("docs") or [])]


def convertir_openlibrary(doc, isbn_buscado=""):
    isbns = doc.get("isbn") or []
    isbn = isbn_buscado or next((i for i in isbns if len(i) == 13 and i.startswith("97884")), "") \
        or next((i for i in isbns if len(i) == 13), "") or (isbns[0] if isbns else "")
    if doc.get("cover_i"):
        portada = "https://covers.openlibrary.org/b/id/%s-L.jpg" % doc["cover_i"]
    elif isbn:
        portada = "https://covers.openlibrary.org/b/isbn/%s-L.jpg?default=false" % isbn
    else:
        portada = ""
    lang = doc.get("language") or []
    idioma = "es" if "spa" in lang else "eu" if "baq" in lang or "eus" in lang else (lang[0] if lang else "")
    titulo = doc.get("title") or ""
    if doc.get("subtitle"):
        titulo += ". " + doc["subtitle"]
    return candidato("Open Library", titulo=titulo, autor=", ".join(doc.get("author_name") or []), isbn=isbn,
                     paginas=doc.get("number_of_pages_median") or "", portada_url=portada, idioma=idioma,
                     editorial=((doc.get("publisher") or [""])[0]), anio=str(doc.get("first_publish_year") or ""))


# ---------- Elkar ----------

def _autor_elkar(s):
    """'ROWLING, J. K. / DUDDLE, JOHNNY (IL. )' -> 'J. K. Rowling'"""
    primero = _texto(s).split("/")[0]
    primero = re.sub(r"\(.*?\)", "", primero).strip()
    if "," in primero:
        apellido, nombre = [p.strip() for p in primero.split(",", 1)]
        primero = (nombre + " " + apellido).strip()
    return " ".join(w.upper() if "." in w else (w[:1].upper() + w[1:].lower() if w.isupper() or w.islower() else w)
                    for w in primero.split())


def parsear_busqueda_elkar(texto_html, referencias=()):
    salida = []
    for bloque in texto_html.split('<div class="artikulu_kutxa">')[1:]:
        m_isbn = re.search(r"<span>ISBN:</span>\s*([0-9Xx]{10,13})", bloque)
        m_tit = re.search(r'class="titulu_nagusia">\s*([^<]+?)\s*</a>', bloque)
        if not (m_isbn and m_tit):
            continue
        bruto = html.unescape(m_tit.group(1))
        if re.match(r"^\s*\((estuche|pack|lote)\)", bruto, re.I):
            continue
        titulo, serie, numero = separar_serie(bruto)
        campo = lambda nombre: (re.search(r"<span>%s:</span>\s*([^<]*)</li>" % nombre, bloque) or [None, ""])[1]
        idioma = IDIOMAS_ELKAR.get(_texto(campo("Hizkuntza")).lower(), "")
        isbn = m_isbn.group(1)
        salida.append(norma_euskera(candidato(
            "Elkar", titulo=arreglar_mayusculas(titulo, referencias), autor=_autor_elkar(campo("Egilea")),
            isbn=isbn, portada_url=ELKAR_IMG % isbn, idioma=idioma,
            editorial=_texto(campo("Argitaletxea")).title(), anio=_texto(campo("Edizio urtea"))[:4],
            coleccion=arreglar_mayusculas(serie, referencias) if serie else "", numero=numero)))
    return salida


def buscar_elkar(titulo, autor="", isbn="", maximo=6):
    q = isbn or titulo
    r = _get("%s/eu/bilaketa/%s" % (ELKAR, requests.utils.quote(q)), params={"s": q})
    r.raise_for_status()
    if "/liburu_fitxa/" in r.url:   # con un solo resultado, Elkar lleva directa a la ficha
        c = candidato_de_ficha_elkar(r.text, (titulo, autor))
        return [c] if c else []
    return parsear_busqueda_elkar(r.text, (titulo, autor))[:maximo]


def candidato_de_ficha_elkar(texto_html, referencias=()):
    img = re.search(r'itemprop="image" src="[^"]*irudia\.cfm\?([0-9Xx]{10,13})[^"]*"[^>]*alt="([^"]*)"', texto_html)
    isbn_m = re.search(r"<span>ISBN:</span>\s*([0-9Xx-]{10,17})", texto_html)
    if not (img or isbn_m):
        return None
    isbn = limpiar_isbn(isbn_m.group(1)) if isbn_m else img.group(1)
    alt = html.unescape(img.group(2)) if img else ""
    titulo_bruto, _, autores = alt.partition(" - ")
    titulo, serie, numero = separar_serie(titulo_bruto)
    campo = lambda nombre: (re.search(r"<span>%s:</span>\s*([^<]*)</li>" % nombre, texto_html) or [None, ""])[1]
    datos = parsear_ficha_elkar(texto_html)
    return norma_euskera(candidato(
        "Elkar", titulo=arreglar_mayusculas(titulo, referencias), autor=_autor_elkar(autores), isbn=isbn,
        portada_url=ELKAR_IMG % isbn, idioma=IDIOMAS_ELKAR.get(_texto(campo("Hizkuntza")).lower(), ""),
        anio=_texto(campo("Edizio urtea"))[:4], coleccion=serie, numero=numero,
        paginas=datos["paginas"], descripcion=datos["descripcion"]))


def parsear_ficha_elkar(texto_html):
    pag = re.search(r'itemprop="numberOfPages"[^>]*>\s*(\d+)', texto_html) \
        or re.search(r"Orri kopurua:</span>\s*(?:<[^>]+>)*\s*(\d+)", texto_html)
    sin = re.search(r'id="sinopsia"[^>]*>(.*?)</div>', texto_html, re.S)
    return {"paginas": pag.group(1) if pag else "", "descripcion": _texto(sin.group(1))[:3000] if sin else ""}


def ficha_elkar(isbn):
    r = _get("%s/eu/liburu_fitxa/x/y/%s" % (ELKAR, isbn))
    if r.status_code != 200 or "numberOfPages" not in r.text and "sinopsia" not in r.text:
        return {}
    datos = parsear_ficha_elkar(r.text)
    # Elkar devuelve una imagen genérica pequeña cuando no tiene la portada
    try:
        img = _get(ELKAR_IMG % isbn, stream=True)
        tam = int(img.headers.get("Content-Length") or 0) or len(img.raw.read(8000, decode_content=True))
        img.close()
        datos["portada_ok"] = img.status_code == 200 and tam > 6000
    except requests.RequestException:
        datos["portada_ok"] = False
    return datos


# ---------- Todostuslibros ----------

def _isbn_de_url(url):
    m = re.search(r"_(97[89][0-9-]{10,14})", url or "")
    return limpiar_isbn(m.group(1)) if m else ""


def parsear_busqueda_ttl(texto_html):
    salida = []
    for bloque in re.split(r'<li\s+class="book-col"', texto_html)[1:]:
        tit = re.search(r'data-gtm-titulo="([^"]*)"', bloque)
        isbn = re.search(r'data-gtm-isbn="([^"]*)"', bloque)
        if not (tit and isbn):
            continue
        titulo, serie, numero = separar_serie(html.unescape(tit.group(1)))
        autor = re.search(r'<p class="author">\s*(.*?)\s*</p>', bloque, re.S)
        edit = re.search(r'data-gtm-editorial="([^"]*)"', bloque)
        img = re.search(r'<img src="(https://static\.cegal\.es/[^"]+)"', bloque)
        salida.append(candidato("Todostuslibros", titulo=titulo, autor=" ".join(_texto(autor.group(1)).split("/")[0].split()) if autor else "",
                                isbn=limpiar_isbn(isbn.group(1)), portada_url=img.group(1) if img else "",
                                editorial=html.unescape(edit.group(1)) if edit else "", coleccion=serie, numero=numero))
    return salida


def parsear_ficha_ttl(texto_html, url=""):
    h1 = re.search(r'<h1 class="title">\s*(.*?)\s*</h1>', texto_html, re.S)
    pag = re.search(r"<dt>\s*N[ºo°]\s*p[áa]ginas:\s*</dt>\s*<dd>\s*(\d+)", texto_html, re.I)
    sin = re.search(r'id="collapseSynopsis"[^>]*>(.*?)</div>', texto_html, re.S)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', texto_html)
    titulo, serie, numero = separar_serie(_texto(h1.group(1))) if h1 else ("", "", "")
    return {"titulo": titulo, "coleccion": serie, "numero": numero, "isbn": _isbn_de_url(url),
            "paginas": pag.group(1) if pag else "", "descripcion": _texto(sin.group(1))[:3000] if sin else "",
            "portada_url": img.group(1) if img and "cegal" in img.group(1) else ""}


def buscar_ttl(titulo, autor="", isbn="", maximo=6):
    q = isbn or (titulo + (" " + autor if autor else ""))
    r = _get(TTL + "/busquedas", params={"keyword": q})
    if r.status_code == 429:
        raise RuntimeError("demasiadas consultas, espera un poco")
    r.raise_for_status()
    if "/libros/" in r.url:   # una búsqueda por ISBN lleva directa a la ficha del libro
        f = parsear_ficha_ttl(r.text, r.url)
        return [candidato("Todostuslibros", **f)] if f["titulo"] else []
    return parsear_busqueda_ttl(r.text)[:maximo]


def ficha_ttl(isbn):
    r = _get(TTL + "/busquedas", params={"keyword": isbn})
    if r.status_code != 200 or "/libros/" not in r.url:
        return {}
    return parsear_ficha_ttl(r.text, r.url)


# ---------- Completar datos por ISBN (con caché) ----------

def completar(isbn):
    """Páginas, sinopsis y si Elkar tiene portada de verdad, para un ISBN. Se guarda en memoria."""
    with _cache_lock:
        if isbn in _cache:
            return _cache[isbn]
    datos = {}
    try:
        datos = ficha_elkar(isbn)
    except requests.RequestException:
        pass
    if not datos.get("paginas") or not datos.get("descripcion"):
        try:
            extra = ficha_ttl(isbn)
            for k in ("paginas", "descripcion"):
                if not datos.get(k) and extra.get(k):
                    datos[k] = extra[k]
            datos.setdefault("ttl_portada", extra.get("portada_url", ""))
        except requests.RequestException:
            pass
    with _cache_lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[isbn] = datos
    return datos


# ---------- Unir y ordenar ----------

def _puntuacion(c, titulo, autor):
    p = 0
    nt, nc = _norm(titulo), _norm(c["titulo"] + " " + c.get("coleccion", ""))
    nct = _norm(c["titulo"])
    if nt and nct == nt:
        p += 6
    elif nt and (nt in nc or nct in nt):
        p += 3
    pal = set(nt.split())
    if pal:
        p += 3 * len(pal & set(nc.split())) / len(pal)
    if autor and _norm(autor).split()[-1:] and _norm(autor).split()[-1] in _norm(c["autor"]):
        p += 2
    if c["idioma"] in ("es", "eu", "spa", "baq") or c["isbn"].startswith("97884"):
        p += 2
    if c["portada_url"]:
        p += 1
    if c["paginas"]:
        p += 0.5
    if c["descripcion"]:
        p += 0.5
    return p


FUENTES = (("Todostuslibros", buscar_ttl), ("Elkar", buscar_elkar),
           ("Google Books", buscar_google), ("Open Library", buscar_openlibrary))


def buscar(linea):
    """Devuelve {'consulta':..., 'candidatos':[...], 'errores':[...]} para una línea."""
    titulo, autor, isbn = interpretar_linea(linea)
    candidatos, errores = [], []

    def una(fuente):
        nombre, fn = fuente
        try:
            return fn(titulo, autor, isbn), None
        except Exception as e:  # una fuente caída no debe romper las demás
            detalle = str(e) if isinstance(e, RuntimeError) else e.__class__.__name__
            return [], "%s: %s" % (nombre, detalle)

    with ThreadPoolExecutor(max_workers=len(FUENTES)) as ex:
        for lista, error in ex.map(una, FUENTES):
            candidatos += lista
            if error:
                errores.append(error)

    # Juntar por ISBN: el mismo libro visto en varias fuentes se queda en uno, con lo mejor de cada una
    grupos, sin_isbn = {}, []
    for c in candidatos:
        if c["isbn"]:
            grupos.setdefault(c["isbn"], []).append(c)
        else:
            sin_isbn.append(c)
    unidos = []
    for isbn_g, grupo in grupos.items():
        base = dict(min(grupo, key=lambda c: PRIORIDAD_TITULO.get(c["fuente"], 9)))
        for campo in ("autor", "paginas", "descripcion", "idioma", "editorial", "anio", "coleccion", "numero"):
            if not base[campo]:
                base[campo] = next((c[campo] for c in grupo if c[campo]), "")
        con_portada = [c for c in grupo if c["portada_url"]]
        if con_portada:
            base["portada_url"] = min(con_portada, key=lambda c: PRIORIDAD_PORTADA.get(c["fuente"], 9))["portada_url"]
        base["fuente"] = " + ".join(sorted({c["fuente"] for c in grupo}, key=lambda f: PRIORIDAD_TITULO.get(f, 9)))
        unidos.append(base)
    vistos, ordenados = set(), []
    for c in sorted(unidos + sin_isbn, key=lambda c: -_puntuacion(c, titulo, autor)):
        clave = c["isbn"] or (_norm(c["titulo"]), _norm(c["autor"]))
        if clave not in vistos:
            vistos.add(clave)
            ordenados.append(c)
    mejores = ordenados[:6]

    # Para los 3 primeros con ISBN: páginas, sinopsis y portada grande de Elkar / Todostuslibros
    con_isbn = [c for c in mejores[:3] if c["isbn"].startswith("97")]
    with ThreadPoolExecutor(max_workers=3) as ex:
        for c, extra in zip(con_isbn, ex.map(lambda c: completar(c["isbn"]), con_isbn)):
            if not c["paginas"] and extra.get("paginas"):
                c["paginas"] = extra["paginas"]
            if extra.get("descripcion") and len(extra["descripcion"]) > len(c["descripcion"]):
                c["descripcion"] = extra["descripcion"]
            if extra.get("portada_ok"):
                c["portada_url"] = ELKAR_IMG % c["isbn"]
            elif "elkarbanaketa" in c["portada_url"] and "portada_ok" in extra:
                c["portada_url"] = extra.get("ttl_portada") or ""
            elif not c["portada_url"] and extra.get("ttl_portada"):
                c["portada_url"] = extra["ttl_portada"]
    for c in mejores:
        norma_euskera(c)
    return {"consulta": {"titulo": titulo, "autor": autor, "isbn": isbn}, "candidatos": mejores, "errores": errores}
