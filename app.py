import os
import json
from datetime import datetime, date
from typing import Optional
from fastapi import FastAPI, Request, Form, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# Importa a estrutura de banco existente
from database import init_db
from models import ColetaModel
from security import SecurityValidator

ADMIN_PASSWORD = "admin123"

app = FastAPI(title="Gestão de Equipamentos Web", version="2.0.0")

# Configuração de Arquivos Estáticos e Templates
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup_event():
    init_db()


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

    lista = []
    for r in regs:
        lista.append({
            "id": r[0],
            "equipamento": r[1],
            "tombamento": r[2],
            "tecnico_coleta": r[3],
            "data_coleta": str(r[4]) if r[4] else None,
            "origem": r[5],
            "os_coleta": r[6],
            "localizacao": r[7],
            "problema": r[8],
            "status": r[9] if len(r) > 9 and r[9] else "Pendente",
        })
    return JSONResponse(content=lista)


@app.get("/api/equipamentos/{registro_id}")
def obter_equipamento(registro_id: int):
    dados = ColetaModel.buscar_por_id(registro_id)
    if not dados:
        raise HTTPException(status_code=404, detail="Registro não encontrado")
    
    # Formatação de campos para JSON
    for key, val in dados.items():
        if isinstance(val, (date, datetime)):
            dados[key] = str(val)
        elif val is None:
            dados[key] = ""
            
    return JSONResponse(content=dados)


@app.post("/api/equipamentos/entrada")
def registrar_entrada(
    equipamento: str = Form(...),
    tombamento: str = Form(...),
    tecnico_coleta: str = Form(...),
    data_coleta: str = Form(...),
    origem: str = Form(...),
    os_coleta: str = Form(...),
    localizacao: str = Form(...),
    problema: str = Form(""),
):
    try:
        reg_id = ColetaModel.registrar_entrada(
            equipamento, tombamento, tecnico_coleta, data_coleta,
            origem, os_coleta, localizacao, problema
        )
        return {"success": True, "id": reg_id, "message": "Entrada registrada com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/equipamentos/{registro_id}/entrada")
def atualizar_entrada(
    registro_id: int,
    equipamento: str = Form(...),
    tombamento: str = Form(...),
    tecnico_coleta: str = Form(...),
    data_coleta: str = Form(...),
    origem: str = Form(...),
    os_coleta: str = Form(...),
    localizacao: str = Form(...),
    problema: str = Form(""),
    admin_password: str = Form(...),
):
    if admin_password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Senha de administrador incorreta!")

    try:
        ColetaModel.atualizar_entrada(
            registro_id, equipamento, tombamento, tecnico_coleta,
            data_coleta, origem, os_coleta, localizacao, problema
        )
        return {"success": True, "message": "Dados de entrada atualizados!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/equipamentos/{registro_id}/saida")
def registrar_saida(
    registro_id: int,
    tecnico_entrega: str = Form(...),
    data_entrega: str = Form(...),
    os_entrega: str = Form(...),
    status_custo: str = Form(...),
    valor_custo: str = Form("0.00"),
    resolucao: str = Form(""),
    laudado: str = Form("Não"),
):
    try:
        ColetaModel.registrar_saida(
            registro_id, tecnico_entrega, data_entrega, os_entrega,
            status_custo, valor_custo, resolucao, laudado
        )
        return {"success": True, "message": "Saída registrada com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/equipamentos/{registro_id}")
def excluir_registro(registro_id: int, admin_password: str = Form(...)):
    if admin_password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Senha de administrador incorreta!")

    try:
        ColetaModel.excluir(registro_id)
        return {"success": True, "message": "Registro excluído com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/relatorios/mensal")
def gerar_relatorio_mensal(mes: int = Form(...), ano: int = Form(...)):
    fpath = f"/tmp/relatorio_coletas_{ano}_{mes:02d}.pdf"
    try:
        from reports import PDFReportGenerator
        PDFReportGenerator.gerar_relatorio_mensal(mes, ano, fpath)
        return FileResponse(
            path=fpath,
            filename=f"relatorio_coletas_{ano}_{mes:02d}.pdf",
            media_type="application/pdf"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar PDF: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)