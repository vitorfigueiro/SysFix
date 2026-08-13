import os
import sys
import subprocess
import requests

# Versão atual do seu programa
VERSAO_ATUAL_APP = "1.0.2"

# URL de um arquivo JSON público contendo a versão mais recente e o link do .exe (ex: GitHub Releases)
URL_CHECAGEM_VERSAO = "https://raw.githubusercontent.com/seu-usuario/seu-repositorio/main/version.json"


def verificar_e_atualizar():
    """Verifica se há novas atualizações e substitui o executável automaticamente."""
    try:
        response = requests.get(URL_CHECAGEM_VERSAO, timeout=5)
        if response.status_code == 200:
            dados = response.json()
            nova_versao = dados.get("version")
            download_url = dados.get("download_url")

            if nova_versao > VERSAO_ATUAL_APP:
                print(f"Nova versão encontrada: {nova_versao}. Baixando atualização...")

                # Baixa o novo .exe com nome temporário
                exe_temp = "update_temp.exe"
                r = requests.get(download_url, stream=True)
                with open(exe_temp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

                # Executa um script em lote (.bat) para substituir o .exe antigo e reiniciar a aplicação
                exe_atual = sys.argv[0]
                script_bat = "updater.bat"

                with open(script_bat, "w") as f:
                    f.write(f"""@echo off
timeout /t 2 /nobreak > nul
move /y "{exe_temp}" "{exe_atual}"
start "" "{exe_atual}"
del "%~f0"
""")

                subprocess.Popen([script_bat], shell=True)
                sys.exit()  # Encerra a aplicação atual para permitir a substituição
    except Exception as e:
        print(f"Não foi possível verificar atualizações: {e}")