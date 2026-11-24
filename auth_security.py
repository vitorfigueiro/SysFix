import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing

from config import get_secret_key, get_token_ttl_hours

_BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


# ==============================================================================
# TOTP (RFC 6238) + QR CODE SVG DATA URI
# ==============================================================================


def generate_totp_secret(length: int = 32) -> str:
    """Gera segredo Base32 criptograficamente seguro compatível com Google Authenticator/Authy."""
    return "".join(secrets.choice(_BASE32_ALPHABET) for _ in range(max(16, length)))


def _decode_base32_secret(secret: str) -> bytes:
    clean = "".join(str(secret or "").strip().upper().split())
    if not clean:
        raise ValueError("Segredo TOTP vazio.")
    padding = "=" * (-len(clean) % 8)
    return base64.b32decode(clean + padding, casefold=True)


def compute_totp(
    secret: str,
    for_time: int | float | None = None,
    step: int = 30,
    digits: int = 6,
) -> str:
    """Calcula código TOTP RFC 6238 (HMAC-SHA1, janela de 30s, 6 dígitos)."""
    key = _decode_base32_secret(secret)
    ts = int(time.time() if for_time is None else for_time)
    counter = int(ts // step)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = (
        ((digest[offset] & 0x7F) << 24)
        | ((digest[offset + 1] & 0xFF) << 16)
        | ((digest[offset + 2] & 0xFF) << 8)
        | (digest[offset + 3] & 0xFF)
    )
    otp = binary % (10**digits)
    return str(otp).zfill(digits)


def verify_totp(
    secret: str,
    code: str,
    window: int = 1,
    for_time: int | float | None = None,
    step: int = 30,
) -> bool:
    """Valida código TOTP de 6 dígitos aceitando desvio de +/- `window` janelas."""
    clean_code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(clean_code) != 6 or not secret:
        return False
    ts = int(time.time() if for_time is None else for_time)
    for delta in range(-window, window + 1):
        expected = compute_totp(secret, for_time=ts + (delta * step), step=step)
        if hmac.compare_digest(expected, clean_code):
            return True
    return False


def build_otpauth_uri(secret: str, email: str, issuer: str = "SysFix") -> str:
    label = urllib.parse.quote(f"{issuer}:{email}")
    issuer_q = urllib.parse.quote(issuer)
    secret_clean = "".join(str(secret or "").strip().upper().split())
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret_clean}&issuer={issuer_q}&algorithm=SHA1&digits=6&period=30"
    )


def generate_qr_data_uri(content: str, size: int = 190) -> str:
    """Gera QR Code SVG nativo via ReportLab e devolve como data URI SVG base64."""
    widget = qr.QrCodeWidget(content)
    bounds = widget.getBounds()
    w = max(1.0, bounds[2] - bounds[0])
    h = max(1.0, bounds[3] - bounds[1])
    drawing = Drawing(
        size,
        size,
        transform=[size / w, 0, 0, size / h, -bounds[0] * (size / w), -bounds[1] * (size / h)],
    )
    drawing.add(widget)
    svg_text = renderSVG.drawToString(drawing)
    svg_bytes = svg_text.encode("utf-8") if isinstance(svg_text, str) else svg_text
    return "data:image/svg+xml;base64," + base64.b64encode(svg_bytes).decode("ascii")


# ==============================================================================
# CÓDIGOS DE RECUPERAÇÃO DE USO ÚNICO
# ==============================================================================


def generate_recovery_codes(count: int = 8) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes: list[str] = []
    for _ in range(count):
        part1 = "".join(secrets.choice(alphabet) for _ in range(4))
        part2 = "".join(secrets.choice(alphabet) for _ in range(4))
        codes.append(f"{part1}-{part2}")
    return codes


