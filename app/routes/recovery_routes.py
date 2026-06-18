import threading
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app import config, database

router = APIRouter(prefix="/api/recuperacao", tags=["recuperacao"])

_status: dict = {
    "running":          False,
    "tipo":             None,
    "modo":             None,        # "chave" | "periodo"
    "total":            0,
    "processados":      0,
    "scaneados":        0,
    "salvos":           0,
    "nsu_atual":        None,
    "erros_lista":      [],
    "data_ini":         None,
    "data_fim":         None,
    "iniciado_em":      None,
    "last_result":      None,
    "fase":             None,        # "localizando" | "varrendo"
    "fase_detalhe":     None,
    "nsu_inicio_scan":  None,
    "nsu_fim_scan":     None,
}
_cancel = threading.Event()


def _load():
    cfg = config.load_config()
    pfx = config.get_cert_path()
    if not pfx:
        raise RuntimeError("Certificado não configurado.")
    if not cfg.get("cnpj"):
        raise RuntimeError("CNPJ não configurado.")
    if not cfg.get("cert_password"):
        raise RuntimeError("Senha do certificado não configurada.")
    cnpj = cfg["cnpj"].replace(".", "").replace("/", "").replace("-", "")
    return cfg, pfx, cnpj, cfg.get("ambiente", "1"), cfg.get("uf_code", "43")


class ChavesBody(BaseModel):
    tipo: str          # "nfe" | "cte"
    chaves: list[str]


class PeriodoBody(BaseModel):
    tipo: str          # "nfe" | "cte"
    data_ini: str      # "YYYY-MM-DD"
    data_fim: str      # "YYYY-MM-DD"


@router.post("/por-chave")
def por_chave(body: ChavesBody):
    if _status["running"]:
        raise HTTPException(409, "Já existe uma recuperação em andamento.")
    if body.tipo not in ("nfe", "cte"):
        raise HTTPException(400, "tipo deve ser 'nfe' ou 'cte'.")

    chaves = [c.strip() for c in body.chaves if c.strip()]
    if not chaves:
        raise HTTPException(400, "Informe ao menos uma chave de acesso.")

    try:
        cfg, pfx, cnpj, tp_amb, cuf = _load()
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    rec_dir = config.REC_DIR
    rec_dir.mkdir(parents=True, exist_ok=True)

    _cancel.clear()
    _status.update({
        "running": True, "tipo": body.tipo, "modo": "chave",
        "total": len(chaves), "processados": 0, "scaneados": 0, "salvos": 0,
        "nsu_atual": None, "erros_lista": [], "data_ini": None, "data_fim": None,
        "iniciado_em": datetime.now().isoformat(), "last_result": None,
    })

    def _run():
        from app.services.recovery_service import recover_by_chaves
        try:
            total_req, total_salvo, meta_list = recover_by_chaves(
                pfx, cfg["cert_password"], cnpj, body.tipo,
                chaves, rec_dir, tp_amb, cuf, _status, _cancel,
            )
            for m in meta_list:
                database.save_document(
                    tipo=f"{body.tipo}_rec",
                    nsu=m["nsu"], chave=m["chave"], schema=m["schema"],
                    file_path=m["file_path"], emitente=m["emitente"],
                    destinatario=m["destinatario"], tomador=m.get("tomador", ""),
                    valor=m["valor"], data_emissao=m["data_emissao"],
                    numero=m.get("numero", ""),
                )
            _status["last_result"] = {
                "status": "success",
                "solicitados": total_req,
                "salvos": total_salvo,
                "erros": _status.get("erros_lista", []),
            }
        except Exception as e:
            _status["last_result"] = {"status": "error", "mensagem": str(e)}
        finally:
            _status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "mensagem": f"Recuperação por chave iniciada: {len(chaves)} chave(s)."}


@router.post("/por-periodo")
def por_periodo(body: PeriodoBody):
    if _status["running"]:
        raise HTTPException(409, "Já existe uma recuperação em andamento.")
    if body.tipo not in ("nfe", "cte"):
        raise HTTPException(400, "tipo deve ser 'nfe' ou 'cte'.")

    try:
        datetime.strptime(body.data_ini, "%Y-%m-%d")
        datetime.strptime(body.data_fim, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Datas devem estar no formato YYYY-MM-DD.")
    if body.data_ini > body.data_fim:
        raise HTTPException(400, "data_ini deve ser anterior a data_fim.")

    try:
        cfg, pfx, cnpj, tp_amb, cuf = _load()
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    rec_dir = config.REC_DIR
    rec_dir.mkdir(parents=True, exist_ok=True)

    _cancel.clear()
    _status.update({
        "running": True, "tipo": body.tipo, "modo": "periodo",
        "total": 0, "processados": 0, "scaneados": 0, "salvos": 0,
        "nsu_atual": None, "erros_lista": [],
        "data_ini": body.data_ini, "data_fim": body.data_fim,
        "iniciado_em": datetime.now().isoformat(), "last_result": None,
        "fase": "localizando", "fase_detalhe": "Iniciando...",
        "nsu_inicio_scan": None, "nsu_fim_scan": None,
    })

    def _run():
        from app.services.recovery_service import recover_by_period
        try:
            total_scan, total_salvo, meta_list = recover_by_period(
                pfx, cfg["cert_password"], cnpj, body.tipo,
                body.data_ini, body.data_fim,
                rec_dir, tp_amb, cuf, _status, _cancel,
            )
            for m in meta_list:
                database.save_document(
                    tipo=f"{body.tipo}_rec",
                    nsu=m["nsu"], chave=m["chave"], schema=m["schema"],
                    file_path=m["file_path"], emitente=m["emitente"],
                    destinatario=m["destinatario"], tomador=m.get("tomador", ""),
                    valor=m["valor"], data_emissao=m["data_emissao"],
                    numero=m.get("numero", ""),
                )
            _status["last_result"] = {
                "status": "success",
                "scaneados": total_scan,
                "salvos": total_salvo,
            }
        except Exception as e:
            _status["last_result"] = {"status": "error", "mensagem": str(e)}
        finally:
            _status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "mensagem": f"Recuperação por período iniciada: {body.data_ini} → {body.data_fim}."}


@router.post("/cancelar")
def cancelar():
    if not _status["running"]:
        return {"ok": False, "mensagem": "Nenhuma recuperação em andamento."}
    _cancel.set()
    return {"ok": True, "mensagem": "Cancelamento solicitado."}


@router.get("/status")
def get_status():
    return dict(_status)
