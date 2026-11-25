import json
import os
import threading
from pathlib import Path
from typing import Any

_INIT_LOCK = threading.Lock()


def _import_firebase_admin():
    try:
        import firebase_admin
        from firebase_admin import auth, credentials, firestore
    except ImportError as exc:
        raise RuntimeError(
            "O pacote 'firebase-admin' não está instalado. "
            "Execute 'pip install -r requirements.txt' ou use SYSFIX_BACKEND=local."
        ) from exc
    return firebase_admin, auth, credentials, firestore


def init_firebase_app():
    """Inicializa o Firebase Admin SDK uma única vez a partir das variáveis de ambiente."""
    firebase_admin, _, credentials, _ = _import_firebase_admin()

    with _INIT_LOCK:
        try:
            return firebase_admin.get_app()
        except ValueError:
            pass

        service_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        service_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
        gac_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

        if service_json:
            cred_dict = json.loads(service_json)
            if isinstance(cred_dict.get("private_key"), str):
                cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
            cred = credentials.Certificate(cred_dict)
        elif service_path:
            expanded = Path(service_path).expanduser()
            if not expanded.exists():
                raise RuntimeError(
                    f"Arquivo de credenciais Firebase não encontrado: {expanded}"
                )
            cred = credentials.Certificate(str(expanded))
        elif gac_path:
            cred = credentials.ApplicationDefault()
        else:
            raise RuntimeError(
                "Credenciais do Firebase não configuradas. Defina "
                "FIREBASE_SERVICE_ACCOUNT_PATH ou FIREBASE_SERVICE_ACCOUNT_JSON."
            )

        return firebase_admin.initialize_app(cred)


def get_firestore_client():
    _, _, _, firestore = _import_firebase_admin()
    app = init_firebase_app()
    return firestore.client(app=app)


def _run_transaction(db: Any, callback):
    """Executa uma operação transacional tanto no Firestore real quanto em mocks."""
    custom_runner = getattr(db, "run_transaction", None)
    if callable(custom_runner):
        return custom_runner(callback)

    _, _, _, firestore = _import_firebase_admin()
    transaction = db.transaction()

    @firestore.transactional
    def _wrapped(tx):
        return callback(tx)

    return _wrapped(transaction)


