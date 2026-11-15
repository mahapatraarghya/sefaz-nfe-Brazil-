"""SOAP 1.2 client for SEFAZ-SP NF-e 4.00 web services.

Endpoints: check them against the Portal Nacional da NF-e
(https://www.nfe.fazenda.gov.br → Serviços → Relação de Serviços Web) before going live.
"""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import requests
from lxml import etree

from .builder import NS, agora_sp
from .certificado import Certificado
from .models import UF_CODIGOS, Ambiente

SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
WSDL = "http://www.portalfiscal.inf.br/nfe/wsdl/"

_HOSTS = {
    Ambiente.PRODUCAO: "https://nfe.fazenda.sp.gov.br/ws/",
    Ambiente.HOMOLOGACAO: "https://homologacao.nfe.fazenda.sp.gov.br/ws/",
}

# serviço -> (arquivo .asmx, método SOAP)
SERVICOS = {
    "NFeStatusServico4": ("nfestatusservico4.asmx", "nfeStatusServicoNF"),
    "NFeAutorizacao4": ("nfeautorizacao4.asmx", "nfeAutorizacaoLote"),
    "NFeRetAutorizacao4": ("nferetautorizacao4.asmx", "nfeRetAutorizacaoLote"),
    "NFeConsultaProtocolo4": ("nfeconsultaprotocolo4.asmx", "nfeConsultaNF"),
    "NFeRecepcaoEvento4": ("nferecepcaoevento4.asmx", "nfeRecepcaoEvento"),
    "NFeInutilizacao4": ("nfeinutilizacao4.asmx", "nfeInutilizacaoNF"),
    "CadConsultaCadastro4": ("cadconsultacadastro4.asmx", "consultaCadastro"),
}


def url_servico(servico: str, ambiente: Ambiente) -> str:
    return _HOSTS[ambiente] + SERVICOS[servico][0]


class Transporte(Protocol):
    def enviar(self, url: str, servico: str, metodo: str, corpo: str) -> str: ...


def montar_envelope(servico: str, corpo: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap12:Envelope xmlns:soap12="{SOAP12}"><soap12:Body>'
        f'<nfeDadosMsg xmlns="{WSDL}{servico}">{corpo}</nfeDadosMsg>'
        "</soap12:Body></soap12:Envelope>"
    )


class TransporteHttp:
    """Real HTTPS transport with mutual TLS using the e-CNPJ certificate."""

    def __init__(self, certificado: Certificado, ca_bundle: str | bool = True, timeout: float = 30):
        self._dir = tempfile.mkdtemp(prefix="nfe-sp-")
        os.chmod(self._dir, 0o700)
        cert_path, key_path = Path(self._dir, "cert.pem"), Path(self._dir, "key.pem")
        for caminho, dados in ((cert_path, certificado.pem_certificado()), (key_path, certificado.pem_chave())):
            fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(dados)
        self._session = requests.Session()
        self._session.cert = (str(cert_path), str(key_path))
        self._session.verify = ca_bundle
        self._timeout = timeout

    def enviar(self, url: str, servico: str, metodo: str, corpo: str) -> str:
        headers = {
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{WSDL}{servico}/{metodo}"'
        }
        try:
            resp = self._session.post(
                url, data=montar_envelope(servico, corpo).encode("utf-8"),
                headers=headers, timeout=self._timeout,
            )
        except requests.exceptions.SSLError as exc:
            raise ConnectionError(
                "Falha TLS com a SEFAZ. Verifique NFE_CA_BUNDLE (cadeia ICP-Brasil) e o certificado A1."
            ) from exc
        if resp.status_code >= 400 and "Envelope" not in resp.text:
            raise ConnectionError(f"SEFAZ respondeu HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.text

    def close(self) -> None:
        self._session.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self) -> "TransporteHttp":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        shutil.rmtree(getattr(self, "_dir", ""), ignore_errors=True)


# --- respostas ---------------------------------------------------------------

@dataclass
class Retorno:
    cstat: str
    motivo: str
    xml: etree._Element = field(repr=False)

    def txt(self, tag: str) -> str | None:
        return self.xml.findtext(f".//{{{NS}}}{tag}")


@dataclass
class Protocolo:
    chave: str
    cstat: str
    motivo: str
    numero: str | None
    data_recebimento: str | None
    xml: etree._Element = field(repr=False)

    @property
    def autorizado(self) -> bool:
        # 100 autorizado; 150 autorizado fora de prazo
        return self.cstat in ("100", "150")

    @property
    def denegado(self) -> bool:
        return self.cstat in ("110", "301", "302", "303")


