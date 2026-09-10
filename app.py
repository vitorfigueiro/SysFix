import os
import uvicorn
from datetime import datetime, date
from typing import Optional, Union, Any
from fastapi import FastAPI, HTTPException, Depends, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from mensagens import obter_mensagem_erro
from database import init_db, get_connection
from models import ColetaModel
from pdf_generator import PDFReportGenerator

# Inicializa o banco de dados e aplica migrações
init_db()

app = FastAPI(
    title="API de Gerenciamento de Coletas - SysFix",
    version="1.0.0"
)

# Configuração de CORS para liberar conexões do frontend/Railway
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Monta diretório de arquivos estáticos (CSS, JS, Imagens) se existir
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


def validar_e_formatar_data(v: Union[str, date, None]) -> str:
    """Converte de forma flexível datas enviadas como String (ISO, BR) ou Objeto date."""
    if not v:
        return ""
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    
    v = str(v).strip()
    if not v:
        return ""

    # Tenta ler formato Brasileiro DD/MM/YYYY
    try:
        dt = datetime.strptime(v, "%d/%m/%Y")
        return dt.strftime("%d/%m/%Y")
    except ValueError:
        pass

    # Tenta ler formato ISO YYYY-MM-DD
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
            # Mapeia tecnico_coleta para tecnico se enviado do frontend
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
    valor_custo: Optional[float] = 0.0
    valor: Optional[float] = 0.0
    resolucao: Optional[str] = ""
    laudado: Optional[str] = "Não"

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def compatibilizar_campos_saida(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Mapeia valor para valor_custo se enviado do frontend
            if "valor" in data and ("valor_custo" not in data or data["valor_custo"] == 0.0):
                data["valor_custo"] = data["valor"]
        return data

    @field_validator("data_entrega", mode="before")
    @classmethod
    def normalizar_data(cls, v):
        return validar_e_formatar_data(v)


# Rota Principal: Servir o Frontend (index.html)
@app.get("/", response_class=FileResponse)
def read_index():
    index_path = os.path.join(os.path.dirname(__file__), "templates/index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="Arquivo index.html não encontrado no servidor.")


# Rota de Diagnóstico do Banco de Dados
@app.get("/debug-db")
def debug_db():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM coletas;")
        res = cursor.fetchone()
        total_coletas = res["count"] if isinstance(res, dict) else res[0]

        cursor.execute("SELECT id, equipamento, status, data_coleta FROM coletas ORDER BY id DESC LIMIT 5;")
        ultimos_registros = cursor.fetchall()
        registros_formatados = [serializar_registro(dict(r)) for r in ultimos_registros]

        return {
            "status": "online",
            "total_registros_coletas": total_coletas,
            "ultimos_5_registros": registros_formatados
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()


# Rotas da API de Equipamentos

@app.get("/api/equipamentos")
def listar_equipamentos(
    filtro: Optional[str] = None,
    status: Optional[str] = None
):
    try:
        valor_filtro = (status or filtro or "nao_finalizados").lower().strip()

        if valor_filtro in ["finalizados", "entregues", "entregue", "finalizado"]:
            dados = ColetaModel.buscar_finalizados_mes_atual()
        elif valor_filtro == "todos":
            dados = ColetaModel.buscar_todos()
        else:
            dados = ColetaModel.buscar_nao_finalizados_mes_atual()
        
        return [serializar_registro(dict(item)) for item in dados]
    except Exception as e:
        erro = obter_mensagem_erro("DB_ERROR", detalhe_tecnico=str(e))
        raise HTTPException(status_code=500, detail=erro)


@app.get("/api/equipamentos/{registro_id}")
def obter_equipamento(registro_id: int):
    try:
        dados = ColetaModel.buscar_por_id(registro_id)
        if not dados:
            erro = obter_mensagem_erro("NOT_FOUND")
            raise HTTPException(status_code=404, detail=erro)
        
        return serializar_registro(dict(dados))
    except HTTPException:
        raise
    except Exception as e:
        erro = obter_mensagem_erro("DB_ERROR", detalhe_tecnico=str(e))
        raise HTTPException(status_code=500, detail=erro)


@app.post("/api/equipamentos")
def criar_entrada(payload: EntradaSchema):
    if not payload.equipamento:
        erro = obter_mensagem_erro("MISSING_FIELDS", detalhe_tecnico="Campo 'equipamento' ausente")
        raise HTTPException(status_code=400, detail=erro)

    try:
        novo_id = ColetaModel.registrar_entrada(
            equipamento=payload.equipamento,
            tombamento=payload.tombamento,
            tecnico=payload.tecnico or payload.tecnico_coleta,
            data_coleta=payload.data_coleta,
            origem=payload.origem,
            os_coleta=payload.os_coleta,
            localizacao=payload.localizacao,
            problema=payload.problema,
        )
        return {"sucesso": True, "id": novo_id, "mensagem": "Entrada registrada com sucesso."}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        erro = obter_mensagem_erro("DB_ERROR", detalhe_tecnico=str(e))
        raise HTTPException(status_code=500, detail=erro)


@app.put("/api/equipamentos/{registro_id}/saida")
def registrar_saida(registro_id: int, payload: SaidaSchema):
    try:
        ColetaModel.registrar_saida(
            registro_id=registro_id,
            tecnico_entrega=payload.tecnico_entrega,
            data_entrega=payload.data_entrega,
            os_entrega=payload.os_entrega,
            status_custo=payload.status_custo,
            valor_custo=payload.valor_custo or payload.valor,
            resolucao=payload.resolucao,
            laudado=payload.laudado,
        )
        return {"sucesso": True, "mensagem": "Saída/Entrega registrada com sucesso."}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        erro = obter_mensagem_erro("DB_ERROR", detalhe_tecnico=str(e))
        raise HTTPException(status_code=500, detail=erro)


@app.put("/api/equipamentos/{registro_id}")
def atualizar_equipamento_unificado(registro_id: int, payload: dict):
    """
    Endpoint unificado PUT para atualização de Entrada ou registro de Saída,
    dependendo dos campos enviados pelo Frontend.
    """
    try:
        # Verifica se é uma requisição de saída/entrega
        if "tecnico_entrega" in payload or payload.get("status") in ["Entregue", "Finalizado"]:
            saida_data = SaidaSchema(**payload)
            ColetaModel.registrar_saida(
                registro_id=registro_id,
                tecnico_entrega=saida_data.tecnico_entrega,
                data_entrega=saida_data.data_entrega,
                os_entrega=saida_data.os_entrega,
                status_custo=saida_data.status_custo,
                valor_custo=saida_data.valor_custo or saida_data.valor,
                resolucao=saida_data.resolucao,
                laudado=saida_data.laudado,
            )
            return {"sucesso": True, "mensagem": "Saída registrada com sucesso."}
        
        # Caso contrário, trata como atualização de dados de Entrada
        entrada_data = EntradaSchema(**payload)
        ColetaModel.atualizar_entrada(
            registro_id=registro_id,
            equipamento=entrada_data.equipamento,
            tombamento=entrada_data.tombamento,
            tecnico=entrada_data.tecnico or entrada_data.tecnico_coleta,
            data_coleta=entrada_data.data_coleta,
            origem=entrada_data.origem,
            os_coleta=entrada_data.os_coleta,
            localizacao=entrada_data.localizacao,
            problema=entrada_data.problema,
        )
        return {"sucesso": True, "mensagem": "Registro atualizado com sucesso."}

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        erro = obter_mensagem_erro("DB_ERROR", detalhe_tecnico=str(e))
        raise HTTPException(status_code=500, detail=erro)


@app.delete("/api/equipamentos/{registro_id}")
def deletar_equipamento(registro_id: int):
    try:
        ColetaModel.excluir(registro_id)
        return {"sucesso": True, "mensagem": "Registro excluído com sucesso."}
    except Exception as e:
        erro = obter_mensagem_erro("DB_ERROR", detalhe_tecnico=str(e))
        raise HTTPException(status_code=500, detail=erro)


# Rota para Geração de Relatórios PDF

@app.get("/api/relatorio/pdf")
def gerar_relatorio_pdf(
    mes: int = Query(..., ge=1, le=12),
    ano: int = Query(..., ge=2000, le=2100),
    anonimizar: bool = Query(False)
):
    try:
        pdf_buffer = PDFReportGenerator.relatorio_por_mes(mes=mes, ano=ano, anonimizar=anonimizar)
        
        filename = f"Relatorio_SysFix_{mes:02d}_{ano}.pdf"
        headers = {
            "Content-Disposition": f'inline; filename="{filename}"'
        }
        
        return StreamingResponse(
            pdf_buffer, 
            media_type="application/pdf", 
            headers=headers
        )
    except Exception as e:
        erro = obter_mensagem_erro("REPORT_ERROR", detalhe_tecnico=str(e)) if "obter_mensagem_erro" in globals() else str(e)
        raise HTTPException(status_code=500, detail=f"Erro ao gerar relatório PDF: {str(e)}")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)