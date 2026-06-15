import base64
import gzip
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests

from app.services.certificate import cert_files

logger = logging.getLogger(__name__)

# ── Endpoints ─────────────────────────────────────────────────────────────────
NFE_URL_PROD    = "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
NFE_URL_HOM     = "https://hom.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
CTE_URL_PROD    = "https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx"
CTE_URL_HOM     = "https://hom.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx"
NS_NFE          = "http://www.portalfiscal.inf.br/nfe"
NS_WSDL_NFE     = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"
NS_CTE          = "http://www.portalfiscal.inf.br/cte"
NS_WSDL_CTE     = "http://www.portalfiscal.inf.br/cte/wsdl/CTeDistribuicaoDFe"


# ── SOAP builders ─────────────────────────────────────────────────────────────

def _soap_nfe_nsu(cnpj, nsu, tp_amb, cuf):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <nfeDistDFeInteresse xmlns="{NS_WSDL_NFE}">
      <nfeDadosMsg>
        <distDFeInt versao="1.01" xmlns="{NS_NFE}">
          <tpAmb>{tp_amb}</tpAmb><cUFAutor>{cuf}</cUFAutor><CNPJ>{cnpj}</CNPJ>
          <distNSU><ultNSU>{nsu.zfill(15)}</ultNSU></distNSU>
        </distDFeInt>
      </nfeDadosMsg>
    </nfeDistDFeInteresse>
  </soap12:Body>
</soap12:Envelope>"""


def _soap_cte_nsu(cnpj, nsu, tp_amb, cuf):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <cteDistDFeInteresse xmlns="{NS_WSDL_CTE}">
      <cteDadosMsg>
        <distDFeInt versao="1.00" xmlns="{NS_CTE}">
          <tpAmb>{tp_amb}</tpAmb><cUFAutor>{cuf}</cUFAutor><CNPJ>{cnpj}</CNPJ>
          <distNSU><ultNSU>{nsu.zfill(15)}</ultNSU></distNSU>
        </distDFeInt>
      </cteDadosMsg>
    </cteDistDFeInteresse>
  </soap12:Body>
</soap12:Envelope>"""


def _soap_nfe_chave(cnpj, chave, tp_amb, cuf):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <nfeDistDFeInteresse xmlns="{NS_WSDL_NFE}">
      <nfeDadosMsg>
        <distDFeInt versao="1.01" xmlns="{NS_NFE}">
          <tpAmb>{tp_amb}</tpAmb><cUFAutor>{cuf}</cUFAutor><CNPJ>{cnpj}</CNPJ>
          <consChNFe><chNFe>{chave}</chNFe></consChNFe>
        </distDFeInt>
      </nfeDadosMsg>
    </nfeDistDFeInteresse>
  </soap12:Body>
</soap12:Envelope>"""


def _soap_cte_chave(cnpj, chave, tp_amb, cuf):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <cteDistDFeInteresse xmlns="{NS_WSDL_CTE}">
      <cteDadosMsg>
        <distDFeInt versao="1.00" xmlns="{NS_CTE}">
          <tpAmb>{tp_amb}</tpAmb><cUFAutor>{cuf}</cUFAutor><CNPJ>{cnpj}</CNPJ>
          <consChCTe><chCTe>{chave}</chCTe></consChCTe>
        </distDFeInt>
      </cteDadosMsg>
    </cteDistDFeInteresse>
  </soap12:Body>
</soap12:Envelope>"""


# ── Response parsers ──────────────────────────────────────────────────────────

def _parse(xml_text: str, ns: str) -> tuple[str, str, str, str, list]:
    root = ET.fromstring(xml_text)
    ret = root.find(f".//{{{ns}}}retDistDFeInt")
    if ret is None:
        raise ValueError(f"retDistDFeInt not found: {xml_text[:400]}")
    c_stat   = ret.findtext(f"{{{ns}}}cStat", "")
    x_motivo = ret.findtext(f"{{{ns}}}xMotivo", "")
    ult_nsu  = ret.findtext(f"{{{ns}}}ultNSU", "000000000000000")
    max_nsu  = ret.findtext(f"{{{ns}}}maxNSU", "000000000000000")
    docs = []
    lote = ret.find(f"{{{ns}}}loteDistDFeInt")
    if lote is not None:
        for dz in lote.findall(f"{{{ns}}}docZip"):
            nsu    = dz.get("NSU", "")
            schema = dz.get("schema", "")
            xml_b  = gzip.decompress(base64.b64decode(dz.text or ""))
            docs.append((nsu, schema, xml_b))
    return c_stat, x_motivo, ult_nsu, max_nsu, docs


