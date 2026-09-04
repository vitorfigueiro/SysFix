import os
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# Imports das camadas do sistema
from database import init_db
from models import ColetaModel
from reports import PDFReportGenerator

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


# Ciclo de vida moderno do FastAPI (substitui @app.on_event)
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Gestão de Equipamentos Web", version="2.0.0", lifespan=lifespan)

# Configuração de Arquivos Estáticos e Templates
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ----------------------------------------------------
# MODELOS PYDANTIC (Para aceitar payloads JSON e Form)
# ----------------------------------------------------
class EntradaEquipamentoSchema(BaseModel):
    equipamento: str
    tombamento: str
    tecnico_coleta: str
    data_coleta: str
    origem: str
    os_coleta: Optional[str] = ""
    localizacao: Optional[str] = ""
    problema: Optional[str] = ""


class AtualizarEntradaSchema(EntradaEquipamentoSchema):
    admin_password: str


class SaidaEquipamentoSchema(BaseModel):
    tecnico_entrega: str
    data_entrega: str
    os_entrega: Optional[str] = ""
    status_custo: Optional[str] = "Sem Custo"
    valor_custo: Optional[float] = 0.0
    resolucao: Optional[str] = ""
    laudado: Optional[str] = "Não"


class AcaoAdminSchema(BaseModel):
    admin_password: str


class RelatorioFiltroSchema(BaseModel):
    mes: int
    ano: int


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

    # Como o RealDictCursor já retorna dicionários, o payload é retornado diretamente
    for r in regs:
        if not r.get("status"):
            r["status"] = "Pendente"
        # Garante serialização simples para datas
        if r.get("data_coleta"):
            r["data_coleta"] = str(r["data_coleta"])
        if r.get("data_entrega"):
            r["data_entrega"] = str(r["data_entrega"])

    return JSONResponse(content=regs)


@app.get("/api/equipamentos/{registro_id}")
def obter_equipamento(registro_id: int):
    dados = ColetaModel.buscar_por_id(registro_id)
    if not dados:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")
    
    # Formatação de nulos e datas para exibição limpa no frontend
    for key, val in dados.items():
        if val is None:
            dados[key] = ""
        else:
            dados[key] = str(val)

    return JSONResponse(content=dados)


@app.post("/api/equipamentos/entrada")
def registrar_entrada(payload: EntradaEquipamentoSchema):
    try:
        reg_id = ColetaModel.registrar_entrada(
            payload.equipamento,
            payload.tombamento,
            payload.tecnico_coleta,
            payload.data_coleta,
            payload.origem,
            payload.os_coleta,
            payload.localizacao,
            payload.problema,
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
            payload.tombamento,
            payload.tecnico_coleta,
            payload.data_coleta,
            payload.origem,
            payload.os_coleta,
            payload.localizacao,
            payload.problema,
        )
        return {"success": True, "message": "Dados de entrada atualizados!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/equipamentos/{registro_id}/saida")
def registrar_saida(registro_id: int, payload: SaidaEquipamentoSchema):
    try:
        ColetaModel.registrar_saida(
            registro_id,
            payload.tecnico_entrega,
            payload.data_entrega,
            payload.os_entrega,
            payload.status_custo,
            payload.valor_custo,
            payload.resolucao,
            payload.laudado,
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
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)