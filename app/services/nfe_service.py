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

NFE_DIST_URL_PROD = "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
NFE_DIST_URL_HOM = "https://hom.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"

NS_NFE = "http://www.portalfiscal.inf.br/nfe"
NS_WSDL = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"
NS_SOAP = "http://www.w3.org/2003/05/soap-envelope"


def _build_soap(cnpj: str, ult_nsu: str, tp_amb: str, cuf: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <nfeDistDFeInteresse xmlns="{NS_WSDL}">
      <nfeDadosMsg>
        <distDFeInt versao="1.01" xmlns="{NS_NFE}">
          <tpAmb>{tp_amb}</tpAmb>
          <cUFAutor>{cuf}</cUFAutor>
          <CNPJ>{cnpj}</CNPJ>
          <distNSU>
            <ultNSU>{ult_nsu.zfill(15)}</ultNSU>
          </distNSU>
        </distDFeInt>
      </nfeDadosMsg>
    </nfeDistDFeInteresse>
  </soap12:Body>
</soap12:Envelope>"""


def _parse_response(xml_text: str) -> tuple[str, str, str, str, list]:
    """Returns (cStat, xMotivo, ultNSU, maxNSU, docs).
    docs = list of (nsu, schema, xml_bytes)."""
    root = ET.fromstring(xml_text)
    ret = root.find(f".//{{{NS_NFE}}}retDistDFeInt")
    if ret is None:
        raise ValueError(f"retDistDFeInt not found. Response:\n{xml_text[:500]}")

    c_stat = ret.findtext(f"{{{NS_NFE}}}cStat", "")
    x_motivo = ret.findtext(f"{{{NS_NFE}}}xMotivo", "")
    ult_nsu = ret.findtext(f"{{{NS_NFE}}}ultNSU", "000000000000000")
    max_nsu = ret.findtext(f"{{{NS_NFE}}}maxNSU", "000000000000000")

    docs = []
    lote = ret.find(f"{{{NS_NFE}}}loteDistDFeInt")
    if lote is not None:
        for doc_zip in lote.findall(f"{{{NS_NFE}}}docZip"):
            nsu = doc_zip.get("NSU", "")
            schema = doc_zip.get("schema", "")
            compressed = base64.b64decode(doc_zip.text or "")
            xml_bytes = gzip.decompress(compressed)
            docs.append((nsu, schema, xml_bytes))

    return c_stat, x_motivo, ult_nsu, max_nsu, docs


def _extract_nfe_meta(xml_bytes: bytes) -> dict:
    """Extract key metadata from procNFe or resNFe XML."""
    try:
        root = ET.fromstring(xml_bytes)
        ns = NS_NFE

        # procNFe has NFe/infNFe
        ide = root.find(f".//{{{ns}}}ide")
        emit = root.find(f".//{{{ns}}}emit")
        dest = root.find(f".//{{{ns}}}dest")
        total = root.find(f".//{{{ns}}}vNF")
        inf_nfe = root.find(f".//{{{ns}}}infNFe")

        chave = ""
        if inf_nfe is not None:
            chave = inf_nfe.get("Id", "").replace("NFe", "")

        data_emissao = ""
        if ide is not None:
            data_emissao = ide.findtext(f"{{{ns}}}dhEmi") or ide.findtext(f"{{{ns}}}dEmi") or ""

        emitente = ""
        if emit is not None:
            emitente = emit.findtext(f"{{{ns}}}xNome") or emit.findtext(f"{{{ns}}}CNPJ") or ""

        destinatario = ""
        if dest is not None:
            destinatario = dest.findtext(f"{{{ns}}}xNome") or dest.findtext(f"{{{ns}}}CNPJ") or ""

        valor = total.text if total is not None else ""

        return {"chave": chave, "emitente": emitente, "destinatario": destinatario,
                "valor": valor, "data_emissao": data_emissao[:10]}
    except Exception:
        return {"chave": "", "emitente": "", "destinatario": "", "valor": "", "data_emissao": ""}


def sync_nfe(pfx_path: Path, password: str, cnpj: str, ult_nsu: str,
             xml_dir: Path, tp_amb: str = "1", cuf: str = "43",
             cancel_flag=None) -> tuple[str, int, list]:
    """
    Download all NF-e since ult_nsu.
    Returns (new_ult_nsu, total_docs, saved_docs_metadata).
    saved_docs_metadata = list of dicts with document info.
    """
    url = NFE_DIST_URL_PROD if tp_amb == "1" else NFE_DIST_URL_HOM
    current_nsu = ult_nsu
    total_saved = 0
    saved_meta = []

    with cert_files(pfx_path, password) as (cert_f, key_f):
        session = requests.Session()
        session.cert = (cert_f, key_f)
        session.verify = True

        while True:
            if cancel_flag and cancel_flag.is_set():
                raise RuntimeError("Sync NF-e cancelado pelo usuário.")
            soap_body = _build_soap(cnpj, current_nsu, tp_amb, cuf)
            logger.info(f"NF-e DistDFe: consultando a partir de NSU {current_nsu}")

            try:
                resp = session.post(
                    url,
                    data=soap_body.encode("utf-8"),
                    headers={
                        "Content-Type": f'application/soap+xml; charset=utf-8; action="{NS_WSDL}/nfeDistDFeInteresse"',
                    },
                    timeout=60,
                )
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.error(f"NF-e DistDFe request error: {e}")
                raise

            c_stat, x_motivo, ult_nsu_resp, max_nsu, docs = _parse_response(resp.text)
            logger.info(f"NF-e DistDFe: cStat={c_stat} ({x_motivo}), {len(docs)} docs, maxNSU={max_nsu}")

            if c_stat == "656":
                raise RuntimeError(
                    "NF-e DistDFe: Consumo Indevido (cStat 656). "
                    "O SEFAZ bloqueou requisições excessivas. Aguarde ~1 hora e tente novamente."
                )

            if c_stat not in ("137", "138"):
                raise RuntimeError(f"NF-e DistDFe erro: {c_stat} - {x_motivo}")

            for nsu, schema, xml_bytes in docs:
                meta = _extract_nfe_meta(xml_bytes)
                chave = meta["chave"] or nsu

                # Determine subfolder by date
                date_str = meta["data_emissao"][:7].replace("-", "") if meta["data_emissao"] else datetime.now().strftime("%Y%m")
                year, month = date_str[:4], date_str[4:6]
                dest_dir = xml_dir / "nfe" / year / month
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

            time.sleep(1)  # be nice to SEFAZ

    return current_nsu, total_saved, saved_meta
