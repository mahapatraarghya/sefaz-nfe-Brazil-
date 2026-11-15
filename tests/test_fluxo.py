"""End-to-end against the mock SEFAZ."""

import pytest
from lxml import etree

from nfe_sp.armazenamento import Armazenamento
from nfe_sp.builder import NS
from nfe_sp.certificado import Certificado, verificar_assinatura
from nfe_sp.emissor import Emissor, ErroValidacao
from nfe_sp.mock import SefazFake
from nfe_sp.models import Ambiente
from nfe_sp.sefaz import ClienteSefaz, montar_envelope


@pytest.fixture
def emissor(tmp_path, certificado):
    cliente = ClienteSefaz(Ambiente.HOMOLOGACAO, certificado, SefazFake())
    st = Armazenamento(tmp_path)
    yield Emissor(cliente, certificado, st)
    st.close()


def test_status(emissor):
    assert emissor.cliente.status_servico().cstat == "107"


def test_emitir_consultar_cancelar(emissor, nota):
    res = emissor.emitir(nota)
    assert res.autorizada, res.motivo
    assert res.arquivo.exists()

    proc = etree.parse(str(res.arquivo)).getroot()
    assert proc.tag == f"{{{NS}}}nfeProc"
    verificar_assinatura(proc.find(f"{{{NS}}}NFe"))
    assert proc.findtext(f".//{{{NS}}}nProt") == res.protocolo.numero

    ret, prot = emissor.cliente.consultar_protocolo(res.chave)
    assert ret.cstat == "100" and prot.numero == res.protocolo.numero

    cstat, _ = emissor.cancelar(res.chave, "Erro na digitacao do pedido", nota.emitente.cnpj)
    assert cstat == "135"
    assert emissor.armazenamento.buscar(res.chave)["status"] == "cancelada"
    assert emissor.armazenamento.buscar(res.chave)["arquivo"].endswith("-procNFe.xml")
    assert emissor.cliente.consultar_protocolo(res.chave)[0].cstat == "101"


def test_justificativa_curta(emissor, nota):
    res = emissor.emitir(nota)
    with pytest.raises(ValueError):
        emissor.cancelar(res.chave, "curta", nota.emitente.cnpj)


def test_certificado_de_outro_cnpj(tmp_path, nota):
    outro = Certificado.autoassinado_para_testes("44555666000181")
    cliente = ClienteSefaz(Ambiente.HOMOLOGACAO, outro, SefazFake())
    with pytest.raises(ErroValidacao, match="Certificado pertence"):
        Emissor(cliente, outro, Armazenamento(tmp_path)).emitir(nota)


def test_rejeicao_registrada(emissor, nota, monkeypatch):
    # força a SEFAZ fake a ver um nome de destinatário errado em homologação
    import nfe_sp.mock as mock
    monkeypatch.setattr(mock, "NOME_HOMOLOGACAO", "OUTRO NOME")
    res = emissor.emitir(nota)
    assert not res.autorizada and res.cstat == "598"
    assert emissor.armazenamento.buscar(res.chave)["status"] == "rejeitada"


def test_inutilizacao(emissor, nota):
    r = emissor.cliente.inutilizar(nota.emitente.cnpj, 1, 10, 12, "Falha no sistema, numeros pulados")
    assert r.cstat == "102"


def test_numeracao_sequencial(tmp_path):
    st = Armazenamento(tmp_path)
    assert [st.proximo_numero(1) for _ in range(3)] == [1, 2, 3]
    assert st.proximo_numero(2) == 1
    st.definir_ultimo_numero(1, 500)
    assert st.proximo_numero(1) == 501


def test_envelope_soap():
    env = montar_envelope("NFeAutorizacao4", "<x/>")
    assert 'xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeAutorizacao4"' in env
    assert "http://www.w3.org/2003/05/soap-envelope" in env
