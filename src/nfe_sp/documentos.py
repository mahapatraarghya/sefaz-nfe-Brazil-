"""CNPJ / CPF validation and the NF-e access key (chave de acesso).

CNPJ check digits use the ASCII-48 rule, so they also work for the
alphanumeric CNPJ format (letters in the first 12 positions) introduced in 2026.
For purely numeric CNPJs the result is identical to the classic algorithm.
"""

from __future__ import annotations

import re
import secrets


def somente_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def normalizar_cnpj(cnpj: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", cnpj or "").upper()


def _valor(c: str) -> int:
    return ord(c) - 48


def _dv_cnpj(base: str) -> str:
    pesos = list(range(2, 10)) * 2
    total = sum(_valor(c) * p for c, p in zip(reversed(base), pesos))
    resto = total % 11
    return "0" if resto < 2 else str(11 - resto)


def cnpj_valido(cnpj: str) -> bool:
    cnpj = normalizar_cnpj(cnpj)
    if not re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", cnpj) or len(set(cnpj)) == 1:
        return False
    dv1 = _dv_cnpj(cnpj[:12])
    dv2 = _dv_cnpj(cnpj[:12] + dv1)
    return cnpj[12:] == dv1 + dv2


def cpf_valido(cpf: str) -> bool:
    cpf = somente_digitos(cpf)
    if len(cpf) != 11 or len(set(cpf)) == 1:
        return False
    for n in (9, 10):
        total = sum(int(cpf[i]) * (n + 1 - i) for i in range(n))
        dv = (total * 10) % 11 % 10
        if dv != int(cpf[n]):
            return False
    return True


def dv_modulo11(base: str) -> int:
    """Check digit of the 43-character access key (pesos 2..9 da direita)."""
    total = 0
    peso = 2
    for c in reversed(base):
        total += _valor(c) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = total % 11
    return 0 if resto < 2 else 11 - resto


def gerar_codigo_numerico(numero: int) -> str:
    """cNF: 8 random digits, which SEFAZ requires to differ from nNF."""
    while True:
        cnf = f"{secrets.randbelow(10**8):08d}"
        if int(cnf) != numero and len(set(cnf)) > 1:
            return cnf


def montar_chave(
    cuf: int, ano_mes: str, cnpj: str, modelo: int, serie: int,
    numero: int, tipo_emissao: int, codigo_numerico: str,
) -> str:
    base = (
        f"{cuf:02d}{ano_mes}{normalizar_cnpj(cnpj):>014}{modelo:02d}"
        f"{serie:03d}{numero:09d}{tipo_emissao}{codigo_numerico}"
    )
    if len(base) != 43:
        raise ValueError(f"Base da chave com tamanho inválido ({len(base)}): {base}")
    return base + str(dv_modulo11(base))


def chave_valida(chave: str) -> bool:
    return (
        len(chave) == 44
        and chave[-1].isdigit()
        and dv_modulo11(chave[:43]) == int(chave[-1])
    )
