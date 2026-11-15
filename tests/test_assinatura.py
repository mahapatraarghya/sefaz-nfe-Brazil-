import pytest
from lxml import etree

from nfe_sp.builder import NS, build_nfe
from nfe_sp.certificado import AssinaturaInvalida, verificar_assinatura
from nfe_sp.models import Ambiente


def _assinada(nota, certificado):
    nfe, _ = build_nfe(nota, Ambiente.HOMOLOGACAO)
    certificado.assinar(nfe, nfe.find(f"{{{NS}}}infNFe"))
    return nfe


def test_assinatura_valida_e_sobrevive_serializacao(nota, certificado):
    nfe = _assinada(nota, certificado)
    verificar_assinatura(nfe)
    # simula o que a SEFAZ recebe: NFe embutida em outro documento
    texto = f'<enviNFe xmlns="{NS}" versao="4.00"><idLote>1</idLote>' + etree.tostring(nfe, encoding="unicode") + "</enviNFe>"
    recebida = etree.fromstring(texto.encode()).find(f"{{{NS}}}NFe")
    verificar_assinatura(recebida)


def test_estrutura_signature(nota, certificado):
    nfe = _assinada(nota, certificado)
    sig = nfe[-1]
    assert sig.tag == "{http://www.w3.org/2000/09/xmldsig#}Signature"
    assert sig.nsmap == {None: "http://www.w3.org/2000/09/xmldsig#"}
    ref = sig.find(".//{http://www.w3.org/2000/09/xmldsig#}Reference")
    assert ref.get("URI") == "#" + nfe[0].get("Id")


def test_alteracao_apos_assinar_e_detectada(nota, certificado):
    nfe = _assinada(nota, certificado)
    nfe.find(f".//{{{NS}}}vNF").text = "1.00"
    with pytest.raises(AssinaturaInvalida, match="DigestValue"):
        verificar_assinatura(nfe)


def test_cnpj_do_certificado(certificado):
    assert certificado.cnpj == "11222333000181"
