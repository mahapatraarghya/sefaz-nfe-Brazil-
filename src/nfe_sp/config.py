"""Settings loaded from environment variables (see .env.example)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .models import Ambiente, Emitente, emitente_from_dict


@dataclass
class Configuracao:
    ambiente: Ambiente
    emitente_arquivo: Path
    pasta_dados: Path
    serie: int
    cert_caminho: Path | None
    cert_senha: str | None
    ca_bundle: str | bool
    pasta_xsd: str | None
    timeout: float

    @classmethod
    def do_ambiente(cls) -> "Configuracao":
        amb = os.environ.get("NFE_AMBIENTE", "homologacao").strip().lower()
        if amb not in ("homologacao", "producao"):
            raise ValueError("NFE_AMBIENTE deve ser 'homologacao' ou 'producao'")
        ca = os.environ.get("NFE_CA_BUNDLE")
        cert = os.environ.get("NFE_CERT_PATH")
        return cls(
            ambiente=Ambiente.PRODUCAO if amb == "producao" else Ambiente.HOMOLOGACAO,
            emitente_arquivo=Path(os.environ.get("NFE_EMITENTE", "emitente.json")),
            pasta_dados=Path(os.environ.get("NFE_DATA_DIR", "data")),
            serie=int(os.environ.get("NFE_SERIE", "1")),
            cert_caminho=Path(cert) if cert else None,
            cert_senha=os.environ.get("NFE_CERT_PASSWORD"),
            ca_bundle=ca if ca else True,
            pasta_xsd=os.environ.get("NFE_XSD_DIR"),
            timeout=float(os.environ.get("NFE_TIMEOUT", "30")),
        )

    def carregar_emitente(self) -> Emitente:
        if not self.emitente_arquivo.exists():
            raise FileNotFoundError(
                f"{self.emitente_arquivo} não encontrado. Copie examples/emitente.json e preencha."
            )
        return emitente_from_dict(json.loads(self.emitente_arquivo.read_text(encoding="utf-8")))
