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

NFSE_ADN_URL_PROD = "https://adn.nfse.gov.br/contribuintes"
NFSE_ADN_URL_HOM  = "https://adn.producaorestrita.nfse.gov.br/contribuintes"

# Namespace do XML NFS-e Nacional (DPS/NFS-e)
NS_NFSE = "http://www.sped.fazenda.gov.br/nfse"


def _decode_xml(xml_b64: str) -> bytes | None:
    """Decodifica ArquivoXml (base64 de gzip, igual ao NF-e DistDFe)."""
    try:
        raw = base64.b64decode(xml_b64)
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw  # talvez não seja gzip
    except Exception:
        return None


def _extract_nfse_cnpjs(xml_bytes: bytes) -> tuple[str, str]:
    """Retorna (prest_cnpj, toma_cnpj) para filtragem por papel."""
    try:
        root = ET.fromstring(xml_bytes)
        ns = NS_NFSE
        prest_cnpj = root.findtext(f".//{{{ns}}}prest/{{{ns}}}CNPJ") or ""
        toma_cnpj  = root.findtext(f".//{{{ns}}}toma/{{{ns}}}CNPJ") or ""
        return prest_cnpj, toma_cnpj
    except Exception:
        return "", ""


def _extract_nfse_meta(xml_bytes: bytes) -> dict:
    """Extrai metadados do XML NFS-e Nacional."""
    empty = {"chave": "", "emitente": "", "destinatario": "", "valor": "", "data_emissao": ""}
    try:
        root = ET.fromstring(xml_bytes)
        ns = NS_NFSE

        # Chave de acesso via Id do InfNfse
        chave = ""
        inf = root.find(f".//{{{ns}}}InfNfse")
        if inf is not None:
            chave = inf.get("Id", "").lstrip("NFS-eNFSe").strip()

        # Data de emissão: dhEmi (DPS) ou dhProc (NFS-e)
        dh = (root.findtext(f".//{{{ns}}}dhEmi") or
              root.findtext(f".//{{{ns}}}dhProc") or "")
        data_emissao = dh[:10] if dh else ""

        # Prestador (emitente do serviço)
        emitente = ""
        prest = root.find(f".//{{{ns}}}prest")
        if prest is not None:
            emitente = (prest.findtext(f"{{{ns}}}xNome") or
                        prest.findtext(f"{{{ns}}}CNPJ") or "")

        # Tomador
        destinatario = ""
        toma = root.find(f".//{{{ns}}}toma")
        if toma is not None:
            destinatario = (toma.findtext(f"{{{ns}}}xNome") or
                            toma.findtext(f"{{{ns}}}CNPJ") or "")

        # Valor do serviço
        valor = (root.findtext(f".//{{{ns}}}vServ") or
                 root.findtext(f".//{{{ns}}}vLiq") or "")

        return {"chave": chave, "emitente": emitente, "destinatario": destinatario,
                "valor": valor, "data_emissao": data_emissao}
    except Exception:
        return empty


