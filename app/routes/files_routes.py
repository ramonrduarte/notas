import io
import zipfile
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from app import config, database

router = APIRouter(prefix="/api/files", tags=["files"])

_ALL_TIPOS = ["nfe", "cte", "nfse", "nfe_hist", "cte_hist", "nfse_hist"]


@router.get("/list")
def list_files(tipo: str = None, limit: int = 200, offset: int = 0):
    docs = database.list_documents(tipo=tipo, limit=limit, offset=offset)
    return {"documents": docs, "total": len(docs)}


@router.get("/count")
def count_files():
    result = {"total": database.count_documents()["total"]}
    for t in _ALL_TIPOS:
        result[t] = database.count_documents(t)["total"]
    return result


@router.get("/nsu")
def get_nsu_state():
    return {t: database.get_ult_nsu(t) for t in _ALL_TIPOS}


@router.get("/download-by-path")
def download_by_path(path: str):
    """Baixa um XML usando o caminho relativo armazenado no banco."""
    file_path = config.DATA_DIR / path.replace("\\", "/")
    if not file_path.exists():
        raise HTTPException(404, "Arquivo não encontrado.")
    return FileResponse(
        path=str(file_path),
        media_type="application/xml",
        filename=file_path.name,
    )


class ExportSelectedBody(BaseModel):
    file_paths: list[str]


@router.post("/export-zip-selected")
def export_zip_selected(body: ExportSelectedBody):
    """Exporta como ZIP somente os arquivos cujos caminhos foram enviados."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in body.file_paths:
            full = config.DATA_DIR / fp.replace("\\", "/")
            if full.exists():
                zf.write(full, fp.replace("\\", "/"))
    buf.seek(0)
    count = len(body.file_paths)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="xmls_selecionados_{count}.zip"'},
    )


@router.get("/export-zip")
def export_zip(tipo: str = None, schema_filter: str = "autorizadas"):
    """Exporta XMLs selecionados como ZIP. tipo pode incluir nfe_hist etc."""
    docs = database.list_documents(tipo=tipo, limit=100000)

    if schema_filter == "autorizadas":
        docs = [d for d in docs if (
            (d.get("schema") or "").startswith("procNFe") or
            (d.get("schema") or "").startswith("procCTe") or
            d.get("schema") == "NFSE"
        )]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            fp = doc.get("file_path")
            if fp:
                full = config.DATA_DIR / fp.replace("\\", "/")
                if full.exists():
                    zf.write(full, fp.replace("\\", "/"))
    buf.seek(0)

    filename = f"xmls_{tipo or 'todos'}_{schema_filter}.zip"
    return StreamingResponse(
        iter([buf.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/purge")
def purge_documents(tipo: str = None, category: str = "all"):
    """
    Remove documentos do banco e do disco.
    tipo: None=todos, ou um de nfe/cte/nfse/nfe_hist/cte_hist/nfse_hist/hist
    category: all | eventos
    """
    removed_files = 0
    removed_db = 0

    with database.get_conn() as conn:
        # Monta cláusula WHERE
        conditions = []
        params = []

        if tipo == "hist":
            conditions.append("tipo IN ('nfe_hist','cte_hist','nfse_hist')")
        elif tipo:
            conditions.append("tipo = ?")
            params.append(tipo)

        if category == "eventos":
            conditions.append("(schema LIKE '%Evento%' OR schema LIKE '%evento%')")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = conn.execute(f"SELECT id, file_path FROM documents {where}", params).fetchall()
        for row in rows:
            if row["file_path"]:
                fp = config.DATA_DIR / row["file_path"].replace("\\", "/")
                if fp.exists():
                    fp.unlink()
                    removed_files += 1
            conn.execute("DELETE FROM documents WHERE id=?", (row["id"],))
            removed_db += 1

    return {"ok": True, "removidos_db": removed_db, "removidos_disco": removed_files}


@router.get("/storage-info")
def storage_info():
    def _dir_stats(d):
        if not d.exists():
            return 0, 0
        files = sum(1 for f in d.rglob("*.xml") if f.is_file())
        size  = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        return files, size

    sync_files, sync_bytes = _dir_stats(config.XML_DIR)
    hist_files, hist_bytes = _dir_stats(config.HIST_DIR)

    return {
        "xml_path":       str(config.XML_DIR),
        "hist_path":      str(config.HIST_DIR),
        "db_path":        str(config.DB_PATH),
        "sync_xml_files": sync_files,
        "sync_size_mb":   round(sync_bytes / 1024 / 1024, 2),
        "hist_xml_files": hist_files,
        "hist_size_mb":   round(hist_bytes / 1024 / 1024, 2),
    }
