import sqlite3
from datetime import datetime
from app.config import DB_PATH


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS nsu_state (
                tipo TEXT PRIMARY KEY,
                ult_nsu TEXT NOT NULL DEFAULT '000000000000000',
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,
                nsu TEXT NOT NULL,
                chave TEXT,
                schema TEXT,
                file_path TEXT,
                emitente TEXT,
                destinatario TEXT,
                tomador TEXT,
                valor TEXT,
                data_emissao TEXT,
                baixado_em TEXT NOT NULL,
                UNIQUE(tipo, nsu)
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,
                iniciado_em TEXT NOT NULL,
                finalizado_em TEXT,
                status TEXT,
                nsu_inicial TEXT,
                nsu_final TEXT,
                documentos_baixados INTEGER DEFAULT 0,
                mensagem TEXT
            );
        """)
        conn.execute("INSERT OR IGNORE INTO nsu_state (tipo, ult_nsu) VALUES ('nfe', '000000000000000')")
        conn.execute("INSERT OR IGNORE INTO nsu_state (tipo, ult_nsu) VALUES ('cte', '000000000000000')")
        conn.execute("INSERT OR IGNORE INTO nsu_state (tipo, ult_nsu) VALUES ('nfse', '0')")
        conn.execute("INSERT OR IGNORE INTO nsu_state (tipo, ult_nsu) VALUES ('nfe_hist', '000000000000000')")
        conn.execute("INSERT OR IGNORE INTO nsu_state (tipo, ult_nsu) VALUES ('cte_hist', '000000000000000')")
        conn.execute("INSERT OR IGNORE INTO nsu_state (tipo, ult_nsu) VALUES ('nfse_hist', '0')")
        # Migração: adiciona coluna tomador se não existir (banco existente)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()]
        if "tomador" not in cols:
            conn.execute("ALTER TABLE documents ADD COLUMN tomador TEXT")
        if "numero" not in cols:
            conn.execute("ALTER TABLE documents ADD COLUMN numero TEXT")
        if "lancado" not in cols:
            conn.execute("ALTER TABLE documents ADD COLUMN lancado INTEGER DEFAULT 0")


def get_ult_nsu(tipo: str) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT ult_nsu FROM nsu_state WHERE tipo = ?", (tipo,)).fetchone()
        return row["ult_nsu"] if row else "000000000000000"


def set_ult_nsu(tipo: str, nsu: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO nsu_state (tipo, ult_nsu, updated_at) VALUES (?, ?, ?)",
            (tipo, nsu, datetime.now().isoformat()),
        )


def save_document(tipo: str, nsu: str, chave: str, schema: str, file_path: str,
                  emitente: str = "", destinatario: str = "", tomador: str = "",
                  valor: str = "", data_emissao: str = "", numero: str = ""):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO documents
               (tipo, nsu, chave, schema, file_path, emitente, destinatario, tomador, valor, data_emissao, baixado_em, numero)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tipo, nsu, chave, schema, file_path, emitente, destinatario, tomador, valor, data_emissao,
             datetime.now().isoformat(), numero),
        )


def set_lancado(doc_id: int, lancado: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET lancado = ? WHERE id = ?",
            (1 if lancado else 0, doc_id),
        )


def list_documents(tipo: str = None, limit: int = 200, offset: int = 0) -> list:
    with get_conn() as conn:
        if tipo:
            rows = conn.execute(
                "SELECT * FROM documents WHERE tipo = ? ORDER BY baixado_em DESC LIMIT ? OFFSET ?",
                (tipo, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM documents ORDER BY baixado_em DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]


def count_documents(tipo: str = None) -> dict:
    with get_conn() as conn:
        if tipo:
            row = conn.execute("SELECT COUNT(*) as total FROM documents WHERE tipo = ?", (tipo,)).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) as total FROM documents").fetchone()
        return {"total": row["total"]}


def start_sync_log(tipo: str, nsu_inicial: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sync_log (tipo, iniciado_em, status, nsu_inicial) VALUES (?, ?, 'running', ?)",
            (tipo, datetime.now().isoformat(), nsu_inicial),
        )
        return cur.lastrowid


def finish_sync_log(log_id: int, status: str, nsu_final: str, docs: int, mensagem: str = ""):
    with get_conn() as conn:
        conn.execute(
            """UPDATE sync_log SET finalizado_em=?, status=?, nsu_final=?, documentos_baixados=?, mensagem=?
               WHERE id=?""",
            (datetime.now().isoformat(), status, nsu_final, docs, mensagem, log_id),
        )


def get_last_success_time(tipo: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT finalizado_em FROM sync_log WHERE tipo = ? AND status = 'success' ORDER BY finalizado_em DESC LIMIT 1",
            (tipo,),
        ).fetchone()
        return row["finalizado_em"] if row else None


def get_last_error_sync(tipo: str) -> tuple:
    """Returns (mensagem, finalizado_em) of the most recent error sync, or (None, None)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT mensagem, finalizado_em FROM sync_log WHERE tipo = ? AND status = 'error' ORDER BY finalizado_em DESC LIMIT 1",
            (tipo,),
        ).fetchone()
        return (row["mensagem"], row["finalizado_em"]) if row else (None, None)


def list_sync_logs(limit: int = 50) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_log ORDER BY iniciado_em DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
