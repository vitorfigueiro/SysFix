import json
import os
import tempfile
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import get_data_dir, get_storage_backend
from models import ColetaModel
from security import SecurityValidator

_OPS_LOCK = threading.RLock()
_VALID_COLLECTIONS = {"estoque", "preventivas", "clientes"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_date(value: str | date | None) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        iso = SecurityValidator.validar_data(value)
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return None


class LocalOpsStore:
    """Armazenamento local atômico para Estoque, Preventivas e Clientes/POPs."""

    def __init__(self, path: str | Path | None = None):
        self._custom_path = Path(path) if path else None

    @property
    def path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "ops_db.json"

    @staticmethod
    def _empty_data() -> dict:
        return {"version": 1, "estoque": [], "preventivas": [], "clientes": []}

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            return self._empty_data()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Erro ao ler {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            return self._empty_data()
        for col in _VALID_COLLECTIONS:
            if not isinstance(data.get(col), list):
                data[col] = []
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
            ) as tf:
                temp_path = Path(tf.name)
                json.dump(data, tf, ensure_ascii=False, allow_nan=False, indent=2)
                tf.write("\n")
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def ensure(self) -> None:
        with _OPS_LOCK:
            if not self.path.exists():
                self._write_unlocked(self._empty_data())

    def list_items(self, collection: str, tenant_id: str) -> list[dict]:
        if collection not in _VALID_COLLECTIONS:
            raise ValueError("Coleção inválida.")
        with _OPS_LOCK:
            return [
                dict(item)
                for item in self._read_unlocked()[collection]
                if item.get("tenant_id") == tenant_id
            ]

    def get_item(self, collection: str, item_id: str, tenant_id: str) -> dict | None:
        for item in self.list_items(collection, tenant_id):
            if item.get("id") == item_id:
                return item
        return None

    def create_item(self, collection: str, item: dict, tenant_id: str) -> dict:
        if collection not in _VALID_COLLECTIONS:
            raise ValueError("Coleção inválida.")
        with _OPS_LOCK:
            data = self._read_unlocked()
            row = dict(item)
            row["tenant_id"] = tenant_id
            data[collection].append(row)
            self._write_unlocked(data)
            return dict(row)

    def update_item(
        self, collection: str, item_id: str, updates: dict, tenant_id: str
    ) -> dict | None:
        if collection not in _VALID_COLLECTIONS:
            raise ValueError("Coleção inválida.")
        with _OPS_LOCK:
            data = self._read_unlocked()
            for row in data[collection]:
                if row.get("id") == item_id and row.get("tenant_id") == tenant_id:
                    row.update(updates)
                    row["tenant_id"] = tenant_id
                    self._write_unlocked(data)
                    return dict(row)
        return None

    def delete_item(self, collection: str, item_id: str, tenant_id: str) -> bool:
        if collection not in _VALID_COLLECTIONS:
            raise ValueError("Coleção inválida.")
        with _OPS_LOCK:
            data = self._read_unlocked()
            antes = len(data[collection])
            data[collection] = [
                row
                for row in data[collection]
                if not (row.get("id") == item_id and row.get("tenant_id") == tenant_id)
            ]
            if len(data[collection]) < antes:
                self._write_unlocked(data)
                return True
            return False


class FirestoreOpsStore:
    """Armazenamento Cloud Firestore para Estoque, Preventivas e Clientes/POPs."""

    def __init__(self, db: Any = None):
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from firebase_backend import get_firestore_client

            self._db = get_firestore_client()
        return self._db

    def _col_ref(self, tenant_id: str, collection: str):
        if collection not in _VALID_COLLECTIONS:
            raise ValueError("Coleção inválida.")
        return self.db.collection("tenants").document(tenant_id).collection(collection)

    def list_items(self, collection: str, tenant_id: str) -> list[dict]:
        rows: list[dict] = []
        for doc in self._col_ref(tenant_id, collection).stream():
            data = dict(doc.to_dict() or {})
            data.setdefault("id", doc.id)
            data["tenant_id"] = tenant_id
            rows.append(data)
        return rows

    def get_item(self, collection: str, item_id: str, tenant_id: str) -> dict | None:
        snap = self._col_ref(tenant_id, collection).document(item_id).get()
        if not snap.exists:
            return None
        data = dict(snap.to_dict() or {})
        data.setdefault("id", item_id)
        data["tenant_id"] = tenant_id
        return data

    def create_item(self, collection: str, item: dict, tenant_id: str) -> dict:
        row = dict(item)
        row["tenant_id"] = tenant_id
        self._col_ref(tenant_id, collection).document(row["id"]).set(row)
        return dict(row)

    def update_item(
        self, collection: str, item_id: str, updates: dict, tenant_id: str
    ) -> dict | None:
        ref = self._col_ref(tenant_id, collection).document(item_id)
        snap = ref.get()
        if not snap.exists:
            return None
        payload = dict(updates)
        payload["tenant_id"] = tenant_id
        ref.update(payload)
        updated = dict(ref.get().to_dict() or {})
        updated.setdefault("id", item_id)
        updated["tenant_id"] = tenant_id
        return updated

    def delete_item(self, collection: str, item_id: str, tenant_id: str) -> bool:
        ref = self._col_ref(tenant_id, collection).document(item_id)
        snap = ref.get()
        if not snap.exists:
            return False
        ref.delete()
        return True


_local_ops_store = LocalOpsStore()


def get_ops_store():
    if get_storage_backend() == "firebase":
        return FirestoreOpsStore()
    return _local_ops_store


class OpsModulesService:
    """Serviços operacionais voltados para TI & Provedores (Estoque, Preventivas, Clientes/POPs)."""

    def __init__(self, store=None):
        self._store = store

    @property
    def store(self):
        return self._store if self._store is not None else get_ops_store()

    # ------------------------------------------------------------------
    # ESTOQUE DE PEÇAS, INSUMOS E EQUIPAMENTOS PRONTOS
    # ------------------------------------------------------------------

    def listar_estoque(self, tenant_id: str) -> list[dict]:
        itens = self.store.list_items("estoque", tenant_id)
        for item in itens:
            qtd = int(item.get("quantidade") or 0)
            minimo = int(item.get("estoque_minimo") or 0)
            item["estoque_baixo"] = qtd <= minimo
        return sorted(itens, key=lambda i: (not i.get("estoque_baixo", False), str(i.get("nome") or "").lower()))

    def criar_item_estoque(self, tenant_id: str, payload: dict, actor: str = "") -> dict:
        nome = SecurityValidator.sanitizar_texto(payload.get("nome"))
        if not nome:
            raise ValueError("Informe o nome da peça ou equipamento de estoque.")
        qtd = max(0, int(payload.get("quantidade") or 0))
        minimo = max(0, int(payload.get("estoque_minimo") or 0))
        custo = SecurityValidator.validate_cost(payload.get("custo_unitario"))
        agora = _now_iso()
        item = {
            "id": f"est_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant_id,
            "nome": nome[:140],
            "codigo": SecurityValidator.sanitizar_texto(payload.get("codigo"))[:60],
            "categoria": SecurityValidator.sanitizar_texto(payload.get("categoria")) or "Peça / Insumo",
            "quantidade": qtd,
            "estoque_minimo": minimo,
            "custo_unitario": custo,
            "localizacao": SecurityValidator.sanitizar_texto(payload.get("localizacao")) or "Almoxarifado",
            "criado_em": agora,
            "atualizado_em": agora,
            "atualizado_por": SecurityValidator.sanitizar_texto(actor),
        }
        criado = self.store.create_item("estoque", item, tenant_id)
        criado["estoque_baixo"] = qtd <= minimo
        return criado

    def atualizar_item_estoque(
        self, tenant_id: str, item_id: str, payload: dict, actor: str = ""
    ) -> dict | None:
        existente = self.store.get_item("estoque", item_id, tenant_id)
        if not existente:
            return None
        updates: dict[str, Any] = {
            "atualizado_em": _now_iso(),
            "atualizado_por": SecurityValidator.sanitizar_texto(actor),
        }
        if "nome" in payload and payload["nome"] is not None:
            nome = SecurityValidator.sanitizar_texto(payload["nome"])
            if not nome:
                raise ValueError("O nome do item não pode ficar vazio.")
            updates["nome"] = nome[:140]
        if "codigo" in payload and payload["codigo"] is not None:
            updates["codigo"] = SecurityValidator.sanitizar_texto(payload["codigo"])[:60]
        if "categoria" in payload and payload["categoria"] is not None:
            updates["categoria"] = SecurityValidator.sanitizar_texto(payload["categoria"]) or "Peça / Insumo"
        if "estoque_minimo" in payload and payload["estoque_minimo"] is not None:
            updates["estoque_minimo"] = max(0, int(payload["estoque_minimo"]))
        if "custo_unitario" in payload and payload["custo_unitario"] is not None:
            updates["custo_unitario"] = SecurityValidator.validate_cost(payload["custo_unitario"])
        if "localizacao" in payload and payload["localizacao"] is not None:
            updates["localizacao"] = SecurityValidator.sanitizar_texto(payload["localizacao"]) or "Almoxarifado"

        atualizado = self.store.update_item("estoque", item_id, updates, tenant_id)
        if atualizado:
            atualizado["estoque_baixo"] = int(atualizado.get("quantidade") or 0) <= int(atualizado.get("estoque_minimo") or 0)
        return atualizado

    def movimentar_estoque(
        self,
        tenant_id: str,
        item_id: str,
        tipo: str,
        quantidade: int,
        os_id: int | None = None,
        observacao: str = "",
        actor: str = "",
    ) -> dict | None:
        item = self.store.get_item("estoque", item_id, tenant_id)
        if not item:
            return None
        tipo_norm = SecurityValidator.sanitizar_texto(tipo).lower()
        if tipo_norm not in {"entrada", "saida", "saída"}:
            raise ValueError("Tipo de movimentação inválido. Use 'entrada' ou 'saida'.")
        if isinstance(quantidade, bool) or not isinstance(quantidade, int) or quantidade < 1:
            raise ValueError("Informe uma quantidade inteira maior que zero.")

        atual = int(item.get("quantidade") or 0)
        eh_saida = tipo_norm in {"saida", "saída"}
        if eh_saida:
            if quantidade > atual:
                raise ValueError(f"Estoque insuficiente para '{item['nome']}' (disponível: {atual}).")
            nova_qtd = atual - quantidade
        else:
            nova_qtd = atual + quantidade

        if os_id is not None:
            os_reg = ColetaModel.buscar_por_id(int(os_id), tenant_id=tenant_id)
            if not os_reg:
                raise LookupError(f"O.S. #{os_id} não encontrada nesta empresa.")
            desc_ev = (
                f"Baixa de estoque vinculada: {quantidade}x {item['nome']}"
                if eh_saida
                else f"Retorno ao estoque vinculado: {quantidade}x {item['nome']}"
            )
            if observacao:
                desc_ev += f" ({SecurityValidator.sanitizar_texto(observacao)})"
            ColetaModel.adicionar_evento_externo(
                int(os_id),
                tenant_id=tenant_id,
                tipo="estoque",
                descricao=desc_ev,
                actor=actor,
                publico=False,
            )

        atualizado = self.store.update_item(
            "estoque",
            item_id,
            {
                "quantidade": nova_qtd,
                "atualizado_em": _now_iso(),
                "atualizado_por": SecurityValidator.sanitizar_texto(actor),
            },
            tenant_id,
        )
        if atualizado:
            atualizado["estoque_baixo"] = nova_qtd <= int(atualizado.get("estoque_minimo") or 0)
        return atualizado

    def excluir_item_estoque(self, tenant_id: str, item_id: str) -> bool:
        return self.store.delete_item("estoque", item_id, tenant_id)

    # ------------------------------------------------------------------
    # PREVENTIVAS DE POPS, SERVIDORES E INFRAESTRUTURA
    # ------------------------------------------------------------------

    @staticmethod
    def _enriquecer_preventiva(item: dict) -> dict:
        hoje = date.today()
        prox = _parse_iso_date(item.get("proxima_execucao"))
        if prox is None:
            item["status_preventiva"] = "Sem agenda"
            item["dias_restantes"] = None
            return item
        diff = (prox - hoje).days
        item["dias_restantes"] = diff
        if diff < 0:
            item["status_preventiva"] = "Vencida"
        elif diff <= 7:
            item["status_preventiva"] = "Próxima"
        else:
            item["status_preventiva"] = "Em dia"
        return item

    def listar_preventivas(self, tenant_id: str) -> list[dict]:
        itens = [self._enriquecer_preventiva(i) for i in self.store.list_items("preventivas", tenant_id)]
        return sorted(
            itens,
            key=lambda i: (
                i.get("dias_restantes") if i.get("dias_restantes") is not None else 99999,
                str(i.get("titulo") or "").lower(),
            ),
        )

    def criar_preventiva(self, tenant_id: str, payload: dict, actor: str = "") -> dict:
        titulo = SecurityValidator.sanitizar_texto(payload.get("titulo"))
        if not titulo:
            raise ValueError("Informe o título da manutenção preventiva (ex.: Nobreak POP Centro).")
        local_pop = SecurityValidator.sanitizar_texto(payload.get("local_pop")) or "POP / Infraestrutura"
        periodicidade = int(payload.get("periodicidade_dias") or 30)
        if periodicidade < 1 or periodicidade > 1095:
            raise ValueError("A periodicidade deve estar entre 1 e 1095 dias.")
        proxima = SecurityValidator.validar_data(payload.get("proxima_execucao"))
        ultima = (
            SecurityValidator.validar_data(payload.get("ultima_execucao"))
            if payload.get("ultima_execucao")
            else ""
        )
        agora = _now_iso()
        item = {
            "id": f"prv_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant_id,
            "titulo": titulo[:140],
            "local_pop": local_pop[:120],
            "periodicidade_dias": periodicidade,
            "ultima_execucao": ultima,
            "proxima_execucao": proxima,
            "responsavel": SecurityValidator.sanitizar_texto(payload.get("responsavel")) or SecurityValidator.sanitizar_texto(actor),
            "checklist_resumo": SecurityValidator.sanitizar_texto(payload.get("checklist_resumo"))[:500],
            "historico_execucoes": [],
            "criado_em": agora,
            "atualizado_em": agora,
        }
        return self._enriquecer_preventiva(self.store.create_item("preventivas", item, tenant_id))

    def concluir_preventiva(
        self,
        tenant_id: str,
        item_id: str,
        observacao: str = "",
        data_execucao: str | None = None,
        actor: str = "",
    ) -> dict | None:
        item = self.store.get_item("preventivas", item_id, tenant_id)
        if not item:
            return None
        exec_iso = SecurityValidator.validar_data(data_execucao) if data_execucao else date.today().strftime("%Y-%m-%d")
        dt_exec = _parse_iso_date(exec_iso) or date.today()
        periodicidade = max(1, int(item.get("periodicidade_dias") or 30))
        nova_prox = (dt_exec + timedelta(days=periodicidade)).strftime("%Y-%m-%d")

        hist = list(item.get("historico_execucoes") or [])
        hist.insert(
            0,
            {
                "data": exec_iso,
                "responsavel": SecurityValidator.sanitizar_texto(actor) or item.get("responsavel") or "Técnico",
                "observacao": SecurityValidator.sanitizar_texto(observacao)[:300] or "Preventiva executada.",
            },
        )
        atualizado = self.store.update_item(
            "preventivas",
            item_id,
            {
                "ultima_execucao": exec_iso,
                "proxima_execucao": nova_prox,
                "historico_execucoes": hist[:25],
                "atualizado_em": _now_iso(),
            },
            tenant_id,
        )
        return self._enriquecer_preventiva(atualizado) if atualizado else None

    def atualizar_preventiva(
        self, tenant_id: str, item_id: str, payload: dict
    ) -> dict | None:
        existente = self.store.get_item("preventivas", item_id, tenant_id)
        if not existente:
            return None
        updates: dict[str, Any] = {"atualizado_em": _now_iso()}
        if "titulo" in payload and payload["titulo"] is not None:
            titulo = SecurityValidator.sanitizar_texto(payload["titulo"])
            if not titulo:
                raise ValueError("O título da preventiva não pode ficar vazio.")
            updates["titulo"] = titulo[:140]
        if "local_pop" in payload and payload["local_pop"] is not None:
            updates["local_pop"] = SecurityValidator.sanitizar_texto(payload["local_pop"])[:120]
        if "periodicidade_dias" in payload and payload["periodicidade_dias"] is not None:
            per = int(payload["periodicidade_dias"])
            if per < 1 or per > 1095:
                raise ValueError("Periodicidade deve estar entre 1 e 1095 dias.")
            updates["periodicidade_dias"] = per
        if "proxima_execucao" in payload and payload["proxima_execucao"] is not None:
            updates["proxima_execucao"] = SecurityValidator.validar_data(payload["proxima_execucao"])
        if "responsavel" in payload and payload["responsavel"] is not None:
            updates["responsavel"] = SecurityValidator.sanitizar_texto(payload["responsavel"])
        if "checklist_resumo" in payload and payload["checklist_resumo"] is not None:
            updates["checklist_resumo"] = SecurityValidator.sanitizar_texto(payload["checklist_resumo"])[:500]
        atualizado = self.store.update_item("preventivas", item_id, updates, tenant_id)
        return self._enriquecer_preventiva(atualizado) if atualizado else None

    def excluir_preventiva(self, tenant_id: str, item_id: str) -> bool:
        return self.store.delete_item("preventivas", item_id, tenant_id)

    # ------------------------------------------------------------------
    # CLIENTES, SETORES & POPS / TORRES
    # ------------------------------------------------------------------

    def listar_clientes(self, tenant_id: str) -> list[dict]:
        itens = self.store.list_items("clientes", tenant_id)
        return sorted(itens, key=lambda c: str(c.get("nome") or "").lower())

    def criar_cliente(self, tenant_id: str, payload: dict) -> dict:
        nome = SecurityValidator.sanitizar_texto(payload.get("nome"))
        if not nome:
            raise ValueError("Informe o nome do cliente, setor ou POP.")
        agora = _now_iso()
        item = {
            "id": f"cli_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant_id,
            "nome": nome[:140],
            "tipo": SecurityValidator.sanitizar_texto(payload.get("tipo")) or "Cliente / Empresa",
            "contato": SecurityValidator.sanitizar_texto(payload.get("contato"))[:100],
            "telefone": SecurityValidator.sanitizar_texto(payload.get("telefone"))[:40],
            "endereco": SecurityValidator.sanitizar_texto(payload.get("endereco"))[:200],
            "observacoes": SecurityValidator.sanitizar_texto(payload.get("observacoes"))[:400],
            "criado_em": agora,
            "atualizado_em": agora,
        }
        return self.store.create_item("clientes", item, tenant_id)

    def atualizar_cliente(
        self, tenant_id: str, item_id: str, payload: dict
    ) -> dict | None:
        existente = self.store.get_item("clientes", item_id, tenant_id)
        if not existente:
            return None
        updates: dict[str, Any] = {"atualizado_em": _now_iso()}
        if "nome" in payload and payload["nome"] is not None:
            nome = SecurityValidator.sanitizar_texto(payload["nome"])
            if not nome:
                raise ValueError("O nome do cliente/POP não pode ficar vazio.")
            updates["nome"] = nome[:140]
        for campo, limite in (
            ("tipo", 60),
            ("contato", 100),
            ("telefone", 40),
            ("endereco", 200),
            ("observacoes", 400),
        ):
            if campo in payload and payload[campo] is not None:
                updates[campo] = SecurityValidator.sanitizar_texto(payload[campo])[:limite]
        return self.store.update_item("clientes", item_id, updates, tenant_id)

    def excluir_cliente(self, tenant_id: str, item_id: str) -> bool:
        return self.store.delete_item("clientes", item_id, tenant_id)
