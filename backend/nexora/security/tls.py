"""Certificado autofirmado del PC para cifrar el tráfico en la red local.

El teléfono no confía en ninguna autoridad: compara la huella SHA-256 del certificado con la que leyó del QR
(certificate pinning).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Certificado:
    certificado: Path
    clave: Path
    huella: str  # SHA-256 del certificado en DER, en hexadecimal


def huella_de(ruta_pem: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    cert = x509.load_pem_x509_certificate(ruta_pem.read_bytes())
    return hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()


def asegurar_certificado(carpeta: Path, nombre: str) -> Certificado:
    certificado, clave = carpeta / "nexora.crt", carpeta / "nexora.key"
    if not (certificado.is_file() and clave.is_file()):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        carpeta.mkdir(parents=True, exist_ok=True)
        privada = ec.generate_private_key(ec.SECP256R1())
        sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombre)])
        ahora = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(sujeto).issuer_name(sujeto)
            .public_key(privada.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(ahora - dt.timedelta(days=1))
            .not_valid_after(ahora + dt.timedelta(days=3650))  # la confianza la da la huella, no la caducidad
            .sign(privada, hashes.SHA256())
        )
        clave.write_bytes(privada.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ))
        certificado.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return Certificado(certificado, clave, huella_de(certificado))
