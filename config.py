import os
import secrets
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent


def load_dotenv_file(env_path: Path | None = None) -> None:
    """Carrega variáveis de um arquivo .env sem sobrescrever variáveis do ambiente."""
    target = env_path or (BASE_DIR / ".env")
    if not target.exists() or not target.is_file():
        return

    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_dotenv_file()


def get_data_dir() -> Path:
    return Path(os.getenv("SYSFIX_DATA_DIR", BASE_DIR / "data"))


def is_firebase_configured() -> bool:
    service_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
    if service_path and Path(service_path).expanduser().exists():
        return True
    if os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip():
        return True
    gac = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if gac and Path(gac).expanduser().exists():
        return True
    return False


def get_storage_backend() -> str:
    mode = os.getenv("SYSFIX_BACKEND", "auto").strip().lower()
    if mode == "firebase":
        return "firebase"
    if mode == "local":
        return "local"
    return "firebase" if is_firebase_configured() else "local"


def get_firebase_web_config() -> dict[str, str] | None:
    api_key = os.getenv("FIREBASE_WEB_API_KEY", "").strip()
    project_id = os.getenv("FIREBASE_WEB_PROJECT_ID", "").strip()
    if not api_key or not project_id:
        return None

    return {
        "apiKey": api_key,
        "authDomain": os.getenv("FIREBASE_WEB_AUTH_DOMAIN", "").strip()
        or f"{project_id}.firebaseapp.com",
        "projectId": project_id,
        "storageBucket": os.getenv("FIREBASE_WEB_STORAGE_BUCKET", "").strip()
        or f"{project_id}.appspot.com",
        "messagingSenderId": os.getenv(
            "FIREBASE_WEB_MESSAGING_SENDER_ID", ""
        ).strip(),
        "appId": os.getenv("FIREBASE_WEB_APP_ID", "").strip(),
    }


def get_secret_key() -> str:
    env_secret = os.getenv("SYSFIX_SECRET_KEY", "").strip()
    if env_secret:
        return env_secret

    data_dir = get_data_dir()
    key_file = data_dir / ".secret_key"
    if key_file.exists():
        stored = key_file.read_text(encoding="utf-8").strip()
        if stored:
            return stored

    data_dir.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_hex(32)
    key_file.write_text(generated, encoding="utf-8")
    return generated


def get_token_ttl_hours() -> int:
    raw = os.getenv("SYSFIX_TOKEN_TTL_HOURS", "24").strip()
    try:
        hours = int(raw)
        return max(1, min(hours, 24 * 30))
    except ValueError:
        return 24


def get_cors_origins() -> list[str]:
    raw = os.getenv("SYSFIX_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_public_client_config() -> dict[str, Any]:
    backend = get_storage_backend()
    web_cfg = get_firebase_web_config()
    auth_mode = "firebase" if (backend == "firebase" and web_cfg) else "local"
    return {
        "auth_mode": auth_mode,
        "storage_backend": backend,
        "firebase_web": web_cfg if auth_mode == "firebase" else None,
        "app_version": "2.0.0",
    }
