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


def _fmt_datetime(s: str) -> str:
    if not s:
        return "—"
    date_part = _fmt_date(s[:10])
    time_part = s[11:19] if len(s) > 10 else ""
    return f"{date_part} {time_part}".strip()


def _fmt_aliq(v: str) -> str:
    try:
        return f"{float(v):.2f}%".replace(".", ",")
    except Exception:
        return v or "—"


# ─── NF-e DANFE ───────────────────────────────────────────────────────────────

def generate_danfe(xml_bytes: bytes) -> bytes:
    from brazilfiscalreport.danfe import Danfe
    xml_str = xml_bytes.decode("utf-8", errors="replace")
    danfe = Danfe(xml=xml_str)
    buf = BytesIO()
    danfe.output(dest="F", name=buf)
    return buf.getvalue()


# ─── CT-e DACTE ───────────────────────────────────────────────────────────────

def generate_dacte(xml_bytes: bytes) -> bytes:
    from brazilfiscalreport.dacte import Dacte
    xml_str = xml_bytes.decode("utf-8", errors="replace")
    dacte = Dacte(xml=xml_str)
    buf = BytesIO()
    dacte.output(dest="F", name=buf)
    return buf.getvalue()


# ─── NFS-e Nacional — DANFSe ──────────────────────────────────────────────────

