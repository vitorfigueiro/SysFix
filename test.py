import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import HTTPException

import app as app_module
import auth as auth_module
import models
from auth import AuthService, LocalTenantUserStore, OnboardingRequiredError
from firebase_backend import (
    FirebaseAuthProvider,
    FirestoreColetaStore,
    FirestoreTenantUserStore,
)
from models import ColetaModel
from reports import PDFReportGenerator
from security import SecurityValidator
from storage import JsonStore


# ==============================================================================
# MOCKS EM MEMÓRIA PARA TESTAR FIREBASE AUTH & FIRESTORE SEM REDE
# ==============================================================================


class _FakeSnapshot:
    def __init__(self, doc_id: str, data: dict | None):
        self.id = doc_id
        self._data = dict(data) if data is not None else None

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _FakeDocumentRef:
    def __init__(self, store_dict: dict, path_tuple: tuple[str, ...]):
        self._store = store_dict
        self._path = path_tuple
        self.id = path_tuple[-1]

    def get(self, transaction=None):
        return _FakeSnapshot(self.id, self._store.get(self._path))

    def set(self, data: dict, merge: bool = False):
        if merge and self._path in self._store:
            merged = dict(self._store[self._path])
            merged.update(data)
            self._store[self._path] = merged
        else:
            self._store[self._path] = dict(data)

    def update(self, updates: dict):
        if self._path not in self._store:
            raise KeyError(f"Documento inexistente: {self._path}")
        updated = dict(self._store[self._path])
        updated.update(updates)
        self._store[self._path] = updated

    def delete(self):
        self._store.pop(self._path, None)

    def collection(self, name: str):
        return _FakeCollectionRef(self._store, self._path + (name,))


class _FakeQuery:
    def __init__(self, docs: list[_FakeSnapshot]):
        self._docs = docs

    def where(self, field: str, op: str, value):
        if op == "==":
            return _FakeQuery(
                [doc for doc in self._docs if (doc.to_dict() or {}).get(field) == value]
            )
        return self

    def limit(self, count: int):
        return _FakeQuery(self._docs[:count])

    def stream(self):
        return iter(self._docs)


class _FakeCollectionRef:
    def __init__(self, store_dict: dict, prefix: tuple[str, ...]):
        self._store = store_dict
        self._prefix = prefix

    def document(self, doc_id: str):
        return _FakeDocumentRef(self._store, self._prefix + (str(doc_id),))

    def _snapshots(self) -> list[_FakeSnapshot]:
        depth = len(self._prefix) + 1
        return [
            _FakeSnapshot(path[-1], data)
            for path, data in self._store.items()
            if len(path) == depth and path[: len(self._prefix)] == self._prefix
        ]

    def where(self, field: str, op: str, value):
        return _FakeQuery(self._snapshots()).where(field, op, value)

    def limit(self, count: int):
        return _FakeQuery(self._snapshots()).limit(count)

    def stream(self):
        return iter(self._snapshots())


class _FakeTransaction:
    def set(self, doc_ref: _FakeDocumentRef, data: dict, merge: bool = False):
        doc_ref.set(data, merge=merge)

    def update(self, doc_ref: _FakeDocumentRef, updates: dict):
        doc_ref.update(updates)


class _FakeWriteBatch:
    def __init__(self):
        self._ops = []

    def set(self, doc_ref: _FakeDocumentRef, data: dict, merge: bool = False):
        self._ops.append((doc_ref, dict(data), merge))

    def commit(self):
        for doc_ref, data, merge in self._ops:
            doc_ref.set(data, merge=merge)
        self._ops.clear()


class FakeFirestoreDB:
    def __init__(self):
        self.data: dict[tuple[str, ...], dict] = {}

    def collection(self, name: str):
        return _FakeCollectionRef(self.data, (name,))

    def batch(self):
        return _FakeWriteBatch()

    def run_transaction(self, callback):
        return callback(_FakeTransaction())


class FakeFirebaseAuthClient:
    def __init__(self):
        self.tokens: dict[str, dict] = {}
        self.users: dict[str, dict] = {}
        self._counter = 1

    def verify_id_token(self, token: str):
        if token not in self.tokens:
            raise ValueError("Token Firebase inválido")
        return dict(self.tokens[token])

    def create_user(self, email: str, password: str, display_name: str):
        uid = f"fb_uid_{self._counter}"
        self._counter += 1
        self.users[uid] = {
            "uid": uid,
            "email": email,
            "display_name": display_name,
            "disabled": False,
        }

        class _Record:
            pass

        rec = _Record()
        rec.uid = uid
        return rec

    def update_user(self, uid: str, disabled: bool = False):
        if uid in self.users:
            self.users[uid]["disabled"] = disabled


