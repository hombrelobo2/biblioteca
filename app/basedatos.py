"""Base de datos de Nuestra Estantería (SQLite). La usan la web, el bot de Telegram y las tareas diarias."""
import os
import sqlite3
import uuid

DATA = os.environ.get("ESTANTERIA_DATA", "/var/lib/estanteria")
DB_PATH = os.path.join(DATA, "estanteria.db")
PORTADAS = os.path.join(DATA, "portadas")

ESQUEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS bibliotecas (
  id      TEXT PRIMARY KEY,
  nombre  TEXT NOT NULL,
  orden   INTEGER NOT NULL DEFAULT 0,
  creada  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS libros (
  id            TEXT PRIMARY KEY,
  biblioteca_id TEXT NOT NULL REFERENCES bibliotecas(id) ON DELETE CASCADE,
  titulo        TEXT NOT NULL,
  autor         TEXT NOT NULL DEFAULT '',
  coleccion     TEXT NOT NULL DEFAULT '',
  numero        TEXT NOT NULL DEFAULT '',
  paginas       TEXT NOT NULL DEFAULT '',
  resumen       TEXT NOT NULL DEFAULT '',
  isbn          TEXT NOT NULL DEFAULT '',
  portada       TEXT NOT NULL DEFAULT '',
  fuente        TEXT NOT NULL DEFAULT '',
  creado        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS libros_biblioteca ON libros(biblioteca_id);

-- Cuentas de familia (una por familia; la lleva una persona adulta)
CREATE TABLE IF NOT EXISTS familias (
  id            TEXT PRIMARY KEY,
  nombre        TEXT NOT NULL,
  usuario       TEXT NOT NULL UNIQUE COLLATE NOCASE,
  clave_hash    TEXT NOT NULL DEFAULT '',
  telegram_chat TEXT NOT NULL DEFAULT '',
  activa        INTEGER NOT NULL DEFAULT 1,
  creada        TEXT NOT NULL DEFAULT (datetime('now')),
  ultimo_acceso TEXT NOT NULL DEFAULT ''
);

-- Enlaces de un solo uso: invitación / nueva contraseña, y vincular Telegram
CREATE TABLE IF NOT EXISTS tokens (
  token_hash TEXT PRIMARY KEY,
  tipo       TEXT NOT NULL,
  familia_id TEXT NOT NULL DEFAULT '',
  expira     TEXT NOT NULL,
  creado     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Préstamos. Estados abiertos: solicitado, aceptado, entregado, activo, devolucion, disputa.
-- Estados cerrados: devuelto, rechazado, cancelado, perdido.
CREATE TABLE IF NOT EXISTS prestamos (
  id             TEXT PRIMARY KEY,
  libro_id       TEXT NOT NULL REFERENCES libros(id) ON DELETE CASCADE,
  duena_id       TEXT NOT NULL REFERENCES familias(id),
  prestataria_id TEXT NOT NULL REFERENCES familias(id),
  estado         TEXT NOT NULL,
  creado         TEXT NOT NULL DEFAULT (datetime('now')),
  actualizado    TEXT NOT NULL DEFAULT (datetime('now')),
  inicio         TEXT NOT NULL DEFAULT '',
  vence          TEXT NOT NULL DEFAULT '',
  fin            TEXT NOT NULL DEFAULT '',
  prorroga_dias  INTEGER NOT NULL DEFAULT 0,
  prorrogas      INTEGER NOT NULL DEFAULT 0,
  disputa        TEXT NOT NULL DEFAULT '',
  nota           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS prestamos_libro ON prestamos(libro_id);
CREATE INDEX IF NOT EXISTS prestamos_estado ON prestamos(estado);

-- Historial de cada préstamo (quién hizo qué y cuándo)
CREATE TABLE IF NOT EXISTS prestamo_eventos (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  prestamo_id TEXT NOT NULL REFERENCES prestamos(id) ON DELETE CASCADE,
  fecha       TEXT NOT NULL DEFAULT (datetime('now')),
  quien       TEXT NOT NULL,
  accion      TEXT NOT NULL,
  detalle     TEXT NOT NULL DEFAULT ''
);

-- «Avísame cuando vuelva»
CREATE TABLE IF NOT EXISTS espera (
  libro_id   TEXT NOT NULL REFERENCES libros(id) ON DELETE CASCADE,
  familia_id TEXT NOT NULL REFERENCES familias(id) ON DELETE CASCADE,
  creado     TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (libro_id, familia_id)
);

-- Recordatorios ya enviados (para no repetirlos el mismo día)
CREATE TABLE IF NOT EXISTS avisos_enviados (
  prestamo_id TEXT NOT NULL,
  tipo        TEXT NOT NULL,
  fecha       TEXT NOT NULL,
  PRIMARY KEY (prestamo_id, tipo, fecha)
);

CREATE TABLE IF NOT EXISTS ajustes (
  clave TEXT PRIMARY KEY,
  valor TEXT NOT NULL DEFAULT ''
);
"""


def conectar(ruta=None):
    os.makedirs(PORTADAS, exist_ok=True)
    con = sqlite3.connect(ruta or DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _columnas(con, tabla):
    return {r[1] for r in con.execute("PRAGMA table_info(%s)" % tabla)}


def iniciar_db():
    con = conectar()
    con.executescript(ESQUEMA)
    # Columnas añadidas en versiones posteriores (las bases de datos antiguas no las tienen)
    libros = _columnas(con, "libros")
    if "completado" not in libros:
        # Fecha en que «Completar datos que faltan» revisó el libro (para no repetirlo cada vez)
        con.execute("ALTER TABLE libros ADD COLUMN completado TEXT NOT NULL DEFAULT ''")
    if "resumen_origen" not in libros:
        # '' = antiguo o escrito a mano · 'ia' = escrito por Gemini sin revisar · 'revisado' = revisado por ti
        con.execute("ALTER TABLE libros ADD COLUMN resumen_origen TEXT NOT NULL DEFAULT ''")
    if "perdido" not in libros:
        con.execute("ALTER TABLE libros ADD COLUMN perdido INTEGER NOT NULL DEFAULT 0")
    bibs = _columnas(con, "bibliotecas")
    if "familia_id" not in bibs:
        # Familia dueña de la biblioteca ('' = sin familia: no se puede pedir prestado)
        con.execute("ALTER TABLE bibliotecas ADD COLUMN familia_id TEXT NOT NULL DEFAULT ''")
    if "presta" not in bibs:
        # 1 = presta libros · 0 = «solo para mirar»
        con.execute("ALTER TABLE bibliotecas ADD COLUMN presta INTEGER NOT NULL DEFAULT 1")
    con.commit()
    con.close()


def ajuste(con, clave, defecto=""):
    r = con.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
    return r["valor"] if r else defecto


def poner_ajuste(con, clave, valor):
    con.execute("INSERT INTO ajustes (clave, valor) VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                (clave, str(valor)))
    con.commit()


def nuevo_id(prefijo):
    return prefijo + uuid.uuid4().hex[:10]


# ---------------- Enlaces de un solo uso ----------------
# Se guarda solo la huella (sha256) del enlace: quien vea la base de datos no puede usarlo.

def _huella(token):
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def crear_token(con, tipo, familia_id, horas):
    import secrets
    from datetime import datetime, timedelta
    con.execute("DELETE FROM tokens WHERE expira < ?", (datetime.now().isoformat(" ", "seconds"),))
    # Solo un enlace válido de cada tipo por familia
    con.execute("DELETE FROM tokens WHERE tipo = ? AND familia_id = ?", (tipo, familia_id))
    token = secrets.token_urlsafe(24)
    expira = (datetime.now() + timedelta(hours=horas)).isoformat(" ", "seconds")
    con.execute("INSERT INTO tokens (token_hash, tipo, familia_id, expira) VALUES (?, ?, ?, ?)",
                (_huella(token), tipo, familia_id, expira))
    con.commit()
    return token


def ver_token(con, token, tipo):
    """Devuelve la familia_id del enlace si es válido (sin gastarlo), o None."""
    from datetime import datetime
    if not token or len(token) > 100:
        return None
    r = con.execute("SELECT familia_id, expira FROM tokens WHERE token_hash = ? AND tipo = ?",
                    (_huella(token), tipo)).fetchone()
    if not r or r["expira"] < datetime.now().isoformat(" ", "seconds"):
        return None
    return r["familia_id"]


def gastar_token(con, token, tipo):
    fid = ver_token(con, token, tipo)
    if fid is not None:
        con.execute("DELETE FROM tokens WHERE token_hash = ?", (_huella(token),))
        con.commit()
    return fid
