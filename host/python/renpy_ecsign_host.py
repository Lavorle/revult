"""
Host pure-Python stand-in for renpy.ecsign.

Stock ecsign is a Cython extension over OpenSSL (ec_sign_core.c). On systems
with OpenSSL 3.x default policy, EVP_DigestSignInit_ex(..., "SHA1") fails with
`invalid digest`, so sign_data raises Exception("Failed to sign data").

This module preserves the stock wire format exactly:
  - curve: P-256 (SECP256R1)
  - digest: SHA-1
  - signature: 64-byte raw R||S (not DER)

Installed into sys.modules["renpy.ecsign"] by host/renpy-host before
renpy.import_all / savetoken.init so product code uses this path.

Imports from cryptography stay *inside* functions so renpy.backup.backup()
can pickle this module (top-level cryptography C helpers are not picklable).
"""

from __future__ import annotations


def generate_private_key() -> bytes | None:
    """Generate an EC P-256 private key and return it in DER format.

    Emits TraditionalOpenSSL/SEC1 DER to match stock OpenSSL OSSL_ENCODER
    (EVP_PKEY_KEYPAIR, DER) output size/shape (~121 bytes for P-256).
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = ec.generate_private_key(ec.SECP256R1())
        return key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    except Exception:
        return None


def _load_private_key(private_key: bytes):
    from cryptography.hazmat.primitives import serialization

    # load_der_private_key accepts both PKCS#8 and SEC1/TraditionalOpenSSL.
    return serialization.load_der_private_key(private_key, password=None)


def _load_public_key(public_key: bytes):
    from cryptography.hazmat.primitives import serialization

    return serialization.load_der_public_key(public_key)


def _raw_rs_from_der(der_sig: bytes) -> bytes:
    """Pack DER ECDSA signature into 64-byte raw R||S (big-endian, 32 each)."""
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    r, s = decode_dss_signature(der_sig)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _der_from_raw_rs(sign: bytes) -> bytes:
    """Unpack 64-byte raw R||S into DER ECDSA signature."""
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    if len(sign) != 64:
        raise ValueError(f"sign size is {len(sign)} bytes, but expect 64 bytes")
    r = int.from_bytes(sign[:32], "big")
    s = int.from_bytes(sign[32:], "big")
    return encode_dss_signature(r, s)


def sign_data(data: bytes, private_key: bytes) -> bytes:
    """Return ECDSA-SHA1 signature as 64-byte raw R||S."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        key = _load_private_key(private_key)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise Exception("Failed to sign data")
        der_sig = key.sign(data, ec.ECDSA(hashes.SHA1()))
        return _raw_rs_from_der(der_sig)
    except Exception:
        raise Exception("Failed to sign data")


def verify_data(data: bytes, public_key: bytes, sign: bytes) -> bool:
    """Verify ECDSA-SHA1 signature (64-byte raw R||S)."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        if len(sign) != 64:
            return False
        key = _load_public_key(public_key)
        if not isinstance(key, ec.EllipticCurvePublicKey):
            return False
        der_sig = _der_from_raw_rs(sign)
        key.verify(der_sig, data, ec.ECDSA(hashes.SHA1()))
        return True
    except Exception:
        return False


def get_public_key_from_private(private_key: bytes) -> bytes | None:
    """Return public key in DER (SubjectPublicKeyInfo) from private key DER."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = _load_private_key(private_key)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            return None
        return key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception:
        return None


def validate_private_key(private_key: bytes) -> bool:
    """True if *private_key* is a loadable EC private key DER."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec

        key = _load_private_key(private_key)
        return isinstance(key, ec.EllipticCurvePrivateKey)
    except Exception:
        return False


def validate_public_key(public_key: bytes) -> bool:
    """True if *public_key* is a loadable EC public key DER."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec

        key = _load_public_key(public_key)
        return isinstance(key, ec.EllipticCurvePublicKey)
    except Exception:
        return False


def _pem_lines(contents: bytes):
    in_pem_part = False
    seen_pem_start = False

    for line in contents.splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith(b"-----BEGIN"):
            if in_pem_part:
                raise ValueError("Seen start marker twice")
            in_pem_part = True
            seen_pem_start = True
            continue

        if not in_pem_part:
            continue

        if in_pem_part and line.startswith(b"-----END"):
            in_pem_part = False
            break

        if b":" in line:
            continue

        yield line

    if not seen_pem_start:
        raise ValueError("No PEM start marker found")

    if in_pem_part:
        raise ValueError("No PEM end marker found")


def pem_to_der(pem: bytes | str) -> bytes:
    import base64

    if isinstance(pem, str):
        pem = pem.encode()

    d = b"".join(_pem_lines(pem))
    return base64.b64decode(d)


def der_to_pem(der: bytes, name: str) -> bytes:
    import base64

    b64 = base64.b64encode(der)
    lines = [(f"-----BEGIN {name} KEY-----\n").encode()]
    lines.extend(
        [b64[start : start + 76] + b"\n" for start in range(0, len(b64), 76)]
    )
    lines.append((f"-----END {name} KEY-----\n").encode())
    return b"".join(lines)
