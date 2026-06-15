import logging
import threading
from app import config, database
from app.services import nfe_service, cte_service, nfse_service

logger = logging.getLogger(__name__)


def run_sync(tipo: str = "all", cancel_flag: threading.Event = None) -> dict:
    cfg = config.load_config()
    pfx_path = config.get_cert_path()
    xml_dir = config.XML_DIR

    if not pfx_path:
        raise RuntimeError("Certificado não configurado. Acesse Configurações e faça o upload do .pfx.")
    if not cfg.get("cnpj"):
        raise RuntimeError("CNPJ não configurado. Acesse Configurações.")
    if not cfg.get("cert_password"):
        raise RuntimeError("Senha do certificado não configurada.")

    cnpj = cfg["cnpj"].replace(".", "").replace("/", "").replace("-", "")
    tp_amb = cfg.get("ambiente", "1")
    cuf = cfg.get("uf_code", "43")
    results = {}

    if tipo in ("nfe", "all") and cfg.get("sync_nfe", True):
        if cancel_flag and cancel_flag.is_set():
            results["nfe"] = {"status": "error", "mensagem": "Cancelado pelo usuário."}
        else:
            results["nfe"] = _sync_tipo("nfe", pfx_path, cfg["cert_password"], cnpj, xml_dir, tp_amb, cuf, cancel_flag)

    if tipo in ("cte", "all") and cfg.get("sync_cte", True):
        if cancel_flag and cancel_flag.is_set():
            results["cte"] = {"status": "error", "mensagem": "Cancelado pelo usuário."}
        else:
            results["cte"] = _sync_tipo("cte", pfx_path, cfg["cert_password"], cnpj, xml_dir, tp_amb, cuf, cancel_flag)

    if tipo in ("nfse", "all") and cfg.get("sync_nfse", True):
        if cancel_flag and cancel_flag.is_set():
            results["nfse"] = {"status": "error", "mensagem": "Cancelado pelo usuário."}
        else:
            results["nfse"] = _sync_tipo("nfse", pfx_path, cfg["cert_password"], cnpj, xml_dir, tp_amb, cuf, cancel_flag)

    return results


def _sync_tipo(tipo: str, pfx_path, password, cnpj, xml_dir, tp_amb, cuf,
               cancel_flag: threading.Event = None) -> dict:
    ult_nsu = database.get_ult_nsu(tipo)
    log_id = database.start_sync_log(tipo, ult_nsu)
    logger.info(f"Iniciando sync {tipo.upper()} a partir de NSU {ult_nsu}")

    try:
        if tipo == "nfe":
            new_nsu, total, meta_list = nfe_service.sync_nfe(
                pfx_path, password, cnpj, ult_nsu, xml_dir, tp_amb, cuf, cancel_flag
            )
        elif tipo == "cte":
            new_nsu, total, meta_list = cte_service.sync_cte(
                pfx_path, password, cnpj, ult_nsu, xml_dir, tp_amb, cuf, cancel_flag
            )
        else:  # nfse
            new_nsu, total, meta_list = nfse_service.sync_nfse(
                pfx_path, password, cnpj, ult_nsu, xml_dir, tp_amb, cancel_flag
            )

        for m in meta_list:
            database.save_document(
                tipo=tipo, nsu=m["nsu"], chave=m["chave"], schema=m["schema"],
                file_path=m["file_path"], emitente=m["emitente"],
                destinatario=m["destinatario"], valor=m["valor"], data_emissao=m["data_emissao"],
            )

        database.set_ult_nsu(tipo, new_nsu)
        database.finish_sync_log(log_id, "success", new_nsu, total)
        logger.info(f"Sync {tipo.upper()} concluído: {total} documentos, NSU final {new_nsu}")
        return {"status": "success", "documentos": total, "nsu_final": new_nsu}

    except Exception as e:
        logger.error(f"Erro no sync {tipo.upper()}: {e}", exc_info=True)
        database.finish_sync_log(log_id, "error", ult_nsu, 0, str(e))
        return {"status": "error", "mensagem": str(e)}
