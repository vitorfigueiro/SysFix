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
import auth_security
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
                "loja",
                "estoque",
                "automacao",
                "ciberseguranca",
                "laudos",
                "financeiro",
                "administracao",
            ],
        )
        self.assertIn("estoquista", me["role_presets"])
        self.assertIn("vendedor_loja", me["role_presets"])
        self.assertIn("gerente_loja", me["role_presets"])
        self.assertIn("tecnico_ti", me["role_presets"])
        self.assertIn("tecnico_automacao", me["role_presets"])
        self.assertIn("analista_cyber", me["role_presets"])

        # Configurar setores ativos da empresa (desabilita tudo -> deve falhar; desabilita só loja -> aceita)
        with self.assertRaises(HTTPException) as ctx_empty_sec:
            app_module.atualizar_configuracoes_empresa(
                app_module.TenantSettingsSchema(
                    setores_ativos={
                        "ti": False,
                        "loja": False,
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
                setores_ativos={"ti": True, "loja": False, "estoque": True}
            ),
            current_user=admin,
        )
        self.assertFalse(upd_sec["tenant"]["setores_ativos"]["loja"])
        self.assertTrue(upd_sec["tenant"]["setores_ativos"]["estoque"])

        # Alias legado 'gerenciar_operacoes_ti' expande corretamente sem quebrar
        expanded = auth_module.normalizar_permissoes_input({"gerenciar_operacoes_ti": False})
        self.assertFalse(expanded["ver_loja"])
        self.assertFalse(expanded["gerenciar_clientes_loja"])
        self.assertFalse(expanded["ver_estoque"])
        self.assertFalse(expanded["movimentar_estoque"])

        # 1. Estoquista puro: cria item com almoxarifado/unidade, movimenta e vê histórico; bloqueado em TI, Loja e Custos
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
                nome="SSD NVMe 1TB PCIe 4.0",
                codigo="SSD-1TB",
                marca="Kingston",
                categoria="Hardware TI",
                almoxarifado="Almoxarifado Central",
                unidade="un",
                quantidade=10,
                estoque_minimo=2,
                custo_unitario=290.0,
                preco_venda=450.0,
                preco_atacado_b2b=390.0,
                garantia_meses=12,
            ),
            current_user=app_module.require_permission("gerenciar_estoque")(est_user),
        )
        self.assertEqual(item_resp["item"]["almoxarifado"], "Almoxarifado Central")
        self.assertEqual(item_resp["item"]["unidade"], "un")
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

        for perm_bloqueada in ("ver_ti", "ver_loja", "operar_pdv_vendas", "gerenciar_laudos", "ver_dashboard", "ver_custos"):
            with self.assertRaises(HTTPException) as ctx_b:
                app_module.require_permission(perm_bloqueada)(est_user)
            self.assertEqual(ctx_b.exception.status_code, 403)

        # 2. Vendedor Loja: acessa garantias/RMA, PDV e consulta estoque, mas não cadastra/exclui itens nem vê custos
        vend_perms = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        vend_perms.update(auth_module.ROLE_PRESETS["vendedor_loja"]["permissoes"])
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Rafael Vendedor",
                email="vendedor@multisetor.com",
                senha="secret123",
                role="tecnico",
                permissoes=vend_perms,
            ),
            current_user=admin,
        )
        tok_vend = app_module.login_usuario(
            app_module.LoginSchema(email="vendedor@multisetor.com", senha="secret123")
        )["token"]
        vend_user = app_module.get_current_user(f"Bearer {tok_vend}")

        rma_vend = app_module.criar_rma_loja_api(
            app_module.LojaRmaSchema(
                tipo="RMA Cliente",
                produto="Fonte 650W 80 Plus",
                serial_imei="PSU-889900",
                cliente_fornecedor="João Silva",
                defeito="Não liga",
            ),
            current_user=app_module.require_permission("gerenciar_garantias_rma")(vend_user),
        )
        self.assertEqual(rma_vend["item"]["produto"], "Fonte 650W 80 Plus")

        with self.assertRaises(HTTPException) as ctx_vend_est:
            app_module.require_permission("gerenciar_estoque")(vend_user)
        self.assertEqual(ctx_vend_est.exception.status_code, 403)

    def test_criador_de_laudos_separado_e_permissao_tecnico(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="LaudoTech Labs",
                nome_usuario="Admin Master",
                email="admin@laudotech.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        me = app_module.obter_sessao_atual(admin)
        cat_keys = {c["key"]: c for c in me["permissions_catalog"]}
        self.assertIn("acessar_criador_laudos", cat_keys)
        self.assertEqual(cat_keys["acessar_criador_laudos"]["setor"], "laudos")
        self.assertTrue(me["role_presets"]["tecnico_ti"]["permissoes"]["acessar_criador_laudos"])

        # Cria uma O.S. com custo interno para testar que o criador de laudos não vaza custos
        ent = app_module.criar_entrada(
            app_module.EntradaSchema(
                equipamento="Nobreak APC 1500VA",
                tombamento="NB-550",
                tecnico="Admin Master",
                data_coleta="24/11/2026",
                origem="Matriz",
                os_coleta="OS-900",
                localizacao="Bancada TI",
                problema="Bateria estufada",
            ),
            current_user=admin,
        )
        os_id = ent["id"]
        app_module.registrar_saida(
            os_id,
            app_module.SaidaSchema(
                tecnico_entrega="Admin Master",
                data_entrega="24/11/2026",
                os_entrega="OS-900",
                status_custo="Com Custo",
                valor_custo=450.0,
                resolucao="Troca de bateria selada",
            ),
            current_user=admin,
        )

        # Técnico 1: possui acessar_criador_laudos=True (sem ver_ti e sem ver_custos)
        perms_laudo_only = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        perms_laudo_only["acessar_criador_laudos"] = True
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Técnico Laudos",
                email="laudos@laudotech.com",
                senha="secret123",
                role="tecnico",
                permissoes=perms_laudo_only,
            ),
            current_user=admin,
        )
        tok_laudo = app_module.login_usuario(
            app_module.LoginSchema(email="laudos@laudotech.com", senha="secret123")
        )["token"]
        tec_laudo = app_module.get_current_user(f"Bearer {tok_laudo}")

        guard = app_module.require_any_permission("acessar_criador_laudos", "gerenciar_laudos")
        painel = app_module.listar_laudos_criador_api(current_user=guard(tec_laudo))
        self.assertEqual(painel["resumo"]["total_equipamentos"], 1)
        self.assertNotIn("valor_custo", painel["equipamentos"][0])

        criado = app_module.criar_laudo_equipamento(
            os_id,
            app_module.LaudoSchema(
                titulo="LAUDO DE BANCADA ELÉTRICA",
                conclusao="Reparado / Aprovado",
                diagnostico="Baterias com tensão abaixo de 10V",
                parecer="Substituído banco de baterias 12V 7Ah e testado autonomia.",
                recomendacoes="Manter em ambiente climatizado.",
                tecnico_responsavel="Técnico Laudos",
            ),
            current_user=guard(tec_laudo),
        )
        laudo_id = criado["laudo"]["id"]
        pdf_resp = app_module.baixar_laudo_pdf(
            os_id, laudo_id, current_user=guard(tec_laudo)
        )
        self.assertEqual(pdf_resp.media_type, "application/pdf")
        self.assertTrue(pdf_resp.body.startswith(b"%PDF"))

        # Técnico 2: sem permissão de criador de laudos -> bloqueado com 403
        perms_sem_laudo = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        perms_sem_laudo["ver_ti"] = True
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Técnico Sem Laudo",
                email="semlaudo@laudotech.com",
                senha="secret123",
                role="tecnico",
                permissoes=perms_sem_laudo,
            ),
            current_user=admin,
        )
        tok_sem = app_module.login_usuario(
            app_module.LoginSchema(email="semlaudo@laudotech.com", senha="secret123")
        )["token"]
        tec_sem = app_module.get_current_user(f"Bearer {tok_sem}")
        with self.assertRaises(HTTPException) as ctx_403:
            guard(tec_sem)
        self.assertEqual(ctx_403.exception.status_code, 403)

    def test_dashboard_entradas_permissao_separada(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="DashEntradas Labs",
                nome_usuario="Admin",
                email="admin@dashentradas.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        me = app_module.obter_sessao_atual(admin)
        cat_keys = {c["key"]: c for c in me["permissions_catalog"]}
        self.assertIn("ver_dashboard_entradas", cat_keys)
        self.assertEqual(cat_keys["ver_dashboard_entradas"]["setor"], "ti")
        self.assertTrue(me["role_presets"]["tecnico_ti"]["permissoes"]["ver_dashboard_entradas"])

        app_module.criar_entrada(
            app_module.EntradaSchema(
                equipamento="Switch 24p",
                tombamento="SW-10",
                tecnico="Admin",
                data_coleta="24/11/2026",
                origem="Matriz",
                os_coleta="OS-10",
                localizacao="Bancada TI",
                problema="Sem link",
            ),
            current_user=admin,
        )

        # Técnico com ver_dashboard_entradas=True, sem ver_dashboard executivo e sem custos
        perms_ent = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        perms_ent["ver_ti"] = True
        perms_ent["ver_dashboard_entradas"] = True
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Técnico Dash Entradas",
                email="entdash@dashentradas.com",
                senha="secret123",
                role="tecnico",
                permissoes=perms_ent,
            ),
            current_user=admin,
        )
        tok_ent = app_module.login_usuario(
            app_module.LoginSchema(email="entdash@dashentradas.com", senha="secret123")
        )["token"]
        tec_ent = app_module.get_current_user(f"Bearer {tok_ent}")

        guard = app_module.require_any_permission("ver_dashboard", "ver_dashboard_entradas")
        dash = app_module.obter_dashboard(mes=11, ano=2026, current_user=guard(tec_ent))
        self.assertTrue(dash["modo_entradas"])
        self.assertEqual(dash["resumo"]["total_pendentes"], 1)
        self.assertNotIn("por_tecnico", dash)
        self.assertNotIn("custo_total_mes", dash["resumo"])

        # Técnico sem ver_dashboard e sem ver_dashboard_entradas -> 403
        perms_no_dash = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        perms_no_dash["ver_ti"] = True
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Técnico Sem Dash",
                email="semdash@dashentradas.com",
                senha="secret123",
                role="tecnico",
                permissoes=perms_no_dash,
            ),
            current_user=admin,
        )
        tok_no = app_module.login_usuario(
            app_module.LoginSchema(email="semdash@dashentradas.com", senha="secret123")
        )["token"]
        tec_no = app_module.get_current_user(f"Bearer {tok_no}")
        with self.assertRaises(HTTPException) as ctx_403:
            guard(tec_no)
        self.assertEqual(ctx_403.exception.status_code, 403)


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


