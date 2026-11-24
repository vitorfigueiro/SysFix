import json
import os
import tempfile
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from config import get_data_dir, get_storage_backend


_STORE_LOCK = threading.RLock()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Tipo não suportado para salvar no JSON: {type(value).__name__}")


def _record_belongs_to_tenant(row: dict, tenant_id: str | None) -> bool:
    if tenant_id is None:
        return True
    return (row.get("tenant_id") or "default") == tenant_id


class JsonStore:
    def __init__(self, path: str | Path | None = None):
        self._custom_path = Path(path) if path else None

    @property
    def path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "coletas.json"

    @staticmethod
    def _empty_data() -> dict:
        return {"version": 1, "next_id": 1, "coletas": []}

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            return self._empty_data()

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Não foi possível ler o arquivo de dados {self.path}: {exc}") from exc

        if (
            not isinstance(data, dict)
            or not isinstance(data.get("coletas"), list)
            or not all(isinstance(row, dict) for row in data["coletas"])
            or isinstance(data.get("next_id"), bool)
            or not isinstance(data.get("next_id"), int)
            or data["next_id"] < 1
        ):
            raise RuntimeError(f"Formato inválido no arquivo de dados: {self.path}")

        max_id = max(
            (
                row.get("id", 0)
                for row in data["coletas"]
                if isinstance(row.get("id"), int) and not isinstance(row.get("id"), bool)
            ),
            default=0,
        )
        if data["next_id"] <= max_id:
            data["next_id"] = max_id + 1
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
                    default=_json_default,
                )
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise RuntimeError(f"Não foi possível salvar os dados em {self.path}: {exc}") from exc
        except Exception:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def ensure(self) -> None:
        with _STORE_LOCK:
            if not self.path.exists():
                self._write_unlocked(self._empty_data())

    def all(self, tenant_id: str | None = None) -> list[dict]:
        with _STORE_LOCK:
            return [
                dict(row)
                for row in self._read_unlocked()["coletas"]
                if _record_belongs_to_tenant(row, tenant_id)
            ]

    def get(self, record_id: int, tenant_id: str | None = None) -> dict | None:
        with _STORE_LOCK:
            for row in self._read_unlocked()["coletas"]:
                if row.get("id") == record_id and _record_belongs_to_tenant(row, tenant_id):
                    return dict(row)
        return None

    def find_by_tracking_code(self, codigo: str) -> tuple[str, dict] | None:
        alvo = str(codigo or "").strip().upper()
        if not alvo:
            return None
        with _STORE_LOCK:
            for row in self._read_unlocked()["coletas"]:
                if str(row.get("codigo_acompanhamento") or "").strip().upper() == alvo:
                    return (row.get("tenant_id") or "default"), dict(row)
        return None

    def has_legacy_default_records(self) -> bool:
        with _STORE_LOCK:
            return any(
                (row.get("tenant_id") or "default") == "default"
                for row in self._read_unlocked()["coletas"]
            )

    def mutate(self, operation: Callable[[dict], Any]) -> Any:
        with _STORE_LOCK:
            data = self._read_unlocked()
            result = operation(data)
            self._write_unlocked(data)
            return result

    def create_records(
        self, records: list[dict], tenant_id: str = "default"
    ) -> list[int]:
        if not records:
            return []

        def insert_batch(data: dict) -> list[int]:
            ids: list[int] = []
            for item in records:
                record = dict(item)
                record["id"] = data["next_id"]
                record["tenant_id"] = tenant_id
                data["next_id"] += 1
                data["coletas"].append(record)
                ids.append(record["id"])
            return ids

        return self.mutate(insert_batch)

    def update_record(
        self, record_id: int, updates: dict, tenant_id: str = "default"
    ) -> bool:
        def update(data: dict) -> bool:
            record = next(
                (
                    row
                    for row in data["coletas"]
                    if row.get("id") == record_id
                    and _record_belongs_to_tenant(row, tenant_id)
                ),
                None,
            )
            if record is None:
                return False
            record.update(updates)
            record["tenant_id"] = tenant_id
            return True

        return self.mutate(update)

    def delete_record(self, record_id: int, tenant_id: str = "default") -> bool:
        def delete(data: dict) -> bool:
            original_length = len(data["coletas"])
            data["coletas"] = [
                row
                for row in data["coletas"]
                if not (
                    row.get("id") == record_id
                    and _record_belongs_to_tenant(row, tenant_id)
                )
            ]
            return len(data["coletas"]) < original_length

        return self.mutate(delete)

    def import_records(
        self, records: list[dict], tenant_id: str | None = None
    ) -> int:
        if not isinstance(records, list) or not all(
            isinstance(record, dict) for record in records
        ):
            raise ValueError("A importação deve conter uma lista de registros JSON.")

        imported = []
        seen_ids = set()
        for record in records:
            if "id" not in record:
                raise ValueError("Cada registro importado precisa ter um campo 'id'.")
            if isinstance(record["id"], bool):
                raise ValueError(f"ID inválido na importação: {record['id']!r}.")
            try:
                record_id = int(record["id"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"ID inválido na importação: {record['id']!r}.") from exc
            if record_id < 1 or record_id in seen_ids:
                raise ValueError(f"ID duplicado ou inválido na importação: {record_id}.")
            seen_ids.add(record_id)
            imported_record = dict(record)
            imported_record["id"] = record_id
            if tenant_id is not None:
                imported_record["tenant_id"] = tenant_id
            imported.append(imported_record)

        def replace(data: dict) -> int:
            if data["coletas"]:
                raise RuntimeError(
                    "A importação foi cancelada porque o arquivo local já contém registros."
                )
            data["coletas"] = sorted(imported, key=lambda row: row["id"])
            data["next_id"] = max(seen_ids, default=0) + 1
            return len(imported)

        return self.mutate(replace)


_default_store = JsonStore()


def get_store():
    if get_storage_backend() == "firebase":
        from firebase_backend import FirestoreColetaStore

        return FirestoreColetaStore()
    return _default_store
