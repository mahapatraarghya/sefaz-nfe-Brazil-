"""Optional local XSD validation against the official schema package (PL_009_V4 / PL_010).

Download the schemas from the Portal Nacional da NF-e and point NFE_XSD_DIR at the folder
that contains nfe_v4.00.xsd. Validate the *signed* NFe.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree


@lru_cache(maxsize=4)
def _schema(pasta: str) -> etree.XMLSchema:
    caminho = Path(pasta) / "nfe_v4.00.xsd"
    if not caminho.exists():
        raise FileNotFoundError(f"nfe_v4.00.xsd não encontrado em {pasta}")
    return etree.XMLSchema(etree.parse(str(caminho)))


def validar_xsd(nfe: etree._Element, pasta_xsd: str) -> list[str]:
    schema = _schema(pasta_xsd)
    if schema.validate(nfe):
        return []
    return [f"linha {e.line}: {e.message}" for e in schema.error_log]