# ==============================================================================
# TESTES DE ARMAZENAMENTO LOCAL E MULTI-TENANCY
# ==============================================================================


class JsonStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store = models._store
        models._store = JsonStore(Path(self.temp_dir.name) / "coletas.json")
        models._store.ensure()

    def tearDown(self):
        models._store = self.original_store
        self.temp_dir.cleanup()

    def entrada(self, equipamento="Notebook"):
        return {
            "equipamento": equipamento,
            "tombamento": "123",
            "tecnico": "Vitor",
            "data_coleta": "2026-10-28",
            "origem": "TI",
            "os_coleta": "OS-001",
            "localizacao": "Bancada TI",
            "problema": "Não liga",
        }

    def test_create_update_filter_and_reload(self):
        record_id = ColetaModel.registrar_entrada(**self.entrada())
        self.assertEqual(record_id, 1)
        self.assertEqual(ColetaModel.buscar_por_id(record_id)["status"], "Pendente")
        self.assertTrue(
            ColetaModel.registrar_saida(
                record_id,
                "Vitor",
                "28/10/2026",
                "OS-002",
                "Sem Custo",
                50,
                "Reparo concluído",
                "nao",
            )
        )
        finalizado = ColetaModel.buscar_finalizados_mes_atual()[0]
        self.assertEqual(finalizado["os_entrega"], "OS-002")
        self.assertEqual(finalizado["valor_custo"], 0.0)
        self.assertEqual(finalizado["laudado"], "Não")

        reopened_store = JsonStore(models._store.path)
        self.assertEqual(reopened_store.get(record_id)["status"], "Entregue")

    def test_batch_is_all_or_nothing(self):
        valid = self.entrada()
        invalid = self.entrada(equipamento="")
        with self.assertRaisesRegex(ValueError, "Item 2"):
            ColetaModel.registrar_entrada_em_lote([valid, invalid])
        self.assertEqual(ColetaModel.buscar_todos(), [])

    def test_import_preserves_ids_and_refuses_overwrite(self):
        store = models._store
        self.assertEqual(
            store.import_records(
                [{"id": 14, "equipamento": "Antigo", "campo_legado": "preservado"}]
            ),
            1,
        )
        self.assertEqual(store.get(14)["campo_legado"], "preservado")
        self.assertEqual(ColetaModel.registrar_entrada(**self.entrada()), 15)
        with self.assertRaisesRegex(RuntimeError, "já contém registros"):
            store.import_records([{"id": 2}])

    def test_import_real_neon_backup_snapshot_if_present(self):
        neon_backup = Path(__file__).resolve().parent.parent / (
            "falling-mouse-65713548_production_neondb_2026-10-28_17-54-22.json"
        )
        if not neon_backup.exists():
            self.skipTest("Arquivo de backup do Neon não está presente no workspace.")

        registros = json.loads(neon_backup.read_text(encoding="utf-8"))
        importado = models._store.import_records(registros)
        self.assertEqual(importado, len(registros))
        todos = ColetaModel.buscar_todos()
        self.assertEqual(len(todos), len(registros))
        self.assertGreater(todos[0]["id"], todos[-1]["id"])

    def test_concurrent_entries_receive_unique_ids(self):
        def create(index):
            return ColetaModel.registrar_entrada(
                **{**self.entrada(), "equipamento": f"Notebook {index}"}
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(create, range(20)))

        self.assertEqual(len(set(ids)), 20)
        self.assertEqual(len(ColetaModel.buscar_todos()), 20)

    def test_corrupt_json_is_reported(self):
        models._store.path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Não foi possível ler"):
            ColetaModel.buscar_todos()

    def test_tenant_isolation_in_json_store(self):
        id_a = ColetaModel.registrar_entrada(**self.entrada("PC A"), tenant_id="emp_a")
        id_b = ColetaModel.registrar_entrada(**self.entrada("PC B"), tenant_id="emp_b")

        self.assertEqual(len(ColetaModel.buscar_todos(tenant_id="emp_a")), 1)
        self.assertIsNone(ColetaModel.buscar_por_id(id_a, tenant_id="emp_b"))
        self.assertFalse(
            ColetaModel.excluir(id_a, is_admin=True, tenant_id="emp_b")
        )
        self.assertIsNotNone(ColetaModel.buscar_por_id(id_a, tenant_id="emp_a"))
        self.assertIsNotNone(ColetaModel.buscar_por_id(id_b, tenant_id="emp_b"))