class FirestoreColetaStore:
    """Armazenamento de coletas multi-tenant no Cloud Firestore."""

    def __init__(self, db: Any = None):
        self._db = db

    @property
    def db(self):
        if self._db is None:
            self._db = get_firestore_client()
        return self._db

    def ensure(self) -> None:
        return None

    def _tenant_ref(self, tenant_id: str):
        return self.db.collection("tenants").document(tenant_id)

    def _coletas_ref(self, tenant_id: str):
        return self._tenant_ref(tenant_id).collection("coletas")

    def _tracking_ref(self, codigo: str):
        return self.db.collection("tracking_codes").document(str(codigo).strip().upper())

    def find_by_tracking_code(self, codigo: str) -> tuple[str, dict] | None:
        alvo = str(codigo or "").strip().upper()
        if not alvo:
            return None
        snap = self._tracking_ref(alvo).get()
        if not snap.exists:
            return None
        meta = dict(snap.to_dict() or {})
        tenant_id = meta.get("tenant_id")
        record_id = meta.get("record_id")
        if not tenant_id or record_id is None:
            return None
        record = self.get(int(record_id), tenant_id=tenant_id)
        if not record:
            return None
        return tenant_id, record

    def all(self, tenant_id: str = "default") -> list[dict]:
        docs = self._coletas_ref(tenant_id).stream()
        rows: list[dict] = []
        for doc in docs:
            data = dict(doc.to_dict() or {})
            data.setdefault("id", int(doc.id) if str(doc.id).isdigit() else doc.id)
            data["tenant_id"] = tenant_id
            rows.append(data)
        return rows

    def get(self, record_id: int, tenant_id: str = "default") -> dict | None:
        snap = self._coletas_ref(tenant_id).document(str(record_id)).get()
        if not snap.exists:
            return None
        data = dict(snap.to_dict() or {})
        data.setdefault("id", record_id)
        data["tenant_id"] = tenant_id
        return data

    def create_records(
        self, records: list[dict], tenant_id: str = "default"
    ) -> list[int]:
        if not records:
            return []

        tenant_ref = self._tenant_ref(tenant_id)
        coletas_ref = self._coletas_ref(tenant_id)

        def _tx_create(transaction):
            tenant_snap = tenant_ref.get(transaction=transaction)
            tenant_data = dict(tenant_snap.to_dict() or {}) if tenant_snap.exists else {}
            next_id = int(tenant_data.get("next_coleta_id") or 1)
            if next_id < 1:
                next_id = 1

            ids: list[int] = []
            tracking_entries: list[tuple[str, int]] = []
            for item in records:
                record = dict(item)
                record["id"] = next_id
                record["tenant_id"] = tenant_id
                doc_ref = coletas_ref.document(str(next_id))
                transaction.set(doc_ref, record)
                codigo = str(record.get("codigo_acompanhamento") or "").strip().upper()
                if codigo:
                    tracking_entries.append((codigo, next_id))
                ids.append(next_id)
                next_id += 1

            for codigo, rec_id in tracking_entries:
                transaction.set(
                    self._tracking_ref(codigo),
                    {"tenant_id": tenant_id, "record_id": rec_id},
                )

            if tenant_snap.exists:
                transaction.update(tenant_ref, {"next_coleta_id": next_id})
            else:
                transaction.set(
                    tenant_ref,
                    {"id": tenant_id, "next_coleta_id": next_id},
                    merge=True,
                )
            return ids

        return _run_transaction(self.db, _tx_create)

    def update_record(
        self, record_id: int, updates: dict, tenant_id: str = "default"
    ) -> bool:
        doc_ref = self._coletas_ref(tenant_id).document(str(record_id))
        snap = doc_ref.get()
        if not snap.exists:
            return False
        payload = dict(updates)
        payload["tenant_id"] = tenant_id
        doc_ref.update(payload)
        codigo = str(
            payload.get("codigo_acompanhamento")
            or (snap.to_dict() or {}).get("codigo_acompanhamento")
            or ""
        ).strip().upper()
        if codigo and "codigo_acompanhamento" in payload:
            self._tracking_ref(codigo).set(
                {"tenant_id": tenant_id, "record_id": int(record_id)}
            )
        return True

    def delete_record(self, record_id: int, tenant_id: str = "default") -> bool:
        doc_ref = self._coletas_ref(tenant_id).document(str(record_id))
        snap = doc_ref.get()
        if not snap.exists:
            return False
        existing = snap.to_dict() or {}
        codigo = str(existing.get("codigo_acompanhamento") or "").strip().upper()
        doc_ref.delete()
        if codigo:
            self._tracking_ref(codigo).delete()
        return True

    def import_records(
        self, records: list[dict], tenant_id: str = "default"
    ) -> int:
        if not isinstance(records, list) or not all(
            isinstance(record, dict) for record in records
        ):
            raise ValueError("A importação deve conter uma lista de registros JSON.")

        existing = list(self._coletas_ref(tenant_id).limit(1).stream())
        if existing:
            raise RuntimeError(
                f"A importação foi cancelada porque o tenant '{tenant_id}' já contém registros."
            )

        imported: list[dict] = []
        seen_ids: set[int] = set()
        for record in records:
            if "id" not in record or isinstance(record["id"], bool):
                raise ValueError("Cada registro importado precisa ter um campo 'id' inteiro válido.")
            try:
                record_id = int(record["id"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"ID inválido na importação: {record['id']!r}.") from exc
            if record_id < 1 or record_id in seen_ids:
                raise ValueError(f"ID duplicado ou inválido na importação: {record_id}.")
            seen_ids.add(record_id)
            item = dict(record)
            item["id"] = record_id
            item["tenant_id"] = tenant_id
            imported.append(item)

        coletas_ref = self._coletas_ref(tenant_id)
        tenant_ref = self._tenant_ref(tenant_id)

        batch = self.db.batch()
        ops = 0
        for item in sorted(imported, key=lambda row: row["id"]):
            batch.set(coletas_ref.document(str(item["id"])), item)
            ops += 1
            codigo = str(item.get("codigo_acompanhamento") or "").strip().upper()
            if codigo:
                batch.set(
                    self._tracking_ref(codigo),
                    {"tenant_id": tenant_id, "record_id": item["id"]},
                )
                ops += 1
            if ops >= 380:
                batch.commit()
                batch = self.db.batch()
                ops = 0

        next_id = max(seen_ids, default=0) + 1
        batch.set(
            tenant_ref,
            {"id": tenant_id, "next_coleta_id": next_id},
            merge=True,
        )
        batch.commit()
        return len(imported)


class FirestoreTenantUserStore:
    """Armazenamento de empresas (tenants) e usuários no Cloud Firestore."""

    def __init__(self, db: Any = None):
        self._db = db

    @property
    def db(self):
        if self._db is None:
            self._db = get_firestore_client()
        return self._db

    def ensure(self) -> None:
        return None

    def count_tenants(self) -> int:
        return sum(1 for _ in self.db.collection("tenants").stream())

    def get_tenant(self, tenant_id: str) -> dict | None:
        snap = self.db.collection("tenants").document(tenant_id).get()
        if not snap.exists:
            return None
        data = dict(snap.to_dict() or {})
        data.setdefault("id", tenant_id)
        return data

    def create_tenant(self, tenant_dict: dict) -> dict:
        tenant_id = tenant_dict["id"]
        data = dict(tenant_dict)
        data.setdefault("next_coleta_id", 1)
        self.db.collection("tenants").document(tenant_id).set(data, merge=True)
        return dict(data)

    def update_tenant(self, tenant_id: str, updates: dict) -> dict | None:
        ref = self.db.collection("tenants").document(tenant_id)
        snap = ref.get()
        if not snap.exists:
            return None
        ref.update(dict(updates))
        updated = ref.get().to_dict() or {}
        updated.setdefault("id", tenant_id)
        return dict(updated)

    def get_user(self, uid: str) -> dict | None:
        snap = self.db.collection("users").document(uid).get()
        if not snap.exists:
            return None
        data = dict(snap.to_dict() or {})
        data.setdefault("uid", uid)
        return data

    def get_user_by_email(self, email: str) -> dict | None:
        email_norm = email.strip().lower()
        docs = (
            self.db.collection("users")
            .where("email", "==", email_norm)
            .limit(1)
            .stream()
        )
        for doc in docs:
            data = dict(doc.to_dict() or {})
            data.setdefault("uid", doc.id)
            return data
        return None

    def list_users_by_tenant(self, tenant_id: str) -> list[dict]:
        docs = (
            self.db.collection("users")
            .where("tenant_id", "==", tenant_id)
            .stream()
        )
        users = []
        for doc in docs:
            data = dict(doc.to_dict() or {})
            data.setdefault("uid", doc.id)
            users.append(data)
        return sorted(users, key=lambda u: str(u.get("nome") or "").lower())

    def create_user(self, user_dict: dict) -> dict:
        uid = user_dict["uid"]
        data = dict(user_dict)
        data["email"] = str(data.get("email") or "").strip().lower()
        self.db.collection("users").document(uid).set(data)
        return dict(data)

    def update_user(self, uid: str, updates: dict) -> dict | None:
        ref = self.db.collection("users").document(uid)
        snap = ref.get()
        if not snap.exists:
            return None
        ref.update(dict(updates))
        updated = ref.get().to_dict() or {}
        updated.setdefault("uid", uid)
        return dict(updated)


class FirebaseAuthProvider:
    """Wrapper para operações do Firebase Authentication Admin SDK."""

    def __init__(self, auth_client: Any = None):
        self._auth = auth_client

    @property
    def auth_client(self):
        if self._auth is None:
            _, auth, _, _ = _import_firebase_admin()
            init_firebase_app()
            self._auth = auth
        return self._auth

    def verify_id_token(self, token: str) -> dict[str, Any]:
        decoded = self.auth_client.verify_id_token(token)
        return {
            "uid": decoded["uid"],
            "email": str(decoded.get("email") or "").strip().lower(),
            "name": str(decoded.get("name") or "").strip(),
        }

    def create_auth_user(
        self, email: str, password: str, display_name: str
    ) -> str:
        user_record = self.auth_client.create_user(
            email=email.strip().lower(),
            password=password,
            display_name=display_name.strip(),
        )
        return user_record.uid

    def set_user_disabled(self, uid: str, disabled: bool) -> None:
        self.auth_client.update_user(uid, disabled=disabled)

    def update_user_password(self, uid: str, new_password: str) -> None:
        self.auth_client.update_user(uid, password=new_password)
