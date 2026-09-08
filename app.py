import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from database import init_db, get_connection
from models import ColetaModel

# Inicializa o banco de dados e aplica migrações
init_db()

app = FastAPI(
    title="API de Gerenciamento de Coletas",
    version="1.0.0"
)

# Configuração de CORS (libera chamadas do frontend no Railway / Local)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Monta diretório de arquivos estáticos (CSS, JS, Imagens) se a pasta 'static' existir
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


# Modelos Pydantic para validação das requisições
class EntradaSchema(BaseModel):
    equipamento: str
    tombamento: Optional[str] = ""
    tecnico: Optional[str] = ""
    data_coleta: Optional[str] = ""
    origem: Optional[str] = ""
    os_coleta: Optional[str] = ""
    localizacao: Optional[str] = ""
    problema: Optional[str] = ""

    model_config = ConfigDict(extra="ignore")


class SaidaSchema(BaseModel):
    tecnico_entrega: str
    data_entrega: str
    os_entrega: Optional[str] = ""
    status_custo: Optional[str] = "Sem Custo"
    valor_custo: Optional[float] = 0.0
    resolucao: Optional[str] = ""
    laudado: Optional[str] = "Não"

    model_config = ConfigDict(extra="ignore")


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
    """Retorna a contagem total e os últimos registros para verificar migração do SQLite."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM coletas;")
        total_coletas = cursor.fetchone()["count"]

        cursor.execute("SELECT id, equipamento, status, data_coleta FROM coletas ORDER BY id DESC LIMIT 5;")
        ultimos_registros = cursor.fetchall()

        return {
            "status": "online",
            "total_registros_coletas": total_coletas,
            "ultimos_5_registros": ultimos_registros
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()


# Rotas da API

@app.get("/api/equipamentos")
def listar_equipamentos(filtro: Optional[str] = Query("nao_finalizados")):
    """
    Retorna a lista de equipamentos.
    - 'nao_finalizados': todos pendentes na bancada.
    - 'finalizados': entregues no mês atual.
    - 'todos': histórico completo.
    """
    try:
        if filtro == "finalizados":
            dados = ColetaModel.buscar_finalizados_mes_atual()
        elif filtro == "todos":
            dados = ColetaModel.buscar_todos()
        else:
            dados = ColetaModel.buscar_nao_finalizados_mes_atual()
        
        return dados
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar registros: {str(e)}")


@app.get("/api/equipamentos/{registro_id}")
def obter_equipamento(registro_id: int):
    try:
        dados = ColetaModel.buscar_por_id(registro_id)
        if not dados:
            raise HTTPException(status_code=404, detail="Equipamento não encontrado.")
        
        # Garante substituição de valores None por string vazia mantendo compatibilidade
        resultado = {}
        for key, val in dados.items():
            resultado[key] = "" if val is None else val

        return resultado
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter equipamento: {str(e)}")


@app.post("/api/equipamentos")
def criar_entrada(payload: EntradaSchema):
    try:
        novo_id = ColetaModel.registrar_entrada(
            equipamento=payload.equipamento,
            tombamento=payload.tombamento,
            tecnico=payload.tecnico,
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
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.put("/api/equipamentos/{registro_id}/saida")
def registrar_saida(registro_id: int, payload: SaidaSchema):
    try:
        ColetaModel.registrar_saida(
            registro_id=registro_id,
            tecnico_entrega=payload.tecnico_entrega,
            data_entrega=payload.data_entrega,
            os_entrega=payload.os_entrega,
            status_custo=payload.status_custo,
            valor_custo=payload.valor_custo,
            resolucao=payload.resolucao,
            laudado=payload.laudado,
        )
        return {"sucesso": True, "mensagem": "Saída/Entrega registrada com sucesso."}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.put("/api/equipamentos/{registro_id}")
def atualizar_entrada(registro_id: int, payload: EntradaSchema):
    try:
        ColetaModel.atualizar_entrada(
            registro_id=registro_id,
            equipamento=payload.equipamento,
            tombamento=payload.tombamento,
            tecnico=payload.tecnico,
            data_coleta=payload.data_coleta,
            origem=payload.origem,
            os_coleta=payload.os_coleta,
            localizacao=payload.localizacao,
            problema=payload.problema,
        )
        return {"sucesso": True, "mensagem": "Registro atualizado com sucesso."}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.delete("/api/equipamentos/{registro_id}")
def deletar_equipamento(registro_id: int):
    try:
        ColetaModel.excluir(registro_id)
        return {"sucesso": True, "mensagem": "Registro excluído com sucesso."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao excluir registro: {str(e)}")