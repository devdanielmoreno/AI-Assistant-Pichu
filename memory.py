import json
import os
import sqlite3
import subprocess
import sys
import threading
import numpy as np
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "memoria.db")
MODELO_EMBED = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMS = 384

_WORKER = (
    "import sys, json\n"
    "from fastembed import TextEmbedding\n"
    "m = TextEmbedding(model_name=%r)\n"
    "for line in sys.stdin:\n"
    "    try:\n"
    "        textos = json.loads(line)\n"
    "        vecs = []\n"
    "        for v in m.embed(textos):\n"
    "            vecs.append([round(float(x), 6) for x in v])\n"
    "        print(json.dumps(vecs), flush=True)\n"
    "    except Exception as e:\n"
    "        print(json.dumps({'error': str(e)}), flush=True)\n"
) % MODELO_EMBED

_proc = None
_embed_lock = threading.Lock()


def _con():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    os.makedirs(BASE, exist_ok=True)
    with _con() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS memorias ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "texto TEXT NOT NULL,"
            "tipo TEXT NOT NULL DEFAULT 'recuerdo',"
            "peso REAL NOT NULL DEFAULT 1.0,"
            "dia_semana INTEGER,"
            "hora_centro INTEGER,"
            "veces INTEGER DEFAULT 1,"
            "ultima_vista TEXT,"
            "creada TEXT)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS memorias_emb ("
            "memoria_id INTEGER PRIMARY KEY REFERENCES memorias(id) ON DELETE CASCADE,"
            "vector BLOB NOT NULL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS telemetry ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "ts INTEGER NOT NULL,"
            "dow INTEGER NOT NULL,"
            "hora_min INTEGER NOT NULL,"
            "ventana TEXT,"
            "proceso TEXT,"
            "exe TEXT,"
            "sitio TEXT)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS arranques ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "ts INTEGER NOT NULL,"
            "dia_semana INTEGER,"
            "hora_min INTEGER)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS series ("
            "serie TEXT PRIMARY KEY,"
            "temporada INTEGER,"
            "episodio INTEGER,"
            "actualizado TEXT)"
        )


def _worker_proc():
    global _proc
    if _proc is None or _proc.poll() is not None:
        flags = 0x08000000 if os.name == "nt" else 0
        _proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=BASE,
            creationflags=flags,
        )
    return _proc


def _embed(textos):
    if isinstance(textos, str):
        textos = [textos]
    with _embed_lock:
        p = _worker_proc()
        try:
            p.stdin.write(json.dumps(textos) + "\n")
            p.stdin.flush()
            linea = p.stdout.readline()
            if not linea:
                raise RuntimeError("subproceso de embeddings cerrado")
            data = json.loads(linea)
            if "error" in data:
                raise RuntimeError(data["error"])
            return [np.asarray(v, dtype=np.float32) for v in data]
        except Exception as e:
            global _proc
            _proc = None
            p = _worker_proc()
            try:
                p.stdin.write(json.dumps(textos) + "\n")
                p.stdin.flush()
                data = json.loads(p.stdout.readline())
            except Exception:
                raise RuntimeError(f"embeddings no disponibles: {e}")
            return [np.asarray(v, dtype=np.float32) for v in data]


def guardar(texto, tipo="recuerdo", peso=1.0, dia_semana=None, hora_centro=None):
    texto = (texto or "").strip()
    if not texto:
        return False
    now = datetime.now()
    vec = _embed(texto)[0].tobytes()
    with _con() as c:
        dupe = c.execute(
            "SELECT id, veces FROM memorias WHERE texto = ? AND tipo = ?",
            (texto, tipo),
        ).fetchone()
        if dupe:
            c.execute(
                "UPDATE memorias SET peso = MAX(peso, ?), veces = veces + 1, ultima_vista = ? WHERE id = ?",
                (peso, now.isoformat(), dupe["id"]),
            )
            c.execute("DELETE FROM memorias_emb WHERE memoria_id = ?", (dupe["id"],))
            c.execute(
                "INSERT INTO memorias_emb (memoria_id, vector) VALUES (?, ?)",
                (dupe["id"], vec),
            )
            return False
        cur = c.execute(
            "INSERT INTO memorias (texto, tipo, peso, dia_semana, hora_centro, ultima_vista, creada)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (texto, tipo, peso, dia_semana, hora_centro, now.isoformat(), now.isoformat()),
        )
        mid = cur.lastrowid
        c.execute(
            "INSERT INTO memorias_emb (memoria_id, vector) VALUES (?, ?)",
            (mid, vec),
        )
    return True


