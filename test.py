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
import automation_cyber
import models
import ops_modules
from auth import AuthService, LocalTenantUserStore, OnboardingRequiredError
from firebase_backend import (
    FirebaseAuthProvider,
    FirestoreColetaStore,
    FirestoreTenantUserStore,
)
from models import ColetaModel
from ops_modules import LocalOpsStore, OpsModulesService, FirestoreOpsStore
from PIL import Image as PILImage
from reports import PDFReportGenerator
from security import SecurityValidator
from storage import JsonStore
from starlette.requests import Request


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


class GranularPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = os.environ.get("SYSFIX_DATA_DIR")
        self.original_backend = os.environ.get("SYSFIX_BACKEND")
        self.original_secret = os.environ.get("SYSFIX_SECRET_KEY")

        os.environ["SYSFIX_DATA_DIR"] = self.temp_dir.name
        os.environ["SYSFIX_BACKEND"] = "local"
        os.environ["SYSFIX_SECRET_KEY"] = "perm-test-secret-2026"

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

    def test_granular_permissions_filter_costs_and_enforce_actions(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="Empresa Perms",
                nome_usuario="Admin Perms",
                email="admin@perms.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        # Cria técnico sem dashboard, sem custos e sem PDF, mas com exclusão liberada
        membro_resp = app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Tec Restrito",
                email="tec@perms.com",
                senha="secret123",
                role="tecnico",
                permissoes={
                    "ver_dashboard": False,
                    "ver_custos": False,
                    "exportar_pdf": False,
                    "excluir_registros": True,
                },
            ),
            current_user=admin,
        )
        tec_uid = membro_resp["usuario"]["uid"]
        login_tec = app_module.login_usuario(
            app_module.LoginSchema(email="tec@perms.com", senha="secret123")
        )
        tec = app_module.get_current_user(f"Bearer {login_tec['token']}")

        # Admin cadastra item e dá saída com custo
        created = app_module.criar_entrada(
            app_module.EntradaSchema(
                equipamento="Notebook Restrito",
                tombamento="321",
                tecnico_coleta="Admin Perms",
                data_coleta="2026-10-10",
                origem="TI",
                os_coleta="OS-10",
                localizacao="Bancada TI",
                problema="Sem imagem",
            ),
            current_user=admin,
        )
        item_id = created["id"]

        # Técnico sem ver_custos dá saída tentando mandar custo 999,00 -> deve permanecer Sem Custo / 0.0
        app_module.registrar_saida(
            item_id,
            app_module.SaidaSchema(
                tecnico_entrega="Tec Restrito",
                data_entrega="2026-10-12",
                os_entrega="OS-11",
                status_custo="Com Custo",
                valor_custo=999.0,
                resolucao="Limpeza",
                laudado="Não",
            ),
            current_user=tec,
        )
        item_como_admin = app_module.obter_equipamento(item_id, current_user=admin)
        self.assertEqual(item_como_admin["valor_custo"], 0.0)
        self.assertEqual(item_como_admin["status_custo"], "Sem Custo")

        # Técnico sem ver_custos não recebe campos financeiros na consulta nem na lista
        item_como_tec = app_module.obter_equipamento(item_id, current_user=tec)
        self.assertNotIn("valor_custo", item_como_tec)
        self.assertNotIn("status_custo", item_como_tec)
        lista_tec = app_module.listar_equipamentos(status="todos", current_user=tec)
        self.assertNotIn("valor_custo", lista_tec[0])

        # Bloqueio em dashboard e PDF pelas dependências granulares
        with self.assertRaises(HTTPException) as ctx_dash:
            app_module.require_permission("ver_dashboard")(tec)
        self.assertEqual(ctx_dash.exception.status_code, 403)

        with self.assertRaises(HTTPException) as ctx_pdf:
            app_module.require_permission("exportar_pdf")(tec)
        self.assertEqual(ctx_pdf.exception.status_code, 403)

        # Técnico com excluir_registros=True pode excluir
        del_res = app_module.deletar_equipamento(
            item_id, current_user=app_module.require_permission("excluir_registros")(tec)
        )
        self.assertTrue(del_res["sucesso"])

        # Admin libera ver_dashboard e exportar_pdf para o técnico (mas mantém ver_custos=False)
        app_module.atualizar_usuario_empresa(
            tec_uid,
            app_module.TeamMemberUpdateSchema(
                permissoes={
                    "ver_dashboard": True,
                    "ver_custos": False,
                    "exportar_pdf": True,
                }
            ),
            current_user=admin,
        )
        tec_atualizado = app_module.get_current_user(f"Bearer {login_tec['token']}")
        dash = app_module.obter_dashboard(mes=10, ano=2026, current_user=tec_atualizado)
        self.assertTrue(dash["ocultar_custos"])
        self.assertNotIn("custo_total_mes", dash["resumo"])

        pdf_resp = app_module.gerar_relatorio_pdf(
            tipo="geral", anonimizar=False, current_user=tec_atualizado
        )
        self.assertEqual(pdf_resp.media_type, "application/pdf")
        self.assertTrue(pdf_resp.body.startswith(b"%PDF-"))


class ITAndISPAndLaudosTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = os.environ.get("SYSFIX_DATA_DIR")
        self.original_backend = os.environ.get("SYSFIX_BACKEND")
        self.original_secret = os.environ.get("SYSFIX_SECRET_KEY")

        os.environ["SYSFIX_DATA_DIR"] = self.temp_dir.name
        os.environ["SYSFIX_BACKEND"] = "local"
        os.environ["SYSFIX_SECRET_KEY"] = "it-isp-test-secret-2026"

        self.original_store = models._store
        self.original_get_auth_service = app_module._get_auth_service
        self.original_get_ops_service = app_module._get_ops_service

        models._store = JsonStore(Path(self.temp_dir.name) / "coletas.json")
        models._store.ensure()

        self.user_store = LocalTenantUserStore(Path(self.temp_dir.name) / "auth_db.json")
        self.user_store.ensure()
        self.ops_store = LocalOpsStore(Path(self.temp_dir.name) / "ops_db.json")
        self.ops_store.ensure()

        app_module._get_auth_service = lambda: AuthService(
            user_store=self.user_store, backend_override="local"
        )
        app_module._get_ops_service = lambda: OpsModulesService(store=self.ops_store)

    def tearDown(self):
        models._store = self.original_store
        app_module._get_auth_service = self.original_get_auth_service
        app_module._get_ops_service = self.original_get_ops_service
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
    def _sample_png_data_url() -> str:
        buf = io.BytesIO()
        img = PILImage.new("RGBA", (32, 16), (124, 58, 237, 255))
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _dummy_request() -> Request:
        return Request(
            {
                "type": "http",
                "scheme": "https",
                "server": ("sysfix-saas.onrender.com", 443),
                "headers": [],
                "path": "/",
            }
        )

    def test_it_isp_fields_history_checklist_laudos_public_link_and_ops(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="FibraNet Provedor",
                nome_usuario="Vitor Admin",
                email="vitor@fibranet.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        # 1. Personalizar timbre (logo PNG), cor, checklist e importar modelo pronto
        logo_b64 = self._sample_png_data_url()
        modelo_txt = (
            "# LAUDO DE TESTE OPTICO FTTH\n"
            "ONU {{equipamento}} com serial {{serial_mac}} testada na {{empresa}}."
        ).encode("utf-8")
        mod_imp = app_module.importar_arquivo_modelo_laudo(
            app_module.ImportarModeloLaudoSchema(
                filename="modelo_fibra.txt",
                conteudo_base64=base64.b64encode(modelo_txt).decode("ascii"),
            ),
            _current_user=admin,
        )
        self.assertEqual(mod_imp["modelo"]["titulo"], "LAUDO DE TESTE OPTICO FTTH")

        upd_brand = app_module.atualizar_modelos_e_branding_empresa(
            app_module.BrandingTemplatesSchema(
                cnpj="12.345.678/0001-99",
                telefone="(69) 99999-0000",
                cor_documento="#6D28D9",
                logo_base64=logo_b64,
                checklist_padrao=["Medição sinal RX/TX", "Teste Wi-Fi 5GHz"],
                modelos_laudo=[mod_imp["modelo"]],
            ),
            current_user=admin,
        )
        self.assertTrue(upd_brand["tenant"]["logo_base64"].startswith("data:image/png;base64,"))
        self.assertEqual(len(upd_brand["tenant"]["checklist_padrao"]), 2)

        # Recarrega admin para refletir tenant atualizado
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        # 2. Cria duas passagens do mesmo equipamento (mesmo Serial/MAC) -> testar reincidência
        os1 = app_module.criar_entrada(
            app_module.EntradaSchema(
                equipamento="ONU Huawei HG8145V5",
                categoria="ONU / ONT (Fibra)",
                tombamento="PAT-500",
                serial_mac="48575443AABBCCDD",
                tipo_fluxo="Comodato (Cliente)",
                prioridade="Crítica",
                tecnico_coleta="Vitor Admin",
                data_coleta="2026-10-01",
                origem="POP Centro",
                os_coleta="OS-101",
                localizacao="Bancada TI",
                problema="LOS vermelho intermitente",
            ),
            current_user=admin,
        )
        app_module.registrar_saida(
            os1["id"],
            app_module.SaidaSchema(
                tecnico_entrega="Vitor Admin",
                data_entrega="2026-10-02",
                os_entrega="OS-102",
                status_custo="Com Custo",
                valor_custo=80.0,
                resolucao="Troca de fonte 12V",
                laudado="Não",
                garantia_dias=90,
            ),
            current_user=admin,
        )

        os2 = app_module.criar_entrada(
            app_module.EntradaSchema(
                equipamento="ONU Huawei HG8145V5",
                categoria="ONU / ONT (Fibra)",
                tombamento="PAT-500",
                serial_mac="48575443AABBCCDD",
                tipo_fluxo="Comodato (Cliente)",
                prioridade="Alta",
                tecnico_coleta="Vitor Admin",
                data_coleta="2026-10-20",
                origem="POP Centro",
                os_coleta="OS-205",
                localizacao="Bancada TI",
                problema="Porta LAN 1 sem link",
            ),
            current_user=admin,
        )

        hist = app_module.obter_historico_equipamento(os2["id"], current_user=admin)
        self.assertTrue(hist["reincidente"])
        self.assertEqual(hist["total_passagens"], 2)
        self.assertEqual(hist["custo_acumulado"], 80.0)

        # 3. Checklist + Laudo anexado + PDF do Laudo + Comprovante com assinatura
        chk_res = app_module.atualizar_checklist_equipamento(
            os2["id"],
            app_module.ChecklistSchema(
                checklist=[
                    {"item": "Medição sinal RX/TX", "concluido": True},
                    {"item": "Teste Wi-Fi 5GHz", "concluido": True},
                ]
            ),
            current_user=admin,
        )
        self.assertEqual(len(chk_res["registro"]["checklist"]), 2)

        laudo_res = app_module.criar_laudo_equipamento(
            os2["id"],
            app_module.LaudoSchema(
                titulo="LAUDO TÉCNICO DE BANCADA",
                conclusao="Condenado / Inviável",
                diagnostico="Curto interno na controladora Ethernet.",
                parecer="Equipamento condenado após falha na porta LAN.",
                recomendacoes="Substituir ONU em comodato.",
                tecnico_responsavel="Vitor Admin",
                registro_profissional="CRT-00123",
                incluir_checklist=True,
                visivel_cliente=True,
            ),
            current_user=admin,
        )
        laudo_id = laudo_res["laudo"]["id"]
        self.assertEqual(laudo_res["registro"]["laudado"], "Sim")

        pdf_laudo = app_module.baixar_laudo_pdf(os2["id"], laudo_id, current_user=admin)
        self.assertEqual(pdf_laudo.media_type, "application/pdf")
        self.assertTrue(pdf_laudo.body.startswith(b"%PDF-"))

        ass_res = app_module.registrar_assinatura_os(
            os2["id"],
            app_module.AssinaturaSchema(
                nome="Cliente Recebedor",
                documento="123.456.789-00",
                imagem_base64=logo_b64,
            ),
            current_user=admin,
        )
        self.assertEqual(ass_res["registro"]["assinatura_entrega"]["nome"], "Cliente Recebedor")

        comp_pdf = app_module.baixar_comprovante_os_pdf(os2["id"], current_user=admin)
        self.assertTrue(comp_pdf.body.startswith(b"%PDF-"))

        # 4. Link Público + Orçamento + Aprovação pública sem expor custos internos
        app_module.enviar_orcamento_os(
            os2["id"],
            app_module.OrcamentoSchema(descricao="Troca de ONU nova", valor=150.0),
            current_user=admin,
        )
        link = app_module.gerar_link_publico_os(
            os2["id"], request=self._dummy_request(), current_user=admin
        )
        codigo = link["codigo"]
        pub = app_module.consultar_acompanhamento_publico(codigo)
        self.assertEqual(pub["ordem"]["orcamento"]["status"], "Aguardando Aprovação")
        self.assertNotIn("valor_custo", pub["ordem"])

        resp_orc = app_module.responder_orcamento_publico_api(
            codigo,
            app_module.OrcamentoRespostaPublicaSchema(
                decisao="aprovar", cliente_nome="João Cliente", observacao="Pode trocar"
            ),
        )
        self.assertEqual(resp_orc["dados"]["ordem"]["orcamento"]["status"], "Aprovado")

        pub_pdf = app_module.baixar_laudo_publico_pdf(codigo, laudo_id)
        self.assertTrue(pub_pdf.body.startswith(b"%PDF-"))

        # 5. Operações TI & ISP: Estoque vinculado à O.S., Preventiva POP e Cliente/POP
        est = app_module.criar_item_estoque_ops(
            app_module.EstoqueItemSchema(
                nome="Fonte 12V 1.5A",
                codigo="FNT-12",
                categoria="Peça / Insumo",
                quantidade=5,
                estoque_minimo=2,
                custo_unitario=25.0,
            ),
            current_user=admin,
        )
        mov = app_module.movimentar_estoque_ops(
            est["item"]["id"],
            app_module.EstoqueMovimentoSchema(
                tipo="saida", quantidade=2, os_id=os2["id"], observacao="Aplicada na O.S."
            ),
            current_user=admin,
        )
        self.assertEqual(mov["item"]["quantidade"], 3)

        prv = app_module.criar_preventiva_ops(
            app_module.PreventivaSchema(
                titulo="Nobreak POP Centro",
                local_pop="POP Centro",
                periodicidade_dias=30,
                proxima_execucao="2026-10-01",
            ),
            current_user=admin,
        )
        self.assertEqual(prv["item"]["status_preventiva"], "Vencida")
        concl = app_module.concluir_preventiva_ops(
            prv["item"]["id"],
            app_module.PreventivaConcluirSchema(observacao="Baterias OK", data_execucao="2026-11-24"),
            current_user=admin,
        )
        self.assertEqual(concl["item"]["proxima_execucao"], "2026-12-24")

        cli = app_module.criar_cliente_ops(
            app_module.ClientePopSchema(nome="POP Norte", tipo="POP / Torre ISP", telefone="6999999999"),
            current_user=admin,
        )
        self.assertEqual(len(app_module.listar_clientes_ops(current_user=admin)), 1)
        self.assertEqual(cli["item"]["nome"], "POP Norte")

        # 6. Permissões granulares bloqueando técnico sem gerenciar_laudos / gerenciar_modelos / gerenciar_operacoes_ti / ver_custos
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Tec Limitado",
                email="limitado@fibranet.com",
                senha="secret123",
                role="tecnico",
                permissoes={
                    "ver_custos": False,
                    "gerenciar_laudos": False,
                    "compartilhar_os": False,
                    "gerenciar_operacoes_ti": True,
                    "gerenciar_modelos": False,
                },
            ),
            current_user=admin,
        )
        tok_lim = app_module.login_usuario(
            app_module.LoginSchema(email="limitado@fibranet.com", senha="secret123")
        )["token"]
        tec_lim = app_module.get_current_user(f"Bearer {tok_lim}")

        with self.assertRaises(HTTPException) as c1:
            app_module.require_permission("gerenciar_laudos")(tec_lim)
        self.assertEqual(c1.exception.status_code, 403)

        with self.assertRaises(HTTPException) as c2:
            app_module.require_any_permission("gerenciar_modelos", "gerenciar_equipe")(tec_lim)
        self.assertEqual(c2.exception.status_code, 403)

        est_tec = app_module.listar_estoque_ops(current_user=tec_lim)
        self.assertNotIn("custo_unitario", est_tec[0])

    def test_firestore_tracking_code_and_ops_mock(self):
        fake_db = FakeFirestoreDB()
        c_store = FirestoreColetaStore(db=fake_db)
        ops_store = FirestoreOpsStore(db=fake_db)

        ids = c_store.create_records(
            [{"equipamento": "MikroTik RB4011", "codigo_acompanhamento": "SF-TESTE123"}],
            tenant_id="emp_isp",
        )
        self.assertEqual(ids, [1])
        found = c_store.find_by_tracking_code("sf-teste123")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], "emp_isp")
        self.assertEqual(found[1]["equipamento"], "MikroTik RB4011")

        c_store.delete_record(1, tenant_id="emp_isp")
        self.assertIsNone(c_store.find_by_tracking_code("SF-TESTE123"))

        ops_svc = OpsModulesService(store=ops_store)
        created_cli = ops_svc.criar_cliente("emp_isp", {"nome": "POP Sul", "tipo": "POP / Torre ISP"})
        self.assertEqual(len(ops_svc.listar_clientes("emp_isp")), 1)
        self.assertTrue(ops_svc.excluir_cliente("emp_isp", created_cli["id"]))


class SectorPermissionsAndSeparationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = os.environ.get("SYSFIX_DATA_DIR")
        self.original_backend = os.environ.get("SYSFIX_BACKEND")
        self.original_secret = os.environ.get("SYSFIX_SECRET_KEY")

        os.environ["SYSFIX_DATA_DIR"] = self.temp_dir.name
        os.environ["SYSFIX_BACKEND"] = "local"
        os.environ["SYSFIX_SECRET_KEY"] = "sector-test-secret-2026"

        self.original_store = models._store
        self.original_get_auth_service = app_module._get_auth_service
        self.original_get_ops_service = app_module._get_ops_service

        models._store = JsonStore(Path(self.temp_dir.name) / "coletas.json")
        models._store.ensure()

        self.user_store = LocalTenantUserStore(Path(self.temp_dir.name) / "auth_db.json")
        self.user_store.ensure()
        self.ops_store = LocalOpsStore(Path(self.temp_dir.name) / "ops_db.json")
        self.ops_store.ensure()

        app_module._get_auth_service = lambda: AuthService(
            user_store=self.user_store, backend_override="local"
        )
        app_module._get_ops_service = lambda: OpsModulesService(store=self.ops_store)

    def tearDown(self):
        models._store = self.original_store
        app_module._get_auth_service = self.original_get_auth_service
        app_module._get_ops_service = self.original_get_ops_service
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

    def test_sector_catalog_presets_legacy_alias_and_role_isolation(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="MultiSetor Tech & ISP",
                nome_usuario="Admin Master",
                email="admin@multisetor.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        me = app_module.obter_sessao_atual(admin)
        sector_keys = [s["key"] for s in me["permission_sectors"]]
        self.assertEqual(
            sector_keys,
            [
                "ti",
                "provedor",
                "estoque",
                "automacao",
                "ciberseguranca",
                "laudos",
                "financeiro",
                "administracao",
            ],
        )
        self.assertIn("estoquista", me["role_presets"])
        self.assertIn("tecnico_isp", me["role_presets"])
        self.assertIn("tecnico_ti", me["role_presets"])
        self.assertIn("tecnico_automacao", me["role_presets"])
        self.assertIn("analista_cyber", me["role_presets"])

        # Configurar setores ativos da empresa (desabilita tudo -> deve falhar; desabilita só provedor -> aceita)
        with self.assertRaises(HTTPException) as ctx_empty_sec:
            app_module.atualizar_configuracoes_empresa(
                app_module.TenantSettingsSchema(
                    setores_ativos={
                        "ti": False,
                        "provedor": False,
                        "estoque": False,
                        "automacao": False,
                        "ciberseguranca": False,
                    }
                ),
                current_user=admin,
            )
        self.assertEqual(ctx_empty_sec.exception.status_code, 400)

        upd_sec = app_module.atualizar_configuracoes_empresa(
            app_module.TenantSettingsSchema(
                setores_ativos={"ti": True, "provedor": False, "estoque": True}
            ),
            current_user=admin,
        )
        self.assertFalse(upd_sec["tenant"]["setores_ativos"]["provedor"])
        self.assertTrue(upd_sec["tenant"]["setores_ativos"]["estoque"])

        # Alias legado 'gerenciar_operacoes_ti' expande corretamente sem quebrar
        expanded = auth_module.normalizar_permissoes_input({"gerenciar_operacoes_ti": False})
        self.assertFalse(expanded["ver_provedor"])
        self.assertFalse(expanded["ver_estoque"])
        self.assertFalse(expanded["movimentar_estoque"])

        # 1. Estoquista puro: cria item com almoxarifado/unidade, movimenta e vê histórico; bloqueado em TI, Provedor e Custos
        estoquista_perms = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        estoquista_perms.update(auth_module.ROLE_PRESETS["estoquista"]["permissoes"])
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Lucas Estoquista",
                email="estoque@multisetor.com",
                senha="secret123",
                role="tecnico",
                permissoes=estoquista_perms,
            ),
            current_user=admin,
        )
        tok_est = app_module.login_usuario(
            app_module.LoginSchema(email="estoque@multisetor.com", senha="secret123")
        )["token"]
        est_user = app_module.get_current_user(f"Bearer {tok_est}")

        item_resp = app_module.criar_item_estoque_ops(
            app_module.EstoqueItemSchema(
                nome="Conector APC Verde",
                codigo="APC-01",
                categoria="Fibra / Conectores",
                almoxarifado="Almoxarifado Central",
                unidade="cx",
                quantidade=10,
                estoque_minimo=2,
                custo_unitario=99.9,
            ),
            current_user=app_module.require_permission("gerenciar_estoque")(est_user),
        )
        self.assertEqual(item_resp["item"]["almoxarifado"], "Almoxarifado Central")
        self.assertEqual(item_resp["item"]["unidade"], "cx")
        self.assertNotIn("custo_unitario", item_resp["item"])

        mov_resp = app_module.movimentar_estoque_ops(
            item_resp["item"]["id"],
            app_module.EstoqueMovimentoSchema(tipo="saida", quantidade=3, observacao="Retirada equipe"),
            current_user=app_module.require_permission("movimentar_estoque")(est_user),
        )
        self.assertEqual(mov_resp["item"]["quantidade"], 7)
        self.assertEqual(len(mov_resp["item"]["historico_movimentacoes"]), 2)
        self.assertEqual(mov_resp["item"]["historico_movimentacoes"][0]["saldo_atual"], 7)

        resumo_est = app_module.obter_resumo_estoque_ops(
            current_user=app_module.require_permission("ver_estoque")(est_user)
        )
        self.assertEqual(resumo_est["total_skus"], 1)
        self.assertEqual(resumo_est["total_unidades"], 7)
        self.assertNotIn("valor_total_estoque", resumo_est)

        for perm_bloqueada in ("ver_ti", "ver_provedor", "gerenciar_preventivas", "gerenciar_laudos", "ver_dashboard", "ver_custos"):
            with self.assertRaises(HTTPException) as ctx_b:
                app_module.require_permission(perm_bloqueada)(est_user)
            self.assertEqual(ctx_b.exception.status_code, 403)

        # 2. Técnico ISP: acessa preventivas, clientes/POPs e dá baixa no estoque, mas não cadastra/exclui itens do almoxarifado
        isp_perms = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        isp_perms.update(auth_module.ROLE_PRESETS["tecnico_isp"]["permissoes"])
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Rafael ISP",
                email="isp@multisetor.com",
                senha="secret123",
                role="tecnico",
                permissoes=isp_perms,
            ),
            current_user=admin,
        )
        tok_isp = app_module.login_usuario(
            app_module.LoginSchema(email="isp@multisetor.com", senha="secret123")
        )["token"]
        isp_user = app_module.get_current_user(f"Bearer {tok_isp}")

        prv = app_module.criar_preventiva_ops(
            app_module.PreventivaSchema(
                titulo="OLT POP Sul",
                local_pop="POP Sul",
                periodicidade_dias=15,
                proxima_execucao="2026-12-01",
            ),
            current_user=app_module.require_permission("gerenciar_preventivas")(isp_user),
        )
        self.assertEqual(prv["item"]["local_pop"], "POP Sul")

        with self.assertRaises(HTTPException) as ctx_isp_est:
            app_module.require_permission("gerenciar_estoque")(isp_user)
        self.assertEqual(ctx_isp_est.exception.status_code, 403)


class AutomationAndCyberSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = os.environ.get("SYSFIX_DATA_DIR")
        self.original_backend = os.environ.get("SYSFIX_BACKEND")
        self.original_secret = os.environ.get("SYSFIX_SECRET_KEY")

        os.environ["SYSFIX_DATA_DIR"] = self.temp_dir.name
        os.environ["SYSFIX_BACKEND"] = "local"
        os.environ["SYSFIX_SECRET_KEY"] = "test-secret-key"

        self.db_path = Path(self.temp_dir.name) / "test_db.json"
        self.auth_db_path = Path(self.temp_dir.name) / "test_auth.json"
        self.sec_ops_path = Path(self.temp_dir.name) / "test_sec_ops.json"

        self.original_store = models._store
        self.original_get_auth_service = app_module._get_auth_service
        self.original_get_sec_ops_service = app_module._get_security_ops_service

        models._store = JsonStore(self.db_path)
        models._store.ensure()
        self.user_store = LocalTenantUserStore(self.auth_db_path)
        self.user_store.ensure()
        self.auth_service = AuthService(user_store=self.user_store, backend_override="local")
        self.sec_ops_store = automation_cyber.LocalSecurityOpsStore(self.sec_ops_path)
        app_module._get_auth_service = lambda: self.auth_service
        app_module._get_security_ops_service = (
            lambda: automation_cyber.AutomationCyberService(store=self.sec_ops_store)
        )

    def tearDown(self):
        models._store = self.original_store
        app_module._get_auth_service = self.original_get_auth_service
        app_module._get_security_ops_service = self.original_get_sec_ops_service
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

    def test_automacao_crud_conformidade_e_pdf(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="AutomaSec Ltda",
                nome_usuario="Gestor Chefe",
                email="gestor@automasec.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        # Cria técnico apenas de Automação
        aut_perms = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        aut_perms.update(auth_module.ROLE_PRESETS["tecnico_automacao"]["permissoes"])
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Técnico CFTV",
                email="aut@automasec.com",
                senha="secret123",
                role="tecnico",
                permissoes=aut_perms,
            ),
            current_user=admin,
        )
        tok = app_module.login_usuario(
            app_module.LoginSchema(email="aut@automasec.com", senha="secret123")
        )["token"]
        tec = app_module.get_current_user(f"Bearer {tok}")

        # Bloqueado em Cibersegurança
        with self.assertRaises(HTTPException) as ctx_block:
            app_module.require_permission("ver_ciberseguranca")(tec)
        self.assertEqual(ctx_block.exception.status_code, 403)

        # Cria câmera sem senha padrão alterada e sem firmware -> Crítico automático
        cam = app_module.criar_ativo_automacao_api(
            app_module.AutomacaoAtivoSchema(
                pilar="cameras",
                nome="Câmera Portaria Norte",
                subtipo="IP Bullet PoE",
                localizacao="Portaria",
                ip_mac="10.20.30.15",
                vlan="30",
                fabricante_modelo="Intelbras VIP",
                firmware="v2.1",
                senha_padrao_alterada=False,
                firmware_atualizado=False,
                rede_segmentada=False,
                energia_backup_ok=True,
                monitoramento_ativo=False,
            ),
            current_user=app_module.require_permission("gerenciar_automacao")(tec),
        )
        self.assertEqual(cam["item"]["status_auditoria"], "Crítico")

        # Cria central de alarme 100% conforme
        app_module.criar_ativo_automacao_api(
            app_module.AutomacaoAtivoSchema(
                pilar="alarmes",
                nome="Central Active 32 Duo",
                subtipo="Alarme Monitorado",
                localizacao="CPD",
                ip_mac="10.20.40.10",
                vlan="40",
                fabricante_modelo="JFL",
                senha_padrao_alterada=True,
                firmware_atualizado=True,
                rede_segmentada=True,
                energia_backup_ok=True,
                monitoramento_ativo=True,
            ),
            current_user=app_module.require_permission("gerenciar_automacao")(tec),
        )

        lst = app_module.listar_ativos_automacao(
            current_user=app_module.require_permission("ver_automacao")(tec)
        )
        self.assertEqual(lst["resumo"]["total_ativos"], 2)
        self.assertEqual(lst["resumo"]["criticos"], 1)
        self.assertEqual(lst["resumo"]["conformes"], 1)

        pdf = app_module.baixar_relatorio_automacao_pdf(
            current_user=app_module.require_permission("ver_automacao")(tec)
        )
        self.assertEqual(pdf.media_type, "application/pdf")
        self.assertTrue(pdf.body.startswith(b"%PDF"))

    def test_ciberseguranca_governanca_anti_ssrf_auditoria_coletor_cis_e_kb(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="CyberCorp",
                nome_usuario="CISO Admin",
                email="ciso@cybercorp.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        # Cria analista Cyber
        cyb_perms = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        cyb_perms.update(auth_module.ROLE_PRESETS["analista_cyber"]["permissoes"])
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Analista SOC",
                email="soc@cybercorp.com",
                senha="secret123",
                role="tecnico",
                permissoes=cyb_perms,
            ),
            current_user=admin,
        )
        tok = app_module.login_usuario(
            app_module.LoginSchema(email="soc@cybercorp.com", senha="secret123")
        )["token"]
        soc = app_module.get_current_user(f"Bearer {tok}")

        # 1. Técnico não-admin não pode alterar governança
        with self.assertRaises(HTTPException) as ctx_gov_non_admin:
            app_module.require_admin(soc)
        self.assertEqual(ctx_gov_non_admin.exception.status_code, 403)

        # 2. Auditoria bloqueada antes do gestor liberar
        with self.assertRaises(HTTPException) as ctx_locked:
            app_module.executar_auditoria_cyber_api(
                app_module.CyberExecutarAuditoriaSchema(alvo="192.168.50.0/29"),
                current_user=app_module.require_permission("executar_auditoria_rede")(soc),
            )
        self.assertEqual(ctx_locked.exception.status_code, 403)

        # 3. Admin tenta colocar localhost/metadata no escopo ou habilita com escopo válido
        gov = app_module.atualizar_governanca_cyber_api(
            app_module.CyberGovernancaSchema(
                permitir_auditoria_rede=True,
                escopo_autorizado=["192.168.50.0/29", "portal.cybercorp.local"],
                observacao_termo="Autorizado pelo CISO para rede interna",
            ),
            current_user=app_module.require_admin(admin),
        )
        self.assertTrue(gov["governanca"]["permitir_auditoria_rede"])
        soc = app_module.get_current_user(f"Bearer {tok}")

        # Anti-SSRF: loopback / metadata / alvo fora do escopo devem ser bloqueados mesmo após liberação
        for bad_target in ("127.0.0.1", "169.254.169.254", "8.8.8.8"):
            with self.assertRaises(HTTPException) as ctx_ssrf:
                app_module.executar_auditoria_cyber_api(
                    app_module.CyberExecutarAuditoriaSchema(
                        alvo=bad_target,
                        tipo_auditoria="rede_privada",
                    ),
                    current_user=app_module.require_permission("executar_auditoria_rede")(soc),
                )
            self.assertEqual(ctx_ssrf.exception.status_code, 403)

        # 4. Executa auditoria autorizada simulada (mock do engine para zero acesso de rede no teste)
        orig_scan = automation_cyber.NetworkAuditEngine.auditar_rede_privada
        try:
            automation_cyber.NetworkAuditEngine.auditar_rede_privada = classmethod(
                lambda cls, cidr: {
                    "alvo": cidr,
                    "hosts_ativos": 2,
                    "portas_expostas": 2,
                    "detalhes": {
                        "cidr": cidr,
                        "hosts": [{"ip": "192.168.50.1", "portas": [{"porta": 23}, {"porta": 443}]}],
                    },
                    "achados_detectados": [
                        {
                            "titulo": "Porta Telnet (23) exposta em 192.168.50.1",
                            "severidade": "Crítica",
                            "categoria": "Superfície de Rede",
                            "alvo": "192.168.50.1",
                            "recomendacao": "Desativar Telnet e migrar para SSHv2.",
                        }
                    ],
                    "score": 70,
                }
            )
            exec_res = app_module.executar_auditoria_cyber_api(
                app_module.CyberExecutarAuditoriaSchema(
                    alvo="192.168.50.0/29",
                    tipo_auditoria="rede_privada",
                    registrar_achados=True,
                ),
                current_user=app_module.require_permission("executar_auditoria_rede")(soc),
            )
            self.assertEqual(exec_res["auditoria"]["score"], 70)
            self.assertEqual(exec_res["achados_criados"], 1)
        finally:
            automation_cyber.NetworkAuditEngine.auditar_rede_privada = orig_scan

        # 5. Coletor LAN e importação JSON
        coletor_ps1 = app_module.baixar_coletor_cyber_api(
            formato="ps1",
            current_user=app_module.require_permission("ver_ciberseguranca")(soc),
        )
        self.assertIn("Escopo autorizado", coletor_ps1.body.decode("utf-8"))

        imp = app_module.importar_auditoria_cyber_api(
            app_module.CyberImportarAuditoriaSchema(
                payload={
                    "alvo": "LAN-MATRIZ",
                    "hosts": [
                        {
                            "ip": "192.168.1.254",
                            "portas": [{"porta": 21}, {"porta": 80}, {"porta": 443}],
                            "hostname": "gw-matriz",
                        }
                    ],
                }
            ),
            current_user=app_module.require_permission("gerenciar_auditoria_cyber")(soc),
        )
        self.assertGreaterEqual(imp["achados_criados"], 1)

        # 6. Checklist CIS/NIST + Base de Conhecimento + PDF Executivo
        chk = app_module.obter_checklist_cyber_api(soc)
        self.assertGreaterEqual(len(chk), 6)
        chk[0]["status"] = "Conforme"
        chk[1]["status"] = "Conforme"
        saved_chk = app_module.salvar_checklist_cyber_api(
            app_module.CyberChecklistSaveSchema(itens=chk),
            current_user=app_module.require_permission("gerenciar_auditoria_cyber")(soc),
        )
        self.assertEqual(saved_chk["itens"][0]["status"], "Conforme")

        kb = app_module.listar_conhecimento_cyber_api(busca="Zero Trust", current_user=soc)
        self.assertGreaterEqual(len(kb), 1)
        app_module.criar_conhecimento_cyber_api(
            app_module.CyberConhecimentoSchema(
                titulo="Hardening Mikrotik ISP",
                categoria="ISP & BGP",
                resumo="Boas práticas de borda",
                conteudo="Desativar IP Services não usados e aplicar bogons.",
                tags="isp,mikrotik",
            ),
            current_user=app_module.require_permission("gerenciar_auditoria_cyber")(soc),
        )

        # Mitiga achado manual e testa PDF
        ach_res = app_module.criar_achado_cyber_api(
            app_module.CyberAchadoSchema(
                titulo="Backup sem teste de restore",
                severidade="Média",
                categoria="Continuidade",
                alvo="NAS-01",
                recomendacao="Testar restore mensalmente.",
            ),
            current_user=app_module.require_permission("gerenciar_auditoria_cyber")(soc),
        )
        upd_ach = app_module.atualizar_achado_cyber_api(
            ach_res["item"]["id"],
            app_module.CyberAchadoSchema(status="Resolvido"),
            current_user=app_module.require_permission("gerenciar_auditoria_cyber")(soc),
        )
        self.assertEqual(upd_ach["item"]["status"], "Resolvido")

        resumo = app_module.obter_resumo_cyber_api(soc)
        self.assertGreater(resumo["maturidade_percent"], 0)
        self.assertGreaterEqual(resumo["total_auditorias"], 2)

        pdf = app_module.baixar_relatorio_cyber_pdf(
            current_user=app_module.require_permission("exportar_pdf")(soc)
        )
        self.assertEqual(pdf.media_type, "application/pdf")
        self.assertTrue(pdf.body.startswith(b"%PDF"))

    def test_firestore_security_ops_store_compat(self):
        fake_db = FakeFirestoreDB()
        fs_store = automation_cyber.FirestoreSecurityOpsStore(fake_db)
        svc = automation_cyber.AutomationCyberService(store=fs_store)

        ativo = svc.criar_ativo_automacao(
            "tenant_fs",
            {
                "pilar": "iot",
                "nome": "Controlador Zigbee",
                "senha_padrao_alterada": True,
                "firmware_atualizado": True,
                "rede_segmentada": True,
                "energia_backup_ok": True,
                "monitoramento_ativo": True,
            },
            actor="Admin",
        )
        self.assertEqual(ativo["status_auditoria"], "Conforme")
        self.assertEqual(len(svc.listar_automacao("tenant_fs")), 1)

        ach = svc.criar_achado(
            "tenant_fs",
            {"titulo": "Teste FS", "severidade": "Baixa"},
            actor="Admin",
        )
        self.assertEqual(ach["titulo"], "Teste FS")
        self.assertEqual(len(svc.listar_achados("tenant_fs")), 1)


if __name__ == "__main__":
    unittest.main()
