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
from ops_modules import OpsModulesService
from security import SecurityValidator

_LOJA_LOCK = threading.RLock()
_VALID_LOJA_COLLECTIONS = {
    "loja_clientes",
    "loja_vendas",
    "loja_orcamentos",
    "loja_caixa",
    "loja_rmas",
}

FORMAS_PAGAMENTO = (
    "Pix",
    "Cartão de Crédito",
    "Cartão de Débito",
    "Dinheiro",
    "Boleto / Faturado B2B",
    "Misto",
)

TIPOS_DOC_FISCAL = ("NFC-e", "NF-e (CNPJ)", "Recibo Interno")

STATUS_ORCAMENTO = ("Rascunho", "Enviado", "Aprovado", "Recusado", "Convertido em Venda", "Convertido em O.S.")

STATUS_RMA = (
    "Aberto",
    "Em Análise Bancada",
    "Enviado ao Fornecedor",
    "Crédito / Troca Aprovada",
    "Concluído",
    "Negado",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hoje_iso() -> str:
    return date.today().strftime("%Y-%m-%d")


def _fmt_brl_num(v: Any) -> float:
    return SecurityValidator.validate_cost(v)


def _limpar_dict_vazio() -> dict[str, list]:
    return {col: [] for col in _VALID_LOJA_COLLECTIONS}


class LocalLojaStore:
    """Armazenamento local atômico em JSON para todo o setor Loja de TI."""

    def __init__(self, path: str | Path | None = None):
        self._custom_path = Path(path) if path else None

    @property
    def path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "loja_db.json"

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            data = _limpar_dict_vazio()
            data["version"] = 1
            return data
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Erro ao ler {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            data = _limpar_dict_vazio()
        for col in _VALID_LOJA_COLLECTIONS:
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

    def list_items(self, collection: str, tenant_id: str) -> list[dict]:
        if collection not in _VALID_LOJA_COLLECTIONS:
            raise ValueError("Coleção da Loja de TI inválida.")
        with _LOJA_LOCK:
            return [
                dict(row)
                for row in self._read_unlocked()[collection]
                if row.get("tenant_id") == tenant_id
            ]

    def get_item(self, collection: str, item_id: str, tenant_id: str) -> dict | None:
        for row in self.list_items(collection, tenant_id):
            if row.get("id") == item_id:
                return row
        return None

    def create_item(self, collection: str, item: dict, tenant_id: str) -> dict:
        if collection not in _VALID_LOJA_COLLECTIONS:
            raise ValueError("Coleção da Loja de TI inválida.")
        with _LOJA_LOCK:
            data = self._read_unlocked()
            row = dict(item)
            row["tenant_id"] = tenant_id
            data[collection].append(row)
            self._write_unlocked(data)
            return dict(row)

    def update_item(
        self, collection: str, item_id: str, updates: dict, tenant_id: str
    ) -> dict | None:
        if collection not in _VALID_LOJA_COLLECTIONS:
            raise ValueError("Coleção da Loja de TI inválida.")
        with _LOJA_LOCK:
            data = self._read_unlocked()
            for row in data[collection]:
                if row.get("id") == item_id and row.get("tenant_id") == tenant_id:
                    row.update(updates)
                    row["tenant_id"] = tenant_id
                    self._write_unlocked(data)
                    return dict(row)
        return None

    def delete_item(self, collection: str, item_id: str, tenant_id: str) -> bool:
        if collection not in _VALID_LOJA_COLLECTIONS:
            raise ValueError("Coleção da Loja de TI inválida.")
        with _LOJA_LOCK:
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


class FirestoreLojaStore:
    """Armazenamento Cloud Firestore isolado por tenant para o setor Loja de TI."""

    def __init__(self, db: Any = None):
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from firebase_backend import get_firestore_client

            self._db = get_firestore_client()
        return self._db

    def _col_ref(self, tenant_id: str, collection: str):
        if collection not in _VALID_LOJA_COLLECTIONS:
            raise ValueError("Coleção da Loja de TI inválida.")
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


_local_loja_store = LocalLojaStore()


def get_loja_store():
    if get_storage_backend() == "firebase":
        return FirestoreLojaStore()
    return _local_loja_store


def filtrar_custos_venda(venda: dict, incluir_custos: bool) -> dict:
    dados = dict(venda)
    if incluir_custos:
        return dados
    for campo in ("custo_total", "lucro_estimado", "margem_percent", "comissao_valor"):
        dados.pop(campo, None)
    itens_clean = []
    for it in dados.get("itens") or []:
        row = dict(it)
        row.pop("custo_unitario", None)
        itens_clean.append(row)
    dados["itens"] = itens_clean
    return dados


def filtrar_custos_orcamento(orc: dict, incluir_custos: bool) -> dict:
    dados = dict(orc)
    if incluir_custos:
        return dados
    for campo in ("custo_estimado", "lucro_estimado"):
        dados.pop(campo, None)
    return dados


def filtrar_custos_rma(rma: dict, incluir_custos: bool) -> dict:
    dados = dict(rma)
    if not incluir_custos:
        dados.pop("valor_credito", None)
    return dados


class LojaTIService:
    """Serviço completo de gestão de Loja de TI (PDV, Orçamentos, Caixa, Garantias/RMA e Clientes B2B/PF)."""

    def __init__(self, store=None, ops_service: OpsModulesService | None = None):
        self._store = store
        self._ops = ops_service

    @property
    def store(self):
        return self._store if self._store is not None else get_loja_store()

    @property
    def ops(self) -> OpsModulesService:
        return self._ops if self._ops is not None else OpsModulesService()

    # ------------------------------------------------------------------
    # CLIENTES PF/PJ (B2B), FORNECEDORES & FILIAIS
    # ------------------------------------------------------------------

    def listar_clientes(self, tenant_id: str, tipo: str | None = None) -> list[dict]:
        itens = self.store.list_items("loja_clientes", tenant_id)
        if tipo:
            alvo = tipo.strip().lower()
            itens = [i for i in itens if alvo in str(i.get("tipo") or "").lower()]
        return sorted(itens, key=lambda c: str(c.get("nome") or "").lower())

    def criar_cliente(self, tenant_id: str, payload: dict) -> dict:
        nome = SecurityValidator.sanitizar_texto(payload.get("nome"))
        if not nome:
            raise ValueError("Informe o nome ou razão social do cliente/fornecedor.")
        tabela = str(payload.get("tabela_preco") or "varejo").strip().lower()
        if tabela not in {"varejo", "b2b"}:
            tabela = "varejo"
        agora = _now_iso()
        item = {
            "id": f"lcli_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant_id,
            "nome": nome[:140],
            "tipo": SecurityValidator.sanitizar_texto(payload.get("tipo"))[:40] or "Cliente PF",
            "documento": SecurityValidator.sanitizar_texto(payload.get("documento"))[:32],
            "tabela_preco": tabela,
            "telefone": SecurityValidator.sanitizar_texto(payload.get("telefone"))[:40],
            "email": SecurityValidator.sanitizar_texto(payload.get("email"))[:120],
            "contato": SecurityValidator.sanitizar_texto(payload.get("contato"))[:100],
            "endereco": SecurityValidator.sanitizar_texto(payload.get("endereco"))[:200],
            "limite_credito": _fmt_brl_num(payload.get("limite_credito")),
            "observacoes": SecurityValidator.sanitizar_texto(payload.get("observacoes"))[:400],
            "criado_em": agora,
            "atualizado_em": agora,
        }
        return self.store.create_item("loja_clientes", item, tenant_id)

    def atualizar_cliente(self, tenant_id: str, item_id: str, payload: dict) -> dict | None:
        existente = self.store.get_item("loja_clientes", item_id, tenant_id)
        if not existente:
            return None
        updates: dict[str, Any] = {"atualizado_em": _now_iso()}
        if "nome" in payload and payload["nome"] is not None:
            nome = SecurityValidator.sanitizar_texto(payload["nome"])
            if not nome:
                raise ValueError("O nome não pode ficar vazio.")
            updates["nome"] = nome[:140]
        for campo, lim in (
            ("tipo", 40),
            ("documento", 32),
            ("telefone", 40),
            ("email", 120),
            ("contato", 100),
            ("endereco", 200),
            ("observacoes", 400),
        ):
            if campo in payload and payload[campo] is not None:
                updates[campo] = SecurityValidator.sanitizar_texto(payload[campo])[:lim]
        if "tabela_preco" in payload and payload["tabela_preco"] is not None:
            tab = str(payload["tabela_preco"]).strip().lower()
            updates["tabela_preco"] = "b2b" if tab == "b2b" else "varejo"
        if "limite_credito" in payload and payload["limite_credito"] is not None:
            updates["limite_credito"] = _fmt_brl_num(payload["limite_credito"])
        return self.store.update_item("loja_clientes", item_id, updates, tenant_id)

    def excluir_cliente(self, tenant_id: str, item_id: str) -> bool:
        return self.store.delete_item("loja_clientes", item_id, tenant_id)

    # ------------------------------------------------------------------
    # CAIXA PDV (ABERTURA, SUPRIMENTO, SANGRIA E FECHAMENTO)
    # ------------------------------------------------------------------

    def listar_turnos_caixa(self, tenant_id: str) -> list[dict]:
        turnos = self.store.list_items("loja_caixa", tenant_id)
        return sorted(turnos, key=lambda c: str(c.get("aberto_em") or ""), reverse=True)

    def obter_caixa_atual(self, tenant_id: str) -> dict | None:
        for turno in self.listar_turnos_caixa(tenant_id):
            if turno.get("status") == "Aberto":
                return turno
        return None

    def abrir_caixa(
        self,
        tenant_id: str,
        valor_abertura: float,
        operador: str = "",
        observacao: str = "",
        filial: str = "Matriz",
    ) -> dict:
        if self.obter_caixa_atual(tenant_id):
            raise ValueError("Já existe um turno de caixa aberto nesta loja. Feche-o antes de abrir outro.")
        fundo = _fmt_brl_num(valor_abertura)
        agora = _now_iso()
        turno = {
            "id": f"cx_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant_id,
            "status": "Aberto",
            "filial": SecurityValidator.sanitizar_texto(filial)[:60] or "Matriz",
            "operador": SecurityValidator.sanitizar_texto(operador)[:80] or "Operador",
            "valor_abertura": fundo,
            "saldo_esperado": fundo,
            "saldo_informado": None,
            "diferenca": 0.0,
            "total_vendido": 0.0,
            "total_dinheiro": 0.0,
            "total_suprimentos": 0.0,
            "total_sangrias": 0.0,
            "qtd_vendas": 0,
            "por_forma": {},
            "movimentacoes": [
                {
                    "id": f"cxm_{uuid.uuid4().hex[:8]}",
                    "data_hora": agora,
                    "tipo": "abertura",
                    "valor": fundo,
                    "motivo": SecurityValidator.sanitizar_texto(observacao)[:160] or "Fundo de troco inicial",
                    "autor": SecurityValidator.sanitizar_texto(operador)[:80] or "Operador",
                }
            ],
            "aberto_em": agora,
            "fechado_em": "",
            "observacao_fechamento": "",
        }
        return self.store.create_item("loja_caixa", turno, tenant_id)

    def movimentar_caixa(
        self,
        tenant_id: str,
        tipo: str,
        valor: float,
        motivo: str = "",
        operador: str = "",
    ) -> dict:
        turno = self.obter_caixa_atual(tenant_id)
        if not turno:
            raise ValueError("Nenhum caixa aberto no momento. Abra o caixa primeiro.")
        tipo_norm = SecurityValidator.sanitizar_texto(tipo).lower()
        if tipo_norm not in {"suprimento", "sangria"}:
            raise ValueError("Tipo inválido. Use 'suprimento' ou 'sangria'.")
        val = _fmt_brl_num(valor)
        if val <= 0:
            raise ValueError("Informe um valor maior que zero.")
        saldo_atual = float(turno.get("saldo_esperado") or 0.0)
        if tipo_norm == "sangria" and val > saldo_atual:
            raise ValueError(f"Saldo em gaveta insuficiente para sangria (disponível: R$ {saldo_atual:.2f}).")

        novo_saldo = round(saldo_atual + val if tipo_norm == "suprimento" else saldo_atual - val, 2)
        movs = list(turno.get("movimentacoes") or [])
        movs.insert(
            0,
            {
                "id": f"cxm_{uuid.uuid4().hex[:8]}",
                "data_hora": _now_iso(),
                "tipo": tipo_norm,
                "valor": val,
                "motivo": SecurityValidator.sanitizar_texto(motivo)[:180] or tipo_norm.capitalize(),
                "autor": SecurityValidator.sanitizar_texto(operador)[:80] or "Operador",
            },
        )
        updates = {
            "saldo_esperado": novo_saldo,
            "total_suprimentos": round(float(turno.get("total_suprimentos") or 0.0) + (val if tipo_norm == "suprimento" else 0.0), 2),
            "total_sangrias": round(float(turno.get("total_sangrias") or 0.0) + (val if tipo_norm == "sangria" else 0.0), 2),
            "movimentacoes": movs[:80],
        }
        return self.store.update_item("loja_caixa", turno["id"], updates, tenant_id) or turno

    def fechar_caixa(
        self,
        tenant_id: str,
        saldo_informado: float,
        observacao: str = "",
        operador: str = "",
    ) -> dict:
        turno = self.obter_caixa_atual(tenant_id)
        if not turno:
            raise ValueError("Não há caixa aberto para fechar.")
        informado = _fmt_brl_num(saldo_informado)
        esperado = round(float(turno.get("saldo_esperado") or 0.0), 2)
        diferenca = round(informado - esperado, 2)
        movs = list(turno.get("movimentacoes") or [])
        agora = _now_iso()
        movs.insert(
            0,
            {
                "id": f"cxm_{uuid.uuid4().hex[:8]}",
                "data_hora": agora,
                "tipo": "fechamento",
                "valor": informado,
                "motivo": f"Conferência de fechamento (Esperado R$ {esperado:.2f} | Dif. R$ {diferenca:.2f})",
                "autor": SecurityValidator.sanitizar_texto(operador)[:80] or "Operador",
            },
        )
        updates = {
            "status": "Fechado",
            "saldo_informado": informado,
            "diferenca": diferenca,
            "fechado_em": agora,
            "fechado_por": SecurityValidator.sanitizar_texto(operador)[:80],
            "observacao_fechamento": SecurityValidator.sanitizar_texto(observacao)[:240],
            "movimentacoes": movs[:80],
        }
        return self.store.update_item("loja_caixa", turno["id"], updates, tenant_id) or turno

    def _registrar_venda_no_caixa_aberto(
        self, tenant_id: str, venda: dict, eh_estorno: bool = False, actor: str = ""
    ) -> None:
        turno = self.obter_caixa_atual(tenant_id)
        if not turno:
            return
        total = float(venda.get("total") or 0.0)
        sinal = -1.0 if eh_estorno else 1.0
        forma = str(venda.get("forma_pagamento") or "Pix")
        por_forma = dict(turno.get("por_forma") or {})
        por_forma[forma] = round(max(0.0, float(por_forma.get(forma) or 0.0) + sinal * total), 2)

        delta_gaveta = total if forma == "Dinheiro" else 0.0
        saldo_esperado = round(max(0.0, float(turno.get("saldo_esperado") or 0.0) + sinal * delta_gaveta), 2)
        movs = list(turno.get("movimentacoes") or [])
        movs.insert(
            0,
            {
                "id": f"cxm_{uuid.uuid4().hex[:8]}",
                "data_hora": _now_iso(),
                "tipo": "estorno" if eh_estorno else "venda",
                "valor": total,
                "motivo": f"{'Estorno' if eh_estorno else 'Venda'} {venda.get('codigo')} ({forma})",
                "autor": SecurityValidator.sanitizar_texto(actor) or venda.get("vendedor") or "PDV",
            },
        )
        self.store.update_item(
            "loja_caixa",
            turno["id"],
            {
                "total_vendido": round(max(0.0, float(turno.get("total_vendido") or 0.0) + sinal * total), 2),
                "total_dinheiro": round(max(0.0, float(turno.get("total_dinheiro") or 0.0) + sinal * delta_gaveta), 2),
                "saldo_esperado": saldo_esperado,
                "qtd_vendas": max(0, int(turno.get("qtd_vendas") or 0) + (-1 if eh_estorno else 1)),
                "por_forma": por_forma,
                "movimentacoes": movs[:80],
            },
            tenant_id,
        )

    # ------------------------------------------------------------------
    # VENDAS PDV & BAIXA DE ESTOQUE + RASTREIO DE SERIAL / GARANTIA
    # ------------------------------------------------------------------

    def listar_vendas(self, tenant_id: str) -> list[dict]:
        vendas = self.store.list_items("loja_vendas", tenant_id)
        return sorted(vendas, key=lambda v: str(v.get("criado_em") or ""), reverse=True)

    def buscar_venda(self, tenant_id: str, venda_id: str) -> dict | None:
        return self.store.get_item("loja_vendas", venda_id, tenant_id)

    def registrar_venda(self, tenant_id: str, payload: dict, actor: str = "") -> dict:
        raw_itens = payload.get("itens") or []
        if not isinstance(raw_itens, list) or not raw_itens:
            raise ValueError("Adicione pelo menos um produto ou serviço para concluir a venda.")

        tabela = str(payload.get("tabela_preco") or "varejo").strip().lower()
        if tabela not in {"varejo", "b2b"}:
            tabela = "varejo"

        estoque_cache = {i["id"]: i for i in self.ops.listar_estoque(tenant_id)}
        qtd_por_sku: dict[str, int] = {}
        itens_finais: list[dict[str, Any]] = []
        subtotal = 0.0
        custo_total = 0.0
        hoje = date.today()

        for entry in raw_itens[:50]:
            if not isinstance(entry, dict):
                continue
            est_id = SecurityValidator.sanitizar_texto(entry.get("estoque_item_id"))
            qtd = int(entry.get("quantidade") or 1)
            if qtd < 1:
                raise ValueError("A quantidade de cada item deve ser maior que zero.")

            est_item = estoque_cache.get(est_id) if est_id else None
            if est_id and not est_item:
                raise LookupError(f"Item de estoque '{est_id}' não encontrado nesta empresa.")

            if est_item:
                qtd_por_sku[est_id] = qtd_por_sku.get(est_id, 0) + qtd
                disp = int(est_item.get("quantidade") or 0)
                if qtd_por_sku[est_id] > disp:
                    raise ValueError(
                        f"Estoque insuficiente para '{est_item['nome']}' (disponível: {disp}, solicitado: {qtd_por_sku[est_id]})."
                    )

            nome_item = SecurityValidator.sanitizar_texto(entry.get("nome")) or (
                est_item.get("nome") if est_item else ""
            )
            if not nome_item:
                raise ValueError("Todo item da venda precisa ter uma descrição/nome.")

            if entry.get("preco_unitario") is not None:
                preco_unit = _fmt_brl_num(entry.get("preco_unitario"))
            elif est_item:
                if tabela == "b2b" and float(est_item.get("preco_atacado_b2b") or 0.0) > 0:
                    preco_unit = float(est_item["preco_atacado_b2b"])
                else:
                    preco_unit = float(est_item.get("preco_venda") or 0.0)
            else:
                preco_unit = 0.0

            custo_unit = float(est_item.get("custo_unitario") or 0.0) if est_item else _fmt_brl_num(entry.get("custo_unitario"))
            gar_meses = entry.get("garantia_meses")
            if gar_meses is None:
                gar_meses = int(est_item.get("garantia_meses") or 3) if est_item else 3
            gar_meses = max(0, min(120, int(gar_meses)))
            gar_ate = (hoje + timedelta(days=gar_meses * 30)).strftime("%Y-%m-%d") if gar_meses > 0 else ""

            serial_imei = SecurityValidator.sanitizar_texto(entry.get("serial_imei"))[:100]
            if est_item and est_item.get("controlar_serial") and not serial_imei:
                raise ValueError(f"O item '{nome_item}' exige o preenchimento do Número de Série / IMEI.")

            total_item = round(preco_unit * qtd, 2)
            subtotal = round(subtotal + total_item, 2)
            custo_total = round(custo_total + custo_unit * qtd, 2)

            itens_finais.append(
                {
                    "estoque_item_id": est_id,
                    "sku": SecurityValidator.sanitizar_texto(entry.get("sku")) or (est_item.get("codigo", "") if est_item else ""),
                    "nome": nome_item[:140],
                    "quantidade": qtd,
                    "preco_unitario": preco_unit,
                    "custo_unitario": custo_unit,
                    "total_item": total_item,
                    "serial_imei": serial_imei,
                    "garantia_meses": gar_meses,
                    "garantia_ate": gar_ate,
                }
            )

        if not itens_finais:
            raise ValueError("Nenhum item válido foi informado para a venda.")

        desconto = _fmt_brl_num(payload.get("desconto"))
        if desconto > subtotal:
            desconto = subtotal
        total_final = round(max(0.0, subtotal - desconto), 2)
        lucro = round(total_final - custo_total, 2)
        margem = round((lucro / total_final) * 100.0, 1) if total_final > 0 else 0.0

        comissao_pct = max(0.0, min(50.0, float(payload.get("comissao_percent") or 0.0)))
        comissao_val = round(total_final * (comissao_pct / 100.0), 2)

        forma = SecurityValidator.sanitizar_texto(payload.get("forma_pagamento")) or "Pix"
        if forma not in FORMAS_PAGAMENTO:
            forma = "Pix"
        parcelas = max(1, min(24, int(payload.get("parcelas") or 1)))
        tipo_doc = SecurityValidator.sanitizar_texto(payload.get("tipo_documento")) or "Recibo Interno"
        if tipo_doc not in TIPOS_DOC_FISCAL:
            tipo_doc = "Recibo Interno"

        vendas_existentes = self.listar_vendas(tenant_id)
        seq = len(vendas_existentes) + 1
        venda_id = f"vnd_{uuid.uuid4().hex[:10]}"
        codigo = f"VND-{hoje.strftime('%Y%m%d')}-{seq:04d}"

        vendedor = SecurityValidator.sanitizar_texto(payload.get("vendedor")) or SecurityValidator.sanitizar_texto(actor) or "Balcão"
        cliente_nome = SecurityValidator.sanitizar_texto(payload.get("cliente_nome")) or "Consumidor Final"
        cliente_id = SecurityValidator.sanitizar_texto(payload.get("cliente_id"))
        cliente_doc = SecurityValidator.sanitizar_texto(payload.get("cliente_doc"))[:32]
        if cliente_id and (not cliente_doc or cliente_nome == "Consumidor Final"):
            cli = self.store.get_item("loja_clientes", cliente_id, tenant_id)
            if cli:
                if cliente_nome == "Consumidor Final":
                    cliente_nome = cli.get("nome", cliente_nome)
                if not cliente_doc:
                    cliente_doc = cli.get("documento", "")

        # Efetua a baixa automática no estoque vinculado
        for it in itens_finais:
            if it["estoque_item_id"]:
                self.ops.movimentar_estoque(
                    tenant_id=tenant_id,
                    item_id=it["estoque_item_id"],
                    tipo="saida",
                    quantidade=it["quantidade"],
                    observacao=f"Venda PDV {codigo} ({cliente_nome})",
                    actor=vendedor,
                    venda_id=venda_id,
                )

        agora = _now_iso()
        venda = {
            "id": venda_id,
            "codigo": codigo,
            "tenant_id": tenant_id,
            "status": "Concluída",
            "data_venda": hoje.strftime("%Y-%m-%d"),
            "filial": SecurityValidator.sanitizar_texto(payload.get("filial"))[:60] or "Matriz",
            "cliente_id": cliente_id,
            "cliente_nome": cliente_nome[:140],
            "cliente_doc": cliente_doc,
            "tabela_preco": tabela,
            "vendedor": vendedor[:80],
            "comissao_percent": comissao_pct,
            "comissao_valor": comissao_val,
            "forma_pagamento": forma,
            "parcelas": parcelas,
            "tipo_documento": tipo_doc,
            "numero_nf": SecurityValidator.sanitizar_texto(payload.get("numero_nf"))[:40],
            "itens": itens_finais,
            "subtotal": subtotal,
            "desconto": desconto,
            "total": total_final,
            "custo_total": custo_total,
            "lucro_estimado": lucro,
            "margem_percent": margem,
            "orcamento_origem_id": SecurityValidator.sanitizar_texto(payload.get("orcamento_origem_id")),
            "observacoes": SecurityValidator.sanitizar_texto(payload.get("observacoes"))[:400],
            "criado_em": agora,
            "cancelado_em": "",
            "motivo_cancelamento": "",
        }
        criada = self.store.create_item("loja_vendas", venda, tenant_id)
        self._registrar_venda_no_caixa_aberto(tenant_id, criada, eh_estorno=False, actor=vendedor)
        return criada

    def cancelar_venda(
        self, tenant_id: str, venda_id: str, motivo: str = "", actor: str = ""
    ) -> dict | None:
        venda = self.store.get_item("loja_vendas", venda_id, tenant_id)
        if not venda:
            return None
        if venda.get("status") == "Cancelada":
            raise ValueError("Esta venda já foi cancelada anteriormente.")

        for it in venda.get("itens") or []:
            est_id = it.get("estoque_item_id")
            if est_id and self.ops.store.get_item("estoque", est_id, tenant_id):
                self.ops.movimentar_estoque(
                    tenant_id=tenant_id,
                    item_id=est_id,
                    tipo="entrada",
                    quantidade=int(it.get("quantidade") or 1),
                    observacao=f"Estorno da venda {venda.get('codigo')}",
                    actor=actor or "PDV",
                    venda_id=venda_id,
                )

        atualizada = self.store.update_item(
            "loja_vendas",
            venda_id,
            {
                "status": "Cancelada",
                "cancelado_em": _now_iso(),
                "cancelado_por": SecurityValidator.sanitizar_texto(actor),
                "motivo_cancelamento": SecurityValidator.sanitizar_texto(motivo)[:240] or "Cancelamento no PDV",
            },
            tenant_id,
        )
        if atualizada:
            self._registrar_venda_no_caixa_aberto(tenant_id, venda, eh_estorno=True, actor=actor)
        return atualizada

    # ------------------------------------------------------------------
    # ORÇAMENTOS COMERCIAIS & MONTAGEM DE PCS SOB MEDIDA
    # ------------------------------------------------------------------

    @staticmethod
    def _enriquecer_orcamento(orc: dict) -> dict:
        row = dict(orc)
        val_str = str(row.get("valido_ate") or "").strip()
        row["expirado"] = False
        if val_str and row.get("status") in {"Rascunho", "Enviado"}:
            try:
                dt_val = datetime.strptime(SecurityValidator.validar_data(val_str), "%Y-%m-%d").date()
                row["expirado"] = dt_val < date.today()
            except ValueError:
                pass
        return row

    def listar_orcamentos(self, tenant_id: str) -> list[dict]:
        itens = [self._enriquecer_orcamento(o) for o in self.store.list_items("loja_orcamentos", tenant_id)]
        return sorted(itens, key=lambda o: str(o.get("criado_em") or ""), reverse=True)

    def criar_orcamento(self, tenant_id: str, payload: dict, actor: str = "") -> dict:
        cliente_nome = SecurityValidator.sanitizar_texto(payload.get("cliente_nome"))
        if not cliente_nome:
            raise ValueError("Informe o nome do cliente para emitir o orçamento.")
        raw_itens = payload.get("itens") or []
        if not isinstance(raw_itens, list) or not raw_itens:
            raise ValueError("Adicione pelo menos 1 item/peça ou serviço ao orçamento.")

        itens_norm: list[dict[str, Any]] = []
        subtotal = 0.0
        for entry in raw_itens[:50]:
            if not isinstance(entry, dict):
                continue
            nome = SecurityValidator.sanitizar_texto(entry.get("nome"))[:140]
            if not nome:
                continue
            qtd = max(1, int(entry.get("quantidade") or 1))
            preco = _fmt_brl_num(entry.get("preco_unitario"))
            gar_m = max(0, min(120, int(entry.get("garantia_meses") or 3)))
            tot = round(preco * qtd, 2)
            subtotal = round(subtotal + tot, 2)
            itens_norm.append(
                {
                    "estoque_item_id": SecurityValidator.sanitizar_texto(entry.get("estoque_item_id")),
                    "nome": nome,
                    "quantidade": qtd,
                    "preco_unitario": preco,
                    "total_item": tot,
                    "garantia_meses": gar_m,
                }
            )
        if not itens_norm:
            raise ValueError("Informe itens válidos para o orçamento.")

        desconto = min(subtotal, _fmt_brl_num(payload.get("desconto")))
        total = round(max(0.0, subtotal - desconto), 2)
        val_dias = max(1, min(180, int(payload.get("validade_dias") or 7)))
        valido_ate = (date.today() + timedelta(days=val_dias)).strftime("%Y-%m-%d")
        seq = len(self.store.list_items("loja_orcamentos", tenant_id)) + 1

        orc = {
            "id": f"lorc_{uuid.uuid4().hex[:10]}",
            "codigo": f"ORC-{date.today().strftime('%Y%m%d')}-{seq:04d}",
            "tenant_id": tenant_id,
            "titulo": SecurityValidator.sanitizar_texto(payload.get("titulo"))[:120] or "Proposta Comercial / Montagem PC",
            "tipo": SecurityValidator.sanitizar_texto(payload.get("tipo"))[:40] or "Montagem PC / Hardware",
            "cliente_id": SecurityValidator.sanitizar_texto(payload.get("cliente_id")),
            "cliente_nome": cliente_nome[:140],
            "cliente_doc": SecurityValidator.sanitizar_texto(payload.get("cliente_doc"))[:32],
            "cliente_telefone": SecurityValidator.sanitizar_texto(payload.get("cliente_telefone"))[:40],
            "vendedor": SecurityValidator.sanitizar_texto(payload.get("vendedor")) or SecurityValidator.sanitizar_texto(actor) or "Consultor",
            "tabela_preco": "b2b" if str(payload.get("tabela_preco") or "").lower() == "b2b" else "varejo",
            "itens": itens_norm,
            "subtotal": subtotal,
            "desconto": desconto,
            "total": total,
            "valido_ate": valido_ate,
            "status": "Enviado",
            "condicoes_pagamento": SecurityValidator.sanitizar_texto(payload.get("condicoes_pagamento"))[:200] or "Pix à vista ou Cartão em até 12x",
            "observacoes": SecurityValidator.sanitizar_texto(payload.get("observacoes"))[:400],
            "venda_gerada_id": "",
            "os_gerada_id": None,
            "criado_em": _now_iso(),
            "atualizado_em": _now_iso(),
        }
        return self._enriquecer_orcamento(self.store.create_item("loja_orcamentos", orc, tenant_id))

    def atualizar_status_orcamento(self, tenant_id: str, orc_id: str, status_novo: str) -> dict | None:
        if status_novo not in STATUS_ORCAMENTO:
            raise ValueError("Status de orçamento inválido.")
        updated = self.store.update_item(
            "loja_orcamentos",
            orc_id,
            {"status": status_novo, "atualizado_em": _now_iso()},
            tenant_id,
        )
        return self._enriquecer_orcamento(updated) if updated else None

    def excluir_orcamento(self, tenant_id: str, orc_id: str) -> bool:
        return self.store.delete_item("loja_orcamentos", orc_id, tenant_id)

    def converter_orcamento_em_venda(
        self, tenant_id: str, orc_id: str, payload: dict, actor: str = ""
    ) -> dict:
        orc = self.store.get_item("loja_orcamentos", orc_id, tenant_id)
        if not orc:
            raise LookupError("Orçamento não encontrado.")
        if orc.get("venda_gerada_id"):
            raise ValueError("Este orçamento já foi convertido em venda.")

        venda_payload = {
            "cliente_id": orc.get("cliente_id") or "",
            "cliente_nome": orc.get("cliente_nome") or "Cliente",
            "cliente_doc": orc.get("cliente_doc") or "",
            "tabela_preco": orc.get("tabela_preco") or "varejo",
            "itens": orc.get("itens") or [],
            "desconto": payload.get("desconto") if payload.get("desconto") is not None else orc.get("desconto", 0.0),
            "forma_pagamento": payload.get("forma_pagamento") or "Pix",
            "parcelas": payload.get("parcelas") or 1,
            "tipo_documento": payload.get("tipo_documento") or "Recibo Interno",
            "numero_nf": payload.get("numero_nf") or "",
            "vendedor": actor or orc.get("vendedor") or "Vendedor",
            "orcamento_origem_id": orc_id,
            "observacoes": f"Convertido do orçamento {orc.get('codigo')}.",
        }
        venda = self.registrar_venda(tenant_id, venda_payload, actor=actor)
        self.store.update_item(
            "loja_orcamentos",
            orc_id,
            {
                "status": "Convertido em Venda",
                "venda_gerada_id": venda["id"],
                "atualizado_em": _now_iso(),
            },
            tenant_id,
        )
        return {"venda": venda, "orcamento": self._enriquecer_orcamento(self.store.get_item("loja_orcamentos", orc_id, tenant_id) or orc)}

    def converter_orcamento_em_os(
        self,
        tenant_id: str,
        orc_id: str,
        localizacao: str = "Bancada TI",
        tenant_nome: str = "SysFix",
        actor: str = "",
    ) -> dict:
        orc = self.store.get_item("loja_orcamentos", orc_id, tenant_id)
        if not orc:
            raise LookupError("Orçamento não encontrado.")
        if orc.get("os_gerada_id"):
            raise ValueError(f"Este orçamento já gerou a O.S. #{orc['os_gerada_id']}.")

        resumo_itens = ", ".join(
            f"{i['quantidade']}x {i['nome']}" for i in (orc.get("itens") or [])[:6]
        )
        os_id = int(
            ColetaModel.registrar_entrada(
                equipamento=f"{orc.get('titulo') or 'Serviço / Montagem'} ({orc.get('codigo')})"[:120],
                tombamento="S/N",
                tipo_tombamento="Sem tombamento",
                os_coleta=orc.get("codigo") or _hoje_iso(),
                origem=orc.get("cliente_nome") or "Loja de TI",
                data_coleta=_hoje_iso(),
                localizacao=localizacao or "Bancada TI",
                tecnico=SecurityValidator.sanitizar_texto(actor) or orc.get("vendedor") or "Loja",
                problema=f"Proposta aprovada ({orc.get('codigo')}): {resumo_itens}"[:450],
                categoria="PC Gamer / Workstation" if "pc" in str(orc.get("tipo") or "").lower() else "Hardware / Peças",
                tipo_fluxo="Montagem / Upgrade PC",
                prioridade="Normal",
                tenant_id=tenant_id,
                actor=actor or "Loja de TI",
                tenant_nome=tenant_nome,
            )
        )
        total_orc = float(orc.get("total") or 0.0)
        if total_orc > 0:
            ColetaModel.salvar_orcamento(
                os_id,
                descricao=f"Orçamento {orc.get('codigo')}: {resumo_itens}",
                valor=total_orc,
                tenant_id=tenant_id,
                actor=actor or "Loja de TI",
            )
        self.store.update_item(
            "loja_orcamentos",
            orc_id,
            {
                "status": "Convertido em O.S.",
                "os_gerada_id": os_id,
                "atualizado_em": _now_iso(),
            },
            tenant_id,
        )
        return {"registro": ColetaModel.buscar_por_id(os_id, tenant_id=tenant_id) or {"id": os_id}, "orcamento_id": orc_id}

    # ------------------------------------------------------------------
    # GARANTIA ANTIFRAUDE POR SERIAL / IMEI & GESTÃO DE RMA
    # ------------------------------------------------------------------

    def consultar_garantia_serial(self, tenant_id: str, termo: str) -> list[dict]:
        busca = SecurityValidator.sanitizar_texto(termo).lower()
        if not busca:
            raise ValueError("Informe um Número de Série, IMEI ou código da venda para consultar.")
        resultados: list[dict] = []
        hoje_str = _hoje_iso()
        for v in self.listar_vendas(tenant_id):
            if v.get("status") == "Cancelada":
                continue
            cod_match = busca in str(v.get("codigo") or "").lower()
            for it in v.get("itens") or []:
                sn = str(it.get("serial_imei") or "").strip()
                if cod_match or (sn and busca in sn.lower()):
                    gar_ate = str(it.get("garantia_ate") or "")
                    em_garantia = bool(gar_ate and gar_ate >= hoje_str)
                    resultados.append(
                        {
                            "venda_id": v["id"],
                            "venda_codigo": v["codigo"],
                            "data_venda": v.get("data_venda"),
                            "cliente_nome": v.get("cliente_nome"),
                            "cliente_doc": v.get("cliente_doc") or "",
                            "produto": it.get("nome"),
                            "serial_imei": sn or "Não informado",
                            "garantia_meses": int(it.get("garantia_meses") or 0),
                            "garantia_ate": gar_ate or "Sem garantia",
                            "em_garantia": em_garantia,
                            "numero_nf": v.get("numero_nf") or "",
                        }
                    )
        return resultados

    def listar_rmas(self, tenant_id: str) -> list[dict]:
        itens = self.store.list_items("loja_rmas", tenant_id)
        return sorted(itens, key=lambda r: str(r.get("criado_em") or ""), reverse=True)

    def criar_rma(self, tenant_id: str, payload: dict, actor: str = "") -> dict:
        produto = SecurityValidator.sanitizar_texto(payload.get("produto"))
        if not produto:
            raise ValueError("Informe o produto/componente em garantia ou RMA.")
        tipo = SecurityValidator.sanitizar_texto(payload.get("tipo"))[:40] or "RMA Cliente"
        status = SecurityValidator.sanitizar_texto(payload.get("status")) or "Aberto"
        if status not in STATUS_RMA:
            status = "Aberto"
        agora = _now_iso()
        rma = {
            "id": f"rma_{uuid.uuid4().hex[:10]}",
            "codigo": f"RMA-{date.today().strftime('%Y%m%d')}-{len(self.store.list_items('loja_rmas', tenant_id)) + 1:03d}",
            "tenant_id": tenant_id,
            "tipo": tipo,
            "produto": produto[:140],
            "serial_imei": SecurityValidator.sanitizar_texto(payload.get("serial_imei"))[:100],
            "venda_codigo": SecurityValidator.sanitizar_texto(payload.get("venda_codigo"))[:40],
            "cliente_fornecedor": SecurityValidator.sanitizar_texto(payload.get("cliente_fornecedor"))[:140],
            "defeito": SecurityValidator.sanitizar_texto(payload.get("defeito"))[:300],
            "protocolo_fabricante": SecurityValidator.sanitizar_texto(payload.get("protocolo_fabricante"))[:80],
            "valor_credito": _fmt_brl_num(payload.get("valor_credito")),
            "status": status,
            "resolucao": SecurityValidator.sanitizar_texto(payload.get("resolucao"))[:300],
            "responsavel": SecurityValidator.sanitizar_texto(actor)[:80] or "RMA",
            "criado_em": agora,
            "atualizado_em": agora,
        }
        return self.store.create_item("loja_rmas", rma, tenant_id)

    def atualizar_rma(self, tenant_id: str, rma_id: str, payload: dict) -> dict | None:
        existente = self.store.get_item("loja_rmas", rma_id, tenant_id)
        if not existente:
            return None
        updates: dict[str, Any] = {"atualizado_em": _now_iso()}
        if "status" in payload and payload["status"] is not None:
            st = SecurityValidator.sanitizar_texto(payload["status"])
            if st not in STATUS_RMA:
                raise ValueError("Status de RMA inválido.")
            updates["status"] = st
        for campo, lim in (("protocolo_fabricante", 80), ("resolucao", 300)):
            if campo in payload and payload[campo] is not None:
                updates[campo] = SecurityValidator.sanitizar_texto(payload[campo])[:lim]
        if "valor_credito" in payload and payload["valor_credito"] is not None:
            updates["valor_credito"] = _fmt_brl_num(payload["valor_credito"])
        return self.store.update_item("loja_rmas", rma_id, updates, tenant_id)

    def excluir_rma(self, tenant_id: str, rma_id: str) -> bool:
        return self.store.delete_item("loja_rmas", rma_id, tenant_id)

    # ------------------------------------------------------------------
    # DASHBOARD COMERCIAL, CURVA ABC & COMISSÕES
    # ------------------------------------------------------------------

    def obter_resumo(self, tenant_id: str, incluir_custos: bool = True) -> dict[str, Any]:
        vendas = [v for v in self.listar_vendas(tenant_id) if v.get("status") != "Cancelada"]
        orcamentos = self.listar_orcamentos(tenant_id)
        rmas = self.listar_rmas(tenant_id)
        caixa = self.obter_caixa_atual(tenant_id)

        hoje_str = _hoje_iso()
        mes_prefix = hoje_str[:7]

        vendas_hoje = [v for v in vendas if str(v.get("data_venda") or "") == hoje_str]
        vendas_mes = [v for v in vendas if str(v.get("data_venda") or "").startswith(mes_prefix)]

        fat_hoje = round(sum(float(v.get("total") or 0.0) for v in vendas_hoje), 2)
        fat_mes = round(sum(float(v.get("total") or 0.0) for v in vendas_mes), 2)
        ticket_medio = round(fat_mes / len(vendas_mes), 2) if vendas_mes else 0.0

        # Curva ABC por receita nos itens vendidos
        por_produto: dict[str, dict[str, Any]] = {}
        por_forma: dict[str, float] = {}
        comissoes: dict[str, dict[str, Any]] = {}

        for v in vendas:
            fp = str(v.get("forma_pagamento") or "Pix")
            por_forma[fp] = round(por_forma.get(fp, 0.0) + float(v.get("total") or 0.0), 2)

            vend = str(v.get("vendedor") or "Balcão")
            if vend not in comissoes:
                comissoes[vend] = {"vendedor": vend, "vendas": 0, "faturamento": 0.0, "comissao": 0.0}
            comissoes[vend]["vendas"] += 1
            comissoes[vend]["faturamento"] = round(comissoes[vend]["faturamento"] + float(v.get("total") or 0.0), 2)
            comissoes[vend]["comissao"] = round(comissoes[vend]["comissao"] + float(v.get("comissao_valor") or 0.0), 2)

            for it in v.get("itens") or []:
                nome = str(it.get("nome") or "Item")
                if nome not in por_produto:
                    por_produto[nome] = {"nome": nome, "quantidade": 0, "receita": 0.0}
                por_produto[nome]["quantidade"] += int(it.get("quantidade") or 1)
                por_produto[nome]["receita"] = round(por_produto[nome]["receita"] + float(it.get("total_item") or 0.0), 2)

        lista_abc = sorted(por_produto.values(), key=lambda x: x["receita"], reverse=True)
        receita_total_abc = sum(x["receita"] for x in lista_abc) or 1.0
        acumulado = 0.0
        for item in lista_abc:
            pct = (item["receita"] / receita_total_abc) * 100.0
            acumulado += pct
            item["participacao_percent"] = round(pct, 1)
            item["classe"] = "A" if acumulado <= 70.0 else ("B" if acumulado <= 90.0 else "C")

        orc_abertos = sum(1 for o in orcamentos if o.get("status") in {"Rascunho", "Enviado", "Aprovado"})
        orc_conv = sum(1 for o in orcamentos if o.get("status") in {"Convertido em Venda", "Convertido em O.S."})
        taxa_conv = round((orc_conv / len(orcamentos)) * 100.0, 1) if orcamentos else 0.0
        rmas_abertos = sum(1 for r in rmas if r.get("status") not in {"Concluído", "Negado"})

        resumo: dict[str, Any] = {
            "total_vendas_mes": len(vendas_mes),
            "orcamentos_abertos": orc_abertos,
            "orcamentos_convertidos": orc_conv,
            "taxa_conversao_orc": taxa_conv,
            "rmas_abertos": rmas_abertos,
            "caixa_aberto": bool(caixa),
            "curva_abc": lista_abc[:6],
            "por_forma_pagamento": por_forma if incluir_custos else {},
            "ocultar_custos": not incluir_custos,
        }
        if incluir_custos:
            lucro_mes = round(sum(float(v.get("lucro_estimado") or 0.0) for v in vendas_mes), 2)
            margem_media = round((lucro_mes / fat_mes) * 100.0, 1) if fat_mes > 0 else 0.0
            resumo.update(
                {
                    "faturamento_hoje": fat_hoje,
                    "faturamento_mes": fat_mes,
                    "ticket_medio": ticket_medio,
                    "lucro_mes": lucro_mes,
                    "margem_media_percent": margem_media,
                    "saldo_caixa": float(caixa.get("saldo_esperado") or 0.0) if caixa else 0.0,
                    "comissoes": sorted(comissoes.values(), key=lambda c: c["faturamento"], reverse=True),
                }
            )
        return resumo
