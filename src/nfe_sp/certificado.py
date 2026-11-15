"""Loading the e-CNPJ A1 certificate (.pfx) and XMLDSig signing as SEFAZ requires.

SEFAZ signature profile (Manual de Orientação do Contribuinte):
  enveloped signature, C14N 1.0 (inclusive, sem comentários), RSA-SHA1,
  digest SHA-1, Reference URI="#<Id>", X509Certificate in KeyInfo.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
SHA1 = "http://www.w3.org/2000/09/xmldsig#sha1"


class AssinaturaInvalida(Exception):
    pass


def _c14n(el: etree._Element) -> bytes:
    return etree.tostring(el, method="c14n", exclusive=False, with_comments=False)


@dataclass
class Certificado:
    chave_privada: rsa.RSAPrivateKey
    certificado: x509.Certificate

    @classmethod
    def from_pfx(cls, caminho: str | Path, senha: str) -> "Certificado":
        dados = Path(caminho).read_bytes()
        chave, cert, _ = pkcs12.load_key_and_certificates(dados, senha.encode())
        if chave is None or cert is None:
            raise ValueError("O arquivo .pfx não contém chave privada e certificado")
        if not isinstance(chave, rsa.RSAPrivateKey):
            raise ValueError("SEFAZ exige certificado com chave RSA")
        return cls(chave, cert)

    @classmethod
    def autoassinado_para_testes(cls, cnpj: str, razao_social: str = "EMPRESA TESTE") -> "Certificado":
        """Throwaway self-signed certificate. Only for the mock SEFAZ — real SEFAZ rejects it."""
        chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{razao_social}:{cnpj}")])
        agora = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(nome).issuer_name(nome)
            .public_key(chave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(agora - timedelta(minutes=5))
            .not_valid_after(agora + timedelta(days=1))
            .sign(chave, hashes.SHA256())
        )
        return cls(chave, cert)

    # --- informações -----------------------------------------------------
    @property
    def titular(self) -> str:
        attrs = self.certificado.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        return attrs[0].value if attrs else ""

    @property
    def cnpj(self) -> str | None:
        """ICP-Brasil e-CNPJ CN is usually 'RAZAO SOCIAL:CNPJ'."""
        cn = self.titular
        return cn.rsplit(":", 1)[1].strip() if ":" in cn else None

    @property
    def validade(self) -> datetime:
        return self.certificado.not_valid_after_utc

    def verificar_validade(self) -> None:
        if datetime.now(timezone.utc) > self.validade:
            raise ValueError(f"Certificado vencido em {self.validade:%d/%m/%Y}")

    # --- PEM para TLS mútuo ---------------------------------------------
    def pem_certificado(self) -> bytes:
        return self.certificado.public_bytes(serialization.Encoding.PEM)

    def pem_chave(self) -> bytes:
        return self.chave_privada.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )

    # --- assinatura ------------------------------------------------------
    def assinar(self, pai: etree._Element, referencia: etree._Element) -> etree._Element:
        """Appends <Signature> to `pai`, signing the `referencia` element (which has an Id)."""
        ref_id = referencia.get("Id")
        if not ref_id:
            raise ValueError("Elemento a assinar não possui atributo Id")
        if pai.find(f"{{{DS}}}Signature") is not None:
            raise ValueError("Elemento já assinado")

        digest = base64.b64encode(hashlib.sha1(_c14n(referencia)).digest()).decode()

        sig = etree.SubElement(pai, f"{{{DS}}}Signature", nsmap={None: DS})
        si = etree.SubElement(sig, f"{{{DS}}}SignedInfo")
        etree.SubElement(si, f"{{{DS}}}CanonicalizationMethod", Algorithm=C14N)
        etree.SubElement(si, f"{{{DS}}}SignatureMethod", Algorithm=RSA_SHA1)
        ref = etree.SubElement(si, f"{{{DS}}}Reference", URI=f"#{ref_id}")
        tr = etree.SubElement(ref, f"{{{DS}}}Transforms")
        etree.SubElement(tr, f"{{{DS}}}Transform", Algorithm=ENVELOPED)
        etree.SubElement(tr, f"{{{DS}}}Transform", Algorithm=C14N)
        etree.SubElement(ref, f"{{{DS}}}DigestMethod", Algorithm=SHA1)
        etree.SubElement(ref, f"{{{DS}}}DigestValue").text = digest

        assinatura = self.chave_privada.sign(_c14n(si), padding.PKCS1v15(), hashes.SHA1())
        etree.SubElement(sig, f"{{{DS}}}SignatureValue").text = base64.b64encode(assinatura).decode()
        x509data = etree.SubElement(etree.SubElement(sig, f"{{{DS}}}KeyInfo"), f"{{{DS}}}X509Data")
        der = self.certificado.public_bytes(serialization.Encoding.DER)
        etree.SubElement(x509data, f"{{{DS}}}X509Certificate").text = base64.b64encode(der).decode()
        return sig


def verificar_assinatura(pai: etree._Element) -> x509.Certificate:
    """Checks digest + RSA signature of a signed NF-e/evento element. Returns the signer cert."""
    sig = pai.find(f"{{{DS}}}Signature")
    if sig is None:
        raise AssinaturaInvalida("Assinatura ausente")
    ns = {"ds": DS}
    ref = sig.find("ds:SignedInfo/ds:Reference", namespaces=ns)
    if ref is None:
        raise AssinaturaInvalida("Reference ausente")
    ref_id = ref.get("URI", "").lstrip("#")
    alvo = next((el for el in pai.iter() if el.get("Id") == ref_id), None)
    if alvo is None:
        raise AssinaturaInvalida(f"Elemento referenciado #{ref_id} não encontrado")

    esperado = sig.findtext("ds:SignedInfo/ds:Reference/ds:DigestValue", namespaces=ns)
    calculado = base64.b64encode(hashlib.sha1(_c14n(alvo)).digest()).decode()
    if esperado != calculado:
        raise AssinaturaInvalida("DigestValue não confere (conteúdo alterado após assinar)")

    der = base64.b64decode(sig.findtext("ds:KeyInfo/ds:X509Data/ds:X509Certificate", namespaces=ns))
    cert = x509.load_der_x509_certificate(der)
    valor = base64.b64decode(sig.findtext("ds:SignatureValue", namespaces=ns))
    si = sig.find("ds:SignedInfo", namespaces=ns)
    try:
        cert.public_key().verify(valor, _c14n(si), padding.PKCS1v15(), hashes.SHA1())
    except Exception as exc:  # cryptography raises InvalidSignature
        raise AssinaturaInvalida("SignatureValue inválido") from exc
    return cert
