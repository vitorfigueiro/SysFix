"""
Módulo para centralizar e formatar mensagens de erro e sucesso amigáveis do SysFix.
"""

from typing import Any, Dict, Optional

# Mapeamento de erros técnicos para textos amigáveis e seus respectivos códigos HTTP
MENSAGENS_ERRO: Dict[str, tuple[str, int]] = {
    "NOT_FOUND": ("Nenhum registro foi encontrado para o item solicitado.", 404),
    "MISSING_FIELDS": ("Por favor, preencha todos os campos obrigatórios.", 400),
    "DB_ERROR": ("Tivemos um problema ao acessar o banco de dados. Tente novamente em instantes.", 500),
    "INVALID_DATE": ("A data informada é inválida. Por favor, verifique o formato.", 400),
    "INVALID_VALUE": ("O valor numérico informado é inválido.", 400),
    "BATCH_ERROR": ("Ocorreu um erro ao processar a inserção em lote. Nenhuma alteração foi salva.", 500),
    "UNEXPECTED_ERROR": ("Ops! Ocorreu um erro inesperado no sistema. Caso persista, entre em contato com o suporte.", 500),
}

# Mapeamento de operações de sucesso
MENSAGENS_SUCESSO: Dict[str, tuple[str, int]] = {
    "CREATED": ("Equipamento/coleta registrado(a) com sucesso!", 201),
    "BATCH_CREATED": ("Todos os registros em lote foram salvos com sucesso!", 201),
    "UPDATED": ("Registro atualizado com sucesso!", 200),
    "DELETED": ("Registro removido com sucesso!", 200),
    "FETCHED": ("Dados recuperados com sucesso!", 200),
}


def obter_mensagem_erro(
    chave: str,
    detalhe_tecnico: Optional[str] = None,
    incluir_codigo_http: bool = False,
) -> Dict[str, Any]:
    """
    Retorna um dicionário padronizado com mensagem de erro amigável,
    chave identificadora e detalhes técnicos para log/debug.
    """
    mensagem, codigo_http = MENSAGENS_ERRO.get(chave, MENSAGENS_ERRO["UNEXPECTED_ERROR"])

    resposta = {
        "status": "erro",
        "codigo": chave if chave in MENSAGENS_ERRO else "UNEXPECTED_ERROR",
        "mensagem": mensagem,
    }

    if detalhe_tecnico:
        resposta["detalhe_tecnico"] = str(detalhe_tecnico)

    if incluir_codigo_http:
        resposta["status_code"] = codigo_http

    return resposta


def obter_mensagem_sucesso(
    chave: str = "CREATED",
    dados: Optional[Any] = None,
    incluir_codigo_http: bool = False,
) -> Dict[str, Any]:
    """
    Retorna um dicionário padronizado para respostas de sucesso do sistema,
    podendo anexar o payload de dados retornados e o código de status HTTP.
    """
    mensagem, codigo_http = MENSAGENS_SUCESSO.get(chave, MENSAGENS_SUCESSO["CREATED"])

    resposta = {
        "status": "sucesso",
        "mensagem": mensagem,
    }

    if dados is not None:
        resposta["dados"] = dados

    if incluir_codigo_http:
        resposta["status_code"] = codigo_http

    return resposta