@dataclass
class ResultadoAutorizacao:
    lote: Retorno
    protocolo: Protocolo | None


def _parse_ret(texto: str, tag: str) -> Retorno:
    doc = etree.fromstring(texto.encode("utf-8"))
    ret = doc.find(f".//{{{NS}}}{tag}")
    if ret is None:
        fault = doc.findtext(f".//{{{SOAP12}}}Text")
        raise RuntimeError(f"Resposta inesperada da SEFAZ (sem {tag}): {fault or texto[:500]}")
    # retConsCad / retInutNFe carry cStat inside infCons / infInut
    cstat = ret.findtext(f"{{{NS}}}cStat") or ret.findtext(f".//{{{NS}}}cStat")
    motivo = ret.findtext(f"{{{NS}}}xMotivo") or ret.findtext(f".//{{{NS}}}xMotivo")
    return Retorno(cstat, motivo, ret)


def _parse_prot(prot: etree._Element) -> Protocolo:
    inf = prot.find(f"{{{NS}}}infProt")
    t = lambda tag: inf.findtext(f"{{{NS}}}{tag}")  # noqa: E731
    return Protocolo(t("chNFe"), t("cStat"), t("xMotivo"), t("nProt"), t("dhRecbto"), prot)


def _xml(el: etree._Element) -> str:
    """Serialize as a standalone fragment, dropping namespaces inherited from the SOAP envelope."""
    copia = copy.deepcopy(el)
    etree.cleanup_namespaces(copia)
    return etree.tostring(copia, encoding="unicode")


# --- cliente -------------------------------------------------------------------

