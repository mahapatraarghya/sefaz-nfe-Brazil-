from decimal import Decimal

import pytest

from nfe_sp.builder import NOME_HOMOLOGACAO, NS, build_nfe
from nfe_sp.models import Ambiente

q = lambda path: "/".join(f"{{{NS}}}{p}" for p in path.split("/"))  # noqa: E731


def test_estrutura_basica(nota):
    nfe, chave = build_nfe(nota, Ambiente.HOMOLOGACAO, codigo_numerico="12345678")
    inf = nfe.find(q("infNFe"))
    assert inf.get("Id") == f"NFe{chave}"
    assert inf.findtext(q("ide/cDV")) == chave[-1]
    assert inf.findtext(q("ide/tpAmb")) == "2"
    assert inf.findtext(q("ide/idDest")) == "1"
    assert inf.findtext(q("ide/dhEmi")) == "2026-11-15T10:30:00-03:00"
    assert inf.findtext(q("emit/CRT")) == "1"
    assert chave[2:6] == "2611"


def test_totais(nota):
    nfe, _ = build_nfe(nota, Ambiente.HOMOLOGACAO)
    tot = nfe.find(q("infNFe/total/ICMSTot"))
    assert tot.findtext(q("vProd")) == "228.70"   # 3 x 49.90 + 79.00
    assert tot.findtext(q("vDesc")) == "9.00"
    assert tot.findtext(q("vNF")) == "219.70"
    assert tot.findtext(q("vICMS")) == "0.00"


def test_simples_nacional_csosn_e_texto(nota):
    nfe, _ = build_nfe(nota, Ambiente.HOMOLOGACAO)
    det = nfe.find(q("infNFe/det"))
    assert det.findtext(q("imposto/ICMS/ICMSSN102/CSOSN")) == "102"
    assert det.findtext(q("imposto/PIS/PISOutr/CST")) == "49"
    assert "SIMPLES NACIONAL" in nfe.findtext(q("infNFe/infAdic/infCpl"))


def test_homologacao_troca_nome_destinatario(nota):
    nfe, _ = build_nfe(nota, Ambiente.HOMOLOGACAO)
    assert nfe.findtext(q("infNFe/dest/xNome")) == NOME_HOMOLOGACAO
    nfe, _ = build_nfe(nota, Ambiente.PRODUCAO)
    assert nfe.findtext(q("infNFe/dest/xNome")) == "CLIENTE EXEMPLO LTDA"


def test_csosn_101_credito(nota):
    nota.emitente.aliquota_credito_sn = Decimal("2.5")
    for item in nota.itens:
        item.csosn = "101"
    nfe, _ = build_nfe(nota, Ambiente.HOMOLOGACAO)
    g = nfe.find(q("infNFe/det/imposto/ICMS/ICMSSN101"))
    assert g.findtext(q("pCredSN")) == "2.5000"
    assert g.findtext(q("vCredICMSSN")) == "3.74"   # 2.5% de 149.70
    assert "R$ 5.49" in nfe.findtext(q("infNFe/infAdic/infCpl"))  # 3.74 + 1.75


def test_cfop_interestadual_rejeitado(nota):
    nota.destinatario.endereco.uf = "RJ"
    with pytest.raises(ValueError, match="CFOP"):
        build_nfe(nota, Ambiente.HOMOLOGACAO)


def test_pagamento_insuficiente(nota):
    nota.pagamentos[0].valor = Decimal("100")
    with pytest.raises(ValueError, match="Pagamentos"):
        build_nfe(nota, Ambiente.HOMOLOGACAO)


def test_troco(nota):
    nota.pagamentos[0].tipo = "01"
    nota.pagamentos[0].valor = Decimal("250")
    nfe, _ = build_nfe(nota, Ambiente.HOMOLOGACAO)
    assert nfe.findtext(q("infNFe/pag/vTroco")) == "30.30"
