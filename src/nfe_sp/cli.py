"""Command line: nfe-sp {status,emitir,xml,consultar,cancelar,inutilizar,cadastro}."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lxml import etree

from .armazenamento import Armazenamento
from .certificado import Certificado
from .config import Configuracao
from .emissor import Emissor, ErroValidacao
from .mock import SefazFake
from .models import Ambiente, nota_from_dict
from .sefaz import ClienteSefaz, TransporteHttp


def _contexto(args, cfg: Configuracao):
    emitente = cfg.carregar_emitente()
    if args.mock:
        cert = Certificado.autoassinado_para_testes(emitente.cnpj, emitente.razao_social)
        transporte = SefazFake()
        pasta = cfg.pasta_dados / "mock"
    else:
        if not cfg.cert_caminho or cfg.cert_senha is None:
            sys.exit("Defina NFE_CERT_PATH e NFE_CERT_PASSWORD (ou use --mock).")
        cert = Certificado.from_pfx(cfg.cert_caminho, cfg.cert_senha)
        transporte = TransporteHttp(cert, cfg.ca_bundle, cfg.timeout)
        pasta = cfg.pasta_dados / ("producao" if cfg.ambiente == Ambiente.PRODUCAO else "homologacao")
    cliente = ClienteSefaz(cfg.ambiente, cert, transporte)
    return emitente, cert, cliente, Armazenamento(pasta)


def _exigir_confirmacao(args, cfg: Configuracao) -> None:
    if cfg.ambiente == Ambiente.PRODUCAO and not args.mock and not args.confirmar_producao:
        sys.exit("Ambiente de PRODUÇÃO: documentos com validade fiscal. Repita com --confirmar-producao.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nfe-sp", description="Emissor de NF-e para SP (Simples Nacional)")
    p.add_argument("--mock", action="store_true", help="usa a SEFAZ simulada local (sem certificado/rede)")
    p.add_argument("--confirmar-producao", action="store_true", help="necessário para emitir/cancelar em produção")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="consulta o status do serviço da SEFAZ-SP")
    e = sub.add_parser("emitir", help="emite uma NF-e a partir de um JSON")
    e.add_argument("arquivo", type=Path)
    e.add_argument("--numero", type=int, help="reutiliza um número (ex.: reenvio após rejeição)")
    x = sub.add_parser("xml", help="gera e assina o XML sem enviar (dry-run)")
    x.add_argument("arquivo", type=Path)
    x.add_argument("--numero", type=int, default=1)
    c = sub.add_parser("consultar", help="consulta situação de uma NF-e pela chave")
    c.add_argument("chave")
    k = sub.add_parser("cancelar", help="cancela uma NF-e autorizada")
    k.add_argument("chave")
    k.add_argument("--justificativa", required=True)
    i = sub.add_parser("inutilizar", help="inutiliza faixa de numeração não utilizada")
    i.add_argument("--inicio", type=int, required=True)
    i.add_argument("--fim", type=int, required=True)
    i.add_argument("--justificativa", required=True)
    cad = sub.add_parser("cadastro", help="consulta cadastro de contribuinte (CNPJ) na SEFAZ")
    cad.add_argument("cnpj")
    args = p.parse_args(argv)

    cfg = Configuracao.do_ambiente()
    emitente, cert, cliente, store = _contexto(args, cfg)
    emissor = Emissor(cliente, cert, store, cfg.pasta_xsd)
    amb = "MOCK" if args.mock else cfg.ambiente.name
    print(f"[{amb}] emitente {emitente.cnpj} — certificado: {cert.titular} (válido até {cert.validade:%d/%m/%Y})",
          file=sys.stderr)

    try:
        if args.cmd == "status":
            r = cliente.status_servico()
            print(f"{r.cstat} - {r.motivo}")
            return 0 if r.cstat == "107" else 1

        if args.cmd in ("emitir", "xml"):
            dados = json.loads(args.arquivo.read_text(encoding="utf-8"))
            if args.cmd == "xml":
                nota = nota_from_dict(dados, emitente, args.numero, cfg.serie)
                nfe, chave = emissor.preparar(nota)
                print(etree.tostring(nfe, encoding="unicode", pretty_print=True))
                print(f"chave: {chave}  (XML formatado só para leitura: reformatar invalida a assinatura)", file=sys.stderr)
                return 0
            _exigir_confirmacao(args, cfg)
            numero = args.numero or store.proximo_numero(cfg.serie)
            nota = nota_from_dict(dados, emitente, numero, cfg.serie)
            res = emissor.emitir(nota)
            print(f"NF-e nº {res.numero} série {cfg.serie}\nchave: {res.chave}\n{res.cstat} - {res.motivo}")
            if res.autorizada:
                print(f"protocolo: {res.protocolo.numero}\nXML: {res.arquivo}")
                return 0
            print(f"Não autorizada. Corrija e reenvie com --numero {res.numero}, ou inutilize o número.")
            return 1

        if args.cmd == "consultar":
            r, prot = cliente.consultar_protocolo(args.chave)
            print(f"{r.cstat} - {r.motivo}")
            if prot:
                print(f"protocolo: {prot.numero} ({prot.cstat})")
            return 0

        if args.cmd == "cancelar":
            _exigir_confirmacao(args, cfg)
            cstat, motivo = emissor.cancelar(args.chave, args.justificativa, emitente.cnpj)
            print(f"{cstat} - {motivo}")
            return 0 if cstat in ("135", "155") else 1

        if args.cmd == "inutilizar":
            _exigir_confirmacao(args, cfg)
            r = cliente.inutilizar(emitente.cnpj, cfg.serie, args.inicio, args.fim, args.justificativa)
            print(f"{r.cstat} - {r.motivo}")
            return 0 if r.cstat == "102" else 1

        if args.cmd == "cadastro":
            r = cliente.consultar_cadastro(args.cnpj)
            print(f"{r.cstat} - {r.motivo}")
            for tag in ("xNome", "IE", "cSit", "xRegApur", "CNAE"):
                if r.txt(tag):
                    print(f"  {tag}: {r.txt(tag)}")
            return 0
    except (ErroValidacao, ValueError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
        close = getattr(cliente.transporte, "close", None)
        if close:
            close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
