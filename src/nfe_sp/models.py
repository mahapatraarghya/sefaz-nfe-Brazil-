"""Domain model for an NF-e issued by a Simples Nacional company in São Paulo."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import IntEnum
from typing import Any

from . import documentos

UF_CODIGOS = {
    "RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26, "AL": 27,
    "SE": 28, "BA": 29, "MG": 31, "ES": 32, "RJ": 33, "SP": 35, "PR": 41,
    "SC": 42, "RS": 43, "MS": 50, "MT": 51, "GO": 52, "DF": 53,
}


class Ambiente(IntEnum):
    PRODUCAO = 1
    HOMOLOGACAO = 2


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass
class Endereco:
    logradouro: str
    numero: str
    bairro: str
    codigo_municipio: str  # código IBGE, 7 dígitos (São Paulo capital = 3550308)
    municipio: str
    uf: str
    cep: str
    complemento: str | None = None
    telefone: str | None = None

    def __post_init__(self) -> None:
        self.uf = self.uf.upper()
        if self.uf not in UF_CODIGOS and self.uf != "EX":
            raise ValueError(f"UF inválida: {self.uf}")
        self.cep = documentos.somente_digitos(self.cep)
        if len(self.cep) != 8:
            raise ValueError(f"CEP inválido: {self.cep}")
        if len(self.codigo_municipio) != 7 or not self.codigo_municipio.isdigit():
            raise ValueError(f"Código de município IBGE inválido: {self.codigo_municipio}")


@dataclass
class ResponsavelTecnico:
    cnpj: str
    contato: str
    email: str
    telefone: str


@dataclass
class Emitente:
    cnpj: str
    razao_social: str
    ie: str
    endereco: Endereco
    nome_fantasia: str | None = None
    crt: int = 1  # 1 = Simples Nacional, 4 = Simples Nacional - MEI
    aliquota_credito_sn: Decimal | None = None  # pCredSN, usado com CSOSN 101
    responsavel_tecnico: ResponsavelTecnico | None = None

    def __post_init__(self) -> None:
        self.cnpj = documentos.normalizar_cnpj(self.cnpj)
        if not documentos.cnpj_valido(self.cnpj):
            raise ValueError(f"CNPJ do emitente inválido: {self.cnpj}")
        if self.crt not in (1, 4):
            raise ValueError("Este emissor suporta apenas CRT 1 (Simples Nacional) ou 4 (MEI)")
        if self.endereco.uf != "SP":
            raise ValueError("Este emissor está configurado para emitentes de SP")
        self.ie = documentos.somente_digitos(self.ie)
        if self.aliquota_credito_sn is not None:
            self.aliquota_credito_sn = _dec(self.aliquota_credito_sn)


@dataclass
class Destinatario:
    nome: str
    endereco: Endereco
    cnpj: str | None = None
    cpf: str | None = None
    ie: str | None = None
    # 1 = contribuinte com IE, 2 = contribuinte isento, 9 = não contribuinte
    indicador_ie: int = 9
    email: str | None = None

    def __post_init__(self) -> None:
        if bool(self.cnpj) == bool(self.cpf):
            raise ValueError("Informe CNPJ ou CPF do destinatário (apenas um)")
        if self.cnpj:
            self.cnpj = documentos.normalizar_cnpj(self.cnpj)
            if not documentos.cnpj_valido(self.cnpj):
                raise ValueError(f"CNPJ do destinatário inválido: {self.cnpj}")
        if self.cpf:
            self.cpf = documentos.somente_digitos(self.cpf)
            if not documentos.cpf_valido(self.cpf):
                raise ValueError(f"CPF do destinatário inválido: {self.cpf}")
        if self.indicador_ie not in (1, 2, 9):
            raise ValueError("indicador_ie deve ser 1, 2 ou 9")
        if self.indicador_ie == 1 and not self.ie:
            raise ValueError("Destinatário contribuinte (indicador_ie=1) exige IE")
        if self.ie:
            self.ie = documentos.somente_digitos(self.ie)


CSOSN_SUPORTADOS = {"101", "102", "103", "300", "400", "500"}


@dataclass
class Item:
    codigo: str
    descricao: str
    ncm: str
    cfop: str
    unidade: str
    quantidade: Decimal
    valor_unitario: Decimal
    csosn: str = "102"
    origem: int = 0
    ean: str = "SEM GTIN"
    cest: str | None = None
    desconto: Decimal = Decimal("0")
    frete: Decimal = Decimal("0")
    pis_cst: str = "49"
    cofins_cst: str = "49"

    def __post_init__(self) -> None:
        self.quantidade = _dec(self.quantidade)
        self.valor_unitario = _dec(self.valor_unitario)
        self.desconto = _dec(self.desconto)
        self.frete = _dec(self.frete)
        self.ncm = documentos.somente_digitos(self.ncm)
        if len(self.ncm) != 8:
            raise ValueError(f"NCM deve ter 8 dígitos: {self.ncm}")
        if len(self.cfop) != 4 or not self.cfop.isdigit():
            raise ValueError(f"CFOP inválido: {self.cfop}")
        if self.csosn not in CSOSN_SUPORTADOS:
            raise ValueError(f"CSOSN {self.csosn} não suportado ({sorted(CSOSN_SUPORTADOS)})")
        if self.quantidade <= 0 or self.valor_unitario < 0:
            raise ValueError("Quantidade deve ser > 0 e valor unitário >= 0")

    @property
    def valor_bruto(self) -> Decimal:
        return (self.quantidade * self.valor_unitario).quantize(Decimal("0.01"))


# tPag: 01 dinheiro, 03 crédito, 04 débito, 05 crédito loja, 15 boleto,
# 16 depósito, 17 PIX dinâmico, 18 transferência, 20 PIX estático, 90 sem pagamento, 99 outros
@dataclass
class Pagamento:
    valor: Decimal
    tipo: str = "17"
    descricao: str | None = None  # obrigatório quando tipo = 99

    def __post_init__(self) -> None:
        self.valor = _dec(self.valor)
        if self.tipo == "99" and not self.descricao:
            raise ValueError("Pagamento tipo 99 (outros) exige descricao")


@dataclass
class NotaFiscal:
    emitente: Emitente
    destinatario: Destinatario
    itens: list[Item]
    pagamentos: list[Pagamento]
    numero: int
    serie: int = 1
    natureza_operacao: str = "Venda de mercadoria"
    data_emissao: datetime | None = None
    tipo_operacao: int = 1  # 0 = entrada, 1 = saída
    finalidade: int = 1  # 1 normal, 2 complementar, 3 ajuste, 4 devolução
    # 1 presencial, 2 internet, 3 teleatendimento, 4 entrega domicílio, 9 outros
    presenca: int = 1
    modalidade_frete: int = 9  # 9 = sem frete
    informacoes_complementares: str | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.itens:
            raise ValueError("A NF-e precisa de ao menos um item")
        if len(self.itens) > 990:
            raise ValueError("Máximo de 990 itens por NF-e")
        if not 1 <= self.numero <= 999_999_999:
            raise ValueError("Número da NF-e deve estar entre 1 e 999999999")
        if not 0 <= self.serie <= 999:
            raise ValueError("Série deve estar entre 0 e 999")

    @property
    def interestadual(self) -> bool:
        return self.destinatario.endereco.uf != self.emitente.endereco.uf

    @property
    def consumidor_final(self) -> bool:
        return self.destinatario.indicador_ie == 9


def nota_from_dict(data: dict, emitente: Emitente, numero: int, serie: int) -> NotaFiscal:
    """Build a NotaFiscal from the JSON invoice format (see examples/nota.json)."""
    dest = dict(data["destinatario"])
    dest["endereco"] = Endereco(**dest["endereco"])
    return NotaFiscal(
        emitente=emitente,
        destinatario=Destinatario(**dest),
        itens=[Item(**i) for i in data["itens"]],
        pagamentos=[Pagamento(**p) for p in data["pagamentos"]],
        numero=numero,
        serie=serie,
        natureza_operacao=data.get("natureza_operacao", "Venda de mercadoria"),
        presenca=data.get("presenca", 1),
        finalidade=data.get("finalidade", 1),
        modalidade_frete=data.get("modalidade_frete", 9),
        informacoes_complementares=data.get("informacoes_complementares"),
    )


def emitente_from_dict(data: dict) -> Emitente:
    data = dict(data)
    data["endereco"] = Endereco(**data["endereco"])
    if data.get("responsavel_tecnico"):
        data["responsavel_tecnico"] = ResponsavelTecnico(**data["responsavel_tecnico"])
    return Emitente(**data)
