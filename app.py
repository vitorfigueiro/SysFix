from contextlib import asynccontextmanager
from datetime import date, datetime
import base64
import binascii
import logging
import os
from pathlib import Path
from typing import Any, List, Optional, Union

from auth import (
    AuthenticationError,
    AuthorizationError,
    AuthService,
    OnboardingRequiredError,
    PERMISSION_SECTORS,
    PERMISSIONS_CATALOG,
    ROLE_PRESETS,
    sanitizar_tenant,
    sanitizar_usuario,
    usuario_tem_permissao,
)
from config import get_cors_origins, get_public_client_config, get_storage_backend
from database import init_db
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from importer import (
    MAX_IMPORT_BYTES,
    exportar_csv,
    exportar_json,
    gerar_modelo_importacao,
    normalizar_registros_importacao,
    parse_import_file,
)
from mensagens import obter_mensagem_erro
from models import ColetaModel
from ops_modules import OpsModulesService
from automation_cyber import AutomationCyberService
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from reports import PDFReportGenerator
from templates_laudo import (
    CATEGORIAS_EQUIPAMENTO,
    CHECKLIST_PRESETS,
    CONCLUSOES_LAUDO,
    DEFAULT_LAUDO_TEMPLATES,
    LAUDO_VARIABLES_HELP,
    MAX_TEMPLATE_UPLOAD_BYTES,
    PRIORIDADES,
    TIPOS_FLUXO,
    parse_uploaded_laudo_template,
)
import uvicorn

logger = logging.getLogger("sysfix")
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="API de Gerenciamento de Coletas - SysFix SaaS",
    version="2.0.0",
    lifespan=lifespan,
)

_cors_origins = get_cors_origins()
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def add_static_revalidation_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path or ""
    if path.startswith("/static/") and (path.endswith(".js") or path.endswith(".css")):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


def _get_auth_service() -> AuthService:
    return AuthService()


def _get_ops_service() -> OpsModulesService:
    return OpsModulesService()


def _get_security_ops_service() -> AutomationCyberService:
    return AutomationCyberService()


def require_any_permission(*perm_keys: str):
    """Permite acesso se o usuário possuir pelo menos uma das permissões listadas."""

    def _dependency(
        current_user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        if any(usuario_tem_permissao(current_user, key) for key in perm_keys):
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não possui permissão para acessar ou executar este recurso.",
        )

    return _dependency


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise AuthenticationError("Cabeçalho Authorization não fornecido.")
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthenticationError("Formato inválido do token Bearer.")
    return parts[1].strip()


def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> dict[str, Any]:
    try:
        token = _extract_bearer_token(authorization)
        return _get_auth_service().authenticate_token(token)
    except OnboardingRequiredError as ore:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "codigo": "ONBOARDING_REQUIRED",
                "mensagem": str(ore),
                "email": ore.identity.get("email", ""),
                "nome": ore.identity.get("name", ""),
            },
        )
    except AuthenticationError as ae:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(ae),
        )
    except AuthorizationError as aze:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(aze),
        )


def require_admin(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito aos administradores da empresa.",
        )
    return current_user


