# mensagens.py
"""
Módulo para centralizar e formatar mensagens de erro e sucesso amigáveis do SysFix.
"""

# Mapeamento de erros técnicos comuns para textos amigáveis
MENSAGENS_ERRO = {
    "NOT_FOUND": "Nenhum registro foi encontrado para o item solicitado.",
    "MISSING_FIELDS": "Por favor, preencha todos os campos obrigatórios.",
    "DB_ERROR": "Tivemos um problema ao acessar o banco de dados. Tente novamente em instantes.",
    "INVALID_DATE": "A data informada é inválida. Por favor, verifique o formato.",
    "INVALID_VALUE": "O valor numérico informado é inválido.",
    "UNEXPECTED_ERROR": "Ops! Ocorreu um erro inesperado no sistema. Caso persista, entre em contato com o suporte."
}


def obter_mensagem_erro(chave: str, detalhe_tecnico: str = None) -> dict:
    """
    Retorna um dicionário padronizado com mensagem amigável e detalhes técnicos para log.
    """
    mensagem = MENSAGENS_ERRO.get(chave, MENSAGENS_ERRO["UNEXPECTED_ERROR"])
    
    resposta = {
        "status": "erro",
        "mensagem": mensagem
    }
    
    # Inclui o detalhe técnico apenas se for repassado
    if detalhe_tecnico:
        resposta["detalhe_tecnico"] = str(detalhe_tecnico)
        
    return resposta