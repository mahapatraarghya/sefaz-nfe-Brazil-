import json
from datetime import datetime
from pathlib import Path

import pytest

from nfe_sp.builder import TZ_SP
from nfe_sp.certificado import Certificado
from nfe_sp.models import emitente_from_dict, nota_from_dict

EXEMPLOS = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def emitente():
    return emitente_from_dict(json.loads((EXEMPLOS / "emitente.json").read_text()))


@pytest.fixture
def dados_nota():
    return json.loads((EXEMPLOS / "nota.json").read_text())


@pytest.fixture
def nota(emitente, dados_nota):
    n = nota_from_dict(dados_nota, emitente, numero=42, serie=1)
    n.data_emissao = datetime(2026, 11, 15, 10, 30, tzinfo=TZ_SP)
    return n


@pytest.fixture(scope="session")
def certificado():
    return Certificado.autoassinado_para_testes("11222333000181", "EMPRESA EXEMPLO COMERCIO LTDA")
