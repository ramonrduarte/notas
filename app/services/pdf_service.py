import logging
import xml.etree.ElementTree as ET
from io import BytesIO

logger = logging.getLogger(__name__)

NS_NFSE = "http://www.sped.fazenda.gov.br/nfse"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_cnpj(v: str) -> str:
    c = "".join(ch for ch in (v or "") if ch.isdigit())
    if len(c) == 14:
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
    if len(c) == 11:
        return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"
    return v or "—"


def _fmt_currency(v: str) -> str:
    try:
        f = float(v)
        return f"R$ {f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return v or "—"


def _fmt_date(s: str) -> str:
    if not s:
        return "—"
    p = s[:10].split("-")
    return f"{p[2]}/{p[1]}/{p[0]}" if len(p) == 3 else s[:10]


def _fmt_aliq(v: str) -> str:
    try:
        return f"{float(v):.2f}%".replace(".", ",")
    except Exception:
        return v or "—"


# ─── NF-e DANFE ───────────────────────────────────────────────────────────────

def generate_danfe(xml_bytes: bytes) -> bytes:
    """Gera DANFE (NF-e) usando brazilfiscalreport."""
    from brazilfiscalreport.danfe import Danfe
    xml_str = xml_bytes.decode("utf-8", errors="replace")
    danfe = Danfe(xml=xml_str)
    buf = BytesIO()
    danfe.output(dest="F", name=buf)
    return buf.getvalue()


# ─── CT-e DACTE ───────────────────────────────────────────────────────────────

def generate_dacte(xml_bytes: bytes) -> bytes:
    """Gera DACTE (CT-e) usando brazilfiscalreport."""
    from brazilfiscalreport.dacte import Dacte
    xml_str = xml_bytes.decode("utf-8", errors="replace")
    dacte = Dacte(xml=xml_str)
    buf = BytesIO()
    dacte.output(dest="F", name=buf)
    return buf.getvalue()


# ─── NFS-e Nacional ───────────────────────────────────────────────────────────