def _cargar():
    with _con() as c:
        rows = c.execute(
            "SELECT m.id, m.texto, m.tipo, m.peso, m.dia_semana, m.hora_centro, e.vector"
            " FROM memorias m LEFT JOIN memorias_emb e ON e.memoria_id = m.id"
        ).fetchall()
    metas = []
    mats = []
    for r in rows:
        if r["vector"] is None:
            continue
        metas.append(r)
        mats.append(np.frombuffer(r["vector"], dtype=np.float32))
    X = np.vstack(mats) if mats else np.zeros((0, DIMS), dtype=np.float32)
    return metas, X


def recuperar(consulta, top_k=3):
    if not consulta or not str(consulta).strip():
        return []
    metas, X = _cargar()
    if len(X) == 0:
        return []
    q = _embed(consulta)[0]
    qn = np.linalg.norm(q)
    if qn == 0:
        return []
    Xn = np.linalg.norm(X, axis=1)
    sim = (X @ q) / (qn * Xn + 1e-9)
    now = datetime.now()
    hoy = now.weekday()
    hmin = now.hour * 60 + now.minute
    for i, m in enumerate(metas):
        sim[i] *= float(m["peso"] or 1.0)
        if m["dia_semana"] == hoy:
            sim[i] += 0.15
        if m["hora_centro"] is not None and abs(int(m["hora_centro"]) - hmin) <= 20:
            sim[i] += 0.20
    idx = np.argsort(sim)[::-1]
    res = []
    for i in idx[:top_k]:
        if float(sim[i]) <= 0.05:
            continue
        res.append({
            "texto": metas[i]["texto"],
            "tipo": metas[i]["tipo"],
            "peso": float(metas[i]["peso"] or 1.0),
            "score": float(sim[i]),
        })
    return res


def registrar_regla(texto, dia_semana, hora_centro, peso=1.0):
    return guardar(texto, tipo="rutina", peso=peso, dia_semana=dia_semana, hora_centro=hora_centro)


def listar_series():
    with _con() as c:
        return [dict(r) for r in c.execute(
            "SELECT serie, temporada, episodio, actualizado FROM series ORDER BY actualizado DESC"
        )]


def registrar_progreso_serie(serie, temporada, episodio):
    serie = (serie or "").strip().lower()
    if not serie or temporada is None or episodio is None:
        return False
    with _con() as c:
        c.execute(
            "INSERT INTO series (serie, temporada, episodio, actualizado) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(serie) DO UPDATE SET temporada = excluded.temporada,"
            " episodio = excluded.episodio, actualizado = excluded.actualizado",
            (serie, int(temporada), int(episodio), datetime.now().isoformat()),
        )
    return True


def recuperar_progreso_serie(serie):
    serie = (serie or "").strip().lower()
    if not serie:
        return None
    with _con() as c:
        r = c.execute(
            "SELECT temporada, episodio, actualizado FROM series WHERE serie = ?",
            (serie,),
        ).fetchone()
    return dict(r) if r else None


def guardar_recuerdo(texto):
    guardar(texto, tipo="recuerdo", peso=1.2)
    return "Recordado."


def listar():
    with _con() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, texto, tipo, peso, dia_semana, hora_centro, veces"
            " FROM memorias ORDER BY id"
        )]


def limpiar():
    with _con() as c:
        c.execute("DELETE FROM memorias_emb")
        c.execute("DELETE FROM memorias")