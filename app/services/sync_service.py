import logging
import threading
from app import config, database
from app.services import nfe_service, cte_service, nfse_service

logger = logging.getLogger(__name__)


def _load_base():
    """Valida config e retorna (cfg, pfx_path, cnpj, tp_amb, cuf, xml_dir)."""
    cfg = config.load_config()
    pfx_path = config.get_cert_path()
    if not pfx_path:
        raise RuntimeError("Certificado não configurado. Acesse Configurações e faça o upload do .pfx.")
    if not cfg.get("cnpj"):
        raise RuntimeError("CNPJ não configurado. Acesse Configurações.")
    if not cfg.get("cert_password"):
        raise RuntimeError("Senha do certificado não configurada.")
    cnpj = cfg["cnpj"].replace(".", "").replace("/", "").replace("-", "")
    return cfg, pfx_path, cnpj, cfg.get("ambiente", "1"), cfg.get("uf_code", "43"), config.XML_DIR


def run_sync(tipo: str = "all", cancel_flag: threading.Event = None) -> dict:
    cfg, pfx_path, cnpj, tp_amb, cuf, xml_dir = _load_base()
    results = {}

    extras = {
        "nfe_direction": cfg.get("nfe_direction", "ambas"),
        "cte_role":      cfg.get("cte_role", "tomador"),
        "nfse_role":     cfg.get("nfse_role", "ambas"),
    }

    if tipo in ("nfe", "all") and cfg.get("sync_nfe", True):
        if cancel_flag and cancel_flag.is_set():
            results["nfe"] = {"status": "error", "mensagem": "Cancelado pelo usuário."}
        else:
            results["nfe"] = _sync_tipo("nfe", pfx_path, cfg["cert_password"], cnpj, xml_dir, tp_amb, cuf, cancel_flag, **extras)

    if tipo in ("cte", "all") and cfg.get("sync_cte", True):
        if cancel_flag and cancel_flag.is_set():
            results["cte"] = {"status": "error", "mensagem": "Cancelado pelo usuário."}
        else:
            results["cte"] = _sync_tipo("cte", pfx_path, cfg["cert_password"], cnpj, xml_dir, tp_amb, cuf, cancel_flag, **extras)

    if tipo in ("nfse", "all") and cfg.get("sync_nfse", True):
        if cancel_flag and cancel_flag.is_set():
            results["nfse"] = {"status": "error", "mensagem": "Cancelado pelo usuário."}
        else:
            results["nfse"] = _sync_tipo("nfse", pfx_path, cfg["cert_password"], cnpj, xml_dir, tp_amb, cuf, cancel_flag, **extras)

    return results


def run_recovery(tipos: list[str], only_authorized: bool,
                 cancel_flag: threading.Event = None) -> dict:
    """Baixa documentos históricos em pasta separada (xmls_historico/).
    Continua de onde parou — para reiniciar do zero, resete os NSUs _hist antes de chamar."""
    cfg, pfx_path, cnpj, tp_amb, cuf, _ = _load_base()
    hist_dir = config.HIST_DIR
    results = {}

    extras = {
        "nfe_direction":   cfg.get("nfe_direction", "ambas"),
        "cte_role":        cfg.get("cte_role", "tomador"),
        "nfse_role":       cfg.get("nfse_role", "ambas"),
        "only_authorized": only_authorized,
    }

    for tipo_base in tipos:
        tipo_hist = f"{tipo_base}_hist"
        if cancel_flag and cancel_flag.is_set():
            results[tipo_hist] = {"status": "error", "mensagem": "Cancelado pelo usuário."}
            continue
        results[tipo_hist] = _sync_tipo(
            tipo_hist, pfx_path, cfg["cert_password"], cnpj, hist_dir, tp_amb, cuf, cancel_flag, **extras
        )

    return results


def _sync_tipo(tipo: str, pfx_path, password, cnpj, xml_dir, tp_amb, cuf,
               cancel_flag: threading.Event = None, only_authorized: bool = False,
               nfe_direction: str = "ambas", cte_role: str = "tomador",
               nfse_role: str = "ambas") -> dict:
    ult_nsu = database.get_ult_nsu(tipo)
    log_id = database.start_sync_log(tipo, ult_nsu)
    logger.info(f"Iniciando sync {tipo.upper()} a partir de NSU {ult_nsu}")

    try:
        base = tipo.replace("_hist", "")  # nfe_hist → nfe
        if base == "nfe":
            new_nsu, total, meta_list = nfe_service.sync_nfe(
                pfx_path, password, cnpj, ult_nsu, xml_dir, tp_amb, cuf, cancel_flag,
                direction=nfe_direction, only_authorized=only_authorized,
            )
        elif base == "cte":
            new_nsu, total, meta_list = cte_service.sync_cte(
                pfx_path, password, cnpj, ult_nsu, xml_dir, tp_amb, cuf, cancel_flag,
                cte_role=cte_role, only_authorized=only_authorized,
            )
        else:  # nfse / nfse_hist
            new_nsu, total, meta_list = nfse_service.sync_nfse(
                pfx_path, password, cnpj, ult_nsu, xml_dir, tp_amb, cancel_flag,
                nfse_role=nfse_role,
            )

        for m in meta_list:
            database.save_document(
                tipo=tipo, nsu=m["nsu"], chave=m["chave"], schema=m["schema"],
                file_path=m["file_path"], emitente=m["emitente"],
                destinatario=m["destinatario"], tomador=m.get("tomador", ""),
                valor=m["valor"], data_emissao=m["data_emissao"],
            )

        database.set_ult_nsu(tipo, new_nsu)
        database.finish_sync_log(log_id, "success", new_nsu, total)
        logger.info(f"Sync {tipo.upper()} concluído: {total} documentos, NSU final {new_nsu}")
        return {"status": "success", "documentos": total, "nsu_final": new_nsu}

    except Exception as e:
        logger.error(f"Erro no sync {tipo.upper()}: {e}", exc_info=True)
        database.finish_sync_log(log_id, "error", ult_nsu, 0, str(e))
        return {"status": "error", "mensagem": str(e)}