def generate_nfse_pdf(xml_bytes: bytes) -> bytes:
    """Gera PDF da NFS-e Nacional usando ReportLab."""
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm

    root = ET.fromstring(xml_bytes)
    ns = NS_NFSE

    # Identificação
    n_nfse      = root.findtext(f".//{{{ns}}}nNfse") or "—"
    serie       = root.findtext(f".//{{{ns}}}serie") or "—"
    dh_emissao  = root.findtext(f".//{{{ns}}}dhProc") or root.findtext(f".//{{{ns}}}dhEmi") or ""
    competencia = (root.findtext(f".//{{{ns}}}dCompet") or "")[:7]

    # Prestador
    prest = root.find(f".//{{{ns}}}prest")
    if prest is not None:
        prest_nome = prest.findtext(f"{{{ns}}}xNome") or ""
        prest_cnpj = _fmt_cnpj(prest.findtext(f"{{{ns}}}CNPJ") or "")
        prest_im   = prest.findtext(f"{{{ns}}}IM") or "—"
    else:
        prest_nome = prest_cnpj = prest_im = "—"

    def _end(el):
        if el is None:
            return "", ""
        logr  = " ".join(filter(None, [el.findtext(f"{{{ns}}}xLgr"),
                                       el.findtext(f"{{{ns}}}nro"),
                                       el.findtext(f"{{{ns}}}xCpl")]))
        bairro = el.findtext(f"{{{ns}}}xBairro") or ""
        mun    = el.findtext(f"{{{ns}}}xMun") or ""
        uf     = el.findtext(f"{{{ns}}}UF") or ""
        cep    = el.findtext(f"{{{ns}}}CEP") or ""
        linha1 = ", ".join(filter(None, [logr, bairro]))
        linha2 = " — ".join(filter(None, [f"{mun}/{uf}" if uf else mun,
                                          f"CEP {cep}" if cep else ""]))
        return linha1, linha2

    prest_end = root.find(f".//{{{ns}}}endPrest") or root.find(f".//{{{ns}}}end")
    prest_l1, prest_l2 = _end(prest_end)

    # Tomador
    toma = root.find(f".//{{{ns}}}toma")
    if toma is not None:
        toma_nome = toma.findtext(f"{{{ns}}}xNome") or ""
        toma_doc  = _fmt_cnpj(toma.findtext(f"{{{ns}}}CNPJ") or
                               toma.findtext(f"{{{ns}}}CPF") or "")
        toma_end_el = toma.find(f"{{{ns}}}end")
        toma_l1, toma_l2 = _end(toma_end_el)
    else:
        toma_nome = toma_doc = toma_l1 = toma_l2 = "—"

    # Serviço
    xDescServ = root.findtext(f".//{{{ns}}}xDescServ") or "—"
    cServ     = root.findtext(f".//{{{ns}}}cServ") or "—"
    xServMun  = root.findtext(f".//{{{ns}}}xServMun") or ""
    xMunIncid = root.findtext(f".//{{{ns}}}xMunIncid") or ""
    retISSQN  = root.findtext(f".//{{{ns}}}retISSQN") or "0"

    # Valores
    vServ  = root.findtext(f".//{{{ns}}}vServ") or ""
    vBC    = root.findtext(f".//{{{ns}}}vBC") or vServ
    pAliq  = root.findtext(f".//{{{ns}}}pAliq") or ""
    vISSQN = root.findtext(f".//{{{ns}}}vISSQN") or ""
    vLiq   = root.findtext(f".//{{{ns}}}vLiq") or vServ

    # ── PDF ───────────────────────────────────────────────────────────────────
    buf = BytesIO()
    W = A4[0] - 30 * mm
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm)

    BLUE  = colors.HexColor("#1e3a5f")
    LBLUE = colors.HexColor("#dbeafe")
    LGRAY = colors.HexColor("#f1f5f9")
    BORD  = colors.HexColor("#334155")
    SEP   = colors.HexColor("#cbd5e1")
    WHITE = colors.white
    BLACK = colors.black

    def sty(name, **kw):
        base = dict(fontName="Helvetica", fontSize=8, textColor=BLACK, leading=11)
        base.update(kw)
        return ParagraphStyle(name, **base)

    S_HDR   = sty("hdr",   fontName="Helvetica-Bold", fontSize=9,  textColor=WHITE, alignment=1)
    S_TITLE = sty("title", fontName="Helvetica-Bold", fontSize=13, textColor=WHITE, alignment=1)
    S_SUB   = sty("sub",   fontSize=8, textColor=WHITE, alignment=1)
    S_LBL   = sty("lbl",   fontName="Helvetica-Bold", fontSize=7,  textColor=BORD)
    S_VAL   = sty("val")
    S_BIG   = sty("big",   fontName="Helvetica-Bold", fontSize=11)
    S_DESC  = sty("desc",  leading=12)
    S_FOOT  = sty("foot",  fontSize=7, textColor=BORD, alignment=1)

    def sec(text):
        t = Table([[Paragraph(text, S_HDR)]], colWidths=[W])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), BLUE),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ]))
        return t

    def info_tbl(rows, col_w=None):
        col_w = col_w or [40 * mm, W / 2 - 40 * mm, 35 * mm, W / 2 - 35 * mm]
        t = Table(rows, colWidths=col_w)
        t.setStyle(TableStyle([
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("LINEBELOW",     (0, 0), (-1, -2), 0.3, SEP),
            ("BOX",           (0, 0), (-1, -1), 0.5, BORD),
        ]))
        return t

    SP = Spacer(1, 3 * mm)
    elems = []

    # Cabeçalho
    hdr = Table([[
        Paragraph("PREFEITURA MUNICIPAL", S_SUB),
        Paragraph("NOTA FISCAL DE SERVIÇOS ELETRÔNICA", S_TITLE),
        Paragraph(f"Nº {n_nfse}<br/>Série: {serie}", S_SUB),
    ]], colWidths=[W * 0.18, W * 0.64, W * 0.18])
    hdr.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), BLUE),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    elems.append(hdr)
    elems.append(SP)

    # Data / Competência
    inf = Table([[
        Paragraph(f"<b>Data de Emissão:</b> {_fmt_date(dh_emissao)}", S_VAL),
        Paragraph(f"<b>Competência:</b> {competencia or '—'}", S_VAL),
    ]], colWidths=[W / 2, W / 2])
    inf.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), LGRAY),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORD),
    ]))
    elems.append(inf)
    elems.append(SP)

    # Prestador
    elems.append(sec("PRESTADOR DE SERVIÇOS"))
    elems.append(info_tbl([
        [Paragraph("<b>Razão Social / Nome:</b>", S_LBL), Paragraph(prest_nome, S_VAL),
         Paragraph("<b>CNPJ:</b>", S_LBL),              Paragraph(prest_cnpj, S_VAL)],
        [Paragraph("<b>Inscrição Municipal:</b>", S_LBL), Paragraph(prest_im, S_VAL),
         Paragraph("", S_LBL),                           Paragraph("", S_VAL)],
        [Paragraph("<b>Endereço:</b>", S_LBL),            Paragraph(prest_l1 or "—", S_VAL),
         Paragraph("<b>Município/UF:</b>", S_LBL),        Paragraph(prest_l2 or "—", S_VAL)],
    ]))
    elems.append(SP)

    # Tomador
    elems.append(sec("TOMADOR DE SERVIÇOS"))
    elems.append(info_tbl([
        [Paragraph("<b>Razão Social / Nome:</b>", S_LBL), Paragraph(toma_nome, S_VAL),
         Paragraph("<b>CNPJ / CPF:</b>", S_LBL),         Paragraph(toma_doc, S_VAL)],
        [Paragraph("<b>Endereço:</b>", S_LBL),            Paragraph(toma_l1 or "—", S_VAL),
         Paragraph("<b>Município/UF:</b>", S_LBL),        Paragraph(toma_l2 or "—", S_VAL)],
    ]))
    elems.append(SP)

    # Serviço
    elems.append(sec("DISCRIMINAÇÃO DOS SERVIÇOS"))
    desc_tbl = Table([
        [Paragraph(xDescServ, S_DESC)],
        [Paragraph(
            f"<b>Cód. Serviço:</b> {cServ}"
            + (f"   <b>Descrição LC 116:</b> {xServMun}" if xServMun else "")
            + (f"   <b>Município de Incidência:</b> {xMunIncid}" if xMunIncid else ""),
            S_VAL)],
    ], colWidths=[W])
    desc_tbl.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("LINEBELOW",     (0, 0), (0, -2),  0.3, SEP),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORD),
    ]))
    elems.append(desc_tbl)
    elems.append(SP)

    # Valores
    elems.append(sec("VALORES"))
    iss_label = "ISSQN Retido (R$):" if retISSQN == "1" else "ISSQN Devido (R$):"
    val_rows = [
        ("Valor dos Serviços (R$):",    _fmt_currency(vServ)),
        ("Base de Cálculo ISSQN (R$):", _fmt_currency(vBC)),
        ("Alíquota ISSQN:",             _fmt_aliq(pAliq)),
        (iss_label,                     _fmt_currency(vISSQN)),
        ("Valor Líquido (R$):",         _fmt_currency(vLiq)),
    ]
    val_tbl = Table(
        [[Paragraph(f"<b>{k}</b>", S_VAL), Paragraph(v, S_BIG)] for k, v in val_rows],
        colWidths=[W * 0.65, W * 0.35],
    )
    val_tbl.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("ALIGN",         (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW",     (0, 0), (-1, -2), 0.3, SEP),
        ("BACKGROUND",    (0, -1), (-1, -1), LBLUE),
        ("FONTSIZE",      (1, -1), (1, -1), 12),
        ("FONTNAME",      (1, -1), (1, -1), "Helvetica-Bold"),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORD),
    ]))
    elems.append(val_tbl)
    elems.append(Spacer(1, 5 * mm))

    elems.append(Paragraph(
        "Documento gerado a partir do XML da NFS-e Nacional — adn.nfse.gov.br",
        S_FOOT,
    ))

    doc.build(elems)
    return buf.getvalue()
