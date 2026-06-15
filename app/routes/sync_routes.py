import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app import database
from app.services.sync_service import run_sync

router = APIRouter(prefix="/api/sync", tags=["sync"])

_sync_status = {"running": False, "last_result": None}
_cancel_flag = threading.Event()


class SyncRequest(BaseModel):
    tipo: str = "all"  # "nfe", "cte", or "all"


@router.post("/trigger")
def trigger_sync(body: SyncRequest):
    if _sync_status["running"]:
        raise HTTPException(409, "Já existe um sync em andamento.")

    _cancel_flag.clear()

    def _run():
        _sync_status["running"] = True
        try:
            result = run_sync(body.tipo, _cancel_flag)
            _sync_status["last_result"] = {"status": "success", "result": result}
        except Exception as e:
            _sync_status["last_result"] = {"status": "error", "mensagem": str(e)}
        finally:
            _sync_status["running"] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True, "mensagem": "Sync iniciado em segundo plano."}


@router.post("/cancel")
def cancel_sync():
    if not _sync_status["running"]:
        return {"ok": False, "mensagem": "Nenhum sync em andamento."}
    _cancel_flag.set()
    return {"ok": True, "mensagem": "Cancelamento solicitado."}


@router.get("/status")
def sync_status():
    from datetime import datetime, timedelta
    from app import scheduler as sched_module

    sched = sched_module.get_next_run_times()
    logs = database.list_sync_logs(limit=40)

    last_nfe = next((l for l in logs if l["tipo"] == "nfe" and l["status"] != "running"), None)
    last_cte = next((l for l in logs if l["tipo"] == "cte" and l["status"] != "running"), None)

    # Detecta cooldown: 90 min após o último erro 656
    cooldown_until = None
    for log in logs:
        if log.get("status") == "error" and "656" in (log.get("mensagem") or ""):
            try:
                fin = datetime.fromisoformat(log["finalizado_em"])
                end = fin + timedelta(minutes=90)
                if datetime.now() < end:
                    cooldown_until = end.isoformat()
                    break
            except Exception:
                pass

    return {
        "running": _sync_status["running"],
        "last_result": _sync_status["last_result"],
        "last_nfe": last_nfe,
        "last_cte": last_cte,
        "next_scheduled": sched.get("next_scheduled"),
        "next_retry": sched.get("next_retry"),
        "cooldown_until": cooldown_until,
    }


@router.post("/reset-nsu")
def reset_nsu(tipo: str = "all"):
    tipos = ["nfe", "cte"] if tipo == "all" else [tipo]
    for t in tipos:
        database.set_ult_nsu(t, "000000000000000")
    return {"ok": True, "resetados": tipos}


@router.get("/logs")
def get_logs(limit: int = 50):
    return database.list_sync_logs(limit)
