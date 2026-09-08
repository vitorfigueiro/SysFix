import os
from contextlib import asynccontextmanager
from typing import Optional, Any
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, field_validator

# Imports das camadas do sistema
from database import init_db
from models import ColetaModel
from reports import PDFReportGenerator
from security import SecurityValidator

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa o schema e migrações no Neon PostgreSQL
    init_db()
    yield


app = FastAPI(
    title="Gestão de Equipamentos Web",
    version="2.0.0",
    lifespan=lifespan
)

# Configuração de Arquivos Estáticos e Templates
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# Handler personalizado para capturar erros de validação do Pydantic (422 -> 400 amigável)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    erros = exc.errors()
    primeiro_erro = erros[0]["msg"] if erros else "Dados de requisição inválidos."
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"success": False, "detail": f"Erro de Validação: {primeiro_erro}"},
    )


# ----------------------------------------------------
# MODELOS PYDANTIC TRATADOS (Prevenção de Erros 400/422)
# ----------------------------------------------------
class EntradaEquipamentoSchema(BaseModel):
    equipamento: str
    tombamento: Optional[str] = ""
    tecnico_coleta: Optional[str] = ""
    data_coleta: Optional[str] = ""
    origem: Optional[str] = ""
    os_coleta: Optional[str] = ""
    localizacao: Optional[str] = "Bancada TI"
    problema: Optional[str] = ""

    @field_validator("equipamento", mode="before")
    @classmethod
    def validar_equipamento(cls, v: Any) -> str:
        if not v or not str(v).strip():
            raise ValueError("O campo 'equipamento' é obrigatório.")
        txt = SecurityValidator.sanitizar_texto(str(v))
        if not txt:
            raise ValueError("O campo 'equipamento' não pode conter caracteres inválidos.")
        return txt

    @field_validator("data_coleta", mode="before")
    @classmethod
    def normalizar_data(cls, v: Optional[Any]) -> str:
        if not v or not str(v).strip():
            return datetime.now().strftime("%Y-%m-%d")
        return SecurityValidator.validar_data(str(v))

    @field_validator("localizacao", mode="before")
    @classmethod
    def normalizar_localizacao(cls, v: Optional[Any]) -> str:
        if not v or not str(v).strip():
            return "Bancada TI"
        return SecurityValidator.validate_location(str(v))


class AtualizarEntradaSchema(EntradaEquipamentoSchema):
    admin_password: str


class SaidaEquipamentoSchema(BaseModel):
    tecnico_entrega: Optional[str] = ""
    data_entrega: Optional[str] = ""
    os_entrega: Optional[str] = ""
    status_custo: Optional[str] = "Sem Custo"
    valor_custo: Optional[float] = 0.0
    resolucao: Optional[str] = ""
    laudado: Optional[str] = "Não"

    @field_validator("data_entrega", mode="before")
    @classmethod
    def normalizar_data_entrega(cls, v: Optional[Any]) -> str:
        if not v or not str(v).strip():
            return datetime.now().strftime("%Y-%m-%d")
        return SecurityValidator.validar_data(str(v))

    @field_validator("valor_custo", mode="before")
    @classmethod
    def normalizar_custo(cls, v: Optional[Any]) -> float:
        if v is None or v == "":
            return 0.0
        return SecurityValidator.validate_cost(v)


class AcaoAdminSchema(BaseModel):
    admin_password: str


class RelatorioFiltroSchema(BaseModel):
    mes: int
    ano: int


# Reconstrução explícita dos esquemas
EntradaEquipamentoSchema.model_rebuild()
AtualizarEntradaSchema.model_rebuild()
SaidaEquipamentoSchema.model_rebuild()
AcaoAdminSchema.model_rebuild()
RelatorioFiltroSchema.model_rebuild()


# ----------------------------------------------------
# ROTAS DE PÁGINAS (HTML)
# ----------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# ----------------------------------------------------
# ENDPOINTS DA API (JSON)
# ----------------------------------------------------
@app.get("/api/equipamentos")
def listar_equipamentos(filtro: str = "nao_finalizados_mes"):
    if filtro == "nao_finalizados_mes":
        regs = ColetaModel.buscar_nao_finalizados_mes_atual()
    elif filtro == "finalizados_mes":
        regs = ColetaModel.buscar_finalizados_mes_atual()
    else:
        regs = ColetaModel.buscar_todos()

    for r in regs:
        if not r.get("status"):
            r["status"] = "Pendente"
        if r.get("data_coleta"):
            r["data_coleta"] = str(r["data_coleta"])
        if r.get("data_entrega"):
            r["data_entrega"] = str(r["data_entrega"])

    return JSONResponse(content=jsonable_encoder(regs))


@app.get("/api/equipamentos/{registro_id}")
def obter_equipamento(registro_id: int):
    dados = ColetaModel.buscar_por_id(registro_id)
    if not dados:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")

    for key, val in dados.items():
        if val is None:
            dados[key] = ""
        else:
            dados[key] = str(val)

    return JSONResponse(content=jsonable_encoder(dados))


@app.post("/api/equipamentos/entrada")
def registrar_entrada(payload: EntradaEquipamentoSchema):
    try:
        reg_id = ColetaModel.registrar_entrada(
            payload.equipamento,
            payload.tombamento or "",
            payload.tecnico_coleta or "",
            payload.data_coleta,
            payload.origem or "",
            payload.os_coleta or "",
            payload.localizacao,
            payload.problema or "",
        )
        return {"success": True, "id": reg_id, "message": "Entrada registrada com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/equipamentos/{registro_id}/entrada")
def atualizar_entrada(registro_id: int, payload: AtualizarEntradaSchema):
    if payload.admin_password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Senha de administrador incorreta!")

    try:
        ColetaModel.atualizar_entrada(
            registro_id,
            payload.equipamento,
            payload.tombamento or "",
            payload.tecnico_coleta or "",
            payload.data_coleta,
            payload.origem or "",
            payload.os_coleta or "",
            payload.localizacao,
            payload.problema or "",
        )
        return {"success": True, "message": "Dados de entrada atualizados!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/equipamentos/{registro_id}/saida")
def registrar_saida(registro_id: int, payload: SaidaEquipamentoSchema):
    try:
        ColetaModel.registrar_saida(
            registro_id,
            payload.tecnico_entrega or "",
            payload.data_entrega,
            payload.os_entrega or "",
            payload.status_custo or "Sem Custo",
            payload.valor_custo or 0.0,
            payload.resolucao or "",
            payload.laudado or "Não",
        )
        return {"success": True, "message": "Saída registrada com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/equipamentos/{registro_id}/excluir")
def excluir_registro(registro_id: int, payload: AcaoAdminSchema):
    if payload.admin_password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Senha de administrador incorreta!")

    try:
        ColetaModel.excluir(registro_id)
        return {"success": True, "message": "Registro excluído com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/relatorios/mensal")
def gerar_relatorio_mensal(payload: RelatorioFiltroSchema):
    try:
        pdf_buffer = PDFReportGenerator.relatorio_por_mes(payload.mes, payload.ano)
        nome_arquivo = f"relatorio_coletas_{payload.ano}_{payload.mes:02d}.pdf"

        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={nome_arquivo}"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar PDF: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    # Lê a porta configurada no ambiente do Railway ou usa 8000 por padrão
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)