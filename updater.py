import os
import sys
import subprocess
import requests

# Defina aqui a versão atual instalada localmente no código
VERSAO_ATUAL = "1.0.4"

# URL pública da API do GitHub para pegar a última release
GITHUB_RELEASE_URL = "https://github.com/vitorfigueiro/controle_equipamentos/releases/tag/v1.0.4"

def verificar_e_atualizar():
    """Verifica se há uma versão mais recente no GitHub e atualiza o .exe."""
    # Garante que só executa o update se estiver rodando através do .exe compilado
    if not getattr(sys, 'frozen', False):
        print("Ambiente de desenvolvimento detectado. Pultando verificação de auto-update.")
        return

    try:
        response = requests.get(GITHUB_RELEASE_URL, timeout=5)
        if response.status_code != 200:
            return

        dados = response.json()
        # Remove o 'v' do início da tag (ex: 'v1.0.4' vira '1.0.4')
        versao_remota = dados.get("tag_name", "").replace("v", "").strip()

        # Se a versão remota do GitHub for maior que a VERSAO_ATUAL local
        if versao_remota > VERSAO_ATUAL:
            for asset in dados.get("assets", []):
                # Procura o arquivo .exe anexo na Release
                if asset["name"].endswith(".exe"):
                    download_url = asset["browser_download_url"]
                    executar_troca_exe(download_url)
                    break
    except Exception as e:
        print(f"Aviso: Não foi possível checar atualizações: {e}")

def executar_troca_exe(download_url):
    """Baixa a nova versão e executa o script batch para substituir o executável atual."""
    exe_atual = sys.executable
    diretorio = os.path.dirname(exe_atual)
    exe_novo = os.path.join(diretorio, "novo_app.exe")
    bat_script = os.path.join(diretorio, "update_script.bat")

    # 1. Faz o download do novo .exe
    response = requests.get(download_url, stream=True)
    with open(exe_novo, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    # 2. Cria um arquivo .bat temporário para substituir o arquivo antigo
    conteudo_bat = f"""@echo off
timeout /t 2 /nobreak > nul
del /f /q "{exe_atual}"
move /y "{exe_novo}" "{exe_atual}"
start "" "{exe_atual}"
del "%~f0"
"""

    with open(bat_script, "w") as f:
        f.write(conteudo_bat)

    # 3. Dispara o .bat e fecha a aplicação atual
    subprocess.Popen(["cmd.exe", "/c", bat_script], creationflags=subprocess.CREATE_NO_WINDOW)
    sys.exit(0)