def generate_nfse_pdf(xml_bytes: bytes) -> bytes:
    """Gera PDF da NFS-e Nacional no formato DANFSe (layout do portal adn.nfse.gov.br)."""
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm

    root = ET.fromstring(xml_bytes)
    ns = NS_NFSE

    def g(tag):
        return root.findtext(f".//{{{ns}}}{tag}") or ""

    def ef(el, tag):
        if el is None:
            return ""
        return el.findtext(f"{{{ns}}}{tag}") or ""

    # ── Identificação ────────────────────────────────────────────────────────
    n_nfse      = g("nNfse") or "—"
    ch_nfse     = g("chNFSe")
    dh_emissao  = g("dhProc") or g("dhEmi")
    competencia = g("dCompet")[:7] if g("dCompet") else ""
    n_dps       = g("nDPS") or "—"
    serie_dps   = g("serieDPS") or g("serie") or "—"
    dh_emi_dps  = g("dhEmiDPS") or dh_emissao
    xmun        = g("xMunPrestacao") or g("xMunIncid") or g("xMun") or ""

    # ── Prestador (Emitente) ─────────────────────────────────────────────────
    prest = root.find(f".//{{{ns}}}prest")
    p_cnpj  = _fmt_cnpj(ef(prest, "CNPJ") or ef(prest, "CPF"))
    p_nome  = ef(prest, "xNome") or "—"
    p_im    = ef(prest, "IM") or "—"
    p_fone  = ef(prest, "fone") or "—"
    p_email = ef(prest, "email") or "—"
    p_end   = prest.find(f"{{{ns}}}end") if prest is not None else None

    def parse_end(el):
        if el is None:
            return "", "", "", "", ""
        logr   = " ".join(filter(None, [ef(el, "xLgr"), ef(el, "nro"), ef(el, "xCpl")]))
        bairro = ef(el, "xBairro")
        mun    = ef(el, "xMun")
        uf     = ef(el, "UF")
        cep    = ef(el, "CEP")
        return logr, bairro, mun, uf, cep

    p_logr, p_bairro, p_mun, p_uf, p_cep = parse_end(p_end)
    p_mun_uf = f"{p_mun} - {p_uf}" if p_uf else p_mun

    # Simples Nacional / Regime
    opSimpNac = g("opSimpNac")
    simpLabel = {"1": "Optante", "2": "Não optante"}.get(opSimpNac, "Não optante")
    regTrib   = g("regTrib")
    regMap    = {"1": "Microempreendedor Individual (MEI)", "2": "Estimativa",
                 "3": "Sociedade de Profissionais", "4": "Cooperativa",
                 "5": "MEI Indústria", "6": "ME/EPP Simples Nacional"}
    regLabel  = regMap.get(regTrib, "Cálculo pelo SN") if regTrib else "—"

    # ── Tomador ──────────────────────────────────────────────────────────────
    toma = root.find(f".//{{{ns}}}toma")
    t_doc   = _fmt_cnpj(ef(toma, "CNPJ") or ef(toma, "CPF")) or "—"
    t_nome  = ef(toma, "xNome") or "—"
    t_im    = ef(toma, "IM") or "—"
    t_fone  = ef(toma, "fone") or "—"
    t_email = ef(toma, "email") or "—"
    t_end   = toma.find(f"{{{ns}}}end") if toma is not None else None
    t_logr, t_bairro, t_mun, t_uf, t_cep = parse_end(t_end)
    t_mun_uf = f"{t_mun} - {t_uf}" if t_uf else t_mun

    # ── Serviço ──────────────────────────────────────────────────────────────
    cTribNac   = g("cTribNac") or "—"
    cTribMun   = g("cTribMun") or g("cServ") or "—"
    xDescServ  = g("xDescServ") or "—"
    xLocPrest  = g("xMunPrestacao") or g("xMunIncid") or "—"
    xPaisPrest = g("xPais") or "Brasil"
    cNBS       = g("cNBS")

    # ── Tributação Municipal ──────────────────────────────────────────────────
    retISSQN   = g("retISSQN") or "0"
    ret_label  = "Retido" if retISSQN == "1" else "Não Retido"
    opTrib     = g("opTributacao") or "—"
    xPaisRes   = g("xPaisResultado") or xPaisPrest
    xMunIncid  = g("xMunIncid") or xLocPrest
    regEspTrib = g("regEspTrib") or "Nenhum"
    tpImun     = g("tpImun") or "—"
    nProc      = g("nProcesso") or "—"
    vServ      = g("vServ") or g("vServPrest") or ""
    vDescInc   = g("vDescIncond") or ""
    vDedRed    = g("vDedRed") or g("vDed") or ""
    vBC        = g("vBC") or vServ
    pAliq      = g("pAliq") or ""
    vISSQN     = g("vISSQN") or ""

    # ── Tributação Federal ────────────────────────────────────────────────────
    vIRRF    = g("vIRRF") or "—"
    vCP      = g("vCP") or g("vCPRPS") or "—"
    vCSLL    = g("vCSLL") or "—"
    vPIS     = g("vPIS") or ""
    vCOFINS  = g("vCOFINS") or ""
    try:
        vPisCofins = f"{float(vPIS or 0) + float(vCOFINS or 0):.2f}" if (vPIS or vCOFINS) else ""
    except Exception:
        vPisCofins = ""
    descCS = "2 - PIS/COFINS Não Retidos" if (vPIS or vCOFINS) else "—"

    # ── Valor Total ───────────────────────────────────────────────────────────
    vDescCond = g("vDescCond") or ""
    vISSQNRet = g("vISSQNRet") or (vISSQN if retISSQN == "1" else "")
    vRetFed   = g("vRetFed") or ""
    vLiq      = g("vLiq") or vServ

    # ── Layout PDF ───────────────────────────────────────────────────────────
    buf  = BytesIO()
    LM   = 8 * mm
    W    = A4[0] - 2 * LM
    doc  = SimpleDocTemplate(buf, pagesize=A4,
                             leftMargin=LM, rightMargin=LM,
                             topMargin=8 * mm, bottomMargin=8 * mm)

    BLUE  = colors.HexColor("#003580")
    LBLUE = colors.HexColor("#cfe2ff")
    BORD  = colors.HexColor("#aaaaaa")
    LBRD  = colors.HexColor("#dddddd")
    WHITE = colors.white
    BLACK = colors.black
    DGRAY = colors.HexColor("#333333")

    def S(n, **kw):
        d = dict(fontName="Helvetica", fontSize=7, textColor=BLACK, leading=9, spaceAfter=0)
        d.update(kw)
        return ParagraphStyle(n, **d)

    SL  = S("L",  fontName="Helvetica-Bold", fontSize=6,  textColor=DGRAY, leading=8)
    SV  = S("V")
    SBD = S("BD", fontName="Helvetica-Bold")
    SSC = S("SC", fontName="Helvetica-Bold", fontSize=7, textColor=WHITE, leading=9)
    SCH = S("CH", fontName="Courier",        fontSize=7, alignment=1, leading=9)
    SFT = S("FT", fontSize=6, textColor=colors.HexColor("#666"), alignment=1, leading=8)

    SP1 = Spacer(1, 1 * mm)

    BASE = [
        ("FONTSIZE",      (0, 0), (-1, -1), 7),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORD),
        ("LINEAFTER",     (0, 0), (-2, -1), 0.3, LBRD),
        ("LINEBELOW",     (0, 0), (-1, -2), 0.3, LBRD),
    ]

    def sec(txt):
        t = Table([[Paragraph(txt, SSC)]], colWidths=[W])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), BLUE),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ]))
        return t

    def mktbl(rows, cw, extra=None):
        t = Table(rows, colWidths=cw)
        t.setStyle(TableStyle(list(BASE) + (extra or [])))
        return t

    elems = []

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    hdr = Table([[
        Paragraph(
            "<font name='Helvetica-Bold' size='20' color='#003580'>NFS</font>"
            "<font name='Helvetica-Bold' size='20' color='#2980b9'>e</font><br/>"
            "<font name='Helvetica' size='6'>Nota Fiscal de Serviço Eletrônica</font>",
            S("brd", leading=26)),
        Paragraph(
            "<font name='Helvetica-Bold' size='10'>DANFSe v1.0</font><br/>"
            "<font name='Helvetica' size='8'>Documento Auxiliar da NFS-e</font>",
            S("ttl", alignment=1, leading=16)),
        Paragraph(xmun,
            S("mun", fontName="Helvetica-Bold", fontSize=10, alignment=2, leading=13)),
    ]], colWidths=[W * 0.22, W * 0.54, W * 0.24])
    hdr.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORD),
        ("LINEAFTER",     (0, 0), (1, 0),   0.3, BORD),
    ]))
    elems += [hdr, SP1]

    # ── Chave de Acesso ───────────────────────────────────────────────────────
    if ch_nfse:
        ca = Table([
            [Paragraph("Chave de Acesso da NFS-e", SL)],
            [Paragraph(ch_nfse, SCH)],
        ], colWidths=[W])
        ca.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("BOX",           (0, 0), (-1, -1), 0.5, BORD),
            ("LINEBELOW",     (0, 0), (-1, 0),  0.3, LBRD),
        ]))
        elems += [ca, SP1]

    # ── Números NFS-e / DPS ───────────────────────────────────────────────────
    W3 = [W * 0.18, W * 0.18, W * 0.64]
    elems.append(mktbl([
        [Paragraph("Número da NFS-e", SL),
         Paragraph("Competência da NFS-e", SL),
         Paragraph("Data e Hora de emissão da NFS-e", SL)],
        [Paragraph(n_nfse, SBD),
         Paragraph(competencia or "—", SV),
         Paragraph(_fmt_datetime(dh_emissao), SV)],
        [Paragraph("Número da DPS", SL),
         Paragraph("Série da DPS", SL),
         Paragraph("Data e Hora de emissão da DPS", SL)],
        [Paragraph(n_dps, SV),
         Paragraph(serie_dps, SV),
         Paragraph(_fmt_datetime(dh_emi_dps), SV)],
    ], W3))
    elems.append(SP1)

    # ── EMITENTE DA NFS-e ────────────────────────────────────────────────────
    Wa, Wb, Wc, Wd = W * 0.28, W * 0.25, W * 0.25, W * 0.22
    elems.append(sec("EMITENTE DA NFS-e"))
    elems.append(mktbl([
        [Paragraph("CNPJ / CPF / NIF", SL),
         Paragraph("Inscrição Municipal", SL),
         Paragraph("Telefone", SL),
         Paragraph("", SL)],
        [Paragraph(p_cnpj, SBD),
         Paragraph(p_im, SV),
         Paragraph(p_fone, SV),
         Paragraph("", SV)],
        [Paragraph("Nome / Nome Empresarial", SL),
         Paragraph("", SL),
         Paragraph("E-mail", SL),
         Paragraph("", SL)],
        [Paragraph(p_nome, SBD),
         Paragraph("", SV),
         Paragraph(p_email, SV),
         Paragraph("", SV)],
        [Paragraph("Endereço", SL),
         Paragraph("Bairro", SL),
         Paragraph("Município", SL),
         Paragraph("CEP", SL)],
        [Paragraph(p_logr or "—", SV),
         Paragraph(p_bairro or "—", SV),
         Paragraph(p_mun_uf or "—", SV),
         Paragraph(p_cep or "—", SV)],
        [Paragraph("Simples Nacional na Data de Competência", SL),
         Paragraph("", SL),
         Paragraph("Regime de Apuração Tributária pelo SN", SL),
         Paragraph("", SL)],
        [Paragraph(simpLabel, SV),
         Paragraph("", SV),
         Paragraph(regLabel, SV),
         Paragraph("", SV)],
    ], [Wa, Wb, Wc, Wd], extra=[
        ("SPAN", (0, 2), (1, 2)), ("SPAN", (0, 3), (1, 3)),
        ("SPAN", (2, 2), (3, 2)), ("SPAN", (2, 3), (3, 3)),
        ("SPAN", (0, 6), (1, 6)), ("SPAN", (0, 7), (1, 7)),
        ("SPAN", (2, 6), (3, 6)), ("SPAN", (2, 7), (3, 7)),
    ]))
    elems.append(SP1)

    # ── TOMADOR DO SERVIÇO ───────────────────────────────────────────────────
    elems.append(sec("TOMADOR DO SERVIÇO"))
    elems.append(mktbl([
        [Paragraph("CNPJ / CPF / NIF", SL),
         Paragraph("Inscrição Municipal", SL),
         Paragraph("Telefone", SL),
         Paragraph("", SL)],
        [Paragraph(t_doc, SBD),
         Paragraph(t_im, SV),
         Paragraph(t_fone, SV),
         Paragraph("", SV)],
        [Paragraph("Nome / Nome Empresarial", SL),
         Paragraph("", SL),
         Paragraph("E-mail", SL),
         Paragraph("", SL)],
        [Paragraph(t_nome, SBD),
         Paragraph("", SV),
         Paragraph(t_email, SV),
         Paragraph("", SV)],
        [Paragraph("Endereço", SL),
         Paragraph("Bairro", SL),
         Paragraph("Município", SL),
         Paragraph("CEP", SL)],
        [Paragraph(t_logr or "—", SV),
         Paragraph(t_bairro or "—", SV),
         Paragraph(t_mun_uf or "—", SV),
         Paragraph(t_cep or "—", SV)],
    ], [Wa, Wb, Wc, Wd], extra=[
        ("SPAN", (0, 2), (1, 2)), ("SPAN", (0, 3), (1, 3)),
        ("SPAN", (2, 2), (3, 2)), ("SPAN", (2, 3), (3, 3)),
    ]))
    elems.append(SP1)

    # ── SERVIÇO PRESTADO ─────────────────────────────────────────────────────
    elems.append(sec("SERVIÇO PRESTADO"))
    W4 = W / 4
    elems.append(mktbl([
        [Paragraph("Código de Tributação Nacional", SL),
         Paragraph("Código de Tributação Municipal", SL),
         Paragraph("Local de Prestação", SL),
         Paragraph("País da Prestação", SL)],
        [Paragraph(cTribNac, SV),
         Paragraph(cTribMun, SV),
         Paragraph(xLocPrest, SV),
         Paragraph(xPaisPrest, SV)],
        [Paragraph("Descrição do Serviço", SL),
         Paragraph("", SL), Paragraph("", SL), Paragraph("", SL)],
        [Paragraph(xDescServ, SV),
         Paragraph("", SV), Paragraph("", SV), Paragraph("", SV)],
    ], [W4, W4, W4, W4], extra=[
        ("SPAN", (0, 2), (3, 2)),
        ("SPAN", (0, 3), (3, 3)),
    ]))
    elems.append(SP1)

    # ── TRIBUTAÇÃO MUNICIPAL ─────────────────────────────────────────────────
    elems.append(sec("TRIBUTAÇÃO MUNICIPAL"))
    elems.append(mktbl([
        [Paragraph("Tributação do ISSQN", SL),
         Paragraph("Operação Tributável", SL),
         Paragraph("País Resultado da Prest. Serviço", SL),
         Paragraph("Município de Incidência do ISSQN", SL)],
        [Paragraph(ret_label, SV),
         Paragraph(opTrib, SV),
         Paragraph(xPaisRes, SV),
         Paragraph(xMunIncid, SV)],
        [Paragraph("Regime Especial de Tributação", SL),
         Paragraph("Tipo de Imunidade", SL),
         Paragraph("Suspensão da Exigibilidade do ISSQN", SL),
         Paragraph("Número Processo Suspensão", SL)],
        [Paragraph(regEspTrib, SV),
         Paragraph(tpImun, SV),
         Paragraph("—", SV),
         Paragraph(nProc, SV)],
        [Paragraph("Valor do Serviço", SL),
         Paragraph("Desconto Incondicionado", SL),
         Paragraph("Total Deduções/Reduções", SL),
         Paragraph("Cálculo do BM", SL)],
        [Paragraph(_fmt_currency(vServ) if vServ else "—", SV),
         Paragraph(_fmt_currency(vDescInc) if vDescInc else "—", SV),
         Paragraph(_fmt_currency(vDedRed) if vDedRed else "—", SV),
         Paragraph("—", SV)],
        [Paragraph("BC ISSQN", SL),
         Paragraph("Alíquota Aplicada", SL),
         Paragraph("Retenção do ISSQN", SL),
         Paragraph("ISSQN Apurado", SL)],
        [Paragraph(_fmt_currency(vBC) if vBC else "—", SV),
         Paragraph(_fmt_aliq(pAliq) if pAliq else "—", SV),
         Paragraph(ret_label, SV),
         Paragraph(_fmt_currency(vISSQN) if vISSQN else "—", SV)],
    ], [W4, W4, W4, W4]))
    elems.append(SP1)

    # ── TRIBUTAÇÃO FEDERAL ───────────────────────────────────────────────────
    elems.append(sec("TRIBUTAÇÃO FEDERAL"))
    elems.append(mktbl([
        [Paragraph("IRRF", SL),
         Paragraph("Contribuição Previdenciária - Retida", SL),
         Paragraph("Contribuições Sociais - Retidas", SL),
         Paragraph("Descrição Contrib. Sociais - Retidas", SL)],
        [Paragraph(vIRRF, SV),
         Paragraph(vCP, SV),
         Paragraph(vCSLL, SV),
         Paragraph(descCS, SV)],
        [Paragraph("PIS - Débito Apuração Própria", SL),
         Paragraph("COFINS - Débito Apuração Própria", SL),
         Paragraph("", SL), Paragraph("", SL)],
        [Paragraph(_fmt_currency(vPIS) if vPIS else "—", SV),
         Paragraph(_fmt_currency(vCOFINS) if vCOFINS else "—", SV),
         Paragraph("", SV), Paragraph("", SV)],
    ], [W4, W4, W4, W4]))
    elems.append(SP1)

    # ── VALOR TOTAL DA NFS-E ─────────────────────────────────────────────────
    elems.append(sec("VALOR TOTAL DA NFS-E"))
    elems.append(mktbl([
        [Paragraph("Valor do Serviço", SL),
         Paragraph("Desconto Condicionado", SL),
         Paragraph("Desconto Incondicionado", SL),
         Paragraph("ISSQN Retido", SL)],
        [Paragraph(_fmt_currency(vServ) if vServ else "—", SV),
         Paragraph(_fmt_currency(vDescCond) if vDescCond else "—", SV),
         Paragraph(_fmt_currency(vDescInc) if vDescInc else "—", SV),
         Paragraph(_fmt_currency(vISSQNRet) if vISSQNRet else "—", SV)],
        [Paragraph("Total das Retenções Federais", SL),
         Paragraph("PIS/COFINS - Débito Apur. Própria", SL),
         Paragraph("", SL),
         Paragraph("Valor Líquido da NFS-e", SL)],
        [Paragraph(_fmt_currency(vRetFed) if vRetFed else "—", SV),
         Paragraph(_fmt_currency(vPisCofins) if vPisCofins else "—", SV),
         Paragraph("", SV),
         Paragraph(_fmt_currency(vLiq) if vLiq else "—",
                   S("liq", fontName="Helvetica-Bold", fontSize=9, leading=12))],
    ], [W4, W4, W4, W4], extra=[
        ("BACKGROUND", (3, 2), (3, 3), LBLUE),
    ]))
    elems.append(SP1)

    # ── TOTAIS APROXIMADOS DOS TRIBUTOS ──────────────────────────────────────
    elems.append(sec("TOTAIS APROXIMADOS DOS TRIBUTOS"))
    W3t = W / 3
    elems.append(mktbl([
        [Paragraph("Federais", SL),
         Paragraph("Estaduais", SL),
         Paragraph("Municipais", SL)],
        [Paragraph("R$ 0,00", SV),
         Paragraph("R$ 0,00", SV),
         Paragraph("R$ 0,00", SV)],
    ], [W3t, W3t, W3t]))
    elems.append(SP1)

    # ── INFORMAÇÕES COMPLEMENTARES ───────────────────────────────────────────
    if cNBS:
        elems.append(sec("INFORMAÇÕES COMPLEMENTARES"))
        elems.append(mktbl([
            [Paragraph("NBS", SL)],
            [Paragraph(cNBS, SV)],
        ], [W]))

    elems.append(Spacer(1, 3 * mm))
    elems.append(Paragraph(
        "Documento gerado a partir do XML da NFS-e Nacional — adn.nfse.gov.br",
        SFT,
    ))

    doc.build(elems)
    return buf.getvalue()