# ==============================================================================
# TESTES DE SEGURANÇA E RELATÓRIOS PDF
# ==============================================================================


class SecurityValidatorTests(unittest.TestCase):
    def setUp(self):
        self.original_env = os.environ.get("ADMIN_PASSWORD")
        self.original_attr = SecurityValidator._ADMIN_PASSWORD

    def tearDown(self):
        if self.original_env is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = self.original_env
        SecurityValidator._ADMIN_PASSWORD = self.original_attr

    def test_admin_password_reads_env_dynamically(self):
        os.environ.pop("ADMIN_PASSWORD", None)
        SecurityValidator._ADMIN_PASSWORD = ""
        self.assertFalse(SecurityValidator.senha_admin_configurada())
        self.assertFalse(SecurityValidator.validar_senha_admin("qualquer"))

        os.environ["ADMIN_PASSWORD"] = "segredo-123"
        self.assertTrue(SecurityValidator.senha_admin_configurada())
        self.assertTrue(SecurityValidator.validar_senha_admin("  segredo-123 "))
        self.assertFalse(SecurityValidator.validar_senha_admin("errada"))

    def test_validate_cost_variants_and_invalid_inputs(self):
        self.assertEqual(SecurityValidator.validate_cost(None), 0.0)
        self.assertEqual(SecurityValidator.validate_cost(""), 0.0)
        self.assertEqual(SecurityValidator.validate_cost(100), 100.0)
        self.assertEqual(SecurityValidator.validate_cost(12.345), 12.35)
        self.assertEqual(SecurityValidator.validate_cost("R$ 1.250,50"), 1250.5)

        for invalid in (-1, -0.01, float("nan"), float("inf"), float("-inf"), True, "abc"):
            with self.assertRaises(ValueError):
                SecurityValidator.validate_cost(invalid)

    def test_validate_location_date_and_mask(self):
        self.assertEqual(SecurityValidator.validate_location(""), "Bancada TI")
        self.assertEqual(SecurityValidator.validate_location("playlan"), "PlayLan")
        with self.assertRaises(ValueError):
            SecurityValidator.validate_location("Inexistente")

        self.assertEqual(SecurityValidator.validar_data("28/10/2026"), "2026-10-28")
        self.assertEqual(SecurityValidator.validar_data("2026-10-28T15:00:00"), "2026-10-28")
        with self.assertRaises(ValueError):
            SecurityValidator.validar_data("data-invalida")

        self.assertEqual(SecurityValidator.mask_personal_data("Carlos Silva"), "C***** S****")
        self.assertEqual(SecurityValidator.mask_personal_data("Bancada TI"), "Bancada TI")
        self.assertEqual(SecurityValidator.mask_personal_data(""), "")


class PDFReportGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store = models._store
        models._store = JsonStore(Path(self.temp_dir.name) / "coletas.json")
        models._store.ensure()

    def tearDown(self):
        models._store = self.original_store
        self.temp_dir.cleanup()

    def test_generate_monthly_pending_and_general_pdfs(self):
        empty_pdf = PDFReportGenerator.relatorio_por_mes(1, 2026, nome_empresa="TechFix")
        self.assertTrue(empty_pdf.getvalue().startswith(b"%PDF-"))

        record_id = ColetaModel.registrar_entrada(
            equipamento="Notebook <Dell> & \"HP\"",
            tombamento="998877",
            tecnico="Carlos Silva",
            data_coleta="2026-10-15",
            origem="Secretaria & Saúde",
            os_coleta="OS-10",
            localizacao="Bancada TI",
            problema="Sem vídeo",
            tenant_id="emp_1",
        )
        ColetaModel.registrar_saida(
            record_id,
            "Ana Souza",
            "2026-10-18",
            "OS-11",
            "Com Custo",
            "150,50",
            "Troca de cabo",
            "Sim",
            tenant_id="emp_1",
        )

        mensal = PDFReportGenerator.relatorio_por_mes(
            10, 2026, anonimizar=True, tenant_id="emp_1", nome_empresa="TechFix"
        )
        pendentes = PDFReportGenerator.relatorio_pendentes(tenant_id="emp_1")
        geral = PDFReportGenerator.relatorio_geral(tenant_id="emp_1")

        self.assertTrue(mensal.getvalue().startswith(b"%PDF-"))
        self.assertTrue(pendentes.getvalue().startswith(b"%PDF-"))
        self.assertTrue(geral.getvalue().startswith(b"%PDF-"))

        with self.assertRaises(ValueError):
            PDFReportGenerator.relatorio_por_mes(13, 2026)
        with self.assertRaises(ValueError):
            PDFReportGenerator.relatorio_por_mes(10, 1999)


