from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app import config, database

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/list")
def list_files(tipo: str = None, limit: int = 200, offset: int = 0):
    docs = database.list_documents(tipo=tipo, limit=limit, offset=offset)
    return {"documents": docs, "total": len(docs)}


@router.get("/count")
def count_files():
    return {
        "total": database.count_documents()["total"],
        "nfe": database.count_documents("nfe")["total"],
        "cte": database.count_documents("cte")["total"],
    }


@router.get("/nsu")
def get_nsu_state():
    return {
        "nfe": database.get_ult_nsu("nfe"),
        "cte": database.get_ult_nsu("cte"),
    }


@router.get("/download/{tipo}/{year}/{month}/{filename}")
def download_file(tipo: str, year: str, month: str, filename: str):
    file_path = config.XML_DIR / tipo / year / month / filename
    if not file_path.exists():
        raise HTTPException(404, "Arquivo não encontrado.")
    return FileResponse(
        path=str(file_path),
        media_type="application/xml",
        filename=filename,
    )


@router.delete("/purge-events")
def purge_events():
    """Remove eventos (NF-e e CT-e) do banco e do disco."""
    removed_files = 0
    removed_db = 0
    with database.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, file_path FROM documents WHERE schema LIKE '%Evento%' OR schema LIKE '%evento%'"
        ).fetchall()
        for row in rows:
            if row["file_path"]:
                fp = config.DATA_DIR / row["file_path"]
                if fp.exists():
                    fp.unlink()
                    removed_files += 1
            conn.execute("DELETE FROM documents WHERE id=?", (row["id"],))
            removed_db += 1
    return {"ok": True, "removidos_db": removed_db, "removidos_disco": removed_files}


@router.get("/storage-info")
def storage_info():
    """Retorna info sobre onde os arquivos estão armazenados."""
    xml_dir = config.XML_DIR
    total_bytes = sum(f.stat().st_size for f in xml_dir.rglob("*") if f.is_file()) if xml_dir.exists() else 0
    total_files = sum(1 for f in xml_dir.rglob("*.xml") if f.is_file()) if xml_dir.exists() else 0
    return {
        "xml_path": str(xml_dir),
        "db_path": str(config.DB_PATH),
        "total_xml_files": total_files,
        "total_size_mb": round(total_bytes / 1024 / 1024, 2),
    }
