import base64
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_data_dir, get_secret_key, get_storage_backend, get_token_ttl_hours
from security import SecurityValidator

_AUTH_LOCK = threading.RLock()
_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DEFAULT_LOCATIONS = [
    "Bancada TI",
    "PlayLan",
    "Central Informática",
    "Aguardando Peça",
]
ROLES_PERMITIDAS = {"admin", "tecnico"}

PERMISSIONS_CATALOG = [
    {
        "key": "ver_dashboard",
        "label": "Ver Dashboard",
        "descricao": "Acessar indicadores, gráficos e alertas de SLA.",
        "default_tecnico": True,
    },
    {
        "key": "ver_custos",
        "label": "Ver e Informar Custos (R$)",
        "descricao": "Visualizar e lançar valores financeiros nas O.S. e relatórios.",
        "default_tecnico": True,
    },
    {
        "key": "registrar_entrada",
        "label": "Registrar Entradas",
        "descricao": "Cadastrar novos equipamentos e entradas em lote.",
        "default_tecnico": True,
    },
    {
        "key": "editar_entrada",
        "label": "Editar Entradas",
        "descricao": "Alterar dados de coleta de equipamentos já cadastrados.",
        "default_tecnico": True,
    },
    {
        "key": "registrar_saida",
        "label": "Dar Saída / Entregar",
        "descricao": "Finalizar ordens de serviço e registrar entrega.",
        "default_tecnico": True,
    },
    {
        "key": "excluir_registros",
        "label": "Excluir Registros",
        "descricao": "Apagar permanentemente registros de equipamentos.",
        "default_tecnico": False,
    },
    {
        "key": "exportar_pdf",
        "label": "Exportar Relatórios PDF",
        "descricao": "Gerar e baixar relatórios em PDF da empresa.",
        "default_tecnico": True,
    },
    {
        "key": "gerenciar_equipe",
        "label": "Gerenciar Equipe e Empresa",
        "descricao": "Editar configurações da empresa e gerenciar colaboradores.",
        "default_tecnico": False,
    },
    {
        "key": "importar_exportar_dados",
        "label": "Importar / Exportar Backup",
        "descricao": "Importar planilhas/JSON e exportar backups completos.",
        "default_tecnico": False,
    },
]

DEFAULT_TECH_PERMISSIONS: dict[str, bool] = {
    item["key"]: bool(item["default_tecnico"]) for item in PERMISSIONS_CATALOG
}
ALL_PERMISSIONS_TRUE: dict[str, bool] = {
    item["key"]: True for item in PERMISSIONS_CATALOG
}
_VALID_PERMISSION_KEYS = set(ALL_PERMISSIONS_TRUE.keys())


def resolver_permissoes(
    role: str, custom: dict[str, Any] | None = None
) -> dict[str, bool]:
    if str(role or "").strip().lower() == "admin":
        return dict(ALL_PERMISSIONS_TRUE)
    resolved = dict(DEFAULT_TECH_PERMISSIONS)
    if isinstance(custom, dict):
        for key, val in custom.items():
            if key in _VALID_PERMISSION_KEYS and isinstance(val, bool):
                resolved[key] = val
    return resolved


def normalizar_permissoes_input(
    permissoes: dict[str, Any] | None,
) -> dict[str, bool]:
    if permissoes is None:
        return dict(DEFAULT_TECH_PERMISSIONS)
    if not isinstance(permissoes, dict):
        raise ValueError("Formato de permissões inválido.")
    unknown = set(permissoes.keys()) - _VALID_PERMISSION_KEYS
    if unknown:
        raise ValueError(
            f"Permissões desconhecidas: {', '.join(sorted(unknown))}."
        )
    resolved = dict(DEFAULT_TECH_PERMISSIONS)
    for key, val in permissoes.items():
        resolved[key] = bool(val)
    return resolved


def usuario_tem_permissao(user: dict[str, Any], perm_key: str) -> bool:
    if not user:
        return False
    if str(user.get("role") or "").strip().lower() == "admin":
        return True
    perms = resolver_permissoes(
        user.get("role", "tecnico"), user.get("permissoes")
    )
    return bool(perms.get(perm_key, False))


