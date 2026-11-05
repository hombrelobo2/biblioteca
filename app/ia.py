"""Ayudas con IA (Gemini, de Google) para Nuestra Estantería.

- leer_lomos(): lee una foto de lomos de libros y devuelve la lista de libros.
- resumir():    escribe resúmenes de 2-3 líneas para niños a partir de la descripción
                de la editorial. Si la descripción no sirve, devuelve el resumen vacío.

Configuración (en /etc/estanteria/estanteria.env):
  GEMINI_API_KEY=tu_clave
  GEMINI_MODEL=            (opcional; si no, se prueban los de MODELOS_RESERVA)
"""
import base64
import io
import json
import os
import re
import sys
import time

import requests
from PIL import Image, ImageOps

API = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
# Orden de prueba. Google retira modelos con el tiempo: si alguno desaparece, se salta solo.
MODELOS_RESERVA = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-flash-lite-latest"]
TIMEOUT = (10, 75)      # (conectar, esperar respuesta) en segundos
LIMITE_TOTAL = 200      # tiempo máximo entre todos los intentos
REINTENTABLES = (404, 500, 502, 503, 504)

INSTRUCCIONES_LOMOS = """Eres un ayudante de una biblioteca infantil. En la foto hay libros vistos por el lomo (o por la portada).
Lee CADA libro que se vea, en orden (de izquierda a derecha o de arriba abajo) y devuelve sus datos.

Reglas:
- Copia el título tal y como está impreso, con sus tildes. No traduzcas nada.
- Si el lomo indica una colección o serie (por ejemplo "Las Ratitas", "Isadora Moon", "Unicornia") y un número, ponlos en "coleccion" y "numero", y deja en "titulo" solo el título del libro.
- "autor": solo si se lee en el lomo. Si no se ve, déjalo vacío. No lo deduzcas.
- "idioma": "es" si está en castellano, "eu" si está en euskera, "ca" catalán, "en" inglés, u "otro".
- "seguro": false si el lomo está cortado, borroso, tapado o no estás seguro de alguna palabra.
- No inventes libros ni completes títulos que no se leen. Si un lomo es ilegible, no lo incluyas.
- No incluyas objetos que no sean libros (juguetes, cajas, carpetas, revistas sueltas)."""

INSTRUCCIONES_RESUMEN = """Eres el bibliotecario de una estantería compartida entre niños y niñas de 6 a 12 años.
Para cada libro te doy el título y la descripción de la editorial. Escribe un resumen en castellano para niños.

Reglas obligatorias:
- Entre 2 y 3 frases cortas, como máximo unos 280 caracteres. Tono cercano y alegre, para que les apetezca leerlo.
- Usa SOLO lo que dice la descripción. No añadas personajes, hechos ni finales que no aparezcan en ella.
- Quita la publicidad: ventas, premios, "bestseller", edades recomendadas, formato, ilustraciones, precio, otros libros de la serie.
- Nada sexista, discriminatorio, despectivo ni malsonante. No uses insultos aunque estén en la descripción
  (por ejemplo "tonto", "gordo", "friki"): cuéntalo de otra manera o déjalo fuera.
- Evita detalles de violencia, miedo intenso, romance o besos; si la historia gira en torno a eso, cuéntala de forma suave.
- Si la descripción está en euskera, inglés u otro idioma, el resumen va igualmente en castellano.
- Devuelve el resumen VACÍO ("") si: la descripción no cuenta de qué va la historia (solo publicidad o la serie en general),
  es demasiado corta para saberlo, o el contenido no es adecuado para niños. Mejor vacío que dudoso.
- No pongas comillas ni el título al principio."""

ESQUEMA_LOMOS = {
    "type": "OBJECT",
    "properties": {
        "libros": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "titulo": {"type": "STRING"},
                    "autor": {"type": "STRING"},
                    "coleccion": {"type": "STRING"},
                    "numero": {"type": "STRING"},
                    "idioma": {"type": "STRING"},
                    "seguro": {"type": "BOOLEAN"},
                },
                "required": ["titulo", "seguro"],
            },
        }
    },
    "required": ["libros"],
}

ESQUEMA_RESUMEN = {
    "type": "OBJECT",
    "properties": {
        "resumenes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"id": {"type": "STRING"}, "resumen": {"type": "STRING"}},
                "required": ["id", "resumen"],
            },
        }
    },
    "required": ["resumenes"],
}

# Última red de seguridad: si aparece alguna de estas palabras, el resumen se descarta.
PALABRAS_VETADAS = re.compile(
    r"\b(tont[oa]s?|idiota|estúpid[oa]s?|imbécil|gord[oa]s?|gordit[oa]s?|fe[oa]s?|friki|mierda|caca|culo|"
    r"joder|put[oa]|maldit[oa]|matar|mat[oó]|sangre|muert[oa]s?|besos?|novi[oa]s?|enamorad[oa]s?|sexy)\b",
    re.IGNORECASE)


class ErrorIA(Exception):
    pass


def configurada():
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def preparar_imagen(datos):
    """Gira la foto según el móvil y la reduce a 2000 px como máximo (menos espera y menos cupo)."""
    try:
        im = Image.open(io.BytesIO(datos))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:
        raise ErrorIA("Ese archivo no es una foto válida.")
    im.thumbnail((2000, 2000))
    salida = io.BytesIO()
    im.save(salida, "JPEG", quality=88)
    return salida.getvalue()


