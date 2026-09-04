from database import get_connection, init_db

def testar():
    print("Conectando ao banco de testes...")
    try:
        init_db()
        print("✅ Migração executada com sucesso!")
        
        # Validação: verifica se as colunas agora existem
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='coletas';
        """)
        colunas = [row[0] for row in cursor.fetchall()]
        print("Colunas presentes na tabela 'coletas':", colunas)
        
        # Checa colunas críticas
        for col in ['os_coleta', 'os_entrega', 'data_entrega']:
            assert col in colunas, f"Erro: coluna {col} não encontrada!"
            
        print("🚀 Validação concluída! As colunas existem.")
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"❌ Falha no teste de migração: {e}")

if __name__ == "__main__":
    testar()