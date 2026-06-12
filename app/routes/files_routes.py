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