# ==============================================================================
# TESTES DE AUTENTICAÇÃO, RBAC, DASHBOARD E MOCK FIREBASE
# ==============================================================================


class AuthAndDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = os.environ.get("SYSFIX_DATA_DIR")
        self.original_backend = os.environ.get("SYSFIX_BACKEND")
        self.original_secret = os.environ.get("SYSFIX_SECRET_KEY")
        os.environ["SYSFIX_DATA_DIR"] = self.temp_dir.name
        os.environ["SYSFIX_BACKEND"] = "local"
        os.environ["SYSFIX_SECRET_KEY"] = "test-secret-key-2026"

        self.original_store = models._store
        models._store = JsonStore(Path(self.temp_dir.name) / "coletas.json")
        models._store.ensure()

        self.user_store = LocalTenantUserStore(Path(self.temp_dir.name) / "auth_db.json")
        self.user_store.ensure()
        self.auth = AuthService(user_store=self.user_store, backend_override="local")

    def tearDown(self):
        models._store = self.original_store
        for key, old in (
            ("SYSFIX_DATA_DIR", self.original_data_dir),
            ("SYSFIX_BACKEND", self.original_backend),
            ("SYSFIX_SECRET_KEY", self.original_secret),
        ):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self.temp_dir.cleanup()

    def test_register_login_rbac_and_settings(self):
        # Registro legado pré-existente deve ser herdado pelo primeiro tenant local
        models._store.create_records(
            [{"equipamento": "PC Legado", "tombamento": "001", "status": "Pendente"}],
            tenant_id="default",
        )

        reg = self.auth.register_local(
            "TechFix", "Vitor", "vitor@techfix.com", "senha123"
        )
        self.assertEqual(reg["tenant"]["id"], "default")
        self.assertEqual(reg["user"]["role"], "admin")

        ctx = self.auth.authenticate_token(reg["token"])
        self.assertEqual(ctx["tenant"]["nome"], "TechFix")

        # Não pode desativar o único admin
        with self.assertRaisesRegex(ValueError, "pelo menos um administrador"):
            self.auth.update_team_member(ctx, ctx["uid"], ativo=False)

        # Cria técnico na equipe
        tec = self.auth.create_team_member(
            ctx, "João Técnico", "joao@techfix.com", "123456", role="tecnico"
        )
        self.assertEqual(tec["role"], "tecnico")

        login_tec = self.auth.login_local("joao@techfix.com", "123456")
        tec_ctx = self.auth.authenticate_token(login_tec["token"])
        with self.assertRaises(auth_module.AuthorizationError):
            self.auth.create_team_member(
                tec_ctx, "Outro", "outro@techfix.com", "123456"
            )

        # Configurações da empresa
        updated_tenant = self.auth.update_tenant_settings(
            ctx, nome="TechFix Pro", sla_dias=5, localizacoes=["Bancada A", "Lab B"]
        )
        self.assertEqual(updated_tenant["nome"], "TechFix Pro")
        self.assertEqual(updated_tenant["sla_dias"], 5)
        self.assertEqual(updated_tenant["localizacoes"], ["Bancada A", "Lab B"])

    def test_dashboard_metrics_and_sla(self):
        id1 = ColetaModel.registrar_entrada(
            "Notebook 1", "111", "Vitor", "2026-10-01", "TI", "OS-1", "Bancada TI", "Lento", tenant_id="emp_1"
        )
        ColetaModel.registrar_entrada(
            "Impressora 2", "222", "vitor", "2026-10-05", "RH", "OS-2", "PlayLan", "Toner", tenant_id="emp_1"
        )
        # Item de outra empresa não deve aparecer
        ColetaModel.registrar_entrada(
            "Servidor X", "999", "Outro", "2026-10-05", "Externo", "OS-9", "Bancada TI", "Erro", tenant_id="emp_2"
        )
        ColetaModel.registrar_saida(
            id1, "Dioni", "2026-10-04", "OS-10", "Com Custo", "120,00", "Formatado", "Não", tenant_id="emp_1"
        )

        dash = ColetaModel.obter_dashboard(tenant_id="emp_1", mes=10, ano=2026, sla_dias=3)
        self.assertEqual(dash["resumo"]["total_registros"], 2)
        self.assertEqual(dash["resumo"]["total_entregues"], 1)
        self.assertEqual(dash["resumo"]["total_pendentes"], 1)
        self.assertEqual(dash["resumo"]["taxa_resolucao_percent"], 50.0)
        self.assertEqual(dash["resumo"]["tempo_medio_resolucao_dias"], 3.0)
        self.assertEqual(dash["resumo"]["custo_total_mes"], 120.0)
        self.assertEqual(len(dash["evolucao_mensal"]), 6)
        self.assertEqual(dash["por_localizacao"][0]["localizacao"], "PlayLan")