class AuthenticationError(Exception):
    """Token ausente, inválido ou expirado."""


class AuthorizationError(Exception):
    """Usuário sem permissão ou conta desativada."""


class OnboardingRequiredError(Exception):
    """Usuário autenticado no Firebase ainda sem empresa vinculada."""

    def __init__(self, identity: dict[str, Any]):
        super().__init__("Finalize o cadastro da sua empresa para continuar.")
        self.identity = identity


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validar_email(email: str) -> str:
    clean = SecurityValidator.sanitizar_texto(email).lower()
    if not clean or not _EMAIL_REGEX.match(clean):
        raise ValueError("Informe um endereço de e-mail válido.")
    return clean


def _validar_senha(senha: str) -> str:
    if not isinstance(senha, str) or len(senha) < 6:
        raise ValueError("A senha deve ter pelo menos 6 caracteres.")
    return senha


def _validar_role(role: str) -> str:
    clean = SecurityValidator.sanitizar_texto(role).lower() or "tecnico"
    if clean not in ROLES_PERMITIDAS:
        raise ValueError("Papel inválido. Use 'admin' ou 'tecnico'.")
    return clean


def _normalizar_localizacoes(localizacoes: list[str] | None) -> list[str]:
    if not localizacoes:
        return list(DEFAULT_LOCATIONS)
    limpas: list[str] = []
    vistas: set[str] = set()
    for item in localizacoes:
        texto = SecurityValidator.sanitizar_texto(item)
        if texto and texto.lower() not in vistas:
            vistas.add(texto.lower())
            limpas.append(texto)
    if not limpas:
        raise ValueError("Informe pelo menos uma localização válida.")
    return limpas


def sanitizar_usuario(user: dict) -> dict:
    """Remove dados sensíveis (ex.: password_hash) antes de expor o usuário na API."""
    role = user.get("role", "tecnico")
    return {
        "uid": user.get("uid", ""),
        "email": user.get("email", ""),
        "nome": user.get("nome", ""),
        "role": role,
        "tenant_id": user.get("tenant_id", ""),
        "ativo": bool(user.get("ativo", True)),
        "permissoes": resolver_permissoes(role, user.get("permissoes")),
        "criado_em": user.get("criado_em", ""),
    }


def sanitizar_tenant(tenant: dict) -> dict:
    return {
        "id": tenant.get("id", ""),
        "nome": tenant.get("nome", "SysFix"),
        "plano": tenant.get("plano", "pro"),
        "ativo": bool(tenant.get("ativo", True)),
        "sla_dias": int(tenant.get("sla_dias") or 3),
        "localizacoes": list(tenant.get("localizacoes") or DEFAULT_LOCATIONS),
        "criado_em": tenant.get("criado_em", ""),
    }


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 260_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iter_str, salt_hex, hash_hex = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False

    computed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(computed, expected)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def create_local_token(user: dict) -> str:
    now = int(time.time())
    ttl_seconds = get_token_ttl_hours() * 3600
    payload = {
        "uid": user["uid"],
        "tenant_id": user["tenant_id"],
        "role": user["role"],
        "email": user["email"],
        "iat": now,
        "exp": now + ttl_seconds,
    }
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    sig = hmac.new(
        get_secret_key().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def verify_local_token(token: str) -> dict[str, Any]:
    if not token or "." not in token:
        raise AuthenticationError("Token de sessão inválido.")
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(
            get_secret_key().encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).digest()
        provided_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, provided_sig):
            raise AuthenticationError("Assinatura do token inválida.")
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError("Token de sessão malformado.") from exc

    if int(payload.get("exp") or 0) < int(time.time()):
        raise AuthenticationError("Sessão expirada. Faça login novamente.")
    if not payload.get("uid"):
        raise AuthenticationError("Token sem identificador de usuário.")
    return payload