class AuthHardeningAnd2FATests(unittest.TestCase):
    def setUp(self):
        auth_security.login_rate_limiter.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = os.environ.get("SYSFIX_DATA_DIR")
        self.original_backend = os.environ.get("SYSFIX_BACKEND")
        self.original_secret = os.environ.get("SYSFIX_SECRET_KEY")

        os.environ["SYSFIX_DATA_DIR"] = self.temp_dir.name
        os.environ["SYSFIX_BACKEND"] = "local"
        os.environ["SYSFIX_SECRET_KEY"] = "hardening-2fa-secret-2026"

        self.original_store = models._store
        self.original_get_auth_service = app_module._get_auth_service

        models._store = JsonStore(Path(self.temp_dir.name) / "coletas.json")
        models._store.ensure()
        self.user_store = LocalTenantUserStore(Path(self.temp_dir.name) / "auth_db.json")
        self.user_store.ensure()
        self.auth_service = AuthService(user_store=self.user_store, backend_override="local")
        app_module._get_auth_service = lambda: self.auth_service

    def tearDown(self):
        auth_security.login_rate_limiter.clear()
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

    def test_local_totp_2fa_recovery_codes_session_revocation_and_password_change(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="SecAuth TI",
                nome_usuario="Vitor Admin",
                email="vitor@secauth.com",
                senha="SenhaForte#1",
            )
        )
        token_inicial = reg["token"]
        admin = app_module.get_current_user(f"Bearer {token_inicial}")
        self.assertFalse(admin["mfa_enabled"])
        self.assertEqual(admin["session_version"], 1)

        # Configura política da empresa recomendando 2FA
        cfg = app_module.atualizar_configuracoes_empresa(
            app_module.TenantSettingsSchema(exigir_mfa_equipe=True),
            current_user=admin,
        )
        self.assertTrue(cfg["tenant"]["exigir_mfa_equipe"])

        # Inicia setup 2FA e valida geração de QR SVG
        setup = app_module.iniciar_setup_mfa_api(admin)
        self.assertIn("secret", setup)
        self.assertTrue(setup["qr_svg_data_uri"].startswith("data:image/svg+xml;base64,"))
        self.assertIn("otpauth://totp/", setup["otpauth_uri"])

        # Confirma com código TOTP gerado pelo módulo
        totp_now = auth_security.compute_totp(setup["secret"])
        confirm = app_module.confirmar_setup_mfa_api(
            app_module.MfaCodeSchema(codigo=totp_now),
            current_user=admin,
        )
        self.assertTrue(confirm["mfa_enabled"])
        self.assertEqual(len(confirm["recovery_codes"]), 8)

        # Sanitização não pode expor segredos
        raw_user = self.user_store.get_user(admin["uid"])
        sanitized = auth_module.sanitizar_usuario(raw_user)
        for hidden_key in ("password_hash", "mfa_secret", "mfa_pending_secret", "mfa_recovery_hashes"):
            self.assertNotIn(hidden_key, sanitized)

        # Novo login retorna challenge MFA e NÃO emite token de sessão direto
        login_res = app_module.login_usuario(
            app_module.LoginSchema(email="vitor@secauth.com", senha="SenhaForte#1")
        )
        self.assertTrue(login_res["mfa_required"])
        self.assertNotIn("token", login_res)

        # Challenge token não deve ser aceito em rotas autenticadas
        with self.assertRaises(HTTPException) as ctx_bad_tok:
            app_module.get_current_user(f"Bearer {login_res['challenge_token']}")
        self.assertEqual(ctx_bad_tok.exception.status_code, 401)

        # Valida challenge com código TOTP
        mfa_ok = app_module.verificar_mfa_login_api(
            app_module.MfaVerifyLoginSchema(
                challenge_token=login_res["challenge_token"],
                codigo=auth_security.compute_totp(setup["secret"]),
            )
        )
        self.assertIn("token", mfa_ok)
        self.assertGreaterEqual(len(mfa_ok["user"]["ultimos_acessos"]), 1)

        # Código de recuperação funciona 1 vez e é consumido
        rec_code = confirm["recovery_codes"][0]
        login_res2 = app_module.login_usuario(
            app_module.LoginSchema(email="vitor@secauth.com", senha="SenhaForte#1")
        )
        rec_ok = app_module.verificar_mfa_login_api(
            app_module.MfaVerifyLoginSchema(
                challenge_token=login_res2["challenge_token"],
                codigo=rec_code,
            )
        )
        self.assertIn("token", rec_ok)

        login_res3 = app_module.login_usuario(
            app_module.LoginSchema(email="vitor@secauth.com", senha="SenhaForte#1")
        )
        with self.assertRaises(HTTPException) as ctx_reuse:
            app_module.verificar_mfa_login_api(
                app_module.MfaVerifyLoginSchema(
                    challenge_token=login_res3["challenge_token"],
                    codigo=rec_code,
                )
            )
        self.assertEqual(ctx_reuse.exception.status_code, 401)

        # Revogar sessões invalida token antigo e gera novo
        admin_atual = app_module.get_current_user(f"Bearer {rec_ok['token']}")
        rev = app_module.revogar_sessoes_api(admin_atual)
        self.assertEqual(rev["session_version"], 2)
        with self.assertRaises(HTTPException) as ctx_revoked:
            app_module.get_current_user(f"Bearer {token_inicial}")
        self.assertEqual(ctx_revoked.exception.status_code, 401)

        # Troca de senha invalida token da v2 e autentica com nova senha
        admin_v2 = app_module.get_current_user(f"Bearer {rev['token']}")
        pwd_res = app_module.alterar_senha_usuario_api(
            app_module.ChangePasswordSchema(
                senha_atual="SenhaForte#1",
                nova_senha="NovaSenha#2026",
            ),
            current_user=admin_v2,
        )
        self.assertEqual(pwd_res["session_version"], 3)
        with self.assertRaises(HTTPException):
            app_module.get_current_user(f"Bearer {rev['token']}")

        # Desativar 2FA exige código válido e permite login direto depois
        admin_v3 = app_module.get_current_user(f"Bearer {pwd_res['token']}")
        app_module.desativar_mfa_api(
            app_module.MfaCodeSchema(codigo=auth_security.compute_totp(setup["secret"])),
            current_user=admin_v3,
        )
        login_sem_2fa = app_module.login_usuario(
            app_module.LoginSchema(email="vitor@secauth.com", senha="NovaSenha#2026")
        )
        self.assertFalse(login_sem_2fa["mfa_required"])
        self.assertIn("token", login_sem_2fa)

    def test_brute_force_rate_limit_blocks_after_five_failures(self):
        app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="RateLimit Labs",
                nome_usuario="Tester",
                email="tester@ratelimit.com",
                senha="SenhaCorreta99",
            )
        )
        for _ in range(5):
            with self.assertRaises(HTTPException) as ctx_401:
                app_module.login_usuario(
                    app_module.LoginSchema(email="tester@ratelimit.com", senha="errada")
                )
            self.assertEqual(ctx_401.exception.status_code, 401)

        # 6ª tentativa deve retornar 429 Too Many Requests mesmo com senha correta
        with self.assertRaises(HTTPException) as ctx_429:
            app_module.login_usuario(
                app_module.LoginSchema(email="tester@ratelimit.com", senha="SenhaCorreta99")
            )
        self.assertEqual(ctx_429.exception.status_code, 429)

    def test_firebase_2fa_enforces_428_until_session_verified(self):
        fake_db = FakeFirestoreDB()
        fake_fb_client = FakeFirebaseAuthClient()
        fake_fb_client.tokens["fb_tok_1"] = {
            "uid": "fb_uid_sec_1",
            "email": "cloud@secfix.com",
            "name": "Cloud Admin",
        }
        fs_user_store = FirestoreTenantUserStore(fake_db)
        fb_provider = FirebaseAuthProvider(auth_client=fake_fb_client)
        fb_auth_service = AuthService(
            user_store=fs_user_store,
            firebase_auth_provider=fb_provider,
            backend_override="firebase",
        )
        app_module._get_auth_service = lambda: fb_auth_service

        onb = app_module.onboarding_firebase(
            app_module.OnboardingSchema(nome_empresa="CloudGuard", nome_usuario="Cloud Admin"),
            authorization="Bearer fb_tok_1",
        )
        self.assertEqual(onb["user"]["uid"], "fb_uid_sec_1")

        ctx_fb = app_module.get_current_user("Bearer fb_tok_1")
        setup = app_module.iniciar_setup_mfa_api(ctx_fb)
        conf = app_module.confirmar_setup_mfa_api(
            app_module.MfaCodeSchema(codigo=auth_security.compute_totp(setup["secret"])),
            current_user=ctx_fb,
        )
        self.assertTrue(conf["mfa_token"])

        # Sem cabeçalho X-SysFix-MFA deve retornar 428 MFA_REQUIRED
        with self.assertRaises(HTTPException) as ctx_428:
            app_module.get_current_user("Bearer fb_tok_1")
        self.assertEqual(ctx_428.exception.status_code, 428)
        self.assertEqual(ctx_428.exception.detail["codigo"], "MFA_REQUIRED")

        # Verifica sessão MFA para obter novo X-SysFix-MFA e autentica com sucesso
        verified = app_module.verificar_mfa_sessao_api(
            app_module.MfaCodeSchema(codigo=auth_security.compute_totp(setup["secret"])),
            authorization="Bearer fb_tok_1",
        )
        user_ok = app_module.get_current_user(
            authorization="Bearer fb_tok_1",
            x_sysfix_mfa=verified["mfa_token"],
        )
        self.assertEqual(user_ok["uid"], "fb_uid_sec_1")
        self.assertTrue(user_ok["mfa_enabled"])


class BancadaCustomFieldsAndSummariesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = os.environ.get("SYSFIX_DATA_DIR")
        self.original_backend = os.environ.get("SYSFIX_BACKEND")
        self.original_secret = os.environ.get("SYSFIX_SECRET_KEY")

        os.environ["SYSFIX_DATA_DIR"] = self.temp_dir.name
        os.environ["SYSFIX_BACKEND"] = "local"
        os.environ["SYSFIX_SECRET_KEY"] = "bancada-custom-secret-2026"

        self.original_store = models._store
        self.original_get_auth_service = app_module._get_auth_service

        models._store = JsonStore(Path(self.temp_dir.name) / "coletas.json")
        models._store.ensure()
        self.user_store = LocalTenantUserStore(Path(self.temp_dir.name) / "auth_db.json")
        self.user_store.ensure()
        self.auth_service = AuthService(user_store=self.user_store, backend_override="local")
        app_module._get_auth_service = lambda: self.auth_service

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

    def test_tombamento_opcoes_resumos_e_campos_editaveis(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="Bancada Flex TI",
                nome_usuario="Gestor Admin",
                email="gestor@bancadaflex.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        # 1. Admin personaliza campos padrão (torna os_coleta opcional/oculto) e cria campos personalizados obrigatórios
        cfg_campos = {
            "padrao": {
                **auth_module.DEFAULT_BANCADA_FIELDS["padrao"],
                "os_coleta": {"label": "O.S Coleta", "visivel": False, "obrigatorio": False},
            },
            "personalizados": [
                {
                    "id": "acessorios",
                    "label": "Acessórios Recebidos",
                    "secao": "entrada",
                    "tipo": "texto",
                    "obrigatorio": True,
                },
                {
                    "id": "nota_fiscal",
                    "label": "Nota Fiscal",
                    "secao": "saida",
                    "tipo": "texto",
                    "obrigatorio": True,
                },
            ],
        }
        upd = app_module.atualizar_configuracoes_empresa(
            app_module.TenantSettingsSchema(campos_bancada=cfg_campos),
            current_user=admin,
        )
        self.assertFalse(upd["tenant"]["campos_bancada"]["padrao"]["os_coleta"]["obrigatorio"])
        self.assertEqual(len(upd["tenant"]["campos_bancada"]["personalizados"]), 2)

        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        # 2. Campo personalizado obrigatório faltando -> HTTP 400
        with self.assertRaises(HTTPException) as ctx_miss:
            app_module.criar_entrada(
                app_module.EntradaSchema(
                    equipamento="Notebook Dell Inspiron",
                    tipo_tombamento="Sem tombamento",
                    tombamento="",
                    tecnico="Gestor Admin",
                    data_coleta="24/11/2026",
                    origem="Cliente Loja",
                    os_coleta="",
                    localizacao="Bancada TI",
                ),
                current_user=admin,
            )
        self.assertEqual(ctx_miss.exception.status_code, 400)

        # 3. Entrada com "Sem tombamento", os_coleta oculto/vazio e campo personalizado preenchido gera resumo_coleta
        ent = app_module.criar_entrada(
            app_module.EntradaSchema(
                equipamento="Notebook Dell Inspiron",
                tipo_tombamento="Sem tombamento",
                tombamento="",
                tecnico="Gestor Admin",
                data_coleta="24/11/2026",
                origem="Cliente Loja",
                os_coleta="",
                localizacao="Bancada TI",
                problema="Não liga",
                campos_personalizados={"acessorios": "Carregador original + mouse"},
            ),
            current_user=admin,
        )
        os_id = ent["id"]
        self.assertIn("Identificação (Sem tombamento): Sem tombamento", ent["resumo_coleta"])
        self.assertIn("Acessórios Recebidos: Carregador original + mouse", ent["resumo_coleta"])

        # 4. Entrada por Serial aproveitando serial_mac quando tombamento vazio
        ent_serial = app_module.criar_entrada(
            [
                app_module.EntradaSchema(
                    equipamento="ONU Huawei",
                    tipo_tombamento="Serial",
                    tombamento="",
                    serial_mac="48575443AABBCC",
                    tecnico="Gestor Admin",
                    data_coleta="24/11/2026",
                    origem="Assinante",
                    localizacao="Bancada TI",
                    campos_personalizados={"acessorios": "Fonte 12V"},
                )
            ],
            current_user=admin,
        )
        self.assertIn("Identificação (Serial): 48575443AABBCC", ent_serial["resumo_coleta"])

        # 5. Saída gera resumo_entrega com campos personalizados e oculta custos para técnico sem ver_custos
        with self.assertRaises(HTTPException) as ctx_out_req:
            app_module.registrar_saida(
                os_id,
                app_module.SaidaSchema(
                    tecnico_entrega="Gestor Admin",
                    data_entrega="24/11/2026",
                    os_entrega="OS-DEL-1",
                    status_custo="Com Custo",
                    valor_custo=180.0,
                    resolucao="Troca de DC Jack",
                ),
                current_user=admin,
            )
        self.assertEqual(ctx_out_req.exception.status_code, 400)

        sai = app_module.registrar_saida(
            os_id,
            app_module.SaidaSchema(
                tecnico_entrega="Gestor Admin",
                data_entrega="24/11/2026",
                os_entrega="OS-DEL-1",
                status_custo="Com Custo",
                valor_custo=180.0,
                resolucao="Troca de DC Jack",
                campos_personalizados={"nota_fiscal": "NF-4410"},
            ),
            current_user=admin,
        )
        self.assertIn("Nota Fiscal: NF-4410", sai["resumo_entrega"])
        self.assertIn("180.00", sai["resumo_entrega"])

        # Técnico sem ver_custos consulta GET /api/equipamentos/{id}/resumo sem vazar valores
        perms_sem_custo = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        perms_sem_custo["ver_ti"] = True
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Técnico Operacional",
                email="op@bancadaflex.com",
                senha="secret123",
                role="tecnico",
                permissoes=perms_sem_custo,
            ),
            current_user=admin,
        )
        tok_op = app_module.login_usuario(
            app_module.LoginSchema(email="op@bancadaflex.com", senha="secret123")
        )["token"]
        tec_op = app_module.get_current_user(f"Bearer {tok_op}")

        resumo_tec = app_module.obter_resumo_os_api(os_id, current_user=tec_op)
        self.assertIn("Nota Fiscal: NF-4410", resumo_tec["resumo_entrega"])
        self.assertNotIn("180.00", resumo_tec["resumo_entrega"])


