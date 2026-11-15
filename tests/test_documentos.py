from nfe_sp import documentos as d


def test_cnpj_numerico():
    assert d.cnpj_valido("11.222.333/0001-81")
    assert not d.cnpj_valido("11.222.333/0001-82")
    assert not d.cnpj_valido("00000000000000")


def test_cnpj_alfanumerico_exemplo_receita():
    # exemplo oficial da Receita Federal para o CNPJ alfanumérico
    assert d.cnpj_valido("12.ABC.345/01DE-35")
    assert not d.cnpj_valido("12.ABC.345/01DE-36")


def test_cpf():
    assert d.cpf_valido("123.456.789-09")
    assert not d.cpf_valido("123.456.789-00")
    assert not d.cpf_valido("111.111.111-11")


def test_dv_modulo11_calculado_a_mao():
    # 43 dígitos '1': soma = 1 * (pesos 2..9 repetidos) = 5*44 + (2+3+4) = 229
    # 229 % 11 = 9  ->  dv = 11 - 9 = 2
    assert d.dv_modulo11("1" * 43) == 2


def test_montar_chave():
    chave = d.montar_chave(35, "2611", "11222333000181", 55, 1, 42, 1, "12345678")
    assert len(chave) == 44
    assert chave.startswith("35261111222333000181550010000000421" + "12345678")
    assert d.chave_valida(chave)
    assert not d.chave_valida(chave[:-1] + str((int(chave[-1]) + 1) % 10))


def test_codigo_numerico_difere_do_numero():
    for _ in range(50):
        cnf = d.gerar_codigo_numerico(42)
        assert len(cnf) == 8 and int(cnf) != 42
