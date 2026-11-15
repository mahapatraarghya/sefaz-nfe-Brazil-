"""High-level issuing flow: build → sign → verify → (XSD) → send → store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .armazenamento import Armazenamento
from .builder import build_nfe
from .certificado import Certificado, verificar_assinatura
from .models import Ambiente, NotaFiscal
from .sefaz import ClienteSefaz, Protocolo, montar_nfe_proc
from .validacao import validar_xsd


class ErroValidacao(Exception):
    pass


@dataclass
class ResultadoEmissao:
    chave: str
    numero: int
    cstat: str
    motivo: str
    protocolo: Protocolo | None
    arquivo: Path | None

    @property
    def autorizada(self) -> bool:
        return bool(self.protocolo and self.protocolo.autorizado)


class Emissor:
    def __init__(self, cliente: ClienteSefaz, certificado: Certificado,
                 armazenamento: Armazenamento, pasta_xsd: str | None = None):
        self.cliente = cliente
        self.certificado = certificado
        self.armazenamento = armazenamento
        self.pasta_xsd = pasta_xsd

    @property
    def ambiente(self) -> Ambiente:
        return self.cliente.ambiente

    def preparar(self, nota: NotaFiscal) -> tuple[etree._Element, str]:
        """Builds and signs the NF-e without sending it."""
        cert_cnpj = self.certificado.cnpj
        if cert_cnpj and cert_cnpj[:8] != nota.emitente.cnpj[:8]:
            raise ErroValidacao(
                f"Certificado pertence ao CNPJ {cert_cnpj}, diferente do emitente {nota.emitente.cnpj}"
            )
        nfe, chave = build_nfe(nota, self.ambiente)
        self.certificado.assinar(nfe, nfe.find("{http://www.portalfiscal.inf.br/nfe}infNFe"))
        verificar_assinatura(nfe)
        if self.pasta_xsd:
            erros = validar_xsd(nfe, self.pasta_xsd)
            if erros:
                raise ErroValidacao("XML inválido pelo XSD:\n" + "\n".join(erros))
        return nfe, chave

    def emitir(self, nota: NotaFiscal) -> ResultadoEmissao:
        self.certificado.verificar_validade()
        nfe, chave = self.preparar(nota)
        st = self.armazenamento
        st.registrar(chave, nota.serie, nota.numero, "enviando", None, None,
                     xml=etree.tostring(nfe, encoding="unicode"), sufixo="assinada")

        resultado = self.cliente.autorizar(nfe)
        prot = resultado.protocolo
        if prot is None:
            # Lot-level rejection or still processing: keep the signed XML to retry/consult.
            st.registrar(chave, nota.serie, nota.numero, "pendente", resultado.lote.cstat, resultado.lote.motivo)
            return ResultadoEmissao(chave, nota.numero, resultado.lote.cstat, resultado.lote.motivo, None, None)

        arquivo = None
        if prot.autorizado or prot.denegado:
            # Denied NF-e are also legally recorded and their number can't be reused.
            arquivo = st.registrar(
                chave, nota.serie, nota.numero, "autorizada" if prot.autorizado else "denegada",
                prot.cstat, prot.motivo, prot.numero, montar_nfe_proc(nfe, prot),
            )
        else:
            st.registrar(chave, nota.serie, nota.numero, "rejeitada", prot.cstat, prot.motivo)
        return ResultadoEmissao(chave, nota.numero, prot.cstat, prot.motivo, prot, arquivo)

    def cancelar(self, chave: str, justificativa: str, cnpj: str) -> tuple[str, str]:
        nota = self.armazenamento.buscar(chave)
        protocolo = nota and nota.get("protocolo")
        if not protocolo:
            _, prot = self.cliente.consultar_protocolo(chave)
            if not prot or not prot.autorizado:
                raise ErroValidacao("NF-e não está autorizada; não é possível cancelar")
            protocolo = prot.numero
        ret = self.cliente.cancelar(chave, protocolo, justificativa, cnpj)
        ev = ret.xml.find(".//{http://www.portalfiscal.inf.br/nfe}retEvento")
        cstat = ev.findtext(".//{http://www.portalfiscal.inf.br/nfe}cStat") if ev is not None else ret.cstat
        motivo = ev.findtext(".//{http://www.portalfiscal.inf.br/nfe}xMotivo") if ev is not None else ret.motivo
        if cstat in ("135", "155") and nota:
            self.armazenamento.registrar(
                chave, nota["serie"], nota["numero"], "cancelada", cstat, motivo,
                xml=etree.tostring(ret.xml, encoding="unicode"), sufixo="cancelamento",
            )
        return cstat, motivo
