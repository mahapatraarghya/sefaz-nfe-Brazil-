"""Builds the NF-e XML (modelo 55, layout 4.00) for Simples Nacional issuers."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from lxml import etree

from . import __version__, documentos
from .models import UF_CODIGOS, Ambiente, Item, NotaFiscal

NS = "http://www.portalfiscal.inf.br/nfe"
MODELO_NFE = 55
TZ_SP = ZoneInfo("America/Sao_Paulo")

# SEFAZ rejects homologação NF-e whose recipient name differs from this (rejeição 598).
NOME_HOMOLOGACAO = "NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL"

TEXTO_SIMPLES = (
    "DOCUMENTO EMITIDO POR ME OU EPP OPTANTE PELO SIMPLES NACIONAL. "
    "NAO GERA DIREITO A CREDITO FISCAL DE IPI."
)

ZERO = Decimal("0")


def fmt(valor: Decimal, casas: int = 2) -> str:
    q = Decimal(1).scaleb(-casas)
    return str(Decimal(valor).quantize(q, rounding=ROUND_HALF_UP))


def limpar(texto: str) -> str:
    """SEFAZ rejects leading/trailing and repeated whitespace and line breaks."""
    return " ".join(str(texto).split())


def agora_sp() -> datetime:
    return datetime.now(TZ_SP).replace(microsecond=0)


def _sub(parent: etree._Element, tag: str, text: object | None = None, **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, f"{{{NS}}}{tag}", **attrs)
    if text is not None:
        el.text = limpar(text) if isinstance(text, str) else str(text)
    return el


def _endereco(parent: etree._Element, tag: str, end, telefone: bool = True) -> None:
    e = _sub(parent, tag)
    _sub(e, "xLgr", end.logradouro)
    _sub(e, "nro", end.numero)
    if end.complemento:
        _sub(e, "xCpl", end.complemento)
    _sub(e, "xBairro", end.bairro)
    _sub(e, "cMun", end.codigo_municipio)
    _sub(e, "xMun", end.municipio)
    _sub(e, "UF", end.uf)
    _sub(e, "CEP", end.cep)
    _sub(e, "cPais", "1058")
    _sub(e, "xPais", "Brasil")
    if telefone and end.telefone:
        _sub(e, "fone", documentos.somente_digitos(end.telefone))


def _validar_cfop(nota: NotaFiscal) -> None:
    esperado = {False: ("5",), True: ("6",)}[nota.interestadual]
    if nota.tipo_operacao == 0:
        esperado = {False: ("1",), True: ("2",)}[nota.interestadual]
    for i, item in enumerate(nota.itens, 1):
        if not item.cfop.startswith(esperado):
            raise ValueError(
                f"Item {i}: CFOP {item.cfop} incompatível com operação "
                f"{'interestadual' if nota.interestadual else 'interna'} "
                f"(esperado iniciando com {'/'.join(esperado)})"
            )


def _icms(imposto: etree._Element, item: Item, nota: NotaFiscal, base_item: Decimal) -> Decimal:
    """Returns vCredICMSSN (credit allowed to the buyer, CSOSN 101)."""
    icms = _sub(imposto, "ICMS")
    if item.csosn == "101":
        p = nota.emitente.aliquota_credito_sn
        if p is None:
            raise ValueError("CSOSN 101 exige aliquota_credito_sn no emitente")
        credito = (base_item * p / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        g = _sub(icms, "ICMSSN101")
        _sub(g, "orig", item.origem)
        _sub(g, "CSOSN", item.csosn)
        _sub(g, "pCredSN", fmt(p, 4))
        _sub(g, "vCredICMSSN", fmt(credito))
        return credito
    grupo = "ICMSSN500" if item.csosn == "500" else "ICMSSN102"
    g = _sub(icms, grupo)
    _sub(g, "orig", item.origem)
    _sub(g, "CSOSN", item.csosn)
    return ZERO


def _pis_cofins(imposto: etree._Element, tributo: str, cst: str) -> None:
    grupo = _sub(imposto, tributo)
    if cst in {"04", "05", "06", "07", "08", "09"}:
        g = _sub(grupo, f"{tributo}NT")
        _sub(g, "CST", cst)
        return
    if cst not in {"49", "99"}:
        raise ValueError(f"CST {tributo} {cst} não suportado para Simples Nacional neste emissor")
    g = _sub(grupo, f"{tributo}Outr")
    _sub(g, "CST", cst)
    _sub(g, "vBC", "0.00")
    _sub(g, f"p{tributo}", "0.0000")
    _sub(g, f"v{tributo}", "0.00")


def build_nfe(
    nota: NotaFiscal,
    ambiente: Ambiente,
    codigo_numerico: str | None = None,
    tipo_emissao: int = 1,
) -> tuple[etree._Element, str]:
    """Returns the unsigned <NFe> element and its 44-digit access key."""
    _validar_cfop(nota)
    emit, dest = nota.emitente, nota.destinatario
    dh = (nota.data_emissao or agora_sp()).astimezone(TZ_SP).replace(microsecond=0)
    cuf = UF_CODIGOS[emit.endereco.uf]
    cnf = codigo_numerico or documentos.gerar_codigo_numerico(nota.numero)
    chave = documentos.montar_chave(
        cuf, dh.strftime("%y%m"), emit.cnpj, MODELO_NFE, nota.serie,
        nota.numero, tipo_emissao, cnf,
    )

    nfe = etree.Element(f"{{{NS}}}NFe", nsmap={None: NS})
    inf = _sub(nfe, "infNFe", versao="4.00", Id=f"NFe{chave}")

    # --- ide -------------------------------------------------------------
    ide = _sub(inf, "ide")
    _sub(ide, "cUF", cuf)
    _sub(ide, "cNF", cnf)
    _sub(ide, "natOp", nota.natureza_operacao)
    _sub(ide, "mod", MODELO_NFE)
    _sub(ide, "serie", nota.serie)
    _sub(ide, "nNF", nota.numero)
    _sub(ide, "dhEmi", dh.isoformat())
    _sub(ide, "tpNF", nota.tipo_operacao)
    if dest.endereco.uf == "EX":
        _sub(ide, "idDest", 3)
    else:
        _sub(ide, "idDest", 2 if nota.interestadual else 1)
    _sub(ide, "cMunFG", emit.endereco.codigo_municipio)
    _sub(ide, "tpImp", 1)  # DANFE retrato
    _sub(ide, "tpEmis", tipo_emissao)
    _sub(ide, "cDV", chave[-1])
    _sub(ide, "tpAmb", int(ambiente))
    _sub(ide, "finNFe", nota.finalidade)
    _sub(ide, "indFinal", 1 if nota.consumidor_final else 0)
    _sub(ide, "indPres", nota.presenca)
    if nota.presenca in (2, 3, 4, 9):
        _sub(ide, "indIntermed", 0)  # operação sem intermediador (marketplace)
    _sub(ide, "procEmi", 0)
    _sub(ide, "verProc", f"nfe-sp {__version__}")

    # --- emit ------------------------------------------------------------
    e = _sub(inf, "emit")
    _sub(e, "CNPJ", emit.cnpj)
    _sub(e, "xNome", emit.razao_social)
    if emit.nome_fantasia:
        _sub(e, "xFant", emit.nome_fantasia)
    _endereco(e, "enderEmit", emit.endereco)
    _sub(e, "IE", emit.ie)
    _sub(e, "CRT", emit.crt)

    # --- dest ------------------------------------------------------------
    d = _sub(inf, "dest")
    if dest.cnpj:
        _sub(d, "CNPJ", dest.cnpj)
    else:
        _sub(d, "CPF", dest.cpf)
    nome = NOME_HOMOLOGACAO if ambiente == Ambiente.HOMOLOGACAO else dest.nome
    _sub(d, "xNome", nome)
    _endereco(d, "enderDest", dest.endereco)
    _sub(d, "indIEDest", dest.indicador_ie)
    if dest.ie and dest.indicador_ie == 1:
        _sub(d, "IE", dest.ie)
    if dest.email:
        _sub(d, "email", dest.email)

    # --- det -------------------------------------------------------------
    v_prod = v_desc = v_frete = credito_total = ZERO
    for n, item in enumerate(nota.itens, 1):
        det = _sub(inf, "det", nItem=str(n))
        prod = _sub(det, "prod")
        _sub(prod, "cProd", item.codigo)
        _sub(prod, "cEAN", item.ean)
        _sub(prod, "xProd", item.descricao)
        _sub(prod, "NCM", item.ncm)
        if item.cest:
            _sub(prod, "CEST", item.cest)
        _sub(prod, "CFOP", item.cfop)
        _sub(prod, "uCom", item.unidade)
        _sub(prod, "qCom", fmt(item.quantidade, 4))
        _sub(prod, "vUnCom", fmt(item.valor_unitario, 10))
        _sub(prod, "vProd", fmt(item.valor_bruto))
        _sub(prod, "cEANTrib", item.ean)
        _sub(prod, "uTrib", item.unidade)
        _sub(prod, "qTrib", fmt(item.quantidade, 4))
        _sub(prod, "vUnTrib", fmt(item.valor_unitario, 10))
        if item.frete:
            _sub(prod, "vFrete", fmt(item.frete))
        if item.desconto:
            _sub(prod, "vDesc", fmt(item.desconto))
        _sub(prod, "indTot", 1)

        imposto = _sub(det, "imposto")
        base_item = item.valor_bruto - item.desconto + item.frete
        credito_total += _icms(imposto, item, nota, base_item)
        _pis_cofins(imposto, "PIS", item.pis_cst)
        _pis_cofins(imposto, "COFINS", item.cofins_cst)

        v_prod += item.valor_bruto
        v_desc += item.desconto
        v_frete += item.frete

    v_nf = v_prod - v_desc + v_frete
    total_pago = sum((p.valor for p in nota.pagamentos), ZERO)
    sem_pagamento = all(p.tipo == "90" for p in nota.pagamentos)
    if not sem_pagamento and total_pago < v_nf:
        raise ValueError(f"Pagamentos ({fmt(total_pago)}) menores que o total da nota ({fmt(v_nf)})")

    # --- total -----------------------------------------------------------
    tot = _sub(_sub(inf, "total"), "ICMSTot")
    for tag, valor in [
        ("vBC", ZERO), ("vICMS", ZERO), ("vICMSDeson", ZERO), ("vFCP", ZERO),
        ("vBCST", ZERO), ("vST", ZERO), ("vFCPST", ZERO), ("vFCPSTRet", ZERO),
        ("vProd", v_prod), ("vFrete", v_frete), ("vSeg", ZERO), ("vDesc", v_desc),
        ("vII", ZERO), ("vIPI", ZERO), ("vIPIDevol", ZERO), ("vPIS", ZERO),
        ("vCOFINS", ZERO), ("vOutro", ZERO), ("vNF", v_nf),
    ]:
        _sub(tot, tag, fmt(valor))

    # --- transp ----------------------------------------------------------
    _sub(_sub(inf, "transp"), "modFrete", nota.modalidade_frete)

    # --- pag -------------------------------------------------------------
    pag = _sub(inf, "pag")
    for p in nota.pagamentos:
        dp = _sub(pag, "detPag")
        _sub(dp, "tPag", p.tipo)
        if p.descricao:
            _sub(dp, "xPag", p.descricao)
        _sub(dp, "vPag", fmt(ZERO if p.tipo == "90" else p.valor))
        if p.tipo in ("03", "04"):
            _sub(_sub(dp, "card"), "tpIntegra", 2)  # pagamento não integrado ao sistema
    troco = total_pago - v_nf
    if not sem_pagamento and troco > 0:
        _sub(pag, "vTroco", fmt(troco))

    # --- infAdic ---------------------------------------------------------
    textos = [TEXTO_SIMPLES]
    if credito_total > 0:
        textos.append(
            f"PERMITE O APROVEITAMENTO DO CREDITO DE ICMS NO VALOR DE R$ {fmt(credito_total)}; "
            f"CORRESPONDENTE A ALIQUOTA DE {fmt(nota.emitente.aliquota_credito_sn)}%, "
            "NOS TERMOS DO ART. 23 DA LC 123/2006."
        )
    if nota.informacoes_complementares:
        textos.append(nota.informacoes_complementares)
    _sub(_sub(inf, "infAdic"), "infCpl", " ".join(textos))

    # --- infRespTec ------------------------------------------------------
    rt = emit.responsavel_tecnico
    if rt:
        r = _sub(inf, "infRespTec")
        _sub(r, "CNPJ", documentos.normalizar_cnpj(rt.cnpj))
        _sub(r, "xContato", rt.contato)
        _sub(r, "email", rt.email)
        _sub(r, "fone", documentos.somente_digitos(rt.telefone))

    return nfe, chave