class FirebaseBackendMockTests(unittest.TestCase):
    def setUp(self):
        self.fake_db = FakeFirestoreDB()
        self.fake_auth_client = FakeFirebaseAuthClient()
        self.coleta_store = FirestoreColetaStore(db=self.fake_db)
        self.user_store = FirestoreTenantUserStore(db=self.fake_db)
        self.fb_auth = FirebaseAuthProvider(auth_client=self.fake_auth_client)
        self.auth = AuthService(
            user_store=self.user_store,
            firebase_auth_provider=self.fb_auth,
            backend_override="firebase",
        )

    def test_firebase_onboarding_auth_and_firestore_crud(self):
        self.fake_auth_client.tokens["tok_admin"] = {
            "uid": "uid_admin_1",
            "email": "admin@cliente.com",
            "name": "Admin Cliente",
        }

        with self.assertRaises(OnboardingRequiredError):
            self.auth.authenticate_token("tok_admin")

        onb = self.auth.onboard_firebase_user("tok_admin", "Cliente Cloud")
        tenant_id = onb["tenant"]["id"]
        self.assertEqual(onb["user"]["role"], "admin")

        ctx = self.auth.authenticate_token("tok_admin")
        self.assertEqual(ctx["tenant_id"], tenant_id)

        # Cria colaborador via Firebase Auth mock + Firestore
        col = self.auth.create_team_member(
            ctx, "Tec Cloud", "tec@cliente.com", "senha123", role="tecnico"
        )
        self.assertTrue(col["uid"].startswith("fb_uid_"))

        # CRUD no FirestoreColetaStore
        ids = self.coleta_store.create_records(
            [
                {"equipamento": "Notebook Cloud", "status": "Pendente"},
                {"equipamento": "Monitor Cloud", "status": "Pendente"},
            ],
            tenant_id=tenant_id,
        )
        self.assertEqual(ids, [1, 2])
        self.assertEqual(len(self.coleta_store.all(tenant_id=tenant_id)), 2)
        self.assertEqual(len(self.coleta_store.all(tenant_id="outra_emp")), 0)

        self.assertTrue(
            self.coleta_store.update_record(
                1, {"status": "Entregue"}, tenant_id=tenant_id
            )
        )
        self.assertEqual(
            self.coleta_store.get(1, tenant_id=tenant_id)["status"], "Entregue"
        )
        self.assertTrue(self.coleta_store.delete_record(2, tenant_id=tenant_id))
        self.assertFalse(self.coleta_store.delete_record(2, tenant_id=tenant_id))


# ==============================================================================
# TESTES DE INTEGRAÇÃO DA API FASTAPI (ROTAS E DEPENDÊNCIAS SÍNCRONAS)
# ==============================================================================


class ApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = os.environ.get("SYSFIX_DATA_DIR")
        self.original_backend = os.environ.get("SYSFIX_BACKEND")
        self.original_secret = os.environ.get("SYSFIX_SECRET_KEY")

        os.environ["SYSFIX_DATA_DIR"] = self.temp_dir.name
        os.environ["SYSFIX_BACKEND"] = "local"
        os.environ["SYSFIX_SECRET_KEY"] = "api-test-secret-2026"

        self.original_store = models._store
        self.original_get_auth_service = app_module._get_auth_service

        models._store = JsonStore(Path(self.temp_dir.name) / "coletas.json")
        models._store.ensure()

        self.user_store = LocalTenantUserStore(Path(self.temp_dir.name) / "auth_db.json")
        self.user_store.ensure()
        app_module._get_auth_service = lambda: AuthService(
            user_store=self.user_store, backend_override="local"
        )

    def tearDown(self):
        models._store = self.original_store
        app_module._get_auth_service = self.original_get_auth_service
        for key, old in (
            ("SYSFIX_DATA_DIR", self.original_data_dir),
            ("SYSFIX_BACKEND", self.original_backend),
            ("SYSFIX_SECRET_KEY", self.original_secret),
        ):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self.temp_dir.cleanup()

    def test_saas_multi_tenant_auth_rbac_dashboard_and_pdf(self):
        # Rotas públicas
        index_resp = app_module.read_index()
        self.assertTrue(Path(index_resp.path).exists())
        self.assertIn("SysFix", Path(index_resp.path).read_text(encoding="utf-8"))

        cfg = app_module.obter_configuracao_cliente()
        self.assertEqual(cfg["auth_mode"], "local")
        health = app_module.health_check()
        self.assertEqual(health["status"], "online")

        # Sem token ou token inválido -> 401
        with self.assertRaises(HTTPException) as ctx_no_tok:
            app_module.get_current_user(None)
        self.assertEqual(ctx_no_tok.exception.status_code, 401)

        with self.assertRaises(HTTPException) as ctx_bad_tok:
            app_module.get_current_user("Bearer token-invalido")
        self.assertEqual(ctx_bad_tok.exception.status_code, 401)

        # Registro Empresa A e Empresa B
        reg_a = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="Empresa Alpha",
                nome_usuario="Ana Admin",
                email="ana@alpha.com",
                senha="secret123",
            )
        )
        reg_b = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="Empresa Beta",
                nome_usuario="Bruno Admin",
                email="bruno@beta.com",
                senha="secret123",
            )
        )

        user_a = app_module.get_current_user(f"Bearer {reg_a['token']}")
        user_b = app_module.get_current_user(f"Bearer {reg_b['token']}")
        sess_a = app_module.obter_sessao_atual(user_a)
        self.assertEqual(sess_a["tenant"]["nome"], "Empresa Alpha")

        # Empresa A cria 2 itens em lote
        created = app_module.criar_entrada(
            [
                app_module.EntradaSchema(
                    equipamento="Notebook Alpha",
                    tombamento="100",
                    tecnico_coleta="Ana",
                    data_coleta="2026-10-20",
                    origem="TI",
                    os_coleta="OS-1",
                    localizacao="Bancada TI",
                    problema="Lento",
                ),
                app_module.EntradaSchema(
                    equipamento="Impressora Alpha",
                    tombamento="101",
                    tecnico_coleta="Ana",
                    data_coleta="2026-10-20",
                    origem="RH",
                    os_coleta="OS-1",
                    localizacao="PlayLan",
                    problema="Atolamento",
                ),
            ],
            current_user=user_a,
        )
        self.assertEqual(created["ids"], [1, 2])

        # Isolamento: Empresa B não vê nem altera dados da Empresa A
        self.assertEqual(
            app_module.listar_equipamentos(status="todos", current_user=user_b),
            [],
        )
        with self.assertRaises(HTTPException) as ctx_get_b:
            app_module.obter_equipamento(1, current_user=user_b)
        self.assertEqual(ctx_get_b.exception.status_code, 404)

        with self.assertRaises(HTTPException) as ctx_del_b:
            app_module.deletar_equipamento(1, current_user=app_module.require_admin(user_b))
        self.assertEqual(ctx_del_b.exception.status_code, 404)

        # Admin da Empresa A cria técnico e valida RBAC
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Carlos Técnico",
                email="carlos@alpha.com",
                senha="secret123",
                role="tecnico",
            ),
            current_user=app_module.require_admin(user_a),
        )
        login_tec = app_module.login_usuario(
            app_module.LoginSchema(email="carlos@alpha.com", senha="secret123")
        )
        user_tec = app_module.get_current_user(f"Bearer {login_tec['token']}")

        # Técnico dá saída (200), mas recebe 403 em ações administrativas
        saida_res = app_module.registrar_saida(
            2,
            app_module.SaidaSchema(
                tecnico_entrega="Carlos Técnico",
                data_entrega="2026-10-22",
                os_entrega="OS-200",
                status_custo="Com Custo",
                valor_custo="150,50",
                resolucao="Troca de rolete",
                laudado="Sim",
            ),
            current_user=user_tec,
        )
        self.assertTrue(saida_res["sucesso"])

        with self.assertRaises(HTTPException) as ctx_rbac:
            app_module.require_admin(user_tec)
        self.assertEqual(ctx_rbac.exception.status_code, 403)

        # Admin exclui registro 1 e atualiza configurações da empresa
        del_res = app_module.deletar_equipamento(
            1, current_user=app_module.require_admin(user_a)
        )
        self.assertTrue(del_res["sucesso"])

        cfg_upd = app_module.atualizar_configuracoes_empresa(
            app_module.TenantSettingsSchema(
                nome="Alpha Tech",
                sla_dias=4,
                localizacoes=["Bancada TI", "Lab Externo"],
            ),
            current_user=app_module.require_admin(user_a),
        )
        self.assertEqual(cfg_upd["tenant"]["nome"], "Alpha Tech")

        # Dashboard Empresa A
        dash = app_module.obter_dashboard(mes=10, ano=2026, current_user=user_a)
        self.assertEqual(dash["resumo"]["total_registros"], 1)
        self.assertEqual(dash["resumo"]["total_entregues"], 1)
        self.assertEqual(dash["resumo"]["custo_total_mes"], 150.5)

        # PDF autenticado
        pdf_resp = app_module.gerar_relatorio_pdf(
            tipo="mensal", mes=10, ano=2026, anonimizar=False, current_user=user_a
        )
        self.assertEqual(pdf_resp.media_type, "application/pdf")
        self.assertTrue(pdf_resp.body.startswith(b"%PDF-"))


class ImportExportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = os.environ.get("SYSFIX_DATA_DIR")
        self.original_backend = os.environ.get("SYSFIX_BACKEND")
        self.original_secret = os.environ.get("SYSFIX_SECRET_KEY")

        os.environ["SYSFIX_DATA_DIR"] = self.temp_dir.name
        os.environ["SYSFIX_BACKEND"] = "local"
        os.environ["SYSFIX_SECRET_KEY"] = "import-test-secret-2026"

        self.original_store = models._store
        self.original_get_auth_service = app_module._get_auth_service

        models._store = JsonStore(Path(self.temp_dir.name) / "coletas.json")
        models._store.ensure()

        self.user_store = LocalTenantUserStore(Path(self.temp_dir.name) / "auth_db.json")
        self.user_store.ensure()
        app_module._get_auth_service = lambda: AuthService(
            user_store=self.user_store, backend_override="local"
        )

    def tearDown(self):
        models._store = self.original_store
        app_module._get_auth_service = self.original_get_auth_service
        for key, old in (
            ("SYSFIX_DATA_DIR", self.original_data_dir),
            ("SYSFIX_BACKEND", self.original_backend),
            ("SYSFIX_SECRET_KEY", self.original_secret),
        ):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self.temp_dir.cleanup()

    @staticmethod
    def _build_sample_xlsx() -> bytes:
        buf = io.BytesIO()
        shared_strings = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="6" uniqueCount="6">'
            "<si><t>Equipamento</t></si>"
            "<si><t>Patrimônio</t></si>"
            "<si><t>Setor</t></si>"
            "<si><t>Data Entrada</t></si>"
            "<si><t>Notebook XLSX</t></si>"
            "<si><t>Financeiro</t></si>"
            "</sst>"
        )
        sheet1 = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            '<row r="1">'
            '<c r="A1" t="s"><v>0</v></c>'
            '<c r="B1" t="s"><v>1</v></c>'
            '<c r="C1" t="s"><v>2</v></c>'
            '<c r="D1" t="s"><v>3</v></c>'
            "</row>"
            '<row r="2">'
            '<c r="A2" t="s"><v>4</v></c>'
            '<c r="B2" t="inlineStr"><is><t>99123</t></is></c>'
            '<c r="C2" t="s"><v>5</v></c>'
            '<c r="D2"><v>46320</v></c>'
            "</row>"
            "</sheetData>"
            "</worksheet>"
        )
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("xl/sharedStrings.xml", shared_strings)
            zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        return buf.getvalue()

    def test_import_json_csv_xlsx_and_export(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="Empresa Import",
                nome_usuario="Admin Import",
                email="admin@import.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Tec Import",
                email="tec@import.com",
                senha="secret123",
                role="tecnico",
            ),
            current_user=admin,
        )
        login_tec = app_module.login_usuario(
            app_module.LoginSchema(email="tec@import.com", senha="secret123")
        )
        tec = app_module.get_current_user(f"Bearer {login_tec['token']}")

        with self.assertRaises(HTTPException) as ctx_rbac:
            app_module.require_admin(tec)
        self.assertEqual(ctx_rbac.exception.status_code, 403)

        # 1. JSON estilo Neon com entidade HTML e os_coleta null (preservando IDs)
        neon_json = json.dumps([
            {
                "id": 6,
                "equipamento": "Computador",
                "tombamento": "62255",
                "tecnico_coleta": "Dioni",
                "data_coleta": "2026-08-27",
                "origem": "UBS Caixa d&#x27;agua",
                "localizacao": "Bancada",
                "problema": "Desligando",
                "status": "Entregue",
                "status_custo": "Sem Custo",
                "valor_custo": 0.0,
                "os_coleta": None,
                "os_entrega": "35340",
                "data_entrega": "2026-08-31",
            }
        ]).encode("utf-8")

        preview = app_module.importar_dados_empresa(
            app_module.ImportarDadosSchema(
                filename="neon.json",
                conteudo_base64=base64.b64encode(neon_json).decode("ascii"),
                modo="preservar_ids",
                simular=True,
            ),
            current_user=admin,
        )
        self.assertTrue(preview["simulado"])
        self.assertEqual(preview["total_validos"], 1)
        self.assertEqual(preview["amostra"][0]["origem"], "UBS Caixa d'agua")

        imp_json = app_module.importar_dados_empresa(
            app_module.ImportarDadosSchema(
                filename="neon.json",
                conteudo_base64=base64.b64encode(neon_json).decode("ascii"),
                modo="preservar_ids",
                simular=False,
            ),
            current_user=admin,
        )
        self.assertEqual(imp_json["importados"], 1)
        self.assertEqual(
            app_module.obter_equipamento(6, current_user=admin)["origem"],
            "UBS Caixa d'agua",
        )

        # 2. CSV PT-BR com delimitador ';' e BOM UTF-8 (modo anexar)
        csv_bytes = (
            "Equipamento;Patrimônio;Setor;Valor (R$);Status\n"
            "Impressora CSV;77889;RH;180,50;Entregue\n"
        ).encode("utf-8-sig")
        imp_csv = app_module.importar_dados_empresa(
            app_module.ImportarDadosSchema(
                filename="planilha.csv",
                conteudo_base64=base64.b64encode(csv_bytes).decode("ascii"),
                modo="anexar",
                simular=False,
            ),
            current_user=admin,
        )
        self.assertEqual(imp_csv["formato"], "csv")
        self.assertEqual(imp_csv["importados"], 1)

        # 3. Excel .xlsx real gerado em memória (modo anexar)
        xlsx_bytes = self._build_sample_xlsx()
        imp_xlsx = app_module.importar_dados_empresa(
            app_module.ImportarDadosSchema(
                filename="legado.xlsx",
                conteudo_base64=base64.b64encode(xlsx_bytes).decode("ascii"),
                modo="anexar",
                simular=False,
            ),
            current_user=admin,
        )
        self.assertEqual(imp_xlsx["formato"], "xlsx")
        self.assertEqual(imp_xlsx["importados"], 1)

        lista = app_module.listar_equipamentos(status="todos", current_user=admin)
        self.assertEqual(len(lista), 3)
        self.assertTrue(any(item["equipamento"] == "Notebook XLSX" for item in lista))

        # 4. Exportação JSON/CSV e modelos
        exp_json = app_module.exportar_dados_empresa(formato="json", current_user=admin)
        self.assertEqual(json.loads(exp_json.body)["total"], 3)

        exp_csv = app_module.exportar_dados_empresa(formato="csv", current_user=admin)
        self.assertIn("Notebook XLSX", exp_csv.body.decode("utf-8-sig"))

        mod_csv = app_module.baixar_modelo_importacao(formato="csv", _current_user=admin)
        self.assertIn("equipamento", mod_csv.body.decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
