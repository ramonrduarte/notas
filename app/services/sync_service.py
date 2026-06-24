import logging
import threading
from datetime import datetime, timedelta
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


_NFE_COOLDOWN_HOURS = 6
_NFE_656_BASE_BACKOFF_HOURS = 48   # 1º bloqueio: 48h
_NFE_656_MAX_BACKOFF_HOURS = 168   # Teto: 7 dias (progressão: 48h → 96h → 168h)


def get_nfe_block_info(tipo: str = "nfe") -> dict:
    """Retorna informações sobre bloqueio 656 ativo com backoff progressivo.

    Retorna dict com chaves: is_blocked, backoff_hours, consecutive, blocked_until (ISO ou None).
    Backoff cresce exponencialmente: 48h → 96h → 168h (teto de 7 dias).
    """
    last_err_msg, last_err_time = database.get_last_error_sync(tipo)
    if not last_err_time or "656" not in (last_err_msg or ""):
        return {"is_blocked": False, "backoff_hours": 0, "consecutive": 0, "blocked_until": None}

    consecutive = database.count_consecutive_656_errors(tipo)
    backoff_hours = min(_NFE_656_BASE_BACKOFF_HOURS * (2 ** (consecutive - 1)), _NFE_656_MAX_BACKOFF_HOURS)

    last_err_dt = datetime.fromisoformat(last_err_time)
    blocked_until_dt = last_err_dt + timedelta(hours=backoff_hours)

    is_blocked = datetime.now() < blocked_until_dt
    return {
        "is_blocked": is_blocked,
        "backoff_hours": backoff_hours,
        "consecutive": consecutive,
        "blocked_until": blocked_until_dt.isoformat() if is_blocked else None,
    }


def _sync_tipo(tipo: str, pfx_path, password, cnpj, xml_dir, tp_amb, cuf,
               cancel_flag: threading.Event = None, only_authorized: bool = False,
               nfe_direction: str = "ambas", cte_role: str = "tomador",
               nfse_role: str = "ambas") -> dict:
    base = tipo.replace("_hist", "")

    ult_nsu = database.get_ult_nsu(tipo)
    log_id = database.start_sync_log(tipo, ult_nsu)

    # NF-e DistDFe: proteções contra cStat 656
    if base == "nfe" and not tipo.endswith("_hist"):
        # 1. Cooldown após último sucesso
        last_ok = database.get_last_success_time(tipo)
        if last_ok:
            elapsed = datetime.now() - datetime.fromisoformat(last_ok)
            if elapsed < timedelta(hours=_NFE_COOLDOWN_HOURS):
                h = int(elapsed.total_seconds() // 3600)
                m = int((elapsed.total_seconds() % 3600) // 60)
                msg = (
                    f"NF-e sincronizada há {h}h{m:02d}min. "
                    f"Aguarde ao menos {_NFE_COOLDOWN_HOURS}h entre sincronizações para evitar cStat 656."
                )
                logger.warning(msg)
                database.finish_sync_log(log_id, "skipped", ult_nsu, 0, msg)
                return {"status": "skipped", "mensagem": msg}

        # 2. Backoff progressivo após bloqueio 656 (48h → 96h → 168h)
        block_info = get_nfe_block_info(tipo)
        if block_info["is_blocked"]:
            elapsed_block = datetime.now() - datetime.fromisoformat(database.get_last_error_sync(tipo)[1])
            h = int(elapsed_block.total_seconds() // 3600)
            m = int((elapsed_block.total_seconds() % 3600) // 60)
            remaining_secs = block_info["backoff_hours"] * 3600 - elapsed_block.total_seconds()
            remaining_h = int(remaining_secs // 3600) + 1
            msg = (
                f"NF-e bloqueada pelo SEFAZ (cStat 656) há {h}h{m:02d}min "
                f"(bloqueio consecutivo #{block_info['consecutive']}, aguardando {block_info['backoff_hours']}h). "
                f"Próxima tentativa em {remaining_h}h para não prolongar o bloqueio."
            )
            logger.warning(msg)
            database.finish_sync_log(log_id, "skipped", ult_nsu, 0, msg)
            return {"status": "skipped", "mensagem": msg}
    logger.info(f"Iniciando sync {tipo.upper()} a partir de NSU {ult_nsu}")

    try:
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
                numero=m.get("numero", ""),
            )

        database.set_ult_nsu(tipo, new_nsu)
        database.finish_sync_log(log_id, "success", new_nsu, total)
        logger.info(f"Sync {tipo.upper()} concluído: {total} documentos, NSU final {new_nsu}")
        return {"status": "success", "documentos": total, "nsu_final": new_nsu}

    except Exception as e:
        logger.error(f"Erro no sync {tipo.upper()}: {e}", exc_info=True)
        database.finish_sync_log(log_id, "error", ult_nsu, 0, str(e))
        return {"status": "error", "mensagem": str(e)}