# ── Helpers ───────────────────────────────────────────────────────────────────

def _post(session, url, body, action, timeout=60):
    r = session.post(
        url,
        data=body.encode("utf-8"),
        headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.text


def _doc_date(schema: str, xml_b: bytes) -> str:
    """Extract emission date YYYY-MM-DD from any doc schema."""
    try:
        root = ET.fromstring(xml_b)
        ns = NS_NFE if ("NF" in schema or "nf" in schema) else NS_CTE
        dh = (root.findtext(f".//{{{ns}}}dhEmi") or
              root.findtext(f".//{{{ns}}}dEmi") or "")
        return dh[:10]
    except Exception:
        return ""


def _summary_chave(schema: str, xml_b: bytes) -> str:
    """Extract chave de acesso from resNFe / resCTe summary."""
    try:
        root = ET.fromstring(xml_b)
        if "NF" in schema or "nf" in schema:
            return root.findtext(f".//{{{NS_NFE}}}chNFe") or ""
        return root.findtext(f".//{{{NS_CTE}}}chCTe") or ""
    except Exception:
        return ""


def _find_nsu_for_date(
    session, url, action, ns, soap_nsu_fn, target_date: str,
    lo: int, hi: int, progress: dict = None, probe_label: str = "",
) -> int:
    """
    Busca binária para encontrar o NSU aproximado onde os documentos têm
    data de emissão próxima a target_date. NSUs são aproximadamente (mas não
    estritamente) ordenados por data, então o resultado é uma estimativa.
    Retorna o NSU inferior do intervalo encontrado.
    """
    probes = 0
    while hi - lo > 100:
        mid = (lo + hi) // 2
        probes += 1
        if progress is not None:
            progress["fase_detalhe"] = f"{probe_label} NSU {str(mid).zfill(15)} (sonda {probes})"

        try:
            body = soap_nsu_fn(str(mid).zfill(15))
            resp = _post(session, url, body, action)
            _, _, _, _, docs = _parse(resp, ns)

            dates = [
                _doc_date(schema, xml_b)
                for _, schema, xml_b in docs
                if "Evento" not in schema and "evento" not in schema
            ]
            dates = [d for d in dates if d]

            if not dates:
                lo = mid  # sem datas úteis → avança
                continue

            median = sorted(dates)[len(dates) // 2]
            logger.debug(f"Sonda NSU={mid}, mediana={median}, alvo={target_date}")

            if median < target_date:
                lo = mid
            else:
                hi = mid

        except Exception as e:
            logger.warning(f"Sonda em NSU {mid} falhou: {e}")
            lo = mid

        time.sleep(1)

    return lo


def _save(tipo: str, schema: str, xml_b: bytes, rec_dir: Path, nsu: str = "") -> dict:
    """Save XML to rec_dir/nfe/ or rec_dir/cte/ and return metadata dict."""
    from app.services.nfe_service import _extract_nfe_meta
    from app.services.cte_service import _extract_cte_meta

    is_nfe = tipo == "nfe"
    meta   = _extract_nfe_meta(xml_b) if is_nfe else _extract_cte_meta(xml_b)
    sub    = "nfe" if is_nfe else "cte"

    chave    = meta.get("chave", "")
    date_str = (meta.get("data_emissao", "")[:7].replace("-", "")
                if meta.get("data_emissao") else datetime.now().strftime("%Y%m"))
    year, month = date_str[:4], date_str[4:6]

    dest = rec_dir / sub / year / month
    dest.mkdir(parents=True, exist_ok=True)
    fp = dest / f"{chave or nsu or 'doc'}.xml"
    fp.write_bytes(xml_b)

    return {
        "nsu":          nsu,
        "chave":        chave,
        "schema":       schema,
        "file_path":    str(fp.relative_to(rec_dir.parent)),
        "emitente":     meta.get("emitente", ""),
        "destinatario": meta.get("destinatario", ""),
        "tomador":      meta.get("tomador", ""),
        "valor":        meta.get("valor", ""),
        "data_emissao": meta.get("data_emissao", ""),
    }


# ── Public functions ──────────────────────────────────────────────────────────

def recover_by_chaves(
    pfx_path: Path, password: str, cnpj: str, tipo: str,
    chaves: list[str], rec_dir: Path,
    tp_amb: str = "1", cuf: str = "43",
    progress: dict = None, cancel_flag=None,
) -> tuple[int, int, list]:
    """
    Baixa documentos específicos pelas chaves de acesso (44 dígitos).
    Retorna (total_solicitados, total_salvos, saved_meta).
    """
    is_nfe  = tipo == "nfe"
    url     = (NFE_URL_PROD if tp_amb == "1" else NFE_URL_HOM) if is_nfe else \
              (CTE_URL_PROD if tp_amb == "1" else CTE_URL_HOM)
    action  = (f"{NS_WSDL_NFE}/nfeDistDFeInteresse" if is_nfe
               else f"{NS_WSDL_CTE}/cteDistDFeInteresse")
    ns      = NS_NFE if is_nfe else NS_CTE

    total_saved = 0
    saved_meta  = []
    erros       = []

    with cert_files(pfx_path, password) as (cert_f, key_f):
        session = requests.Session()
        session.cert = (cert_f, key_f)
        session.verify = True

        for i, chave in enumerate(chaves):
            if cancel_flag and cancel_flag.is_set():
                break

            chave = chave.strip()
            if not chave:
                continue
            if len(chave) != 44 or not chave.isdigit():
                erros.append(f"Chave inválida (precisa ter 44 dígitos): {chave!r}")
                continue

            if progress is not None:
                progress["processados"] = i + 1
                progress["nsu_atual"]   = chave

            try:
                body     = _soap_nfe_chave(cnpj, chave, tp_amb, cuf) if is_nfe else \
                           _soap_cte_chave(cnpj, chave, tp_amb, cuf)
                resp_txt = _post(session, url, body, action)
                c_stat, x_motivo, _, _, docs = _parse(resp_txt, ns)

                if c_stat == "656":
                    raise RuntimeError("SEFAZ bloqueou (cStat 656). Aguarde ~1 hora e tente novamente.")

                if c_stat not in ("137", "138"):
                    erros.append(f"{chave}: cStat {c_stat} — {x_motivo}")
                    continue

                if not docs:
                    erros.append(f"{chave}: documento não encontrado no SEFAZ.")
                    continue

                for nsu, schema, xml_b in docs:
                    m = _save(tipo, schema, xml_b, rec_dir, nsu)
                    saved_meta.append(m)
                    total_saved += 1

                if progress is not None:
                    progress["salvos"] = total_saved

                time.sleep(2)

            except RuntimeError:
                raise
            except Exception as e:
                logger.error(f"Erro na chave {chave}: {e}")
                erros.append(f"{chave}: {e}")

    if progress is not None:
        progress["erros_lista"] = erros

    return len(chaves), total_saved, saved_meta


def recover_by_period(
    pfx_path: Path, password: str, cnpj: str, tipo: str,
    data_ini: str, data_fim: str,
    rec_dir: Path,
    tp_amb: str = "1", cuf: str = "43",
    progress: dict = None, cancel_flag=None,
) -> tuple[int, int, list]:
    """
    Usa busca binária para localizar o intervalo de NSUs correspondente ao
    período e depois varre sequencialmente só esse trecho.
    Para resumos (resNFe/resCTe) faz chamada secundária para obter XML completo.
    Retorna (total_scaneados, total_salvos, saved_meta).
    """
    is_nfe = tipo == "nfe"
    url    = (NFE_URL_PROD if tp_amb == "1" else NFE_URL_HOM) if is_nfe else \
             (CTE_URL_PROD if tp_amb == "1" else CTE_URL_HOM)
    action = (f"{NS_WSDL_NFE}/nfeDistDFeInteresse" if is_nfe
              else f"{NS_WSDL_CTE}/cteDistDFeInteresse")
    ns     = NS_NFE if is_nfe else NS_CTE

    def soap_nsu(nsu: str) -> str:
        return (_soap_nfe_nsu if is_nfe else _soap_cte_nsu)(cnpj, nsu, tp_amb, cuf)

    def soap_chave(ch: str) -> str:
        return (_soap_nfe_chave if is_nfe else _soap_cte_chave)(cnpj, ch, tp_amb, cuf)

    total_scanned = 0
    total_saved   = 0
    saved_meta    = []

    with cert_files(pfx_path, password) as (cert_f, key_f):
        session = requests.Session()
        session.cert = (cert_f, key_f)
        session.verify = True

        # ── Fase 1: obter NSU máximo ──────────────────────────────────────
        if progress is not None:
            progress["fase"] = "localizando"
            progress["fase_detalhe"] = "Consultando NSU máximo do SEFAZ..."

        resp0 = _post(session, url, soap_nsu("000000000000000"), action)
        _, _, _, max_nsu_str, _ = _parse(resp0, ns)
        max_nsu_int = int(max_nsu_str)
        logger.info(f"NSU máximo: {max_nsu_int}")
        time.sleep(2)

        # ── Fase 2: busca binária para data_ini ──────────────────────────
        if progress is not None:
            progress["fase_detalhe"] = f"Localizando início {data_ini}..."

        nsu_ini_approx = _find_nsu_for_date(
            session, url, action, ns, soap_nsu, data_ini,
            0, max_nsu_int, progress, f"Início {data_ini}:",
        )
        time.sleep(2)

        # ── Fase 3: busca binária para data_fim ──────────────────────────
        if progress is not None:
            progress["fase_detalhe"] = f"Localizando fim {data_fim}..."

        nsu_fim_approx = _find_nsu_for_date(
            session, url, action, ns, soap_nsu, data_fim,
            nsu_ini_approx, max_nsu_int, progress, f"Fim {data_fim}:",
        )
        time.sleep(2)

        # Margem de 2000 NSUs (~36 dias de buffer) para absorver NSUs fora de ordem
        MARGIN = 2000
        nsu_start = max(0, nsu_ini_approx - MARGIN)
        nsu_end   = min(max_nsu_int, nsu_fim_approx + MARGIN)

        logger.info(
            f"Período {data_ini}–{data_fim}: NSUs aprox. {nsu_ini_approx}–{nsu_fim_approx}, "
            f"varredura {nsu_start}–{nsu_end} (max {max_nsu_int})"
        )

        if progress is not None:
            progress["fase"]            = "varrendo"
            progress["fase_detalhe"]    = f"NSU {str(nsu_start).zfill(15)} → {str(nsu_end).zfill(15)}"
            progress["nsu_inicio_scan"] = str(nsu_start).zfill(15)
            progress["nsu_fim_scan"]    = str(nsu_end).zfill(15)

        # ── Fase 4: varredura sequencial no intervalo encontrado ─────────
        current_nsu = str(nsu_start).zfill(15)

        while True:
            if cancel_flag and cancel_flag.is_set():
                logger.info("Recuperação por período cancelada pelo usuário.")
                break

            if progress is not None:
                progress["nsu_atual"]  = current_nsu
                progress["scaneados"]  = total_scanned
                progress["salvos"]     = total_saved

            resp_txt = _post(session, url, soap_nsu(current_nsu), action)
            c_stat, x_motivo, ult_nsu_resp, _, docs = _parse(resp_txt, ns)

            logger.info(f"Rec período {tipo.upper()}: NSU {current_nsu}, cStat={c_stat}, {len(docs)} docs")

            if c_stat == "656":
                raise RuntimeError("SEFAZ bloqueou (cStat 656). Aguarde ~1 hora e tente novamente.")
            if c_stat not in ("137", "138"):
                raise RuntimeError(f"Erro SEFAZ: {c_stat} — {x_motivo}")

            for nsu, schema, xml_b in docs:
                total_scanned += 1

                if "Evento" in schema or "evento" in schema:
                    continue

                doc_date = _doc_date(schema, xml_b)
                if not doc_date:
                    continue

                if doc_date < data_ini or doc_date > data_fim:
                    continue

                if schema.startswith("res"):
                    chave = _summary_chave(schema, xml_b)
                    if not chave:
                        continue
                    try:
                        resp2 = _post(session, url, soap_chave(chave), action)
                        _, _, _, _, docs2 = _parse(resp2, ns)
                        if not docs2:
                            logger.warning(f"XML completo não disponível para chave {chave}")
                            continue
                        nsu2, schema2, xml_b2 = docs2[0]
                        m = _save(tipo, schema2, xml_b2, rec_dir, nsu)
                        time.sleep(1)
                    except RuntimeError:
                        raise
                    except Exception as e:
                        logger.warning(f"Falha ao buscar XML completo da chave {chave}: {e}")
                        continue
                else:
                    m = _save(tipo, schema, xml_b, rec_dir, nsu)

                saved_meta.append(m)
                total_saved += 1
                if progress is not None:
                    progress["salvos"] = total_saved

            current_nsu = ult_nsu_resp

            if int(current_nsu) > nsu_end:
                logger.info(f"NSU {current_nsu} ultrapassou limite estimado {nsu_end}, encerrando.")
                break

            if c_stat == "137" or ult_nsu_resp == max_nsu_str:
                break

            time.sleep(5)

    return total_scanned, total_saved, saved_meta
