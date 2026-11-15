"""An in-memory fake SEFAZ for development and tests — no certificate or network needed.

It checks the things that most often cause real rejections (signature, access key,
homologação recipient name) so bugs show up before you ever talk to the real SEFAZ.
"""

from __future__ import annotations

import random

from lxml import etree

from .builder import NOME_HOMOLOGACAO, NS, agora_sp
from .certificado import AssinaturaInvalida, verificar_assinatura
from .documentos import chave_valida


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _resp(servico: str, xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
        f'<nfeResultMsg xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/{servico}">{xml}</nfeResultMsg>'
        "</soap:Body></soap:Envelope>"
    )


class SefazFake:
    def __init__(self) -> None:
        self.autorizadas: dict[str, str] = {}  # chave -> nProt
        self.canceladas: set[str] = set()
        self.requisicoes: list[tuple[str, str]] = []

    def enviar(self, url: str, servico: str, metodo: str, corpo: str) -> str:
        self.requisicoes.append((servico, corpo))
        doc = etree.fromstring(corpo.encode("utf-8"))
        handler = getattr(self, f"_{servico}")
        return _resp(servico, handler(doc))

    # --- serviços --------------------------------------------------------
    def _NFeStatusServico4(self, doc) -> str:
        return (
            f'<retConsStatServ xmlns="{NS}" versao="4.00"><tpAmb>{doc.findtext(_q("tpAmb"))}</tpAmb>'
            f"<verAplic>MOCK</verAplic><cStat>107</cStat><xMotivo>Servico em Operacao</xMotivo>"
            f"<cUF>35</cUF><dhRecbto>{agora_sp().isoformat()}</dhRecbto><tMed>1</tMed></retConsStatServ>"
        )

    def _NFeAutorizacao4(self, doc) -> str:
        nfe = doc.find(_q("NFe"))
        inf = nfe.find(_q("infNFe"))
        chave = inf.get("Id")[3:]
        tp_amb = inf.findtext(f"{_q('ide')}/{_q('tpAmb')}")
        cstat, motivo = "100", "Autorizado o uso da NF-e"
        try:
            verificar_assinatura(nfe)
        except AssinaturaInvalida as exc:
            cstat, motivo = "297", f"Rejeicao: Assinatura difere do calculado ({exc})"
        if not chave_valida(chave):
            cstat, motivo = "502", "Rejeicao: Erro na Chave de Acesso"
        elif chave in self.autorizadas:
            cstat, motivo = "204", "Rejeicao: Duplicidade de NF-e"
        elif tp_amb == "2" and inf.findtext(f"{_q('dest')}/{_q('xNome')}") != NOME_HOMOLOGACAO:
            cstat, motivo = "598", "Rejeicao: NF-e emitida em ambiente de homologacao com Razao Social do destinatario diferente de NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL"

        nprot = ""
        if cstat == "100":
            nprot = f"135{agora_sp():%y}{random.randrange(10**10):010d}"
            self.autorizadas[chave] = nprot
        digval = nfe.findtext(".//{http://www.w3.org/2000/09/xmldsig#}DigestValue") or ""
        prot = (
            f'<protNFe versao="4.00"><infProt><tpAmb>{tp_amb}</tpAmb><verAplic>MOCK</verAplic>'
            f"<chNFe>{chave}</chNFe><dhRecbto>{agora_sp().isoformat()}</dhRecbto>"
            + (f"<nProt>{nprot}</nProt>" if nprot else "")
            + f"<digVal>{digval}</digVal><cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo></infProt></protNFe>"
        )
        return (
            f'<retEnviNFe xmlns="{NS}" versao="4.00"><tpAmb>{tp_amb}</tpAmb><verAplic>MOCK</verAplic>'
            f"<cStat>104</cStat><xMotivo>Lote processado</xMotivo><cUF>35</cUF>"
            f"<dhRecbto>{agora_sp().isoformat()}</dhRecbto>{prot}</retEnviNFe>"
        )

    def _NFeConsultaProtocolo4(self, doc) -> str:
        chave = doc.findtext(_q("chNFe"))
        tp_amb = doc.findtext(_q("tpAmb"))
        if chave not in self.autorizadas:
            cstat, motivo, prot = "217", "Rejeicao: NF-e nao consta na base de dados da SEFAZ", ""
        else:
            cstat = "101" if chave in self.canceladas else "100"
            motivo = "Cancelamento de NF-e homologado" if cstat == "101" else "Autorizado o uso da NF-e"
            prot = (
                f'<protNFe versao="4.00"><infProt><tpAmb>{tp_amb}</tpAmb><verAplic>MOCK</verAplic>'
                f"<chNFe>{chave}</chNFe><dhRecbto>{agora_sp().isoformat()}</dhRecbto>"
                f"<nProt>{self.autorizadas[chave]}</nProt><cStat>100</cStat>"
                f"<xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe>"
            )
        return (
            f'<retConsSitNFe xmlns="{NS}" versao="4.00"><tpAmb>{tp_amb}</tpAmb><verAplic>MOCK</verAplic>'
            f"<cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo><cUF>35</cUF><chNFe>{chave}</chNFe>{prot}"
            "</retConsSitNFe>"
        )

    def _NFeRecepcaoEvento4(self, doc) -> str:
        evento = doc.find(_q("evento"))
        inf = evento.find(_q("infEvento"))
        chave = inf.findtext(_q("chNFe"))
        nprot = inf.findtext(f"{_q('detEvento')}/{_q('nProt')}")
        try:
            verificar_assinatura(evento)
            if self.autorizadas.get(chave) != nprot:
                cstat, motivo = "222", "Rejeicao: Protocolo de Autorizacao de Uso difere do cadastrado"
            elif chave in self.canceladas:
                cstat, motivo = "573", "Rejeicao: Duplicidade de Evento"
            else:
                cstat, motivo = "135", "Evento registrado e vinculado a NF-e"
                self.canceladas.add(chave)
        except AssinaturaInvalida:
            cstat, motivo = "297", "Rejeicao: Assinatura difere do calculado"
        prot_ev = f"135{random.randrange(10**12):012d}" if cstat == "135" else ""
        return (
            f'<retEnvEvento xmlns="{NS}" versao="1.00"><idLote>{doc.findtext(_q("idLote"))}</idLote>'
            f"<tpAmb>{inf.findtext(_q('tpAmb'))}</tpAmb><verAplic>MOCK</verAplic><cOrgao>35</cOrgao>"
            f"<cStat>128</cStat><xMotivo>Lote de Evento Processado</xMotivo>"
            f'<retEvento versao="1.00"><infEvento><tpAmb>{inf.findtext(_q("tpAmb"))}</tpAmb>'
            f"<verAplic>MOCK</verAplic><cOrgao>35</cOrgao><cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo>"
            f"<chNFe>{chave}</chNFe><tpEvento>110111</tpEvento><nSeqEvento>1</nSeqEvento>"
            f"<dhRegEvento>{agora_sp().isoformat()}</dhRegEvento>"
            + (f"<nProt>{prot_ev}</nProt>" if prot_ev else "")
            + "</infEvento></retEvento></retEnvEvento>"
        )

    def _NFeInutilizacao4(self, doc) -> str:
        try:
            verificar_assinatura(doc)
            cstat, motivo = "102", "Inutilizacao de numero homologado"
        except AssinaturaInvalida:
            cstat, motivo = "297", "Rejeicao: Assinatura difere do calculado"
        inf = doc.find(_q("infInut"))
        return (
            f'<retInutNFe xmlns="{NS}" versao="4.00"><infInut><tpAmb>{inf.findtext(_q("tpAmb"))}</tpAmb>'
            f"<verAplic>MOCK</verAplic><cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo><cUF>35</cUF>"
            f"<nNFIni>{inf.findtext(_q('nNFIni'))}</nNFIni><nNFFin>{inf.findtext(_q('nNFFin'))}</nNFFin>"
            "</infInut></retInutNFe>"
        )

    def _CadConsultaCadastro4(self, doc) -> str:
        cnpj = doc.findtext(f"{_q('infCons')}/{_q('CNPJ')}")
        return (
            f'<retConsCad xmlns="{NS}" versao="2.00"><infCons><verAplic>MOCK</verAplic>'
            f"<cStat>111</cStat><xMotivo>Consulta cadastro com uma ocorrencia</xMotivo><UF>SP</UF>"
            f"<CNPJ>{cnpj}</CNPJ><dhCons>{agora_sp().isoformat()}</dhCons><cUF>35</cUF>"
            f"<infCad><IE>123456789012</IE><CNPJ>{cnpj}</CNPJ><UF>SP</UF><cSit>1</cSit>"
            f"<indCredNFe>1</indCredNFe><indCredCTe>4</indCredCTe><xNome>EMPRESA FICTICIA LTDA</xNome>"
            f"<xRegApur>SIMPLES NACIONAL</xRegApur><CNAE>4751201</CNAE></infCad></infCons></retConsCad>"
        )
