import base64
import gzip
import time
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

import requests

from app.services.certificate import cert_files

logger = logging.getLogger(__name__)

CTE_DIST_URL_PROD = "https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx"
CTE_DIST_URL_HOM = "https://hom.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx"

NS_CTE = "http://www.portalfiscal.inf.br/cte"
NS_WSDL = "http://www.portalfiscal.inf.br/cte/wsdl/CTeDistribuicaoDFe"
NS_SOAP = "http://www.w3.org/2003/05/soap-envelope"


def _build_soap(cnpj: str, ult_nsu: str, tp_amb: str, cuf: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <cteDistDFeInteresse xmlns="{NS_WSDL}">
      <cteDadosMsg>
        <distDFeInt versao="1.00" xmlns="{NS_CTE}">
          <tpAmb>{tp_amb}</tpAmb>
          <cUFAutor>{cuf}</cUFAutor>
          <CNPJ>{cnpj}</CNPJ>
          <distNSU>
            <ultNSU>{ult_nsu.zfill(15)}</ultNSU>
          </distNSU>
        </distDFeInt>
      </cteDadosMsg>
    </cteDistDFeInteresse>
  </soap12:Body>
</soap12:Envelope>"""


def _parse_response(xml_text: str) -> tuple[str, str, str, str, list]:
    root = ET.fromstring(xml_text)
    ret = root.find(f".//{{{NS_CTE}}}retDistDFeInt")
    if ret is None:
        raise ValueError(f"retDistDFeInt (CT-e) not found. Response:\n{xml_text[:500]}")

    c_stat = ret.findtext(f"{{{NS_CTE}}}cStat", "")
    x_motivo = ret.findtext(f"{{{NS_CTE}}}xMotivo", "")
    ult_nsu = ret.findtext(f"{{{NS_CTE}}}ultNSU", "000000000000000")
    max_nsu = ret.findtext(f"{{{NS_CTE}}}maxNSU", "000000000000000")

    docs = []
    lote = ret.find(f"{{{NS_CTE}}}loteDistDFeInt")
    if lote is not None:
        for doc_zip in lote.findall(f"{{{NS_CTE}}}docZip"):
            nsu = doc_zip.get("NSU", "")
            schema = doc_zip.get("schema", "")
            compressed = base64.b64decode(doc_zip.text or "")
            xml_bytes = gzip.decompress(compressed)
            docs.append((nsu, schema, xml_bytes))

    return c_stat, x_motivo, ult_nsu, max_nsu, docs


def _extract_cte_emit_cnpj(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
        return root.findtext(f".//{{{NS_CTE}}}emit/{{{NS_CTE}}}CNPJ") or ""
    except Exception:
        return ""


def _extract_tomador_cnpj(xml_bytes: bytes) -> str:
    """Extrai o CNPJ do tomador do serviço do XML CT-e."""
    try:
        root = ET.fromstring(xml_bytes)
        ns = NS_CTE
        toma = root.findtext(f".//{{{ns}}}toma") or ""
        tag_map = {"0": "rem", "1": "exped", "2": "receb"}
        tag = tag_map.get(toma)
        if tag:
            return (root.findtext(f".//{{{ns}}}{tag}/{{{ns}}}CNPJ") or
                    root.findtext(f".//{{{ns}}}{tag}/{{{ns}}}CPF") or "")
        if toma == "3":
            return (root.findtext(f".//{{{ns}}}toma03/{{{ns}}}CNPJ") or
                    root.findtext(f".//{{{ns}}}toma03/{{{ns}}}CPF") or "")
    except Exception:
        pass
    return ""


def _extract_cte_meta(xml_bytes: bytes) -> dict:
    try:
        root = ET.fromstring(xml_bytes)
        ns = NS_CTE

        inf_cte = root.find(f".//{{{ns}}}infCte")
        ide = root.find(f".//{{{ns}}}ide")
        emit = root.find(f".//{{{ns}}}emit")
        dest = root.find(f".//{{{ns}}}dest")
        v_tot = root.find(f".//{{{ns}}}vTPrest")

        chave = ""
        if inf_cte is not None:
            chave = inf_cte.get("Id", "").replace("CTe", "")

        data_emissao = ""
        if ide is not None:
            data_emissao = ide.findtext(f"{{{ns}}}dhEmi") or ""

        emitente = ""
        if emit is not None:
            emitente = emit.findtext(f"{{{ns}}}xNome") or emit.findtext(f"{{{ns}}}CNPJ") or ""

        destinatario = ""
        if dest is not None:
            destinatario = dest.findtext(f"{{{ns}}}xNome") or dest.findtext(f"{{{ns}}}CNPJ") or ""

        valor = ""
        if v_tot is not None:
            valor = v_tot.findtext(f"{{{ns}}}vTPrest") or ""

        return {"chave": chave, "emitente": emitente, "destinatario": destinatario,
                "valor": valor, "data_emissao": data_emissao[:10]}
    except Exception:
        return {"chave": "", "emitente": "", "destinatario": "", "valor": "", "data_emissao": ""}


def sync_cte(pfx_path: Path, password: str, cnpj: str, ult_nsu: str,
             xml_dir: Path, tp_amb: str = "1", cuf: str = "43",
             cancel_flag=None, cte_role: str = "tomador",
             only_authorized: bool = False) -> tuple[str, int, list]:
    """
    Download all CT-e since ult_nsu.
    Returns (new_ult_nsu, total_docs, saved_docs_metadata).
    """
    url = CTE_DIST_URL_PROD if tp_amb == "1" else CTE_DIST_URL_HOM
    current_nsu = ult_nsu
    total_saved = 0
    saved_meta = []

    with cert_files(pfx_path, password) as (cert_f, key_f):
        session = requests.Session()
        session.cert = (cert_f, key_f)
        session.verify = True

        while True:
            if cancel_flag and cancel_flag.is_set():
                raise RuntimeError("Sync CT-e cancelado pelo usuário.")
            soap_body = _build_soap(cnpj, current_nsu, tp_amb, cuf)
            logger.info(f"CT-e DistDFe: consultando a partir de NSU {current_nsu}")

            try:
                resp = session.post(
                    url,
                    data=soap_body.encode("utf-8"),
                    headers={
                        "Content-Type": f'application/soap+xml; charset=utf-8; action="{NS_WSDL}/cteDistDFeInteresse"',
                    },
                    timeout=60,
                )
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.error(f"CT-e DistDFe request error: {e}")
                raise

            c_stat, x_motivo, ult_nsu_resp, max_nsu, docs = _parse_response(resp.text)
            logger.info(f"CT-e DistDFe: cStat={c_stat} ({x_motivo}), {len(docs)} docs, maxNSU={max_nsu}")

            if c_stat == "656":
                raise RuntimeError(
                    "CT-e DistDFe: Consumo Indevido (cStat 656). "
                    "O SEFAZ bloqueou requisições excessivas. Aguarde ~1 hora e tente novamente."
                )

            if c_stat not in ("137", "138"):
                raise RuntimeError(f"CT-e DistDFe erro: {c_stat} - {x_motivo}")

            for nsu, schema, xml_bytes in docs:
                # Ignora eventos (cancelamento, carta de correção, etc.)
                if "Evento" in schema or "evento" in schema:
                    logger.debug(f"CT-e: ignorando evento NSU {nsu} schema {schema}")
                    continue

                # Filtro: somente autorizadas (procCTe) — pula resumos também
                if only_authorized and not schema.startswith("procCTe"):
                    continue

                # Filtro de participação (tomador / emitente / ambas)
                if schema.startswith("proc"):
                    if cte_role == "tomador":
                        tomador_cnpj = _extract_tomador_cnpj(xml_bytes)
                        if tomador_cnpj and tomador_cnpj != cnpj:
                            logger.debug(f"CT-e NSU {nsu}: empresa não é tomador (tomador={tomador_cnpj}), ignorando.")
                            continue
                    elif cte_role == "emitente":
                        emit_cnpj = _extract_cte_emit_cnpj(xml_bytes)
                        if emit_cnpj and emit_cnpj != cnpj:
                            logger.debug(f"CT-e NSU {nsu}: empresa não é emitente (emit={emit_cnpj}), ignorando.")
                            continue
                    # "ambas" → sem filtro adicional

                meta = _extract_cte_meta(xml_bytes)
                chave = meta["chave"] or nsu

                date_str = meta["data_emissao"][:7].replace("-", "") if meta["data_emissao"] else datetime.now().strftime("%Y%m")
                year, month = date_str[:4], date_str[4:6]
                dest_dir = xml_dir / "cte" / year / month
                dest_dir.mkdir(parents=True, exist_ok=True)

                filename = f"{chave or nsu}.xml"
                file_path = dest_dir / filename
                file_path.write_bytes(xml_bytes)

                saved_meta.append({
                    "nsu": nsu,
                    "chave": meta["chave"],
                    "schema": schema,
                    "file_path": str(file_path.relative_to(xml_dir.parent)),
                    "emitente": meta["emitente"],
                    "destinatario": meta["destinatario"],
                    "valor": meta["valor"],
                    "data_emissao": meta["data_emissao"],
                })
                total_saved += 1

            current_nsu = ult_nsu_resp

            # 137 = nenhum doc localizado (fim); 138 = doc localizado (continuar)
            if c_stat == "137" or ult_nsu_resp == max_nsu:
                break

            time.sleep(5)  # SEFAZ bloqueia com requisições rápidas (cStat 656)

    return current_nsu, total_saved, saved_meta