class LojaTIAndFirstLoginAndSectorDashTests(unittest.TestCase):
    def setUp(self):
        from loja_ti import LocalLojaStore, LojaTIService
        from ops_modules import LocalOpsStore, OpsModulesService

        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = os.environ.get("SYSFIX_DATA_DIR")
        self.original_backend = os.environ.get("SYSFIX_BACKEND")
        self.original_secret = os.environ.get("SYSFIX_SECRET_KEY")

        os.environ["SYSFIX_DATA_DIR"] = self.temp_dir.name
        os.environ["SYSFIX_BACKEND"] = "local"
        os.environ["SYSFIX_SECRET_KEY"] = "test-secret-key"

        self.original_store = models._store
        self.original_get_auth_service = app_module._get_auth_service
        self.original_get_ops_service = app_module._get_ops_service
        self.original_get_loja_service = app_module._get_loja_service
        self.original_get_sec_service = app_module._get_security_ops_service

        models._store = JsonStore(Path(self.temp_dir.name) / "coletas.json")
        models._store.ensure()
        self.user_store = LocalTenantUserStore(Path(self.temp_dir.name) / "auth_db.json")
        self.user_store.ensure()
        self.auth_service = AuthService(user_store=self.user_store, backend_override="local")
        self.ops_store = LocalOpsStore(Path(self.temp_dir.name) / "ops_db.json")
        self.ops_store.ensure()
        self.ops_service = OpsModulesService(store=self.ops_store)
        self.loja_store = LocalLojaStore(Path(self.temp_dir.name) / "loja_db.json")
        self.loja_service = LojaTIService(store=self.loja_store, ops_service=self.ops_service)
        self.sec_store = automation_cyber.LocalSecurityOpsStore(Path(self.temp_dir.name) / "sec_db.json")
        self.sec_service = automation_cyber.AutomationCyberService(store=self.sec_store)

        app_module._get_auth_service = lambda: self.auth_service
        app_module._get_ops_service = lambda: self.ops_service
        app_module._get_loja_service = lambda: self.loja_service
        app_module._get_security_ops_service = lambda: self.sec_service

    def tearDown(self):
        models._store = self.original_store
        app_module._get_auth_service = self.original_get_auth_service
        app_module._get_ops_service = self.original_get_ops_service
        app_module._get_loja_service = self.original_get_loja_service
        app_module._get_security_ops_service = self.original_get_sec_service
        for var, orig in (
            ("SYSFIX_DATA_DIR", self.original_data_dir),
            ("SYSFIX_BACKEND", self.original_backend),
            ("SYSFIX_SECRET_KEY", self.original_secret),
        ):
            if orig is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = orig
        self.temp_dir.cleanup()

    def test_permissoes_dashboards_por_setor_e_laudos_apenas_emitir_exportar(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="SetorDash TI",
                nome_usuario="Gestor Admin",
                email="admin@setordash.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")
        me = app_module.obter_sessao_atual(admin)
        cat_keys = {c["key"]: c for c in me["permissions_catalog"]}

        esperados_dash = {
            "ver_dashboard_entradas": "ti",
            "ver_dashboard_loja": "loja",
            "ver_dashboard_estoque": "estoque",
            "ver_dashboard_automacao": "automacao",
            "ver_dashboard_cyber": "ciberseguranca",
            "ver_dashboard_laudos": "laudos",
            "ver_dashboard": "financeiro",
        }
        for key, setor in esperados_dash.items():
            self.assertIn(key, cat_keys)
            self.assertEqual(cat_keys[key]["setor"], setor)
            self.assertTrue(len(cat_keys[key]["descricao"]) >= 30)

        # Técnico apenas com emitir laudo e exportar PDF (sem ver_dashboard_laudos nem gerenciar_modelos)
        perms_tec = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        perms_tec["gerenciar_laudos"] = True
        perms_tec["exportar_pdf"] = True
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Técnico Só Laudo",
                email="solaudo@setordash.com",
                senha="secret123",
                role="tecnico",
                permissoes=perms_tec,
            ),
            current_user=admin,
        )
        tok = app_module.login_usuario(
            app_module.LoginSchema(email="solaudo@setordash.com", senha="secret123")
        )["token"]
        tec = app_module.get_current_user(f"Bearer {tok}")
        painel = app_module.listar_laudos_criador_api(
            current_user=app_module.require_any_permission("acessar_criador_laudos", "gerenciar_laudos")(tec)
        )
        self.assertFalse(painel["mostrar_dashboard"])

    def test_primeiro_login_troca_senha_obrigatoria_e_middleware_http(self):
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        def _drive_coro(coro):
            try:
                coro.send(None)
                raise AssertionError("Coroutine inesperadamente suspensa.")
            except StopIteration as stop:
                return stop.value
            finally:
                coro.close()

        def _make_req(path: str, token: str = "") -> Request:
            headers = []
            if token:
                headers.append((b"authorization", f"Bearer {token}".encode("latin-1")))
            scope = {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": b"",
                "headers": headers,
                "client": ("127.0.0.1", 50000),
                "server": ("testserver", 80),
                "scheme": "http",
            }
            return Request(scope)

        async def _ok_next(_req):
            return JSONResponse({"ok": True}, status_code=200)

        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="FirstLogin Corp",
                nome_usuario="Admin",
                email="admin@firstlogin.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")
        self.assertFalse(admin["must_change_password"])

        membro = app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Novo Técnico",
                email="novo@firstlogin.com",
                senha="temp123",
                role="tecnico",
            ),
            current_user=admin,
        )
        self.assertTrue(membro["usuario"]["must_change_password"])

        login_data = app_module.login_usuario(
            app_module.LoginSchema(email="novo@firstlogin.com", senha="temp123")
        )
        tok_temp = login_data["token"]
        self.assertTrue(login_data["user"]["must_change_password"])

        # Rotas de negócio bloqueadas pelo middleware (428 PASSWORD_CHANGE_REQUIRED) até definir senha própria
        blocked_res = _drive_coro(
            app_module.enforce_first_login_password_change(
                _make_req("/api/equipamentos", tok_temp), _ok_next
            )
        )
        self.assertEqual(blocked_res.status_code, 428)
        self.assertEqual(
            json.loads(blocked_res.body.decode("utf-8"))["detail"]["codigo"],
            "PASSWORD_CHANGE_REQUIRED",
        )

        # Rota de troca de primeiro acesso é permitida pelo middleware
        allow_change_res = _drive_coro(
            app_module.enforce_first_login_password_change(
                _make_req("/api/auth/primeiro-acesso-senha", tok_temp), _ok_next
            )
        )
        self.assertEqual(allow_change_res.status_code, 200)

        tec_temp_ctx = app_module.get_current_user(f"Bearer {tok_temp}")

        # Não aceita repetir a senha temporária
        with self.assertRaises(HTTPException) as ctx_same:
            app_module.primeiro_acesso_definir_senha_api(
                app_module.FirstAccessPasswordSchema(
                    nova_senha="temp123", confirmar_senha="temp123"
                ),
                current_user=tec_temp_ctx,
            )
        self.assertEqual(ctx_same.exception.status_code, 400)

        # Define senha pessoal com sucesso e revoga token antigo
        body_ok = app_module.primeiro_acesso_definir_senha_api(
            app_module.FirstAccessPasswordSchema(
                nova_senha="MinhaSenha@2026", confirmar_senha="MinhaSenha@2026"
            ),
            current_user=tec_temp_ctx,
        )
        self.assertFalse(body_ok["user"]["must_change_password"])
        tok_novo = body_ok["token"]

        # Token antigo revogado (401); token novo liberado no middleware (200)
        with self.assertRaises(HTTPException) as ctx_revoked:
            app_module.get_current_user(f"Bearer {tok_temp}")
        self.assertEqual(ctx_revoked.exception.status_code, 401)

        unlocked_res = _drive_coro(
            app_module.enforce_first_login_password_change(
                _make_req("/api/equipamentos", tok_novo), _ok_next
            )
        )
        self.assertEqual(unlocked_res.status_code, 200)

    def test_loja_ti_pdv_orcamentos_caixa_garantia_rma_e_isolamento_custos(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="MegaTech Store & Assistência",
                nome_usuario="Diretor Loja",
                email="diretor@megatech.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        # 1. Cadastrar cliente PJ (B2B) e produto com preço varejo/B2B e garantia
        cli = app_module.criar_cliente_loja_api(
            app_module.LojaClienteSchema(
                nome="Empresa Alfa Ltda",
                tipo="Cliente PJ (B2B)",
                documento="12.345.678/0001-90",
                tabela_preco="b2b",
                telefone="11999998888",
            ),
            current_user=admin,
        )["item"]
        prod = app_module.criar_item_estoque_ops(
            app_module.EstoqueItemSchema(
                nome="Placa de Vídeo RTX 4060 8GB",
                codigo="GPU-4060",
                marca="ASUS",
                categoria="Hardware TI",
                quantidade=5,
                estoque_minimo=1,
                custo_unitario=1400.0,
                preco_venda=2100.0,
                preco_atacado_b2b=1950.0,
                garantia_meses=12,
                controlar_serial=True,
            ),
            current_user=admin,
        )["item"]

        # 2. Abrir Caixa PDV
        cx_open = app_module.abrir_caixa_loja_api(
            app_module.LojaCaixaAbrirSchema(valor_abertura=200.0, filial="Matriz"),
            current_user=admin,
        )["turno"]
        self.assertEqual(cx_open["valor_abertura"], 200.0)

        # 3. Registrar Venda PDV (tabela B2B + serial + baixa automática em estoque)
        venda = app_module.registrar_venda_loja_api(
            app_module.LojaVendaSchema(
                cliente_id=cli["id"],
                cliente_nome=cli["nome"],
                tabela_preco="b2b",
                forma_pagamento="Dinheiro",
                tipo_documento="NF-e (CNPJ)",
                numero_nf="000512",
                vendedor="Diretor Loja",
                comissao_percent=2.0,
                itens=[
                    {
                        "estoque_item_id": prod["id"],
                        "quantidade": 1,
                        "serial_imei": "SN-RTX4060-991",
                    }
                ],
            ),
            current_user=admin,
        )["venda"]
        self.assertEqual(venda["total"], 1950.0)
        self.assertEqual(venda["lucro_estimado"], 550.0)

        # Estoque caiu de 5 para 4
        est_atual = app_module.listar_estoque_ops(admin)[0]
        self.assertEqual(est_atual["quantidade"], 4)

        # Recibo PDF da venda
        recibo_pdf = app_module.baixar_recibo_venda_pdf_api(venda["id"], current_user=admin)
        self.assertTrue(recibo_pdf.body.startswith(b"%PDF"))

        # 4. Consulta antifraude de garantia por Serial
        gar = app_module.consultar_garantia_serial_api(termo="SN-RTX4060-991", current_user=admin)
        self.assertEqual(len(gar), 1)
        self.assertTrue(gar[0]["em_garantia"])
        self.assertEqual(gar[0]["cliente_nome"], "Empresa Alfa Ltda")

        # 5. Fechar Caixa (200 abertura + 1950 dinheiro = 2150)
        cx_close = app_module.fechar_caixa_loja_api(
            app_module.LojaCaixaFecharSchema(saldo_informado=2150.0, observacao="Caixa batido"),
            current_user=admin,
        )["turno"]
        self.assertEqual(cx_close["diferenca"], 0.0)

        # 6. Criar Orçamento + PDF + Converter em O.S. + Converter outro Orçamento em Venda
        orc = app_module.criar_orcamento_loja_api(
            app_module.LojaOrcamentoSchema(
                titulo="Workstation Engenharia",
                cliente_nome="Empresa Alfa Ltda",
                itens=[{"estoque_item_id": prod["id"], "nome": prod["nome"], "quantidade": 1, "preco_unitario": 2000.0}],
            ),
            current_user=admin,
        )["orcamento"]
        orc_pdf = app_module.baixar_orcamento_loja_pdf_api(orc["id"], current_user=admin)
        self.assertTrue(orc_pdf.body.startswith(b"%PDF"))

        conv_os = app_module.converter_orcamento_em_os_api(
            orc["id"],
            app_module.LojaOrcamentoConverterOsSchema(localizacao="Bancada TI"),
            current_user=admin,
        )
        self.assertEqual(conv_os["registro"]["tipo_fluxo"], "Montagem / Upgrade PC")

        # 7. Cancelar a primeira venda devolve o item ao estoque (4 -> 5)
        canc = app_module.cancelar_venda_loja_api(
            venda["id"],
            app_module.LojaVendaCancelarSchema(motivo="Cliente alterou pedido"),
            current_user=admin,
        )["venda"]
        self.assertEqual(canc["status"], "Cancelada")
        self.assertEqual(app_module.listar_estoque_ops(admin)[0]["quantidade"], 5)

        # 8. Vendedor sem ver_custos e sem gerenciar_caixa_loja não vê lucros nem abre caixa
        perms_vend = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        perms_vend.update(auth_module.ROLE_PRESETS["vendedor_loja"]["permissoes"])
        app_module.criar_usuario_empresa(
            app_module.TeamMemberCreateSchema(
                nome="Vendedor Balcão",
                email="balcao@megatech.com",
                senha="secret123",
                role="tecnico",
                permissoes=perms_vend,
            ),
            current_user=admin,
        )
        tok_v = app_module.login_usuario(
            app_module.LoginSchema(email="balcao@megatech.com", senha="secret123")
        )["token"]
        vend = app_module.get_current_user(f"Bearer {tok_v}")

        vendas_vend = app_module.listar_vendas_loja_api(current_user=vend)
        self.assertNotIn("lucro_estimado", vendas_vend[0])
        self.assertNotIn("custo_total", vendas_vend[0])

        resumo_vend = app_module.obter_resumo_loja_api(current_user=vend)
        self.assertTrue(resumo_vend["ocultar_custos"])
        self.assertNotIn("lucro_mes", resumo_vend)

        with self.assertRaises(HTTPException) as ctx_cx:
            app_module.require_permission("gerenciar_caixa_loja")(vend)
        self.assertEqual(ctx_cx.exception.status_code, 403)

        rel_loja = app_module.baixar_relatorio_loja_pdf_api(current_user=admin)
        self.assertTrue(rel_loja.body.startswith(b"%PDF"))

    def test_wiki_noticias_separadas_govkey_operadores_byte_scanner_url_e_automacoes_defesa(self):
        reg = app_module.registrar_empresa(
            app_module.RegisterSchema(
                nome_empresa="CyberOps 2.9",
                nome_usuario="Gestor Sec",
                email="gestor@cyber29.com",
                senha="secret123",
            )
        )
        admin = app_module.get_current_user(f"Bearer {reg['token']}")

        cyb_perms = {k: False for k in auth_module.ALL_PERMISSIONS_TRUE}
        cyb_perms.update(auth_module.ROLE_PRESETS["analista_cyber"]["permissoes"])

        for nome, email in (("Analista Autorizado", "autorizado@cyber29.com"), ("Analista NãoListado", "bloqueado@cyber29.com")):
            app_module.criar_usuario_empresa(
                app_module.TeamMemberCreateSchema(
                    nome=nome,
                    email=email,
                    senha="secret123",
                    role="tecnico",
                    permissoes=cyb_perms,
                    exigir_troca_senha=False,
                ),
                current_user=admin,
            )

        tok_aut = app_module.login_usuario(app_module.LoginSchema(email="autorizado@cyber29.com", senha="secret123"))["token"]
        tok_blq = app_module.login_usuario(app_module.LoginSchema(email="bloqueado@cyber29.com", senha="secret123"))["token"]

        # 1. Gov-Key autorizando QUEM pode realizar tarefas (sem exigir lista de alvos!)
        opcoes = app_module.listar_equipe_opcoes_govkey_api(current_user=admin)
        self.assertEqual(len(opcoes), 3)

        gov_res = app_module.atualizar_governanca_cyber_api(
            app_module.CyberGovernancaSchema(
                permitir_auditoria_rede=True,
                usuarios_autorizados=["autorizado@cyber29.com"],
                restringir_usuarios=True,
                escopo_autorizado=[],
                observacao_termo="Apenas Analista Autorizado liberado no Gov-Key",
            ),
            current_user=admin,
        )
        self.assertTrue(gov_res["governanca"]["permitir_auditoria_rede"])
        self.assertEqual(gov_res["governanca"]["usuarios_autorizados"], ["autorizado@cyber29.com"])

        user_aut = app_module.get_current_user(f"Bearer {tok_aut}")
        user_blq = app_module.get_current_user(f"Bearer {tok_blq}")

        orig_web = automation_cyber.NetworkAuditEngine.auditar_web_tls
        try:
            automation_cyber.NetworkAuditEngine.auditar_web_tls = staticmethod(
                lambda url, host: {
                    "score": 92,
                    "hosts_ativos": 1,
                    "portas_expostas": 1,
                    "detalhes": {"host": host, "url": url},
                    "achados_detectados": [],
                }
            )
            # Colaborador não listado no Gov-Key é bloqueado (403)
            with self.assertRaises(HTTPException) as ctx_blq:
                app_module.executar_auditoria_cyber_api(
                    app_module.CyberExecutarAuditoriaSchema(alvo="https://portal.cyber29.com.br/", tipo_auditoria="web_tls"),
                    current_user=user_blq,
                )
            self.assertEqual(ctx_blq.exception.status_code, 403)

            # Colaborador autorizado no Gov-Key executa domínio sem bloqueio falso de escopo
            ok_scan = app_module.executar_auditoria_cyber_api(
                app_module.CyberExecutarAuditoriaSchema(alvo="https://portal.cyber29.com.br/", tipo_auditoria="web_tls"),
                current_user=user_aut,
            )
            self.assertEqual(ok_scan["auditoria"]["score"], 92)

            # Mesmo se o gestor preencher escopo opcional com https://www.dominio.com.br/login, dominio.com.br casa!
            app_module.atualizar_governanca_cyber_api(
                app_module.CyberGovernancaSchema(
                    permitir_auditoria_rede=True,
                    usuarios_autorizados=["autorizado@cyber29.com"],
                    restringir_usuarios=True,
                    escopo_autorizado=["https://www.portal-oficial.com.br/painel"],
                ),
                current_user=admin,
            )
            user_aut = app_module.get_current_user(f"Bearer {tok_aut}")
            ok_url_scope = app_module.executar_auditoria_cyber_api(
                app_module.CyberExecutarAuditoriaSchema(alvo="portal-oficial.com.br", tipo_auditoria="web_tls"),
                current_user=user_aut,
            )
            self.assertEqual(ok_url_scope["auditoria"]["score"], 92)

            # Erro didático caso digite domínio no modo rede_privada
            with self.assertRaises(HTTPException) as ctx_modo:
                app_module.executar_auditoria_cyber_api(
                    app_module.CyberExecutarAuditoriaSchema(alvo="portal-oficial.com.br", tipo_auditoria="rede_privada"),
                    current_user=user_aut,
                )
            self.assertEqual(ctx_modo.exception.status_code, 400)
            self.assertIn("Web / TLS", ctx_modo.exception.detail)
        finally:
            automation_cyber.NetworkAuditEngine.auditar_web_tls = orig_web

        # 2. Wiki-Core separa Notícias do Mundo Cyber dos Playbooks Internos
        app_module.criar_conhecimento_cyber_api(
            app_module.CyberConhecimentoSchema(
                titulo="Playbook Exclusivo Cyber29",
                categoria="SOC Interno",
                resumo="Uso restrito da equipe",
                conteudo="Passo 1: Isolar switch.",
                tags="soc,interno",
            ),
            current_user=user_aut,
        )
        orig_news = automation_cyber.CyberNewsFeedService.obter_noticias
        try:
            automation_cyber.CyberNewsFeedService.obter_noticias = classmethod(
                lambda cls, forcar=False: {
                    "ao_vivo": True,
                    "atualizado_em": "2026-11-25T10:00:00Z",
                    "fontes_ativas": ["CISA KEV", "CERT.br"],
                    "itens": [
                        {
                            "titulo": "CVE-2026-9999 explorada ativamente",
                            "fonte": "CISA KEV",
                            "categoria": "CVE",
                            "data": "2026-11-25",
                            "resumo": "Atualizar firewall imediatamente.",
                            "url": "https://www.cisa.gov/",
                            "destaque": "Crítica",
                        }
                    ],
                }
            )
            wiki = app_module.obter_wiki_separado_cyber_api(current_user=user_aut)
            self.assertEqual(len(wiki["noticias_mundo_cyber"]["itens"]), 1)
            self.assertEqual(len(wiki["publicados_internos"]), 1)
            self.assertEqual(wiki["publicados_internos"][0]["titulo"], "Playbook Exclusivo Cyber29")
            self.assertGreaterEqual(len(wiki["guias_referencia"]), 6)
        finally:
            automation_cyber.CyberNewsFeedService.obter_noticias = orig_news

        # 3. Laboratório Didático Ataque × Mitigação + Automações Defensivas
        cenarios = app_module.listar_laboratorio_cyber_api(current_user=user_aut)
        self.assertGreaterEqual(len(cenarios), 6)
        sim = app_module.simular_laboratorio_cyber_api(
            app_module.CyberSimulacaoDidaticaSchema(cenario_id="bruteforce_credenciais", registrar_plano=True),
            current_user=user_aut,
        )
        self.assertGreaterEqual(sim["achados_gerados"], 1)

        aut_task = app_module.criar_automacao_defesa_api(
            app_module.CyberAutomacaoDefesaSchema(
                nome="Caça Diária de Contas sem 2FA",
                tipo="auditoria_contas_2fa_senhas",
                frequencia="Diária",
            ),
            current_user=user_aut,
        )["item"]
        exec_task = app_module.executar_automacao_defesa_api(aut_task["id"], current_user=user_aut)
        self.assertEqual(exec_task["automacao"]["ultimo_status"], "Alerta")
        self.assertGreaterEqual(exec_task["achados_criados"], 1)

        script_ps1 = app_module.baixar_script_protecao_cyber_api(tipo="firewall_windows_ps1", current_user=user_aut)
        self.assertIn("Set-SmbServerConfiguration", script_ps1.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
