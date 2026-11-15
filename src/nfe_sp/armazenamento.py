"""SQLite store for invoice numbering and issued documents.

SEFAZ forbids reusing a number that was authorized or denied, so numbering must be
persistent and atomic. Numbers reserved but rejected can be retried (same number)
or voided with `inutilizar`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class Armazenamento:
    def __init__(self, pasta: Path):
        self.pasta = Path(pasta)
        (self.pasta / "xml").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.pasta / "nfe.sqlite3", isolation_level=None)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS numeracao (serie INTEGER PRIMARY KEY, ultimo INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS notas (
                chave TEXT PRIMARY KEY, serie INTEGER, numero INTEGER, status TEXT,
                cstat TEXT, motivo TEXT, protocolo TEXT, arquivo TEXT, atualizado_em TEXT
            );
            """
        )

    def proximo_numero(self, serie: int) -> int:
        cur = self.db.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            row = cur.execute("SELECT ultimo FROM numeracao WHERE serie = ?", (serie,)).fetchone()
            numero = (row[0] if row else 0) + 1
            cur.execute(
                "INSERT INTO numeracao (serie, ultimo) VALUES (?, ?) "
                "ON CONFLICT(serie) DO UPDATE SET ultimo = excluded.ultimo",
                (serie, numero),
            )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        return numero

    def definir_ultimo_numero(self, serie: int, ultimo: int) -> None:
        """Use when migrating from another system: the next NF-e will be ultimo + 1."""
        self.db.execute(
            "INSERT INTO numeracao (serie, ultimo) VALUES (?, ?) "
            "ON CONFLICT(serie) DO UPDATE SET ultimo = excluded.ultimo",
            (serie, ultimo),
        )

    def registrar(self, chave: str, serie: int, numero: int, status: str, cstat: str | None,
                  motivo: str | None, protocolo: str | None = None, xml: str | None = None,
                  sufixo: str = "procNFe") -> Path | None:
        arquivo = None
        if xml:
            arquivo = self.pasta / "xml" / f"{chave}-{sufixo}.xml"
            arquivo.write_text(xml, encoding="utf-8")
        self.db.execute(
            "INSERT INTO notas (chave, serie, numero, status, cstat, motivo, protocolo, arquivo, atualizado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(chave) DO UPDATE SET "
            "status=excluded.status, cstat=excluded.cstat, motivo=excluded.motivo, "
            "protocolo=COALESCE(excluded.protocolo, notas.protocolo), "
            "arquivo=CASE WHEN excluded.arquivo LIKE '%-procNFe.xml' THEN excluded.arquivo "
            "ELSE COALESCE(notas.arquivo, excluded.arquivo) END, atualizado_em=excluded.atualizado_em",
            (chave, serie, numero, status, cstat, motivo, protocolo,
             str(arquivo) if arquivo else None, datetime.now().isoformat(timespec="seconds")),
        )
        return arquivo

    def buscar(self, chave: str) -> dict | None:
        cur = self.db.execute("SELECT * FROM notas WHERE chave = ?", (chave,))
        row = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None

    def close(self) -> None:
        self.db.close()