def require_permission(perm_key: str):
    """Dependência FastAPI para validar permissões granulares por usuário."""

    def _dependency(
        current_user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        if not usuario_tem_permissao(current_user, perm_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Você não possui permissão para acessar ou executar este recurso.",
            )
        return current_user

    return _dependency


def _filtrar_registro_por_permissao(
    registro: dict, current_user: dict[str, Any]
) -> dict:
    dados = serializar_registro(dict(registro))
    if not usuario_tem_permissao(current_user, "ver_custos"):
        dados.pop("status_custo", None)
        dados.pop("valor_custo", None)
        dados.pop("valor", None)
        if isinstance(dados.get("orcamento"), dict):
            orc = dict(dados["orcamento"])
            orc.pop("valor", None)
            dados["orcamento"] = orc
    return dados


def _filtrar_dashboard_por_permissao(
    dashboard: dict, current_user: dict[str, Any]
) -> dict:
    if usuario_tem_permissao(current_user, "ver_custos"):
        dashboard["ocultar_custos"] = False
        return dashboard

    dashboard["ocultar_custos"] = True
    resumo = dashboard.get("resumo", {})
    for campo in (
        "custo_total_mes",
        "custo_total_geral",
        "qtd_com_custo",
        "qtd_sem_custo",
    ):
        resumo.pop(campo, None)

    for item in dashboard.get("evolucao_mensal", []):
        item.pop("custo", None)
    for item in dashboard.get("top_origens", []):
        item.pop("custo", None)
    return dashboard


def validar_e_formatar_data(v: Union[str, date, None]) -> str:
    """Converte de forma flexível datas enviadas como String (ISO, BR) ou Objeto date."""
    if not v:
        return ""
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")

    v = str(v).strip()
    if not v:
        return ""

    try:
        dt = datetime.strptime(v, "%d/%m/%Y")
        return dt.strftime("%d/%m/%Y")
    except ValueError:
        pass

    try:
        dt = datetime.strptime(v, "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except ValueError:
        pass

    return v


def serializar_registro(registro: dict) -> dict:
    """Garante que objetos date/datetime ou None sejam convertidos para strings válidas para o JSON."""
    if not registro:
        return {}

    resultado = {}
    for key, val in registro.items():
        if isinstance(val, (date, datetime)):
            resultado[key] = val.strftime("%d/%m/%Y")
        else:
            resultado[key] = "" if val is None else val
    return resultado


class RegisterSchema(BaseModel):
    nome_empresa: str
    nome_usuario: str
    email: str
    senha: str


class LoginSchema(BaseModel):
    email: str
    senha: str


class OnboardingSchema(BaseModel):
    nome_empresa: str
    nome_usuario: Optional[str] = ""


class TeamMemberCreateSchema(BaseModel):
    nome: str
    email: str
    senha: str
    role: Optional[str] = "tecnico"
    permissoes: Optional[dict[str, bool]] = None


class TeamMemberUpdateSchema(BaseModel):
    nome: Optional[str] = None
    role: Optional[str] = None
    ativo: Optional[bool] = None
    permissoes: Optional[dict[str, bool]] = None


class TenantSettingsSchema(BaseModel):
    nome: Optional[str] = None
    sla_dias: Optional[int] = None
    localizacoes: Optional[List[str]] = None
    setores_ativos: Optional[dict[str, bool]] = None


class ImportarDadosSchema(BaseModel):
    filename: str
    conteudo_base64: str
    modo: str = "anexar"
    simular: bool = False


class BrandingTemplatesSchema(BaseModel):
    cnpj: Optional[str] = None
    telefone: Optional[str] = None
    endereco: Optional[str] = None
    rodape_documento: Optional[str] = None
    cor_documento: Optional[str] = None
    logo_base64: Optional[str] = None
    checklist_padrao: Optional[List[str]] = None
    modelos_laudo: Optional[List[dict]] = None


class ImportarModeloLaudoSchema(BaseModel):
    filename: str
    conteudo_base64: str


class ChecklistSchema(BaseModel):
    checklist: List[dict]


class LaudoSchema(BaseModel):
    titulo: str
    conclusao: Optional[str] = "Reparado / Aprovado"
    diagnostico: Optional[str] = ""
    parecer: str
    recomendacoes: Optional[str] = ""
    tecnico_responsavel: Optional[str] = ""
    registro_profissional: Optional[str] = ""
    incluir_checklist: bool = True
    visivel_cliente: bool = True
    modelo_nome: Optional[str] = ""


class OrcamentoSchema(BaseModel):
    descricao: str
    valor: Union[float, int, str]


class OrcamentoRespostaPublicaSchema(BaseModel):
    decisao: str
    cliente_nome: str
    observacao: Optional[str] = ""


class AssinaturaSchema(BaseModel):
    nome: str
    documento: Optional[str] = ""
    imagem_base64: str


class EstoqueItemSchema(BaseModel):
    nome: Optional[str] = None
    codigo: Optional[str] = None
    categoria: Optional[str] = None
    almoxarifado: Optional[str] = None
    unidade: Optional[str] = None
    quantidade: Optional[int] = 0
    estoque_minimo: Optional[int] = 0
    custo_unitario: Optional[Union[float, int, str]] = 0.0
    localizacao: Optional[str] = None


class EstoqueMovimentoSchema(BaseModel):
    tipo: str
    quantidade: int
    os_id: Optional[int] = None
    observacao: Optional[str] = ""


class PreventivaSchema(BaseModel):
    titulo: Optional[str] = None
    local_pop: Optional[str] = None
    periodicidade_dias: Optional[int] = 30
    proxima_execucao: Optional[Union[str, date]] = None
    ultima_execucao: Optional[Union[str, date]] = None
    responsavel: Optional[str] = None
    checklist_resumo: Optional[str] = None


class PreventivaConcluirSchema(BaseModel):
    observacao: Optional[str] = ""
    data_execucao: Optional[Union[str, date]] = None


class ClientePopSchema(BaseModel):
    nome: Optional[str] = None
    tipo: Optional[str] = None
    contato: Optional[str] = None
    telefone: Optional[str] = None
    endereco: Optional[str] = None
    observacoes: Optional[str] = None


class AutomacaoAtivoSchema(BaseModel):
    pilar: Optional[str] = None
    nome: Optional[str] = None
    subtipo: Optional[str] = None
    localizacao: Optional[str] = None
    ip_mac: Optional[str] = None
    vlan: Optional[str] = None
    fabricante_modelo: Optional[str] = None
    firmware: Optional[str] = None
    senha_padrao_alterada: Optional[bool] = None
    firmware_atualizado: Optional[bool] = None
    rede_segmentada: Optional[bool] = None
    energia_backup_ok: Optional[bool] = None
    monitoramento_ativo: Optional[bool] = None
    status_auditoria: Optional[str] = None
    observacoes: Optional[str] = None
    ultima_auditoria: Optional[Union[str, date]] = None


class CyberGovernancaSchema(BaseModel):
    permitir_auditoria_rede: bool = False
    escopo_autorizado: List[str] = []
    observacao_termo: Optional[str] = ""


class CyberExecutarAuditoriaSchema(BaseModel):
    alvo: str
    tipo_auditoria: str = "web_tls"
    registrar_achados: bool = True


class CyberImportarAuditoriaSchema(BaseModel):
    payload: dict


class CyberAchadoSchema(BaseModel):
    titulo: Optional[str] = None
    severidade: Optional[str] = "Média"
    categoria: Optional[str] = "Geral"
    alvo: Optional[str] = ""
    status: Optional[str] = "Aberto"
    recomendacao: Optional[str] = ""


class CyberChecklistSaveSchema(BaseModel):
    itens: List[dict]


class CyberConhecimentoSchema(BaseModel):
    titulo: str
    categoria: Optional[str] = "Playbook Interno"
    nivel: Optional[str] = "Operacional"
    resumo: Optional[str] = ""
    conteudo: str
    tags: Optional[Union[str, List[str]]] = ""


class EntradaSchema(BaseModel):
    equipamento: Optional[str] = ""
    tombamento: Optional[str] = ""
    tecnico: Optional[str] = ""
    tecnico_coleta: Optional[str] = ""
    data_coleta: Optional[Union[str, date]] = ""
    origem: Optional[str] = ""
    os_coleta: Optional[str] = ""
    localizacao: Optional[str] = ""
    problema: Optional[str] = ""
    categoria: Optional[str] = ""
    serial_mac: Optional[str] = ""
    tipo_fluxo: Optional[str] = ""
    prioridade: Optional[str] = ""
    garantia_dias: Optional[int] = 0
    checklist: Optional[List[Any]] = None

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def compatibilizar_campos_entrada(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "tecnico_coleta" in data and not data.get("tecnico"):
                data["tecnico"] = data["tecnico_coleta"]
        return data

    @field_validator("data_coleta", mode="before")
    @classmethod
    def normalizar_data(cls, v):
        return validar_e_formatar_data(v)


class SaidaSchema(BaseModel):
    tecnico_entrega: Optional[str] = ""
    data_entrega: Optional[Union[str, date]] = ""
    os_entrega: Optional[str] = ""
    status_custo: Optional[str] = "Sem Custo"
    valor_custo: Optional[Union[float, int, str]] = 0.0
    valor: Optional[Union[float, int, str]] = None
    resolucao: Optional[str] = ""
    laudado: Optional[str] = "Não"
    garantia_dias: Optional[int] = None

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def compatibilizar_campos_saida(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "valor" in data and (
                "valor_custo" not in data or data["valor_custo"] in (None, "")
            ):
                data["valor_custo"] = data["valor"]
        return data

    @field_validator("data_entrega", mode="before")
    @classmethod
    def normalizar_data(cls, v):
        return validar_e_formatar_data(v)


# ==============================================================================
# ROTAS PÚBLICAS (FRONTEND, CONFIGURAÇÃO E AUTENTICAÇÃO)
# ==============================================================================


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
}


@app.get("/", response_class=FileResponse)
def read_index():
    index_path = BASE_DIR / "templates" / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), headers=_NO_CACHE_HEADERS)
    raise HTTPException(
        status_code=404, detail="Arquivo index.html não encontrado no servidor."
    )


@app.get("/acompanhar/{codigo}", response_class=FileResponse)
def read_acompanhar_page(codigo: str):
    page_path = BASE_DIR / "templates" / "acompanhar.html"
    if page_path.exists():
        return FileResponse(str(page_path), headers=_NO_CACHE_HEADERS)
    raise HTTPException(
        status_code=404, detail="Página de acompanhamento não encontrada."
    )


@app.get("/favicon.ico", include_in_schema=False)
def read_favicon():
    ico_path = STATIC_DIR / "favicon.ico"
    if ico_path.exists():
        return FileResponse(str(ico_path), media_type="image/x-icon")
    svg_path = STATIC_DIR / "favicon.svg"
    if svg_path.exists():
        return FileResponse(str(svg_path), media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon não encontrado.")


@app.get("/api/config")
def obter_configuracao_cliente():
    return get_public_client_config()


@app.get("/api/health")
@app.get("/debug-db")
def health_check():
    try:
        backend = get_storage_backend()
        return {
            "status": "online",
            "armazenamento": "Cloud Firestore" if backend == "firebase" else "JSON local",
            "backend": backend,
            "versao": "2.0.0",
        }
    except Exception:
        logger.exception("Erro no health check do sistema")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/auth/register")
def registrar_empresa(payload: RegisterSchema):
    try:
        return _get_auth_service().register_local(
            nome_empresa=payload.nome_empresa,
            nome_usuario=payload.nome_usuario,
            email=payload.email,
            senha=payload.senha,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao registrar empresa")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/auth/login")
def login_usuario(payload: LoginSchema):
    try:
        return _get_auth_service().login_local(
            email=payload.email, senha=payload.senha
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except AuthenticationError as ae:
        raise HTTPException(status_code=401, detail=str(ae))
    except AuthorizationError as aze:
        raise HTTPException(status_code=403, detail=str(aze))
    except Exception:
        logger.exception("Erro ao autenticar usuário")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/auth/onboarding")
def onboarding_firebase(
    payload: OnboardingSchema,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    try:
        token = _extract_bearer_token(authorization)
        return _get_auth_service().onboard_firebase_user(
            token=token,
            nome_empresa=payload.nome_empresa,
            nome_usuario=payload.nome_usuario or "",
        )
    except AuthenticationError as ae:
        raise HTTPException(status_code=401, detail=str(ae))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro no onboarding Firebase")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.get("/api/auth/me")
def obter_sessao_atual(current_user: dict[str, Any] = Depends(get_current_user)):
    return {
        "user": sanitizar_usuario(current_user),
        "tenant": sanitizar_tenant(current_user["tenant"]),
        "permissions_catalog": PERMISSIONS_CATALOG,
        "permission_sectors": PERMISSION_SECTORS,
        "role_presets": ROLE_PRESETS,
    }


# ==============================================================================
# ROTAS DO DASHBOARD E OPERAÇÃO DE EQUIPAMENTOS (ISOLADAS POR TENANT)
# ==============================================================================


@app.get("/api/dashboard")
def obter_dashboard(
    mes: Optional[int] = Query(None, ge=1, le=12),
    ano: Optional[int] = Query(None, ge=2000, le=2100),
    current_user: dict[str, Any] = Depends(require_permission("ver_dashboard")),
):
    try:
        tenant = current_user["tenant"]
        dados = ColetaModel.obter_dashboard(
            tenant_id=current_user["tenant_id"],
            mes=mes,
            ano=ano,
            sla_dias=tenant.get("sla_dias", 3),
        )
        return _filtrar_dashboard_por_permissao(dados, current_user)
    except Exception:
        logger.exception("Erro ao calcular métricas do dashboard")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.get("/api/equipamentos")
def listar_equipamentos(
    filtro: Optional[str] = None,
    status: Optional[str] = None,
    busca: Optional[str] = None,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        tenant_id = current_user["tenant_id"]
        valor_filtro = (status or filtro or "nao_finalizados").lower().strip()

        if valor_filtro in ["finalizados", "entregues", "entregue", "finalizado"]:
            dados = ColetaModel.buscar_finalizados_mes_atual(tenant_id=tenant_id)
        elif valor_filtro == "todos":
            dados = ColetaModel.buscar_todos(tenant_id=tenant_id)
        else:
            dados = ColetaModel.buscar_nao_finalizados_mes_atual(tenant_id=tenant_id)

        termo = (busca or "").strip().lower()
        if termo:
            dados = [
                item
                for item in dados
                if any(
                    termo in str(item.get(campo) or "").lower()
                    for campo in (
                        "id",
                        "equipamento",
                        "tombamento",
                        "serial_mac",
                        "categoria",
                        "tipo_fluxo",
                        "prioridade",
                        "os_coleta",
                        "os_entrega",
                        "origem",
                        "localizacao",
                        "tecnico_coleta",
                        "tecnico_entrega",
                        "codigo_acompanhamento",
                    )
                )
            ]

        return [
            _filtrar_registro_por_permissao(item, current_user) for item in dados
        ]
    except Exception:
        logger.exception("Erro ao listar equipamentos")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.get("/api/equipamentos/{registro_id}")
def obter_equipamento(
    registro_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        dados = ColetaModel.buscar_por_id(
            registro_id, tenant_id=current_user["tenant_id"]
        )
        if not dados:
            raise HTTPException(
                status_code=404, detail=obter_mensagem_erro("NOT_FOUND")
            )
        return _filtrar_registro_por_permissao(dados, current_user)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Erro ao buscar equipamento %s", registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/equipamentos")
def criar_entrada(
    payload: Union[EntradaSchema, List[EntradaSchema]],
    current_user: dict[str, Any] = Depends(require_permission("registrar_entrada")),
):
    """Endpoint flexível: aceita um único objeto de entrada ou uma lista em lote."""
    tenant_id = current_user["tenant_id"]
    actor = current_user.get("nome") or current_user.get("email") or ""
    allowed_locations = current_user["tenant"].get("localizacoes")

    try:
        if isinstance(payload, list):
            if not payload:
                raise HTTPException(
                    status_code=400,
                    detail="A lista de equipamentos enviados está vazia.",
                )

            ids_criados = ColetaModel.registrar_entrada_em_lote(
                [item.model_dump() for item in payload],
                tenant_id=tenant_id,
                actor=actor,
                allowed_locations=allowed_locations,
                default_checklist=current_user["tenant"].get("checklist_padrao"),
            )

            return {
                "sucesso": True,
                "ids": ids_criados,
                "mensagem": f"{len(ids_criados)} entrada(s) registrada(s) com sucesso.",
            }

        checklist_inicial = (
            payload.checklist
            if payload.checklist is not None
            else current_user["tenant"].get("checklist_padrao")
        )
        novo_id = ColetaModel.registrar_entrada(
            equipamento=payload.equipamento,
            tombamento=payload.tombamento,
            tecnico=payload.tecnico or payload.tecnico_coleta,
            data_coleta=payload.data_coleta,
            origem=payload.origem,
            os_coleta=payload.os_coleta,
            localizacao=payload.localizacao,
            problema=payload.problema,
            tenant_id=tenant_id,
            actor=actor,
            allowed_locations=allowed_locations,
            categoria=payload.categoria or "",
            serial_mac=payload.serial_mac or "",
            tipo_fluxo=payload.tipo_fluxo or "",
            prioridade=payload.prioridade or "",
            garantia_dias=payload.garantia_dias or 0,
            checklist=checklist_inicial,
        )
        return {
            "sucesso": True,
            "id": novo_id,
            "mensagem": "Entrada registrada com sucesso.",
        }

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Erro ao registrar entrada")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


def _resolver_custo_saida(
    registro_id: int,
    status_custo: Optional[str],
    valor_informado: Any,
    current_user: dict[str, Any],
) -> tuple[str, Any]:
    if usuario_tem_permissao(current_user, "ver_custos"):
        return status_custo or "Sem Custo", valor_informado

    existente = ColetaModel.buscar_por_id(
        registro_id, tenant_id=current_user["tenant_id"]
    )
    if not existente:
        raise HTTPException(
            status_code=404, detail=obter_mensagem_erro("NOT_FOUND")
        )
    return (
        existente.get("status_custo") or "Sem Custo",
        existente.get("valor_custo") or 0.0,
    )


@app.put("/api/equipamentos/{registro_id}/saida")
def registrar_saida(
    registro_id: int,
    payload: SaidaSchema,
    current_user: dict[str, Any] = Depends(require_permission("registrar_saida")),
):
    try:
        valor_informado = (
            payload.valor_custo
            if payload.valor_custo is not None
            else payload.valor
        )
        status_custo_final, valor_final = _resolver_custo_saida(
            registro_id, payload.status_custo, valor_informado, current_user
        )
        atualizado = ColetaModel.registrar_saida(
            registro_id=registro_id,
            tecnico_entrega=payload.tecnico_entrega,
            data_entrega=payload.data_entrega,
            os_entrega=payload.os_entrega,
            status_custo=status_custo_final,
            valor_custo=valor_final,
            resolucao=payload.resolucao,
            laudado=payload.laudado,
            tenant_id=current_user["tenant_id"],
            actor=current_user.get("nome") or current_user.get("email") or "",
            garantia_dias=payload.garantia_dias,
        )
        if not atualizado:
            raise HTTPException(
                status_code=404, detail=obter_mensagem_erro("NOT_FOUND")
            )
        return {
            "sucesso": True,
            "mensagem": "Saída/Entrega registrada com sucesso.",
        }
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao registrar saída do registro %s", registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.put("/api/equipamentos/{registro_id}")
def atualizar_equipamento_unificado(
    registro_id: int,
    payload: dict,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Endpoint unificado PUT para atualização de Entrada ou registro de Saída."""
    tenant_id = current_user["tenant_id"]
    actor = current_user.get("nome") or current_user.get("email") or ""
    allowed_locations = current_user["tenant"].get("localizacoes")

    try:
        if "tecnico_entrega" in payload or payload.get("status") in [
            "Entregue",
            "Finalizado",
        ]:
            if not usuario_tem_permissao(current_user, "registrar_saida"):
                raise HTTPException(
                    status_code=403,
                    detail="Você não possui permissão para dar saída em equipamentos.",
                )
            saida_data = SaidaSchema(**payload)
            valor_informado = (
                saida_data.valor_custo
                if saida_data.valor_custo is not None
                else saida_data.valor
            )
            status_custo_final, valor_final = _resolver_custo_saida(
                registro_id,
                saida_data.status_custo,
                valor_informado,
                current_user,
            )
            atualizado = ColetaModel.registrar_saida(
                registro_id=registro_id,
                tecnico_entrega=saida_data.tecnico_entrega,
                data_entrega=saida_data.data_entrega,
                os_entrega=saida_data.os_entrega,
                status_custo=status_custo_final,
                valor_custo=valor_final,
                resolucao=saida_data.resolucao,
                laudado=saida_data.laudado,
                tenant_id=tenant_id,
                actor=actor,
                garantia_dias=saida_data.garantia_dias,
            )
            if not atualizado:
                raise HTTPException(
                    status_code=404, detail=obter_mensagem_erro("NOT_FOUND")
                )
            return {
                "sucesso": True,
                "mensagem": "Saída registrada com sucesso.",
            }

        if not usuario_tem_permissao(current_user, "editar_entrada"):
            raise HTTPException(
                status_code=403,
                detail="Você não possui permissão para editar dados de entrada.",
            )
        entrada_data = EntradaSchema(**payload)
        atualizado = ColetaModel.atualizar_entrada(
            registro_id=registro_id,
            equipamento=entrada_data.equipamento,
            tombamento=entrada_data.tombamento,
            tecnico=entrada_data.tecnico or entrada_data.tecnico_coleta,
            data_coleta=entrada_data.data_coleta,
            origem=entrada_data.origem,
            os_coleta=entrada_data.os_coleta,
            localizacao=entrada_data.localizacao,
            problema=entrada_data.problema,
            tenant_id=tenant_id,
            actor=actor,
            allowed_locations=allowed_locations,
            categoria=entrada_data.categoria if "categoria" in payload else None,
            serial_mac=entrada_data.serial_mac if "serial_mac" in payload else None,
            tipo_fluxo=entrada_data.tipo_fluxo if "tipo_fluxo" in payload else None,
            prioridade=entrada_data.prioridade if "prioridade" in payload else None,
            garantia_dias=entrada_data.garantia_dias if "garantia_dias" in payload else None,
        )
        if not atualizado:
            raise HTTPException(
                status_code=404, detail=obter_mensagem_erro("NOT_FOUND")
            )
        return {
            "sucesso": True,
            "mensagem": "Registro atualizado com sucesso.",
        }

    except HTTPException:
        raise
    except ValidationError as ve:
        detalhes = "; ".join(err["msg"] for err in ve.errors())
        raise HTTPException(status_code=400, detail=detalhes)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao atualizar registro %s", registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.delete("/api/equipamentos/{registro_id}")
def deletar_equipamento(
    registro_id: int,
    current_user: dict[str, Any] = Depends(require_permission("excluir_registros")),
):
    """Exclui um registro da empresa atual (permitido apenas para administradores)."""
    try:
        excluido = ColetaModel.excluir(
            registro_id, is_admin=True, tenant_id=current_user["tenant_id"]
        )
        if not excluido:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=obter_mensagem_erro("NOT_FOUND"),
            )
        return {"sucesso": True, "mensagem": "Registro excluído com sucesso."}
    except HTTPException:
        raise
    except PermissionError as pe:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(pe)
        )
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve)
        )
    except Exception:
        logger.exception("Erro ao excluir registro %s", registro_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=obter_mensagem_erro("DB_ERROR"),
        )


# ==============================================================================
# GESTÃO DE EQUIPE E CONFIGURAÇÕES DA EMPRESA (SOMENTE ADMIN)
# ==============================================================================


@app.get("/api/empresa/usuarios")
def listar_usuarios_empresa(
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_equipe")),
):
    try:
        return _get_auth_service().list_team_members(current_user)
    except AuthorizationError as aze:
        raise HTTPException(status_code=403, detail=str(aze))
    except Exception:
        logger.exception("Erro ao listar usuários da empresa")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/empresa/usuarios")
def criar_usuario_empresa(
    payload: TeamMemberCreateSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_equipe")),
):
    try:
        membro = _get_auth_service().create_team_member(
            admin_ctx=current_user,
            nome=payload.nome,
            email=payload.email,
            senha=payload.senha,
            role=payload.role or "tecnico",
            permissoes=payload.permissoes,
        )
        return {"sucesso": True, "usuario": membro}
    except AuthorizationError as aze:
        raise HTTPException(status_code=403, detail=str(aze))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao criar usuário na empresa")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.patch("/api/empresa/usuarios/{uid}")
def atualizar_usuario_empresa(
    uid: str,
    payload: TeamMemberUpdateSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_equipe")),
):
    try:
        membro = _get_auth_service().update_team_member(
            admin_ctx=current_user,
            target_uid=uid,
            nome=payload.nome,
            role=payload.role,
            ativo=payload.ativo,
            permissoes=payload.permissoes,
        )
        return {"sucesso": True, "usuario": membro}
    except AuthorizationError as aze:
        raise HTTPException(status_code=403, detail=str(aze))
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao atualizar usuário %s", uid)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.put("/api/empresa/configuracoes")
def atualizar_configuracoes_empresa(
    payload: TenantSettingsSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_equipe")),
):
    try:
        tenant = _get_auth_service().update_tenant_settings(
            admin_ctx=current_user,
            nome=payload.nome,
            sla_dias=payload.sla_dias,
            localizacoes=payload.localizacoes,
            setores_ativos=payload.setores_ativos,
        )
        return {"sucesso": True, "tenant": tenant}
    except AuthorizationError as aze:
        raise HTTPException(status_code=403, detail=str(aze))
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao atualizar configurações da empresa")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/empresa/importar")
def importar_dados_empresa(
    payload: ImportarDadosSchema,
    current_user: dict[str, Any] = Depends(require_permission("importar_exportar_dados")),
):
    """Importa registros em JSON, CSV ou Excel (.xlsx) para a empresa do administrador."""
    try:
        raw_b64 = (payload.conteudo_base64 or "").strip()
        if "," in raw_b64 and raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            raw_bytes = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Conteúdo do arquivo em base64 inválido.") from exc

        if len(raw_bytes) > MAX_IMPORT_BYTES:
            raise ValueError("O arquivo excede o limite máximo permitido de 5 MB.")

        formato, raw_rows = parse_import_file(payload.filename, raw_bytes)
        tenant = current_user["tenant"]
        locs = tenant.get("localizacoes") or ["Bancada TI"]
        resultado = normalizar_registros_importacao(
            raw_rows=raw_rows,
            formato=formato,
            tenant_id=current_user["tenant_id"],
            actor=current_user.get("nome") or current_user.get("email") or "",
            default_location=locs[0],
        )

        if payload.simular:
            return {
                "sucesso": True,
                "simulado": True,
                "formato": resultado["formato"],
                "total_lidos": resultado["total_lidos"],
                "total_validos": len(resultado["registros"]),
                "erros": resultado["erros"][:30],
                "amostra": resultado["registros"][:5],
            }

        if not resultado["registros"]:
            detalhe = (
                resultado["erros"][0]
                if resultado["erros"]
                else "Nenhum registro válido encontrado no arquivo."
            )
            raise ValueError(detalhe)

        importado = ColetaModel.importar_registros(
            registros=resultado["registros"],
            tenant_id=current_user["tenant_id"],
            modo=payload.modo,
        )
        return {
            "sucesso": True,
            "simulado": False,
            "formato": resultado["formato"],
            "modo": importado["modo"],
            "importados": importado["importados"],
            "total_lidos": resultado["total_lidos"],
            "ignorados_com_erro": len(resultado["erros"]),
            "erros": resultado["erros"][:20],
            "mensagem": f"{importado['importados']} registro(s) importado(s) com sucesso ({resultado['formato'].upper()}).",
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re_err:
        raise HTTPException(status_code=409, detail=str(re_err))
    except Exception:
        logger.exception("Erro ao importar arquivo de dados")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.get("/api/empresa/exportar")
def exportar_dados_empresa(
    formato: str = Query("json"),
    current_user: dict[str, Any] = Depends(require_permission("importar_exportar_dados")),
):
    """Exporta todos os registros da empresa em JSON ou CSV (Excel PT-BR)."""
    try:
        registros = ColetaModel.buscar_todos(tenant_id=current_user["tenant_id"])
        fmt = formato.strip().lower()
        if fmt == "json":
            conteudo = exportar_json(
                registros, nome_empresa=current_user["tenant"].get("nome") or "SysFix"
            )
            media_type = "application/json; charset=utf-8"
            filename = "backup_sysfix.json"
        elif fmt == "csv":
            conteudo = exportar_csv(registros)
            media_type = "text/csv; charset=utf-8"
            filename = "backup_sysfix.csv"
        else:
            raise HTTPException(
                status_code=400,
                detail="Formato de exportação inválido. Use 'json' ou 'csv'.",
            )

        return Response(
            content=conteudo,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Erro ao exportar backup da empresa")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.get("/api/empresa/modelo-importacao")
def baixar_modelo_importacao(
    formato: str = Query("csv"),
    _current_user: dict[str, Any] = Depends(require_permission("importar_exportar_dados")),
):
    try:
        conteudo, media_type, filename = gerar_modelo_importacao(formato)
        return Response(
            content=conteudo,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


# ==============================================================================
# RELATÓRIOS PDF (ISOLADOS POR TENANT)
# ==============================================================================


@app.get("/api/relatorio/pdf")
def gerar_relatorio_pdf(
    tipo: str = Query("mensal"),
    mes: Optional[int] = Query(None, ge=1, le=12),
    ano: Optional[int] = Query(None, ge=2000, le=2100),
    anonimizar: bool = Query(False),
    current_user: dict[str, Any] = Depends(require_permission("exportar_pdf")),
):
    try:
        tenant_id = current_user["tenant_id"]
        nome_empresa = current_user["tenant"].get("nome") or "SysFix"
        ocultar_custos = not usuario_tem_permissao(current_user, "ver_custos")
        tipo_normalizado = tipo.strip().lower()

        if tipo_normalizado == "mensal":
            if mes is None or ano is None:
                raise HTTPException(
                    status_code=400,
                    detail="Informe os parâmetros 'mes' e 'ano' para o relatório mensal.",
                )
            pdf_buffer = PDFReportGenerator.relatorio_por_mes(
                mes=mes,
                ano=ano,
                anonimizar=anonimizar,
                tenant_id=tenant_id,
                nome_empresa=nome_empresa,
                ocultar_custos=ocultar_custos,
            )
            filename = f"Relatorio_SysFix_{mes:02d}_{ano}.pdf"
        elif tipo_normalizado == "pendentes":
            pdf_buffer = PDFReportGenerator.relatorio_pendentes(
                anonimizar=anonimizar,
                tenant_id=tenant_id,
                nome_empresa=nome_empresa,
                ocultar_custos=ocultar_custos,
            )
            filename = "Relatorio_SysFix_Pendentes.pdf"
        elif tipo_normalizado == "geral":
            pdf_buffer = PDFReportGenerator.relatorio_geral(
                anonimizar=anonimizar,
                tenant_id=tenant_id,
                nome_empresa=nome_empresa,
                ocultar_custos=ocultar_custos,
            )
            filename = "Relatorio_SysFix_Geral.pdf"
        else:
            raise HTTPException(
                status_code=400,
                detail="Tipo de relatório inválido. Use 'mensal', 'pendentes' ou 'geral'.",
            )

        headers = {"Content-Disposition": f'inline; filename="{filename}"'}
        return Response(
            content=pdf_buffer.getvalue(),
            media_type="application/pdf",
            headers=headers,
        )
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao gerar relatório PDF")
        raise HTTPException(
            status_code=500, detail="Erro ao gerar relatório PDF."
        )


# ==============================================================================
# ROTAS DE TI / PROVEDORES, CHECKLIST, LAUDOS, MODELOS, HISTÓRICO E COMPROVANTE
# ==============================================================================


@app.get("/api/empresa/presets-ti")
def obter_presets_ti(current_user: dict[str, Any] = Depends(get_current_user)):
    return {
        "categorias": CATEGORIAS_EQUIPAMENTO,
        "tipos_fluxo": TIPOS_FLUXO,
        "prioridades": PRIORIDADES,
        "conclusoes_laudo": CONCLUSOES_LAUDO,
        "checklist_presets": CHECKLIST_PRESETS,
        "modelos_laudo_padrao": DEFAULT_LAUDO_TEMPLATES,
        "variaveis_laudo": LAUDO_VARIABLES_HELP,
    }


@app.put("/api/empresa/modelos")
def atualizar_modelos_e_branding_empresa(
    payload: BrandingTemplatesSchema,
    current_user: dict[str, Any] = Depends(
        require_any_permission("gerenciar_modelos", "gerenciar_equipe")
    ),
):
    try:
        tenant = _get_auth_service().update_tenant_branding_and_templates(
            user_ctx=current_user,
            cnpj=payload.cnpj,
            telefone=payload.telefone,
            endereco=payload.endereco,
            rodape_documento=payload.rodape_documento,
            cor_documento=payload.cor_documento,
            logo_base64=payload.logo_base64,
            checklist_padrao=payload.checklist_padrao,
            modelos_laudo=payload.modelos_laudo,
        )
        return {"sucesso": True, "tenant": tenant}
    except AuthorizationError as aze:
        raise HTTPException(status_code=403, detail=str(aze))
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao atualizar modelos e timbre da empresa")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/laudos/importar-modelo")
def importar_arquivo_modelo_laudo(
    payload: ImportarModeloLaudoSchema,
    _current_user: dict[str, Any] = Depends(
        require_any_permission("gerenciar_modelos", "gerenciar_laudos", "gerenciar_equipe")
    ),
):
    try:
        raw_b64 = (payload.conteudo_base64 or "").strip()
        if "," in raw_b64 and raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            raw_bytes = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Conteúdo do modelo em base64 inválido.") from exc
        if len(raw_bytes) > MAX_TEMPLATE_UPLOAD_BYTES:
            raise ValueError("O arquivo de modelo excede 512 KB.")
        modelo = parse_uploaded_laudo_template(payload.filename, raw_bytes)
        return {"sucesso": True, "modelo": modelo}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao importar arquivo de modelo de laudo")
        raise HTTPException(status_code=500, detail="Erro ao processar modelo enviado.")


@app.get("/api/equipamentos/{registro_id}/historico")
def obter_historico_equipamento(
    registro_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        hist = ColetaModel.obter_historico_ativo(
            registro_id, tenant_id=current_user["tenant_id"]
        )
        if not hist:
            raise HTTPException(status_code=404, detail=obter_mensagem_erro("NOT_FOUND"))
        pode_custos = usuario_tem_permissao(current_user, "ver_custos")
        resp = {
            "total_passagens": hist["total_passagens"],
            "reincidente": hist["reincidente"],
            "registros": [
                _filtrar_registro_por_permissao(r, current_user) for r in hist["registros"]
            ],
            "timeline_atual": hist["timeline_atual"],
        }
        if pode_custos:
            resp["custo_acumulado"] = hist["custo_acumulado"]
        return resp
    except HTTPException:
        raise
    except Exception:
        logger.exception("Erro ao buscar histórico do equipamento %s", registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.put("/api/equipamentos/{registro_id}/checklist")
def atualizar_checklist_equipamento(
    registro_id: int,
    payload: ChecklistSchema,
    current_user: dict[str, Any] = Depends(require_permission("editar_entrada")),
):
    try:
        atualizado = ColetaModel.atualizar_checklist(
            registro_id=registro_id,
            checklist=payload.checklist,
            tenant_id=current_user["tenant_id"],
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        if not atualizado:
            raise HTTPException(status_code=404, detail=obter_mensagem_erro("NOT_FOUND"))
        return {
            "sucesso": True,
            "registro": _filtrar_registro_por_permissao(atualizado, current_user),
        }
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao atualizar checklist do registro %s", registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/equipamentos/{registro_id}/link-publico")
def gerar_link_publico_os(
    registro_id: int,
    request: Request,
    current_user: dict[str, Any] = Depends(require_permission("compartilhar_os")),
):
    try:
        codigo = ColetaModel.garantir_codigo_acompanhamento(
            registro_id, tenant_id=current_user["tenant_id"]
        )
        if not codigo:
            raise HTTPException(status_code=404, detail=obter_mensagem_erro("NOT_FOUND"))
        base = str(request.base_url).rstrip("/")
        return {
            "sucesso": True,
            "codigo": codigo,
            "url": f"{base}/acompanhar/{codigo}",
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Erro ao gerar link público da O.S. %s", registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.get("/api/equipamentos/{registro_id}/comprovante/pdf")
def baixar_comprovante_os_pdf(
    registro_id: int,
    current_user: dict[str, Any] = Depends(require_permission("compartilhar_os")),
):
    try:
        reg = ColetaModel.buscar_por_id(
            registro_id, tenant_id=current_user["tenant_id"]
        )
        if not reg:
            raise HTTPException(status_code=404, detail=obter_mensagem_erro("NOT_FOUND"))
        ocultar_custos = not usuario_tem_permissao(current_user, "ver_custos")
        pdf_buf = PDFReportGenerator.comprovante_os(
            reg, tenant=current_user["tenant"], ocultar_custos=ocultar_custos
        )
        return Response(
            content=pdf_buf.getvalue(),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="Comprovante_OS_{registro_id}.pdf"'
            },
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Erro ao gerar comprovante PDF da O.S. %s", registro_id)
        raise HTTPException(status_code=500, detail="Erro ao gerar comprovante PDF.")


@app.put("/api/equipamentos/{registro_id}/assinatura")
def registrar_assinatura_os(
    registro_id: int,
    payload: AssinaturaSchema,
    current_user: dict[str, Any] = Depends(require_permission("compartilhar_os")),
):
    try:
        atualizado = ColetaModel.salvar_assinatura(
            registro_id=registro_id,
            nome=payload.nome,
            documento=payload.documento or "",
            imagem_base64=payload.imagem_base64,
            tenant_id=current_user["tenant_id"],
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        if not atualizado:
            raise HTTPException(status_code=404, detail=obter_mensagem_erro("NOT_FOUND"))
        return {
            "sucesso": True,
            "mensagem": "Assinatura registrada na O.S.!",
            "registro": _filtrar_registro_por_permissao(atualizado, current_user),
        }
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao registrar assinatura da O.S. %s", registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.put("/api/equipamentos/{registro_id}/orcamento")
def enviar_orcamento_os(
    registro_id: int,
    payload: OrcamentoSchema,
    current_user: dict[str, Any] = Depends(require_permission("compartilhar_os")),
):
    if not usuario_tem_permissao(current_user, "ver_custos"):
        raise HTTPException(
            status_code=403,
            detail="É necessário ter permissão de custos para enviar orçamentos.",
        )
    try:
        atualizado = ColetaModel.salvar_orcamento(
            registro_id=registro_id,
            descricao=payload.descricao,
            valor=payload.valor,
            tenant_id=current_user["tenant_id"],
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        if not atualizado:
            raise HTTPException(status_code=404, detail=obter_mensagem_erro("NOT_FOUND"))
        return {
            "sucesso": True,
            "mensagem": "Orçamento registrado e pronto para aprovação pelo link público.",
            "registro": _filtrar_registro_por_permissao(atualizado, current_user),
        }
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao enviar orçamento da O.S. %s", registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/equipamentos/{registro_id}/laudos")
def criar_laudo_equipamento(
    registro_id: int,
    payload: LaudoSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_laudos")),
):
    try:
        res = ColetaModel.salvar_laudo(
            registro_id=registro_id,
            dados_laudo=payload.model_dump(),
            tenant_id=current_user["tenant_id"],
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        if not res:
            raise HTTPException(status_code=404, detail=obter_mensagem_erro("NOT_FOUND"))
        reg_atualizado, laudo = res
        return {
            "sucesso": True,
            "mensagem": f"Laudo {laudo['numero']} anexado ao equipamento!",
            "laudo": laudo,
            "registro": _filtrar_registro_por_permissao(reg_atualizado, current_user),
        }
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao criar laudo da O.S. %s", registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.put("/api/equipamentos/{registro_id}/laudos/{laudo_id}")
def atualizar_laudo_equipamento(
    registro_id: int,
    laudo_id: str,
    payload: LaudoSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_laudos")),
):
    try:
        res = ColetaModel.salvar_laudo(
            registro_id=registro_id,
            dados_laudo=payload.model_dump(),
            tenant_id=current_user["tenant_id"],
            actor=current_user.get("nome") or current_user.get("email") or "",
            laudo_id=laudo_id,
        )
        if not res:
            raise HTTPException(status_code=404, detail=obter_mensagem_erro("NOT_FOUND"))
        reg_atualizado, laudo = res
        return {
            "sucesso": True,
            "mensagem": f"Laudo {laudo['numero']} atualizado!",
            "laudo": laudo,
            "registro": _filtrar_registro_por_permissao(reg_atualizado, current_user),
        }
    except HTTPException:
        raise
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao atualizar laudo %s da O.S. %s", laudo_id, registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.delete("/api/equipamentos/{registro_id}/laudos/{laudo_id}")
def remover_laudo_equipamento(
    registro_id: int,
    laudo_id: str,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_laudos")),
):
    try:
        reg_atualizado = ColetaModel.excluir_laudo(
            registro_id=registro_id,
            laudo_id=laudo_id,
            tenant_id=current_user["tenant_id"],
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        if not reg_atualizado:
            raise HTTPException(status_code=404, detail=obter_mensagem_erro("NOT_FOUND"))
        return {
            "sucesso": True,
            "mensagem": "Laudo removido do equipamento.",
            "registro": _filtrar_registro_por_permissao(reg_atualizado, current_user),
        }
    except HTTPException:
        raise
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except Exception:
        logger.exception("Erro ao remover laudo %s da O.S. %s", laudo_id, registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.get("/api/equipamentos/{registro_id}/laudos/{laudo_id}/pdf")
def baixar_laudo_pdf(
    registro_id: int,
    laudo_id: str,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_laudos")),
):
    try:
        reg = ColetaModel.buscar_por_id(
            registro_id, tenant_id=current_user["tenant_id"]
        )
        if not reg:
            raise HTTPException(status_code=404, detail=obter_mensagem_erro("NOT_FOUND"))
        laudo = next(
            (item for item in (reg.get("laudos") or []) if item.get("id") == laudo_id),
            None,
        )
        if not laudo:
            raise HTTPException(status_code=404, detail="Laudo técnico não encontrado.")
        pdf_buf = PDFReportGenerator.laudo_tecnico_pdf(
            reg, laudo, tenant=current_user["tenant"]
        )
        numero = str(laudo.get("numero") or laudo_id).replace("/", "-")
        return Response(
            content=pdf_buf.getvalue(),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="Laudo_{numero}.pdf"'
            },
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Erro ao gerar PDF do laudo %s", laudo_id)
        raise HTTPException(status_code=500, detail="Erro ao gerar PDF do laudo.")


# ==============================================================================
# ROTAS PÚBLICAS DE ACOMPANHAMENTO DO CLIENTE (SEM LOGIN E SEM CUSTOS INTERNOS)
# ==============================================================================


def _montar_visao_publica(tenant_id: str, reg: dict) -> dict:
    tenant_raw = _get_auth_service().user_store.get_tenant(tenant_id) or {}
    tenant = sanitizar_tenant(tenant_raw)
    laudos_publicos = [
        {
            "id": l.get("id"),
            "numero": l.get("numero"),
            "titulo": l.get("titulo"),
            "conclusao": l.get("conclusao"),
            "tecnico_responsavel": l.get("tecnico_responsavel"),
            "emitido_em": l.get("emitido_em"),
        }
        for l in (reg.get("laudos") or [])
        if l.get("visivel_cliente", True)
    ]
    eventos_publicos = [
        ev for ev in (reg.get("historico_eventos") or []) if ev.get("publico", True)
    ]
    orc = reg.get("orcamento")
    orc_publico = (
        {
            "status": orc.get("status"),
            "descricao": orc.get("descricao"),
            "valor": orc.get("valor"),
            "enviado_em": orc.get("enviado_em"),
            "respondido_em": orc.get("respondido_em"),
            "respondido_por": orc.get("respondido_por"),
        }
        if isinstance(orc, dict) and orc.get("valor")
        else None
    )
    ass = reg.get("assinatura_entrega")
    ass_publica = (
        {
            "nome": ass.get("nome"),
            "assinado_em": ass.get("assinado_em"),
        }
        if isinstance(ass, dict) and ass.get("nome")
        else None
    )
    return {
        "empresa": {
            "nome": tenant.get("nome", "SysFix"),
            "telefone": tenant.get("telefone", ""),
            "endereco": tenant.get("endereco", ""),
            "cor_documento": tenant.get("cor_documento", "#4C1D95"),
            "logo_base64": tenant.get("logo_base64", ""),
        },
        "ordem": {
            "id": reg.get("id"),
            "codigo_acompanhamento": reg.get("codigo_acompanhamento"),
            "equipamento": reg.get("equipamento"),
            "categoria": reg.get("categoria") or "",
            "tombamento": reg.get("tombamento"),
            "serial_mac": reg.get("serial_mac") or "",
            "tipo_fluxo": reg.get("tipo_fluxo") or "Manutenção",
            "prioridade": reg.get("prioridade") or "Normal",
            "origem": reg.get("origem"),
            "localizacao": reg.get("localizacao"),
            "status": reg.get("status"),
            "data_coleta": reg.get("data_coleta"),
            "data_entrega": reg.get("data_entrega"),
            "os_coleta": reg.get("os_coleta"),
            "os_entrega": reg.get("os_entrega"),
            "problema": reg.get("problema"),
            "resolucao": reg.get("resolucao"),
            "garantia_dias": reg.get("garantia_dias") or 0,
            "garantia_ate": reg.get("garantia_ate") or "",
            "checklist": list(reg.get("checklist") or []),
            "laudos": laudos_publicos,
            "eventos": eventos_publicos,
            "orcamento": orc_publico,
            "assinatura": ass_publica,
        },
    }


@app.get("/api/public/acompanhar/{codigo}")
def consultar_acompanhamento_publico(codigo: str):
    encontrado = ColetaModel.buscar_por_codigo_publico(codigo)
    if not encontrado:
        raise HTTPException(
            status_code=404, detail="Ordem de Serviço não encontrada para este código."
        )
    tenant_id, reg = encontrado
    return _montar_visao_publica(tenant_id, reg)


@app.post("/api/public/acompanhar/{codigo}/orcamento")
def responder_orcamento_publico_api(
    codigo: str, payload: OrcamentoRespostaPublicaSchema
):
    try:
        res = ColetaModel.responder_orcamento_publico(
            codigo=codigo,
            decisao=payload.decisao,
            cliente_nome=payload.cliente_nome,
            observacao=payload.observacao or "",
        )
        if not res:
            raise HTTPException(
                status_code=404, detail="Ordem de Serviço não encontrada."
            )
        tenant_id, reg_atualizado = res
        return {
            "sucesso": True,
            "dados": _montar_visao_publica(tenant_id, reg_atualizado),
        }
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao responder orçamento público %s", codigo)
        raise HTTPException(status_code=500, detail="Erro ao registrar resposta.")


@app.get("/api/public/acompanhar/{codigo}/laudos/{laudo_id}/pdf")
def baixar_laudo_publico_pdf(codigo: str, laudo_id: str):
    encontrado = ColetaModel.buscar_por_codigo_publico(codigo)
    if not encontrado:
        raise HTTPException(status_code=404, detail="O.S. não encontrada.")
    tenant_id, reg = encontrado
    laudo = next(
        (
            item
            for item in (reg.get("laudos") or [])
            if item.get("id") == laudo_id and item.get("visivel_cliente", True)
        ),
        None,
    )
    if not laudo:
        raise HTTPException(status_code=404, detail="Laudo não disponível para o cliente.")
    tenant_raw = _get_auth_service().user_store.get_tenant(tenant_id) or {}
    pdf_buf = PDFReportGenerator.laudo_tecnico_pdf(
        reg, laudo, tenant=sanitizar_tenant(tenant_raw)
    )
    numero = str(laudo.get("numero") or laudo_id).replace("/", "-")
    return Response(
        content=pdf_buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="Laudo_{numero}.pdf"'},
    )


# ==============================================================================
# OPERAÇÕES TI & PROVEDORES: ESTOQUE, PREVENTIVAS E CLIENTES/POPS
# ==============================================================================


def _filtrar_item_estoque(item: dict, current_user: dict[str, Any]) -> dict:
    dados = dict(item)
    if not usuario_tem_permissao(current_user, "ver_custos"):
        dados.pop("custo_unitario", None)
    return dados


@app.get("/api/ops/estoque")
def listar_estoque_ops(
    current_user: dict[str, Any] = Depends(require_permission("ver_estoque")),
):
    itens = _get_ops_service().listar_estoque(current_user["tenant_id"])
    return [_filtrar_item_estoque(i, current_user) for i in itens]


@app.get("/api/ops/estoque/resumo")
def obter_resumo_estoque_ops(
    current_user: dict[str, Any] = Depends(require_permission("ver_estoque")),
):
    return _get_ops_service().resumo_estoque(
        current_user["tenant_id"],
        incluir_custos=usuario_tem_permissao(current_user, "ver_custos"),
    )


@app.post("/api/ops/estoque")
def criar_item_estoque_ops(
    payload: EstoqueItemSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_estoque")),
):
    try:
        dados = payload.model_dump()
        if not usuario_tem_permissao(current_user, "ver_custos"):
            dados["custo_unitario"] = 0.0
        item = _get_ops_service().criar_item_estoque(
            current_user["tenant_id"],
            dados,
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        return {"sucesso": True, "item": _filtrar_item_estoque(item, current_user)}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao criar item de estoque")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.patch("/api/ops/estoque/{item_id}")
def atualizar_item_estoque_ops(
    item_id: str,
    payload: EstoqueItemSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_estoque")),
):
    try:
        dados = payload.model_dump(exclude_unset=True)
        if not usuario_tem_permissao(current_user, "ver_custos"):
            dados.pop("custo_unitario", None)
        item = _get_ops_service().atualizar_item_estoque(
            current_user["tenant_id"],
            item_id,
            dados,
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        if not item:
            raise HTTPException(status_code=404, detail="Item de estoque não encontrado.")
        return {"sucesso": True, "item": _filtrar_item_estoque(item, current_user)}
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao atualizar item de estoque")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/ops/estoque/{item_id}/movimentar")
def movimentar_estoque_ops(
    item_id: str,
    payload: EstoqueMovimentoSchema,
    current_user: dict[str, Any] = Depends(require_permission("movimentar_estoque")),
):
    try:
        item = _get_ops_service().movimentar_estoque(
            tenant_id=current_user["tenant_id"],
            item_id=item_id,
            tipo=payload.tipo,
            quantidade=payload.quantidade,
            os_id=payload.os_id,
            observacao=payload.observacao or "",
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        if not item:
            raise HTTPException(status_code=404, detail="Item de estoque não encontrado.")
        return {"sucesso": True, "item": _filtrar_item_estoque(item, current_user)}
    except HTTPException:
        raise
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao movimentar estoque")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.delete("/api/ops/estoque/{item_id}")
def excluir_item_estoque_ops(
    item_id: str,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_estoque")),
):
    if not _get_ops_service().excluir_item_estoque(current_user["tenant_id"], item_id):
        raise HTTPException(status_code=404, detail="Item de estoque não encontrado.")
    return {"sucesso": True}


@app.get("/api/ops/preventivas")
def listar_preventivas_ops(
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_preventivas")),
):
    return _get_ops_service().listar_preventivas(current_user["tenant_id"])


@app.post("/api/ops/preventivas")
def criar_preventiva_ops(
    payload: PreventivaSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_preventivas")),
):
    try:
        item = _get_ops_service().criar_preventiva(
            current_user["tenant_id"],
            payload.model_dump(),
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        return {"sucesso": True, "item": item}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao criar preventiva")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/ops/preventivas/{item_id}/concluir")
def concluir_preventiva_ops(
    item_id: str,
    payload: PreventivaConcluirSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_preventivas")),
):
    try:
        data_exec = (
            payload.data_execucao.strftime("%Y-%m-%d")
            if isinstance(payload.data_execucao, date)
            else payload.data_execucao
        )
        item = _get_ops_service().concluir_preventiva(
            current_user["tenant_id"],
            item_id,
            observacao=payload.observacao or "",
            data_execucao=data_exec,
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        if not item:
            raise HTTPException(status_code=404, detail="Agenda preventiva não encontrada.")
        return {"sucesso": True, "item": item}
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao concluir preventiva")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.delete("/api/ops/preventivas/{item_id}")
def excluir_preventiva_ops(
    item_id: str,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_preventivas")),
):
    if not _get_ops_service().excluir_preventiva(current_user["tenant_id"], item_id):
        raise HTTPException(status_code=404, detail="Agenda preventiva não encontrada.")
    return {"sucesso": True}


@app.get("/api/ops/clientes")
def listar_clientes_ops(
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_clientes_pops")),
):
    return _get_ops_service().listar_clientes(current_user["tenant_id"])


@app.post("/api/ops/clientes")
def criar_cliente_ops(
    payload: ClientePopSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_clientes_pops")),
):
    try:
        item = _get_ops_service().criar_cliente(
            current_user["tenant_id"], payload.model_dump()
        )
        return {"sucesso": True, "item": item}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao criar cliente/POP")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.delete("/api/ops/clientes/{item_id}")
def excluir_cliente_ops(
    item_id: str,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_clientes_pops")),
):
    if not _get_ops_service().excluir_cliente(current_user["tenant_id"], item_id):
        raise HTTPException(status_code=404, detail="Cliente/POP não encontrado.")
    return {"sucesso": True}


# ==============================================================================
# SETOR AUTOMAÇÃO (CÂMERAS, INFRAESTRUTURA, IOT & ALARMES) E CIBERSEGURANÇA
# ==============================================================================


@app.get("/api/automacao/ativos")
def listar_ativos_automacao(
    pilar: Optional[str] = None,
    current_user: dict[str, Any] = Depends(require_permission("ver_automacao")),
):
    svc = _get_security_ops_service()
    return {
        "resumo": svc.resumo_automacao(current_user["tenant_id"]),
        "ativos": svc.listar_automacao(current_user["tenant_id"], pilar=pilar),
    }


@app.post("/api/automacao/ativos")
def criar_ativo_automacao_api(
    payload: AutomacaoAtivoSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_automacao")),
):
    try:
        dados = payload.model_dump()
        if isinstance(dados.get("ultima_auditoria"), date):
            dados["ultima_auditoria"] = dados["ultima_auditoria"].strftime("%Y-%m-%d")
        item = _get_security_ops_service().criar_ativo_automacao(
            current_user["tenant_id"],
            dados,
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        return {"sucesso": True, "item": item}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao criar ativo de automação")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.patch("/api/automacao/ativos/{item_id}")
def atualizar_ativo_automacao_api(
    item_id: str,
    payload: AutomacaoAtivoSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_automacao")),
):
    try:
        dados = payload.model_dump(exclude_unset=True)
        item = _get_security_ops_service().atualizar_ativo_automacao(
            current_user["tenant_id"],
            item_id,
            dados,
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        if not item:
            raise HTTPException(status_code=404, detail="Ativo de automação não encontrado.")
        return {"sucesso": True, "item": item}
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao atualizar ativo de automação")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.delete("/api/automacao/ativos/{item_id}")
def excluir_ativo_automacao_api(
    item_id: str,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_automacao")),
):
    if not _get_security_ops_service().excluir_ativo_automacao(
        current_user["tenant_id"], item_id
    ):
        raise HTTPException(status_code=404, detail="Ativo de automação não encontrado.")
    return {"sucesso": True}


@app.get("/api/automacao/relatorio/pdf")
def baixar_relatorio_automacao_pdf(
    current_user: dict[str, Any] = Depends(require_permission("ver_automacao")),
):
    try:
        ativos = _get_security_ops_service().listar_automacao(current_user["tenant_id"])
        pdf_buf = PDFReportGenerator.relatorio_auditoria_automacao(
            ativos, tenant=current_user["tenant"]
        )
        return Response(
            content=pdf_buf.getvalue(),
            media_type="application/pdf",
            headers={'Content-Disposition': 'inline; filename="Auditoria_Automacao_SysFix.pdf"'},
        )
    except Exception:
        logger.exception("Erro ao gerar relatório PDF de automação")
        raise HTTPException(status_code=500, detail="Erro ao gerar relatório de automação.")


@app.get("/api/cyber/resumo")
def obter_resumo_cyber_api(
    current_user: dict[str, Any] = Depends(require_permission("ver_ciberseguranca")),
):
    return _get_security_ops_service().resumo_cyber(current_user["tenant"])


@app.put("/api/cyber/governanca")
def atualizar_governanca_cyber_api(
    payload: CyberGovernancaSchema,
    current_user: dict[str, Any] = Depends(require_admin),
):
    try:
        tenant = _get_auth_service().update_cyber_governance(
            admin_ctx=current_user,
            permitir_auditoria_rede=payload.permitir_auditoria_rede,
            escopo_autorizado=payload.escopo_autorizado,
            observacao_termo=payload.observacao_termo or "",
        )
        return {"sucesso": True, "tenant": tenant, "governanca": tenant["cyber_governanca"]}
    except AuthorizationError as aze:
        raise HTTPException(status_code=403, detail=str(aze))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao atualizar governança de cibersegurança")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.get("/api/cyber/auditorias")
def listar_auditorias_cyber_api(
    current_user: dict[str, Any] = Depends(require_permission("ver_ciberseguranca")),
):
    return _get_security_ops_service().listar_auditorias(current_user["tenant_id"])


@app.post("/api/cyber/auditorias/executar")
def executar_auditoria_cyber_api(
    payload: CyberExecutarAuditoriaSchema,
    current_user: dict[str, Any] = Depends(require_permission("executar_auditoria_rede")),
):
    try:
        res = _get_security_ops_service().executar_auditoria_rede(
            tenant=current_user["tenant"],
            alvo=payload.alvo,
            tipo_auditoria=payload.tipo_auditoria,
            registrar_achados=payload.registrar_achados,
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        return {"sucesso": True, **res}
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao executar auditoria de cibersegurança")
        raise HTTPException(status_code=500, detail="Erro ao executar auditoria defensiva.")


@app.post("/api/cyber/auditorias/importar")
def importar_auditoria_cyber_api(
    payload: CyberImportarAuditoriaSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_auditoria_cyber")),
):
    try:
        res = _get_security_ops_service().importar_auditoria_json(
            tenant_id=current_user["tenant_id"],
            payload=payload.payload,
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        return {"sucesso": True, **res}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        logger.exception("Erro ao importar JSON de auditoria")
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.get("/api/cyber/coletor")
def baixar_coletor_cyber_api(
    formato: str = Query("ps1"),
    current_user: dict[str, Any] = Depends(require_permission("executar_auditoria_rede")),
):
    try:
        from automation_cyber import NetworkAuditEngine

        escopo = (current_user["tenant"].get("cyber_governanca") or {}).get("escopo_autorizado") or []
        conteudo, media_type, filename = NetworkAuditEngine.gerar_script_coletor_local(formato, escopo)
        return Response(
            content=conteudo,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@app.get("/api/cyber/achados")
def listar_achados_cyber_api(
    current_user: dict[str, Any] = Depends(require_permission("ver_ciberseguranca")),
):
    return _get_security_ops_service().listar_achados(current_user["tenant_id"])


@app.post("/api/cyber/achados")
def criar_achado_cyber_api(
    payload: CyberAchadoSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_auditoria_cyber")),
):
    try:
        item = _get_security_ops_service().criar_achado(
            current_user["tenant_id"],
            payload.model_dump(),
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        return {"sucesso": True, "item": item}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@app.patch("/api/cyber/achados/{item_id}")
def atualizar_achado_cyber_api(
    item_id: str,
    payload: CyberAchadoSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_auditoria_cyber")),
):
    try:
        item = _get_security_ops_service().atualizar_achado(
            current_user["tenant_id"], item_id, payload.model_dump(exclude_unset=True)
        )
        if not item:
            raise HTTPException(status_code=404, detail="Achado não encontrado.")
        return {"sucesso": True, "item": item}
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@app.delete("/api/cyber/achados/{item_id}")
def excluir_achado_cyber_api(
    item_id: str,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_auditoria_cyber")),
):
    if not _get_security_ops_service().excluir_achado(current_user["tenant_id"], item_id):
        raise HTTPException(status_code=404, detail="Achado não encontrado.")
    return {"sucesso": True}


@app.get("/api/cyber/checklist")
def obter_checklist_cyber_api(
    current_user: dict[str, Any] = Depends(require_permission("ver_ciberseguranca")),
):
    return _get_security_ops_service().obter_checklist_cyber(current_user["tenant_id"])


@app.put("/api/cyber/checklist")
def salvar_checklist_cyber_api(
    payload: CyberChecklistSaveSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_auditoria_cyber")),
):
    try:
        itens = _get_security_ops_service().salvar_checklist_cyber(
            current_user["tenant_id"],
            payload.itens,
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        return {"sucesso": True, "itens": itens}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@app.get("/api/cyber/conhecimento")
def listar_conhecimento_cyber_api(
    busca: Optional[str] = "",
    current_user: dict[str, Any] = Depends(require_permission("ver_ciberseguranca")),
):
    return _get_security_ops_service().listar_conhecimento(
        current_user["tenant_id"], busca=busca or ""
    )


@app.post("/api/cyber/conhecimento")
def criar_conhecimento_cyber_api(
    payload: CyberConhecimentoSchema,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_auditoria_cyber")),
):
    try:
        item = _get_security_ops_service().criar_artigo_conhecimento(
            current_user["tenant_id"],
            payload.model_dump(),
            actor=current_user.get("nome") or current_user.get("email") or "",
        )
        return {"sucesso": True, "item": item}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@app.delete("/api/cyber/conhecimento/{item_id}")
def excluir_conhecimento_cyber_api(
    item_id: str,
    current_user: dict[str, Any] = Depends(require_permission("gerenciar_auditoria_cyber")),
):
    if not _get_security_ops_service().excluir_artigo_conhecimento(
        current_user["tenant_id"], item_id
    ):
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    return {"sucesso": True}


@app.get("/api/cyber/relatorio/pdf")
def baixar_relatorio_cyber_pdf(
    current_user: dict[str, Any] = Depends(require_permission("ver_ciberseguranca")),
):
    try:
        svc = _get_security_ops_service()
        tid = current_user["tenant_id"]
        pdf_buf = PDFReportGenerator.relatorio_auditoria_cyber(
            auditorias=svc.listar_auditorias(tid),
            achados=svc.listar_achados(tid),
            checklist=svc.obter_checklist_cyber(tid),
            tenant=current_user["tenant"],
        )
        return Response(
            content=pdf_buf.getvalue(),
            media_type="application/pdf",
            headers={'Content-Disposition': 'inline; filename="Auditoria_Ciberseguranca_SysFix.pdf"'},
        )
    except Exception:
        logger.exception("Erro ao gerar relatório PDF de cibersegurança")
        raise HTTPException(status_code=500, detail="Erro ao gerar relatório de cibersegurança.")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