def sync_nfse(pfx_path: Path, password: str, cnpj: str, ult_nsu: str,
              xml_dir: Path, tp_amb: str = "1",
              cancel_flag=None, nfse_role: str = "ambas") -> tuple[str, int, list]:
    """
    Baixa NFS-e do ADN Contribuinte (NFS-e Nacional - Receita Federal).
    Retorna (new_ult_nsu, total_docs, saved_docs_metadata).
    """
    base_url = NFSE_ADN_URL_PROD if tp_amb == "1" else NFSE_ADN_URL_HOM
    current_nsu = int(ult_nsu) if str(ult_nsu).isdigit() else 0
    total_saved = 0
    saved_meta = []

    with cert_files(pfx_path, password) as (cert_f, key_f):
        session = requests.Session()
        session.cert = (cert_f, key_f)
        session.verify = True
        session.headers.update({"Accept": "application/json"})

        while True:
            if cancel_flag and cancel_flag.is_set():
                raise RuntimeError("Sync NFS-e cancelado pelo usuário.")

            url = f"{base_url}/DFe/{current_nsu}"
            logger.info(f"NFS-e ADN: consultando a partir de NSU {current_nsu}")

            try:
                resp = session.get(url, params={"cnpjConsulta": cnpj, "lote": "true"}, timeout=60)
            except requests.RequestException as e:
                logger.error(f"NFS-e ADN request error: {e}")
                raise

            # 404 significa que não há mais documentos a partir deste NSU
            if resp.status_code == 404:
                logger.info(f"NFS-e ADN: NSU {current_nsu} — fim dos documentos (HTTP 404).")
                break

            if not resp.ok:
                logger.error(f"NFS-e ADN HTTP {resp.status_code}: {resp.text[:200]}")
                resp.raise_for_status()

            data = resp.json()
            status = data.get("StatusProcessamento", "")
            lote  = data.get("LoteDFe") or []
            logger.info(f"NFS-e ADN: status={status}, {len(lote)} doc(s)")

            if status == "REJEICAO":
                erros = data.get("Erros") or []
                msg = "; ".join(e.get("Descricao", "") for e in erros) or "Rejeição desconhecida"
                raise RuntimeError(f"NFS-e ADN Rejeição: {msg}")

            if status == "NENHUM_DOCUMENTO_LOCALIZADO":
                break

            max_nsu_batch = current_nsu
            for doc in lote:
                doc_nsu = doc.get("NSU") or 0
                max_nsu_batch = max(max_nsu_batch, doc_nsu)

                # Apenas NFS-e autorizadas (ignora DPS, eventos, CNC etc.)
                if doc.get("TipoDocumento") != "NFSE":
                    continue

                xml_b64 = doc.get("ArquivoXml") or ""
                if not xml_b64:
                    continue

                xml_bytes = _decode_xml(xml_b64)
                if not xml_bytes:
                    logger.warning(f"NFS-e NSU {doc_nsu}: falha ao decodificar XML")
                    continue

                # Filtro de papel (tomadora / emitida / ambas)
                if nfse_role != "ambas":
                    prest_cnpj, toma_cnpj = _extract_nfse_cnpjs(xml_bytes)
                    if nfse_role == "tomadora" and toma_cnpj and toma_cnpj != cnpj:
                        logger.debug(f"NFS-e NSU {doc_nsu}: empresa não é tomador, ignorando.")
                        continue
                    if nfse_role == "emitida" and prest_cnpj and prest_cnpj != cnpj:
                        logger.debug(f"NFS-e NSU {doc_nsu}: empresa não é prestador, ignorando.")
                        continue

                chave = doc.get("ChaveAcesso") or ""
                meta  = _extract_nfse_meta(xml_bytes)
                if not chave:
                    chave = meta["chave"]

                date_str = meta["data_emissao"][:7].replace("-", "") if meta["data_emissao"] else datetime.now().strftime("%Y%m")
                year, month = date_str[:4], date_str[4:6]
                dest_dir = xml_dir / "nfse" / year / month
                dest_dir.mkdir(parents=True, exist_ok=True)

                filename  = f"{chave or str(doc_nsu)}.xml"
                file_path = dest_dir / filename
                file_path.write_bytes(xml_bytes)

                saved_meta.append({
                    "nsu":          str(doc_nsu),
                    "chave":        chave,
                    "schema":       "NFSE",
                    "file_path":    str(file_path.relative_to(xml_dir.parent)),
                    "emitente":     meta["emitente"],
                    "destinatario": meta["destinatario"],
                    "valor":        meta["valor"],
                    "data_emissao": meta["data_emissao"],
                })
                total_saved += 1

            if max_nsu_batch <= current_nsu:
                break  # NSU não avançou — fim dos documentos

            current_nsu = max_nsu_batch
            time.sleep(5)

    return str(current_nsu), total_saved, saved_meta