class LocalTenantUserStore:
    """Armazenamento local em JSON de empresas (tenants) e usuários."""

    def __init__(self, path: str | Path | None = None):
        self._custom_path = Path(path) if path else None

    @property
    def path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "auth_db.json"

    @staticmethod
    def _empty_data() -> dict:
        return {"version": 1, "tenants": {}, "users": {}}

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            return self._empty_data()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Não foi possível ler o arquivo de autenticação {self.path}: {exc}"
            ) from exc
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("tenants"), dict)
            or not isinstance(data.get("users"), dict)
        ):
            raise RuntimeError(f"Formato inválido em {self.path}")
        return data

    def _write_unlocked(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                json.dump(
                    data,
                    temp_file,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                )
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def ensure(self) -> None:
        with _AUTH_LOCK:
            if not self.path.exists():
                self._write_unlocked(self._empty_data())

    def count_tenants(self) -> int:
        with _AUTH_LOCK:
            return len(self._read_unlocked()["tenants"])

    def get_tenant(self, tenant_id: str) -> dict | None:
        with _AUTH_LOCK:
            tenant = self._read_unlocked()["tenants"].get(tenant_id)
            return dict(tenant) if tenant else None

    def create_tenant(self, tenant_dict: dict) -> dict:
        with _AUTH_LOCK:
            data = self._read_unlocked()
            tenant = dict(tenant_dict)
            data["tenants"][tenant["id"]] = tenant
            self._write_unlocked(data)
            return dict(tenant)

    def update_tenant(self, tenant_id: str, updates: dict) -> dict | None:
        with _AUTH_LOCK:
            data = self._read_unlocked()
            tenant = data["tenants"].get(tenant_id)
            if not tenant:
                return None
            tenant.update(updates)
            self._write_unlocked(data)
            return dict(tenant)

    def get_user(self, uid: str) -> dict | None:
        with _AUTH_LOCK:
            user = self._read_unlocked()["users"].get(uid)
            return dict(user) if user else None

    def get_user_by_email(self, email: str) -> dict | None:
        email_norm = email.strip().lower()
        with _AUTH_LOCK:
            for user in self._read_unlocked()["users"].values():
                if str(user.get("email") or "").strip().lower() == email_norm:
                    return dict(user)
        return None

    def list_users_by_tenant(self, tenant_id: str) -> list[dict]:
        with _AUTH_LOCK:
            users = [
                dict(user)
                for user in self._read_unlocked()["users"].values()
                if user.get("tenant_id") == tenant_id
            ]
        return sorted(users, key=lambda u: str(u.get("nome") or "").lower())

    def create_user(self, user_dict: dict) -> dict:
        with _AUTH_LOCK:
            data = self._read_unlocked()
            user = dict(user_dict)
            user["email"] = str(user.get("email") or "").strip().lower()
            data["users"][user["uid"]] = user
            self._write_unlocked(data)
            return dict(user)

    def update_user(self, uid: str, updates: dict) -> dict | None:
        with _AUTH_LOCK:
            data = self._read_unlocked()
            user = data["users"].get(uid)
            if not user:
                return None
            user.update(updates)
            self._write_unlocked(data)
            return dict(user)


_local_auth_store = LocalTenantUserStore()


def get_tenant_user_store():
    if get_storage_backend() == "firebase":
        from firebase_backend import FirestoreTenantUserStore

        return FirestoreTenantUserStore()
    return _local_auth_store


class AuthService:
    """Orquestra cadastro, autenticação, RBAC e gestão de empresa em modo Local ou Firebase."""

    def __init__(
        self,
        user_store=None,
        firebase_auth_provider=None,
        backend_override: str | None = None,
    ):
        self._user_store = user_store
        self._firebase_auth = firebase_auth_provider
        self._backend_override = backend_override

    @property
    def backend(self) -> str:
        return self._backend_override or get_storage_backend()

    @property
    def user_store(self):
        if self._user_store is not None:
            return self._user_store
        return get_tenant_user_store()

    @property
    def firebase_auth(self):
        if self._firebase_auth is None:
            from firebase_backend import FirebaseAuthProvider

            self._firebase_auth = FirebaseAuthProvider()
        return self._firebase_auth

    def _next_tenant_id(self) -> str:
        if self.backend == "local" and self.user_store.count_tenants() == 0:
            from storage import JsonStore

            if JsonStore().has_legacy_default_records():
                return "default"
        return f"emp_{uuid.uuid4().hex[:10]}"

    def _build_tenant(self, nome_empresa: str, tenant_id: str | None = None) -> dict:
        nome = SecurityValidator.sanitizar_texto(nome_empresa)
        if not nome:
            raise ValueError("O nome da empresa é obrigatório.")
        return {
            "id": tenant_id or self._next_tenant_id(),
            "nome": nome,
            "plano": "pro",
            "ativo": True,
            "sla_dias": 3,
            "localizacoes": list(DEFAULT_LOCATIONS),
            "criado_em": _now_iso(),
        }

    def register_local(
        self,
        nome_empresa: str,
        nome_usuario: str,
        email: str,
        senha: str,
    ) -> dict[str, Any]:
        if self.backend == "firebase":
            raise ValueError(
                "O cadastro em modo Firebase deve ser feito pelo fluxo de onboarding autenticado."
            )

        nome_resp = SecurityValidator.sanitizar_texto(nome_usuario)
        if not nome_resp:
            raise ValueError("O nome do responsável é obrigatório.")
        email_norm = _validar_email(email)
        senha_valida = _validar_senha(senha)

        if self.user_store.get_user_by_email(email_norm):
            raise ValueError("Já existe uma conta cadastrada com este e-mail.")

        tenant = self.user_store.create_tenant(self._build_tenant(nome_empresa))
        user = self.user_store.create_user(
            {
                "uid": f"usr_{uuid.uuid4().hex[:12]}",
                "email": email_norm,
                "nome": nome_resp,
                "role": "admin",
                "tenant_id": tenant["id"],
                "ativo": True,
                "permissoes": dict(ALL_PERMISSIONS_TRUE),
                "password_hash": hash_password(senha_valida),
                "criado_em": _now_iso(),
            }
        )
        token = create_local_token(user)
        return {
            "token": token,
            "user": sanitizar_usuario(user),
            "tenant": sanitizar_tenant(tenant),
        }

    def login_local(self, email: str, senha: str) -> dict[str, Any]:
        if self.backend == "firebase":
            raise ValueError(
                "O login em modo Firebase é realizado pelo SDK do Firebase no cliente."
            )

        email_norm = _validar_email(email)
        if not isinstance(senha, str) or not senha:
            raise ValueError("Informe a senha.")

        user = self.user_store.get_user_by_email(email_norm)
        if not user or not verify_password(senha, str(user.get("password_hash") or "")):
            raise AuthenticationError("E-mail ou senha inválidos.")

        if not user.get("ativo", True):
            raise AuthorizationError("Sua conta está desativada. Procure o administrador da empresa.")

        tenant = self.user_store.get_tenant(user["tenant_id"])
        if not tenant or not tenant.get("ativo", True):
            raise AuthorizationError("Esta empresa está inativa ou indisponível.")

        token = create_local_token(user)
        return {
            "token": token,
            "user": sanitizar_usuario(user),
            "tenant": sanitizar_tenant(tenant),
        }

    def onboard_firebase_user(
        self, token: str, nome_empresa: str, nome_usuario: str = ""
    ) -> dict[str, Any]:
        if self.backend != "firebase":
            raise ValueError("Onboarding Firebase disponível apenas quando SYSFIX_BACKEND=firebase.")

        try:
            identity = self.firebase_auth.verify_id_token(token)
        except Exception as exc:
            raise AuthenticationError("Token Firebase inválido ou expirado.") from exc

        uid = identity["uid"]
        if self.user_store.get_user(uid):
            raise ValueError("Este usuário já possui uma empresa configurada.")

        email_norm = _validar_email(identity.get("email") or "")
        nome_final = SecurityValidator.sanitizar_texto(
            nome_usuario or identity.get("name") or email_norm.split("@")[0]
        )
        if not nome_final:
            raise ValueError("O nome do responsável é obrigatório.")

        tenant_id = (
            "default"
            if self.user_store.count_tenants() == 0 and not self.user_store.get_tenant("default")
            else f"emp_{uuid.uuid4().hex[:10]}"
        )
        tenant = self.user_store.create_tenant(
            self._build_tenant(nome_empresa, tenant_id=tenant_id)
        )
        user = self.user_store.create_user(
            {
                "uid": uid,
                "email": email_norm,
                "nome": nome_final,
                "role": "admin",
                "tenant_id": tenant["id"],
                "ativo": True,
                "permissoes": dict(ALL_PERMISSIONS_TRUE),
                "criado_em": _now_iso(),
            }
        )
        return {
            "user": sanitizar_usuario(user),
            "tenant": sanitizar_tenant(tenant),
        }

    def authenticate_token(self, token: str) -> dict[str, Any]:
        if not token:
            raise AuthenticationError("Token de autenticação não fornecido.")

        if self.backend == "firebase":
            try:
                identity = self.firebase_auth.verify_id_token(token)
            except Exception as exc:
                raise AuthenticationError("Token Firebase inválido ou expirado.") from exc

            user = self.user_store.get_user(identity["uid"])
            if not user:
                raise OnboardingRequiredError(identity)
        else:
            payload = verify_local_token(token)
            user = self.user_store.get_user(payload["uid"])
            if not user:
                raise AuthenticationError("Sessão inválida ou usuário removido.")

        if not user.get("ativo", True):
            raise AuthorizationError("Sua conta foi desativada pelo administrador.")

        tenant = self.user_store.get_tenant(user["tenant_id"])
        if not tenant or not tenant.get("ativo", True):
            raise AuthorizationError("Empresa suspensa ou não encontrada.")

        ctx = sanitizar_usuario(user)
        ctx["tenant"] = sanitizar_tenant(tenant)
        return ctx

    def create_team_member(
        self,
        admin_ctx: dict[str, Any],
        nome: str,
        email: str,
        senha: str,
        role: str = "tecnico",
        permissoes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not usuario_tem_permissao(admin_ctx, "gerenciar_equipe"):
            raise AuthorizationError("Você não tem permissão para adicionar membros.")

        nome_limpo = SecurityValidator.sanitizar_texto(nome)
        if not nome_limpo:
            raise ValueError("O nome do colaborador é obrigatório.")
        email_norm = _validar_email(email)
        senha_valida = _validar_senha(senha)
        role_valida = _validar_role(role)

        is_caller_admin = admin_ctx.get("role") == "admin"
        if role_valida == "admin" and not is_caller_admin:
            raise AuthorizationError("Apenas administradores podem criar outros administradores.")

        if role_valida == "admin":
            perms_finais = dict(ALL_PERMISSIONS_TRUE)
        else:
            perms_finais = normalizar_permissoes_input(permissoes)
            if not is_caller_admin:
                for k, v in perms_finais.items():
                    if v and not usuario_tem_permissao(admin_ctx, k):
                        raise AuthorizationError(
                            f"Você não pode conceder a permissão '{k}' sem possuí-la."
                        )

        if self.user_store.get_user_by_email(email_norm):
            raise ValueError("Já existe um usuário cadastrado com este e-mail.")

        tenant_id = admin_ctx["tenant_id"]
        user_record = {
            "email": email_norm,
            "nome": nome_limpo,
            "role": role_valida,
            "tenant_id": tenant_id,
            "ativo": True,
            "permissoes": perms_finais,
            "criado_em": _now_iso(),
        }

        if self.backend == "firebase":
            uid = self.firebase_auth.create_auth_user(
                email=email_norm, password=senha_valida, display_name=nome_limpo
            )
            user_record["uid"] = uid
        else:
            user_record["uid"] = f"usr_{uuid.uuid4().hex[:12]}"
            user_record["password_hash"] = hash_password(senha_valida)

        created = self.user_store.create_user(user_record)
        return sanitizar_usuario(created)

    def update_team_member(
        self,
        admin_ctx: dict[str, Any],
        target_uid: str,
        nome: str | None = None,
        role: str | None = None,
        ativo: bool | None = None,
        permissoes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not usuario_tem_permissao(admin_ctx, "gerenciar_equipe"):
            raise AuthorizationError("Você não tem permissão para editar membros.")

        target = self.user_store.get_user(target_uid)
        if not target or target.get("tenant_id") != admin_ctx["tenant_id"]:
            raise LookupError("Colaborador não encontrado nesta empresa.")

        is_caller_admin = admin_ctx.get("role") == "admin"
        if target.get("role") == "admin" and not is_caller_admin:
            raise AuthorizationError("Apenas administradores podem alterar outros administradores.")

        updates: dict[str, Any] = {}
        if nome is not None:
            nome_limpo = SecurityValidator.sanitizar_texto(nome)
            if not nome_limpo:
                raise ValueError("O nome do colaborador não pode ficar vazio.")
            updates["nome"] = nome_limpo

        if role is not None:
            nova_role = _validar_role(role)
            if nova_role == "admin" and not is_caller_admin:
                raise AuthorizationError("Apenas administradores podem promover usuários a administrador.")
            updates["role"] = nova_role

        if ativo is not None:
            updates["ativo"] = bool(ativo)

        novo_role = updates.get("role", target.get("role"))
        novo_ativo = updates.get("ativo", target.get("ativo", True))
        if target.get("role") == "admin" and (novo_role != "admin" or not novo_ativo):
            membros = self.user_store.list_users_by_tenant(admin_ctx["tenant_id"])
            outros_admins_ativos = [
                m
                for m in membros
                if m.get("uid") != target_uid
                and m.get("role") == "admin"
                and m.get("ativo", True)
            ]
            if not outros_admins_ativos:
                raise ValueError(
                    "A empresa precisa manter pelo menos um administrador ativo."
                )

        if novo_role == "admin":
            if role == "admin" or permissoes is not None:
                updates["permissoes"] = dict(ALL_PERMISSIONS_TRUE)
        elif permissoes is not None:
            perms_norm = normalizar_permissoes_input(permissoes)
            if not is_caller_admin:
                for k, v in perms_norm.items():
                    if v and not usuario_tem_permissao(admin_ctx, k):
                        raise AuthorizationError(
                            f"Você não pode conceder a permissão '{k}' sem possuí-la."
                        )
            updates["permissoes"] = perms_norm
        elif role is not None and novo_role == "tecnico":
            updates["permissoes"] = resolver_permissoes(
                "tecnico", target.get("permissoes")
            )

        if not updates:
            return sanitizar_usuario(target)

        updated = self.user_store.update_user(target_uid, updates)
        if self.backend == "firebase" and ativo is not None:
            self.firebase_auth.set_user_disabled(target_uid, disabled=not bool(ativo))
        return sanitizar_usuario(updated)

    def list_team_members(self, admin_ctx: dict[str, Any]) -> list[dict[str, Any]]:
        if not usuario_tem_permissao(admin_ctx, "gerenciar_equipe"):
            raise AuthorizationError("Você não tem permissão para visualizar a equipe.")
        membros = self.user_store.list_users_by_tenant(admin_ctx["tenant_id"])
        return [sanitizar_usuario(m) for m in membros]

    def update_tenant_settings(
        self,
        admin_ctx: dict[str, Any],
        nome: str | None = None,
        sla_dias: int | None = None,
        localizacoes: list[str] | None = None,
    ) -> dict[str, Any]:
        if not usuario_tem_permissao(admin_ctx, "gerenciar_equipe"):
            raise AuthorizationError(
                "Você não tem permissão para alterar configurações da empresa."
            )

        updates: dict[str, Any] = {}
        if nome is not None:
            nome_limpo = SecurityValidator.sanitizar_texto(nome)
            if not nome_limpo:
                raise ValueError("O nome da empresa não pode ficar vazio.")
            updates["nome"] = nome_limpo

        if sla_dias is not None:
            if isinstance(sla_dias, bool) or not isinstance(sla_dias, int) or not (1 <= sla_dias <= 60):
                raise ValueError("O SLA em dias deve ser um número inteiro entre 1 e 60.")
            updates["sla_dias"] = sla_dias

        if localizacoes is not None:
            updates["localizacoes"] = _normalizar_localizacoes(localizacoes)

        if not updates:
            tenant = self.user_store.get_tenant(admin_ctx["tenant_id"])
            return sanitizar_tenant(tenant)

        updated = self.user_store.update_tenant(admin_ctx["tenant_id"], updates)
        if not updated:
            raise LookupError("Empresa não encontrada.")
        return sanitizar_tenant(updated)