class ClienteSefaz:
    def __init__(self, ambiente: Ambiente, certificado: Certificado, transporte: Transporte, uf: str = "SP"):
        self.ambiente = ambiente
        self.certificado = certificado
        self.transporte = transporte
        self.uf = uf
        self.cuf = UF_CODIGOS[uf]

    def _chamar(self, servico: str, corpo: str, tag_retorno: str) -> Retorno:
        url = url_servico(servico, self.ambiente)
        resposta = self.transporte.enviar(url, servico, SERVICOS[servico][1], corpo)
        return _parse_ret(resposta, tag_retorno)

    def status_servico(self) -> Retorno:
        corpo = (
            f'<consStatServ xmlns="{NS}" versao="4.00"><tpAmb>{int(self.ambiente)}</tpAmb>'
            f"<cUF>{self.cuf}</cUF><xServ>STATUS</xServ></consStatServ>"
        )
        return self._chamar("NFeStatusServico4", corpo, "retConsStatServ")

    def autorizar(self, nfe_assinada: etree._Element, id_lote: int | None = None,
                  tentativas_recibo: int = 5, espera: float = 2.0) -> ResultadoAutorizacao:
        """Sends one NF-e synchronously (indSinc=1). Falls back to polling the receipt if
        SEFAZ processes the batch asynchronously (cStat 103)."""
        id_lote = id_lote or int(time.time() * 1000) % 10**15
        corpo = (
            f'<enviNFe xmlns="{NS}" versao="4.00"><idLote>{id_lote}</idLote>'
            f"<indSinc>1</indSinc>{_xml(nfe_assinada)}</enviNFe>"
        )
        lote = self._chamar("NFeAutorizacao4", corpo, "retEnviNFe")
        if lote.cstat == "104":
            prot = lote.xml.find(f"{{{NS}}}protNFe")
            return ResultadoAutorizacao(lote, _parse_prot(prot) if prot is not None else None)
        if lote.cstat == "103":
            recibo = lote.txt("nRec")
            for _ in range(tentativas_recibo):
                time.sleep(espera)
                ret = self.consultar_recibo(recibo)
                if ret.lote.cstat != "105":  # 105 = lote em processamento
                    return ret
            return ResultadoAutorizacao(lote, None)
        return ResultadoAutorizacao(lote, None)

    def consultar_recibo(self, recibo: str) -> ResultadoAutorizacao:
        corpo = (
            f'<consReciNFe xmlns="{NS}" versao="4.00"><tpAmb>{int(self.ambiente)}</tpAmb>'
            f"<nRec>{recibo}</nRec></consReciNFe>"
        )
        ret = self._chamar("NFeRetAutorizacao4", corpo, "retConsReciNFe")
        prot = ret.xml.find(f"{{{NS}}}protNFe")
        return ResultadoAutorizacao(ret, _parse_prot(prot) if prot is not None else None)

    def consultar_protocolo(self, chave: str) -> tuple[Retorno, Protocolo | None]:
        corpo = (
            f'<consSitNFe xmlns="{NS}" versao="4.00"><tpAmb>{int(self.ambiente)}</tpAmb>'
            f"<xServ>CONSULTAR</xServ><chNFe>{chave}</chNFe></consSitNFe>"
        )
        ret = self._chamar("NFeConsultaProtocolo4", corpo, "retConsSitNFe")
        prot = ret.xml.find(f"{{{NS}}}protNFe")
        return ret, (_parse_prot(prot) if prot is not None else None)

    def cancelar(self, chave: str, protocolo: str, justificativa: str, cnpj: str) -> Retorno:
        justificativa = " ".join(justificativa.split())
        if not 15 <= len(justificativa) <= 255:
            raise ValueError("Justificativa de cancelamento deve ter entre 15 e 255 caracteres")
        evento = self._evento(chave, cnpj, "110111", 1, [
            ("descEvento", "Cancelamento"), ("nProt", protocolo), ("xJust", justificativa),
        ])
        corpo = (
            f'<envEvento xmlns="{NS}" versao="1.00"><idLote>{int(time.time())}</idLote>'
            f"{_xml(evento)}</envEvento>"
        )
        return self._chamar("NFeRecepcaoEvento4", corpo, "retEnvEvento")

    def _evento(self, chave: str, cnpj: str, tipo: str, seq: int, detalhes: list[tuple[str, str]]) -> etree._Element:
        ev = etree.Element(f"{{{NS}}}evento", nsmap={None: NS}, versao="1.00")
        inf = etree.SubElement(ev, f"{{{NS}}}infEvento", Id=f"ID{tipo}{chave}{seq:02d}")
        for tag, valor in [
            ("cOrgao", self.cuf), ("tpAmb", int(self.ambiente)), ("CNPJ", cnpj), ("chNFe", chave),
            ("dhEvento", agora_sp().isoformat()), ("tpEvento", tipo), ("nSeqEvento", seq),
            ("verEvento", "1.00"),
        ]:
            etree.SubElement(inf, f"{{{NS}}}{tag}").text = str(valor)
        det = etree.SubElement(inf, f"{{{NS}}}detEvento", versao="1.00")
        for tag, valor in detalhes:
            etree.SubElement(det, f"{{{NS}}}{tag}").text = valor
        self.certificado.assinar(ev, inf)
        return ev

    def inutilizar(self, cnpj: str, serie: int, inicio: int, fim: int, justificativa: str, ano: int | None = None) -> Retorno:
        justificativa = " ".join(justificativa.split())
        if not 15 <= len(justificativa) <= 255:
            raise ValueError("Justificativa de inutilização deve ter entre 15 e 255 caracteres")
        if not 1 <= inicio <= fim:
            raise ValueError("Faixa de numeração inválida")
        aa = f"{(ano or agora_sp().year) % 100:02d}"
        id_ = f"ID{self.cuf}{aa}{cnpj}55{serie:03d}{inicio:09d}{fim:09d}"
        raiz = etree.Element(f"{{{NS}}}inutNFe", nsmap={None: NS}, versao="4.00")
        inf = etree.SubElement(raiz, f"{{{NS}}}infInut", Id=id_)
        for tag, valor in [
            ("tpAmb", int(self.ambiente)), ("xServ", "INUTILIZAR"), ("cUF", self.cuf), ("ano", aa),
            ("CNPJ", cnpj), ("mod", 55), ("serie", serie), ("nNFIni", inicio), ("nNFFin", fim),
            ("xJust", justificativa),
        ]:
            etree.SubElement(inf, f"{{{NS}}}{tag}").text = str(valor)
        self.certificado.assinar(raiz, inf)
        return self._chamar("NFeInutilizacao4", _xml(raiz), "retInutNFe")

    def consultar_cadastro(self, cnpj: str, uf: str = "SP") -> Retorno:
        corpo = (
            f'<ConsCad xmlns="{NS}" versao="2.00"><infCons><xServ>CONS-CAD</xServ>'
            f"<UF>{uf}</UF><CNPJ>{cnpj}</CNPJ></infCons></ConsCad>"
        )
        return self._chamar("CadConsultaCadastro4", corpo, "retConsCad")


def montar_nfe_proc(nfe_assinada: etree._Element, protocolo: Protocolo) -> str:
    """The legally valid file to store for 5+ years and send to the buyer."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<nfeProc xmlns="{NS}" versao="4.00">{_xml(nfe_assinada)}{_xml(protocolo.xml)}</nfeProc>'
    )