def _generar(partes, esquema, temperatura=0.1):
    """Llama a Gemini probando modelos y reintentando si están saturados. Devuelve el JSON pedido."""
    clave = os.environ.get("GEMINI_API_KEY", "").strip()
    if not clave:
        raise ErrorIA("Falta la clave de Gemini (GEMINI_API_KEY) en /etc/estanteria/estanteria.env.")
    elegido = os.environ.get("GEMINI_MODEL", "").strip()
    modelos = ([elegido] if elegido else []) + [m for m in MODELOS_RESERVA if m != elegido]
    cuerpo = {"contents": [{"parts": partes}],
              "generationConfig": {"temperature": temperatura, "responseMimeType": "application/json",
                                   "responseSchema": esquema}}
    r, fallo_red, inicio = None, None, time.time()
    for _vuelta in range(2):
        for modelo in modelos:
            if time.time() - inicio > LIMITE_TOTAL:
                break
            try:
                r = requests.post(API % modelo, json=cuerpo, timeout=TIMEOUT,
                                  headers={"x-goog-api-key": clave, "Content-Type": "application/json"})
                fallo_red = None
            except requests.RequestException as e:
                fallo_red = e
                print("Gemini %s: %r" % (modelo, e), file=sys.stderr, flush=True)
                continue
            print("Gemini %s: %s" % (modelo, r.status_code), file=sys.stderr, flush=True)
            if r.status_code not in REINTENTABLES:
                break
        if r is not None and r.status_code not in REINTENTABLES:
            break
        time.sleep(3)
    if r is None:
        if isinstance(fallo_red, requests.Timeout):
            raise ErrorIA("Gemini ha tardado demasiado en responder. Prueba otra vez.")
        raise ErrorIA("No se ha podido conectar con Gemini (%s). Comprueba que el contenedor tiene internet."
                      % fallo_red.__class__.__name__)
    if r.status_code in (500, 502, 503, 504):
        raise ErrorIA("Gemini está saturado ahora mismo (error %s). Suele pasar en unos minutos: prueba otra vez." % r.status_code)
    if r.status_code == 404:
        raise ErrorIA("Ninguno de los modelos de Gemini configurados está disponible. Pon uno válido en GEMINI_MODEL.")
    if r.status_code == 429:
        raise ErrorIA("Se ha agotado el cupo gratuito de Gemini por ahora. Prueba más tarde o mañana.")
    if r.status_code in (400, 401, 403):
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        raise ErrorIA("Gemini ha rechazado la petición (%s). Revisa la clave. %s" % (r.status_code, msg[:200]))
    if r.status_code != 200:
        raise ErrorIA("Gemini ha respondido con un error (%s). Prueba otra vez." % r.status_code)
    try:
        cand = r.json()["candidates"][0]
        texto = "".join(p.get("text", "") for p in cand["content"]["parts"])
        return json.loads(texto)
    except (KeyError, IndexError, ValueError, AttributeError):
        raise ErrorIA("Gemini no ha devuelto una respuesta válida. Prueba otra vez.")


# ---------- Fotos de lomos ----------

def leer_lomos(datos, colecciones=()):
    imagen = preparar_imagen(datos)
    texto = INSTRUCCIONES_LOMOS
    if colecciones:
        texto += ("\n- Si un libro pertenece a una de estas colecciones que ya hay en la estantería, "
                  "escribe el nombre de la colección exactamente así: " + "; ".join(list(colecciones)[:150]))
    partes = [{"text": texto},
              {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(imagen).decode()}}]
    libros = _generar(partes, ESQUEMA_LOMOS).get("libros", [])
    return [normalizar(l) for l in libros if str(l.get("titulo", "")).strip()]


def normalizar(l):
    titulo = " ".join(str(l.get("titulo", "")).split())[:200]
    coleccion = " ".join(str(l.get("coleccion", "")).split())[:100]
    numero = str(l.get("numero", "")).strip()[:6]
    idioma = str(l.get("idioma", "")).strip().lower()
    # Norma de la estantería: los libros en euskera van a la colección "En euskera",
    # con el nombre de la serie y el número dentro del título.
    if idioma == "eu":
        if coleccion and coleccion.lower() not in titulo.lower():
            titulo = "%s%s: %s" % (coleccion, (" " + numero) if numero else "", titulo)
        coleccion, numero = "En euskera", ""
    return {
        "titulo": titulo,
        "autor": " ".join(str(l.get("autor", "")).split())[:150],
        "coleccion": coleccion,
        "numero": numero,
        "idioma": idioma,
        "seguro": bool(l.get("seguro", True)),
    }


# ---------- Resúmenes ----------

def resumir(libros):
    """libros = [{"id", "titulo", "descripcion"}] -> {id: resumen}. Vacío si no hay un resumen fiable."""
    libros = [l for l in libros if len(str(l.get("descripcion", "")).strip()) >= 60][:40]
    if not libros:
        return {}
    datos = [{"id": str(l["id"]), "titulo": str(l.get("titulo", ""))[:200],
              "descripcion": str(l["descripcion"])[:2500]} for l in libros]
    respuesta = _generar([{"text": INSTRUCCIONES_RESUMEN + "\n\nLibros:\n" + json.dumps(datos, ensure_ascii=False)}],
                         ESQUEMA_RESUMEN, temperatura=0.4)
    validos = {d["id"] for d in datos}
    salida = {}
    for item in respuesta.get("resumenes", []):
        rid, texto = str(item.get("id", "")), " ".join(str(item.get("resumen", "")).split())
        if rid not in validos:
            continue
        if not texto or len(texto) > 420 or PALABRAS_VETADAS.search(texto):
            texto = ""
        salida[rid] = texto
    return salida
