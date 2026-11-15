# nfe-sp — NF-e emitter for São Paulo (Simples Nacional)

Python app that issues **NF-e modelo 55, layout 4.00** directly against **SEFAZ-SP** web services
for companies in **Simples Nacional** (CRT 1, or 4 for MEI).

What it does:

- builds the NF-e XML (ide, emit, dest, items with CSOSN, totals, payment, Simples Nacional text)
- generates the 44-digit access key (supports the 2026 alphanumeric CNPJ)
- signs with your **e-CNPJ A1** certificate (XMLDSig, RSA-SHA1, C14N, as SEFAZ requires)
- sends via SOAP 1.2 over mutual TLS, handling both synchronous and asynchronous (receipt) replies
- stores the legal `procNFe` XML (signed NF-e + protocol) and keeps numbering in SQLite
- service status, protocol lookup, **cancellation**, **number voiding (inutilização)**, taxpayer **cadastro** lookup
- a **mock SEFAZ** so you can develop and test without a certificate or network access

## Quick start (mock, no certificate)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp examples/emitente.json emitente.json      # edit with your company data
nfe-sp --mock status
nfe-sp --mock emitir examples/nota.json
nfe-sp --mock xml examples/nota.json         # print the signed XML without sending
pytest
```

The mock lives in memory, so `consultar`/`cancelar` against `--mock` only work inside one process
(see `tests/test_fluxo.py` for the full emit → consult → cancel flow).

## Homologação (real SEFAZ-SP test environment)

Requirements:

1. An **e-CNPJ A1** certificate (`.pfx`) issued by ICP-Brasil for the issuing CNPJ (A3 tokens don't work on servers).
2. The company's **Inscrição Estadual** in SP, with NF-e issuing enabled at SEFAZ-SP.
3. The **ICP-Brasil certificate chain** as a PEM file (from ITI) in `NFE_CA_BUNDLE`, so TLS to SEFAZ can be verified.
   Never turn verification off.
4. Network access from your machine/server to `homologacao.nfe.fazenda.sp.gov.br`.

```bash
cp .env.example .env   # fill in; never commit .env or the .pfx
set -a; . ./.env; set +a
nfe-sp status          # expect "107 - Servico em Operacao"
nfe-sp emitir examples/nota.json
```

In homologação the recipient name is automatically replaced with
`NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL` (otherwise SEFAZ rejects with 598).

## Produção

Set `NFE_AMBIENTE=producao`. Commands that create fiscal documents (`emitir`, `cancelar`,
`inutilizar`) then also require `--confirmar-producao`. Production and homologação use separate
data folders, so the numbering doesn't mix. If you're moving from another system, set the last number used:

```python
from nfe_sp.armazenamento import Armazenamento
Armazenamento("data/producao").definir_ultimo_numero(serie=1, ultimo=1234)
```

## Invoice JSON

See `examples/nota.json`. Main fields per item: `ncm` (8 digits), `cfop` (5xxx inside SP, 6xxx interstate),
`csosn` (`101`, `102`, `103`, `300`, `400`, `500`), `quantidade`, `valor_unitario`, optional `desconto`, `frete`, `cest`.
Payment `tipo` uses SEFAZ codes (`01` cash, `03` credit card, `04` debit, `15` boleto, `17` PIX, `90` no payment).
For CSOSN 101, set `aliquota_credito_sn` in `emitente.json`; the credit text goes into `infCpl` automatically.

## Rejected invoices

A rejected NF-e (e.g. `cStat` 2xx/5xx/6xx) doesn't use up its number. Fix the data and resend with the same number:
`nfe-sp emitir nota.json --numero 17`. Void a number you won't use with `nfe-sp inutilizar --inicio 17 --fim 17 --justificativa "..."`.

## Optional XSD validation

Download the schema package (PL_009_V4 or newer) from the Portal Nacional da NF-e and set
`NFE_XSD_DIR` to the folder containing `nfe_v4.00.xsd`. Every NF-e is then validated before sending.

## Project layout

| File | Purpose |
|---|---|
| `src/nfe_sp/models.py` | data classes and input validation |
| `src/nfe_sp/documentos.py` | CNPJ/CPF check digits, access key |
| `src/nfe_sp/builder.py` | NF-e XML generation |
| `src/nfe_sp/certificado.py` | `.pfx` loading, XML signing and verification |
| `src/nfe_sp/sefaz.py` | SEFAZ-SP endpoints, SOAP client, response parsing |
| `src/nfe_sp/emissor.py` | issuing flow (build → sign → send → store) |
| `src/nfe_sp/armazenamento.py` | SQLite numbering + XML storage |
| `src/nfe_sp/mock.py` | fake SEFAZ for development |
| `src/nfe_sp/cli.py` | `nfe-sp` command |

## Not handled yet

- DANFE PDF (the printable invoice). The `brazilfiscalreport` library can render it from the stored `procNFe` XML.
- Contingency (SVC-AN/SVC-RS, `tpEmis` 6/7) when SEFAZ-SP is down
- NFC-e (modelo 65), correction letter (CC-e), interstate DIFAL, ICMS-ST (CSOSN 201/202/500 with ST data, 900)
- Reforma Tributária IBS/CBS groups (track the SEFAZ technical notes for when they become mandatory for Simples Nacional)
- Emailing the XML/DANFE to the buyer

## Security

- Keep the `.pfx` and its password out of git (`.gitignore` covers `*.pfx`, `.env`, `data/`).
- Use a secrets manager in production. The certificate is written to a temporary `0600` file only during the TLS session.
- Keep the authorized XMLs for at least 5 years (legal requirement).

Verify the SEFAZ-SP endpoint URLs in `sefaz.py` against the Portal Nacional's list of services before going live.
