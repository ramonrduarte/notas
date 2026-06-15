import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from app import config
from app import scheduler as sched

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdate(BaseModel):
    cnpj: str = ""
    cert_password: str = ""
    schedule_hour: int = 7
    schedule_minute: int = 0
    ambiente: str = "1"
    uf_code: str = "43"
    sync_nfe: bool = True
    sync_cte: bool = True
    sync_nfse: bool = True
    nfe_direction: str = "ambas"   # "emitidas" | "recebidas" | "ambas"
    cte_role: str = "tomador"      # "tomador" | "emitente" | "ambas"
    nfse_role: str = "ambas"       # "tomadora" | "emitida" | "ambas"


@router.get("")
def get_config():
    cfg = config.load_config()
    # Never return the password
    safe = {k: v for k, v in cfg.items() if k != "cert_password"}
    safe["has_password"] = bool(cfg.get("cert_password"))
    safe["has_cert"] = config.get_cert_path() is not None
    return safe


@router.post("")
def update_config(body: ConfigUpdate):
    cfg = config.load_config()
    cfg["cnpj"] = body.cnpj.replace(".", "").replace("/", "").replace("-", "")
    if body.cert_password:
        cfg["cert_password"] = body.cert_password
    cfg["schedule_hour"] = body.schedule_hour
    cfg["schedule_minute"] = body.schedule_minute
    cfg["ambiente"] = body.ambiente
    cfg["uf_code"] = body.uf_code
    cfg["sync_nfe"] = body.sync_nfe
    cfg["sync_cte"] = body.sync_cte
    cfg["sync_nfse"] = body.sync_nfse
    cfg["nfe_direction"] = body.nfe_direction
    cfg["cte_role"]      = body.cte_role
    cfg["nfse_role"]     = body.nfse_role
    config.save_config(cfg)

    sched.update_schedule(body.schedule_hour, body.schedule_minute)
    return {"ok": True}


@router.post("/certificate")
async def upload_certificate(file: UploadFile = File(...)):
    if not file.filename.endswith((".pfx", ".p12")):
        raise HTTPException(400, "Arquivo deve ser .pfx ou .p12")

    dest = config.CERT_DIR / file.filename
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    cfg = config.load_config()
    cfg["cert_filename"] = file.filename
    config.save_config(cfg)
    return {"ok": True, "filename": file.filename}


@router.delete("/certificate")
def delete_certificate():
    cfg = config.load_config()
    if cfg.get("cert_filename"):
        p = config.CERT_DIR / cfg["cert_filename"]
        if p.exists():
            p.unlink()
        cfg["cert_filename"] = ""
        config.save_config(cfg)
    return {"ok": True}
