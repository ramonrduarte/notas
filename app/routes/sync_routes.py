import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app import database
from app.services.sync_service import run_sync, run_recovery

router = APIRouter(prefix="/api/sync", tags=["sync"])

_sync_status = {"running": False, "last_result": None}
_cancel_flag = threading.Event()


class SyncRequest(BaseModel):
    tipo: str = "all"  # "nfe", "cte", "nfse", or "all"


class RecoveryRequest(BaseModel):
    tipos: list[str] = ["nfe", "cte"]   # bases: nfe, cte, nfse
    only_authorized: bool = True
    reset_nsu: bool = False              # se True, zera NSU _hist antes de sincronizar


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

    last_nfe      = next((l for l in logs if l["tipo"] == "nfe"      and l["status"] != "running"), None)
    last_cte      = next((l for l in logs if l["tipo"] == "cte"      and l["status"] != "running"), None)
    last_nfse     = next((l for l in logs if l["tipo"] == "nfse"     and l["status"] != "running"), None)
    last_nfe_hist = next((l for l in logs if l["tipo"] == "nfe_hist" and l["status"] != "running"), None)
    last_cte_hist = next((l for l in logs if l["tipo"] == "cte_hist" and l["status"] != "running"), None)

    # Cooldown NF-e: 6h após o último sync bem-sucedido
    cooldown_until = None
    last_nfe_ok = next((l for l in logs if l["tipo"] == "nfe" and l["status"] == "success"), None)
    if last_nfe_ok and last_nfe_ok.get("finalizado_em"):
        try:
            fin = datetime.fromisoformat(last_nfe_ok["finalizado_em"])
            end = fin + timedelta(hours=6)
            if datetime.now() < end:
                cooldown_until = end.isoformat()
        except Exception:
            pass

    # Bloqueio 656: 48h após o último erro 656
    blocked_until = None
    last_nfe_656 = next(
        (l for l in logs if l["tipo"] == "nfe" and l["status"] == "error" and "656" in (l.get("mensagem") or "")),
        None,
    )
    if last_nfe_656 and last_nfe_656.get("finalizado_em"):
        try:
            fin = datetime.fromisoformat(last_nfe_656["finalizado_em"])
            end = fin + timedelta(hours=48)
            if datetime.now() < end:
                blocked_until = end.isoformat()
        except Exception:
            pass

    return {
        "running": _sync_status["running"],
        "last_result": _sync_status["last_result"],
        "last_nfe": last_nfe,
        "last_cte": last_cte,
        "last_nfse": last_nfse,
        "last_nfe_hist": last_nfe_hist,
        "last_cte_hist": last_cte_hist,
        "next_scheduled": sched.get("next_scheduled"),
        "cooldown_until": cooldown_until,
        "blocked_until": blocked_until,
    }


@router.post("/recovery")
def start_recovery(body: RecoveryRequest):
    if _sync_status["running"]:
        raise HTTPException(409, "Já existe um sync em andamento.")

    valid = {"nfe", "cte", "nfse"}
    tipos = [t for t in body.tipos if t in valid]
    if not tipos:
        raise HTTPException(400, "Selecione ao menos um tipo.")

    _cancel_flag.clear()

    def _run():
        _sync_status["running"] = True
        try:
            if body.reset_nsu:
                for tipo_base in tipos:
                    nsu_zero = "0" if tipo_base == "nfse" else "000000000000000"
                    database.set_ult_nsu(f"{tipo_base}_hist", nsu_zero)
            result = run_recovery(tipos, body.only_authorized, _cancel_flag)
            _sync_status["last_result"] = {"status": "success", "result": result}
        except Exception as e:
            _sync_status["last_result"] = {"status": "error", "mensagem": str(e)}
        finally:
            _sync_status["running"] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True, "mensagem": f"Recuperação iniciada para: {', '.join(tipos).upper()}"}


@router.post("/reset-nsu")
def reset_nsu(tipo: str = "all"):
    if tipo == "all":
        tipos = ["nfe", "cte", "nfse", "nfe_hist", "cte_hist", "nfse_hist"]
    elif tipo == "hist":
        tipos = ["nfe_hist", "cte_hist", "nfse_hist"]
    else:
        tipos = [tipo]
    for t in tipos:
        nsu_zero = "0" if "nfse" in t else "000000000000000"
        database.set_ult_nsu(t, nsu_zero)
    return {"ok": True, "resetados": tipos}


@router.get("/logs")
def get_logs(limit: int = 50):
    return database.list_sync_logs(limit)
