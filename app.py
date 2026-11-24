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
    sanitizar_tenant,
    sanitizar_usuario,
)
from config import get_cors_origins, get_public_client_config, get_storage_backend
from database import init_db
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
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
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from reports import PDFReportGenerator
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


def _get_auth_service() -> AuthService:
    return AuthService()


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


class TeamMemberUpdateSchema(BaseModel):
    nome: Optional[str] = None
    role: Optional[str] = None
    ativo: Optional[bool] = None


class TenantSettingsSchema(BaseModel):
    nome: Optional[str] = None
    sla_dias: Optional[int] = None
    localizacoes: Optional[List[str]] = None


class ImportarDadosSchema(BaseModel):
    filename: str
    conteudo_base64: str
    modo: str = "anexar"
    simular: bool = False


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


@app.get("/", response_class=FileResponse)
def read_index():
    index_path = BASE_DIR / "templates" / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    raise HTTPException(
        status_code=404, detail="Arquivo index.html não encontrado no servidor."
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
    }


# ==============================================================================
# ROTAS DO DASHBOARD E OPERAÇÃO DE EQUIPAMENTOS (ISOLADAS POR TENANT)
# ==============================================================================


@app.get("/api/dashboard")
def obter_dashboard(
    mes: Optional[int] = Query(None, ge=1, le=12),
    ano: Optional[int] = Query(None, ge=2000, le=2100),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        tenant = current_user["tenant"]
        return ColetaModel.obter_dashboard(
            tenant_id=current_user["tenant_id"],
            mes=mes,
            ano=ano,
            sla_dias=tenant.get("sla_dias", 3),
        )
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
                        "os_coleta",
                        "os_entrega",
                        "origem",
                        "localizacao",
                        "tecnico_coleta",
                        "tecnico_entrega",
                    )
                )
            ]

        return [serializar_registro(dict(item)) for item in dados]
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
        return serializar_registro(dict(dados))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Erro ao buscar equipamento %s", registro_id)
        raise HTTPException(status_code=500, detail=obter_mensagem_erro("DB_ERROR"))


@app.post("/api/equipamentos")
def criar_entrada(
    payload: Union[EntradaSchema, List[EntradaSchema]],
    current_user: dict[str, Any] = Depends(get_current_user),
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
            )

            return {
                "sucesso": True,
                "ids": ids_criados,
                "mensagem": f"{len(ids_criados)} entrada(s) registrada(s) com sucesso.",
            }

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


@app.put("/api/equipamentos/{registro_id}/saida")
def registrar_saida(
    registro_id: int,
    payload: SaidaSchema,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        valor_informado = (
            payload.valor_custo
            if payload.valor_custo is not None
            else payload.valor
        )
        atualizado = ColetaModel.registrar_saida(
            registro_id=registro_id,
            tecnico_entrega=payload.tecnico_entrega,
            data_entrega=payload.data_entrega,
            os_entrega=payload.os_entrega,
            status_custo=payload.status_custo,
            valor_custo=valor_informado,
            resolucao=payload.resolucao,
            laudado=payload.laudado,
            tenant_id=current_user["tenant_id"],
            actor=current_user.get("nome") or current_user.get("email") or "",
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
            saida_data = SaidaSchema(**payload)
            valor_informado = (
                saida_data.valor_custo
                if saida_data.valor_custo is not None
                else saida_data.valor
            )
            atualizado = ColetaModel.registrar_saida(
                registro_id=registro_id,
                tecnico_entrega=saida_data.tecnico_entrega,
                data_entrega=saida_data.data_entrega,
                os_entrega=saida_data.os_entrega,
                status_custo=saida_data.status_custo,
                valor_custo=valor_informado,
                resolucao=saida_data.resolucao,
                laudado=saida_data.laudado,
                tenant_id=tenant_id,
                actor=actor,
            )
            if not atualizado:
                raise HTTPException(
                    status_code=404, detail=obter_mensagem_erro("NOT_FOUND")
                )
            return {
                "sucesso": True,
                "mensagem": "Saída registrada com sucesso.",
            }

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
    current_user: dict[str, Any] = Depends(require_admin),
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
    current_user: dict[str, Any] = Depends(require_admin),
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
    current_user: dict[str, Any] = Depends(require_admin),
):
    try:
        membro = _get_auth_service().create_team_member(
            admin_ctx=current_user,
            nome=payload.nome,
            email=payload.email,
            senha=payload.senha,
            role=payload.role or "tecnico",
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
    current_user: dict[str, Any] = Depends(require_admin),
):
    try:
        membro = _get_auth_service().update_team_member(
            admin_ctx=current_user,
            target_uid=uid,
            nome=payload.nome,
            role=payload.role,
            ativo=payload.ativo,
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
    current_user: dict[str, Any] = Depends(require_admin),
):
    try:
        tenant = _get_auth_service().update_tenant_settings(
            admin_ctx=current_user,
            nome=payload.nome,
            sla_dias=payload.sla_dias,
            localizacoes=payload.localizacoes,
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
    current_user: dict[str, Any] = Depends(require_admin),
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
    current_user: dict[str, Any] = Depends(require_admin),
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
    _current_user: dict[str, Any] = Depends(require_admin),
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
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        tenant_id = current_user["tenant_id"]
        nome_empresa = current_user["tenant"].get("nome") or "SysFix"
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
            )
            filename = f"Relatorio_SysFix_{mes:02d}_{ano}.pdf"
        elif tipo_normalizado == "pendentes":
            pdf_buffer = PDFReportGenerator.relatorio_pendentes(
                anonimizar=anonimizar,
                tenant_id=tenant_id,
                nome_empresa=nome_empresa,
            )
            filename = "Relatorio_SysFix_Pendentes.pdf"
        elif tipo_normalizado == "geral":
            pdf_buffer = PDFReportGenerator.relatorio_geral(
                anonimizar=anonimizar,
                tenant_id=tenant_id,
                nome_empresa=nome_empresa,
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


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
