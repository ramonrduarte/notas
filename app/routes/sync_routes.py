import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app import database
from app.services.sync_service import run_sync

router = APIRouter(prefix="/api/sync", tags=["sync"])

_sync_lock = threading.Lock()
_sync_status = {"running": False, "last_result": None}


class SyncRequest(BaseModel):
    tipo: str = "all"  # "nfe", "cte", or "all"


@router.post("/trigger")
def trigger_sync(body: SyncRequest):
    if _sync_status["running"]:
        raise HTTPException(409, "Já existe um sync em andamento.")

    def _run():
        _sync_status["running"] = True
        try:
            result = run_sync(body.tipo)
            _sync_status["last_result"] = {"status": "success", "result": result}
        except Exception as e:
            _sync_status["last_result"] = {"status": "error", "mensagem": str(e)}
        finally:
            _sync_status["running"] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True, "mensagem": "Sync iniciado em segundo plano."}


@router.get("/status")
def sync_status():
    return {
        "running": _sync_status["running"],
        "last_result": _sync_status["last_result"],
    }


@router.post("/reset-nsu")
def reset_nsu(tipo: str = "all"):
    """Reset NSU to 0 to re-download everything."""
    tipos = ["nfe", "cte"] if tipo == "all" else [tipo]
    for t in tipos:
        database.set_ult_nsu(t, "000000000000000")
    return {"ok": True, "resetados": tipos}


@router.get("/logs")
def get_logs(limit: int = 50):
    return database.list_sync_logs(limit)