def _normalize_recovery_code(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def hash_recovery_code(code: str) -> str:
    clean = _normalize_recovery_code(code)
    salt = os.urandom(12)
    digest = hashlib.pbkdf2_hmac("sha256", clean.encode("utf-8"), salt, 120_000)
    return f"rc_sha256${salt.hex()}${digest.hex()}"


def verify_and_consume_recovery_code(
    stored_hashes: list[str] | None, code: str
) -> tuple[bool, list[str]]:
    clean = _normalize_recovery_code(code)
    if len(clean) != 8 or not isinstance(stored_hashes, list):
        return False, list(stored_hashes or [])

    remaining = list(stored_hashes)
    for idx, entry in enumerate(remaining):
        try:
            scheme, salt_hex, hash_hex = str(entry).split("$", 2)
            if scheme != "rc_sha256":
                continue
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
        except (ValueError, TypeError):
            continue
        computed = hashlib.pbkdf2_hmac("sha256", clean.encode("utf-8"), salt, 120_000)
        if hmac.compare_digest(computed, expected):
            del remaining[idx]
            return True, remaining

    return False, remaining


# ==============================================================================
# TOKENS ASSINADOS COM PROPÓSITO ESPECÍFICO (MFA CHALLENGE / MFA VERIFIED)
# ==============================================================================


def _sign_scoped_payload(payload: dict[str, Any]) -> str:
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    sig = hmac.new(
        get_secret_key().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def _verify_scoped_token(token: str, expected_purpose: str) -> dict[str, Any]:
    if not token or "." not in token:
        raise ValueError("Token de verificação inválido.")
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(
            get_secret_key().encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).digest()
        provided_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, provided_sig):
            raise ValueError("Assinatura do token inválida.")
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Token malformado.") from exc

    if payload.get("purpose") != expected_purpose:
        raise ValueError("Propósito do token incompatível.")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("Desafio ou verificação MFA expirada. Faça login novamente.")
    return payload


def create_mfa_challenge_token(user: dict, ttl_seconds: int = 300) -> str:
    now = int(time.time())
    payload = {
        "purpose": "mfa_challenge",
        "uid": user["uid"],
        "tenant_id": user["tenant_id"],
        "email": user.get("email", ""),
        "sv": int(user.get("session_version") or 1),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return _sign_scoped_payload(payload)


def verify_mfa_challenge_token(token: str) -> dict[str, Any]:
    return _verify_scoped_token(token, "mfa_challenge")


def create_mfa_verified_token(user: dict, ttl_seconds: int | None = None) -> str:
    now = int(time.time())
    ttl = ttl_seconds if ttl_seconds is not None else get_token_ttl_hours() * 3600
    payload = {
        "purpose": "mfa_verified",
        "uid": user["uid"],
        "tenant_id": user["tenant_id"],
        "sv": int(user.get("session_version") or 1),
        "iat": now,
        "exp": now + ttl,
    }
    return _sign_scoped_payload(payload)


def verify_mfa_verified_token(
    token: str, expected_uid: str, expected_sv: int = 1
) -> bool:
    if not token:
        return False
    try:
        payload = _verify_scoped_token(token, "mfa_verified")
    except ValueError:
        return False
    return (
        payload.get("uid") == expected_uid
        and int(payload.get("sv") or 1) == int(expected_sv or 1)
    )


# ==============================================================================
# RATE LIMITER ANTI-FORÇA BRUTA & AUDITORIA DE ACESSOS
# ==============================================================================


class LoginRateLimiter:
    """Controle em memória thread-safe contra força bruta por conta/IP."""

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 900,
        lockout_seconds: int = 300,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._lock = threading.RLock()
        self._attempts: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}

    def check_allowed(self, key: str) -> tuple[bool, int]:
        if not key:
            return True, 0
        now = time.time()
        with self._lock:
            blocked_until = self._blocked_until.get(key, 0.0)
            if blocked_until > now:
                return False, max(1, int(blocked_until - now))
            if blocked_until:
                self._blocked_until.pop(key, None)
            return True, 0

    def record_failure(self, key: str) -> int:
        if not key:
            return 0
        now = time.time()
        with self._lock:
            cutoff = now - self.window_seconds
            entries = [t for t in self._attempts.get(key, []) if t >= cutoff]
            entries.append(now)
            self._attempts[key] = entries
            if len(entries) >= self.max_attempts:
                self._blocked_until[key] = now + self.lockout_seconds
                self._attempts.pop(key, None)
                return self.lockout_seconds
            return 0

    def reset(self, key: str) -> None:
        if not key:
            return
        with self._lock:
            self._attempts.pop(key, None)
            self._blocked_until.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()
            self._blocked_until.clear()


login_rate_limiter = LoginRateLimiter()


def build_login_audit_entry(
    status_label: str,
    ip: str = "",
    user_agent: str = "",
    metodo: str = "senha",
) -> dict[str, str]:
    ua_clean = " ".join(str(user_agent or "").split())[:120]
    ip_clean = str(ip or "").strip()[:64] or "desconhecido"
    return {
        "data_hora": _now_iso(),
        "status": str(status_label or "Sucesso")[:40],
        "metodo": str(metodo or "senha")[:30],
        "ip": ip_clean,
        "dispositivo": ua_clean or "Navegador / API",
    }


def append_login_audit(
    existing_list: Any, entry: dict[str, str], max_items: int = 15
) -> list[dict[str, str]]:
    lista = [dict(x) for x in existing_list if isinstance(x, dict)] if isinstance(existing_list, list) else []
    lista.insert(0, entry)
    return lista[:max_items]
