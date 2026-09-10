import re
from datetime import datetime, date
from typing import Union


class SecurityValidator:
    # Whitelist atualizada com todas as opções presentes no front-end HTML
    LOCALIZACOES_PERMITIDAS = [
        "Bancada TI",
        "PlayLan",
        "Central Informática",
        "Aguardando Peça",
        "Bancada",
        "Sede",
    ]

    @staticmethod
    def sanitizar_texto(texto: Union[str, int, float, None]) -> str:
        """
        Limpa espaços extras nas extremidades de entradas de texto 
        e previne injeção de múltiplos espaços em branco.
        """
        if texto is None:
            return ""
        
        texto_str = str(texto).strip()
        if not texto_str:
            return ""
            
        # Remove múltiplos espaços em branco consecutivos
        clean = re.sub(r"\s+", " ", texto_str)
        return clean.strip()

    @staticmethod
    def validate_location(location: str) -> str:
        """Valida se a localização pertence à Whitelist e aplica valor padrão seguro."""
        if not location or not str(location).strip():
            return "Bancada TI"

        clean_loc = SecurityValidator.sanitizar_texto(location)
        
        # Busca insensível a maiúsculas/minúsculas para maior tolerância
        mapa_loc = {loc.lower(): loc for loc in SecurityValidator.LOCALIZACOES_PERMITIDAS}
        
        if clean_loc.lower() in mapa_loc:
            return mapa_loc[clean_loc.lower()]

        raise ValueError(
            f"Localização inválida ('{clean_loc}'). Opções permitidas: {', '.join(SecurityValidator.LOCALIZACOES_PERMITIDAS)}"
        )

    @staticmethod
    def validar_data(data_input: Union[str, date, datetime, None]) -> str:
        """
        Valida se a data enviada está correta e converte SEMPRE 
        para o formato ISO padrão do banco de dados (YYYY-MM-DD).
        Aceita objetos date/datetime, formatos brasileiros (DD/MM/YYYY) e ISO.
        Se a data for vazia ou nula, retorna a data atual em formato ISO.
        """
        if not data_input:
            return datetime.now().strftime("%Y-%m-%d")

        # Se já for um objeto date ou datetime do Python
        if isinstance(data_input, (date, datetime)):
            return data_input.strftime("%Y-%m-%d")

        data_str = str(data_input).strip()
        if not data_str:
            return datetime.now().strftime("%Y-%m-%d")

        # Trata separadores de data/hora ISO (ex: "2026-09-08T14:30:00" -> "2026-09-08")
        data_limpa = data_str.replace("T", " ").split(" ")[0]

        formatos = (
            "%Y-%m-%d",  # 2026-09-08 (ISO / HTML5 input)
            "%d/%m/%Y",  # 08/09/2026 (BR)
            "%d-%m-%Y",  # 08-09-2026
            "%Y/%m/%d",  # 2026/09/08
        )

        for formato in formatos:
            try:
                dt = datetime.strptime(data_limpa, formato)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        raise ValueError(
            f"Data '{data_str}' em formato inválido. Use o formato AAAA-MM-DD ou DD/MM/AAAA."
        )

    @staticmethod
    def validate_cost(value_input: Union[str, int, float, None]) -> float:
        """
        Valida e converte valores monetários nos formatos numérico ou string (R$),
        rejeitando valores negativos.
        """
        if value_input is None or value_input == "":
            return 0.0

        if isinstance(value_input, (int, float)):
            val = float(value_input)
        else:
            clean_value = str(value_input).replace("R$", "").replace(" ", "").strip()
            
            # Converte formato brasileiro (1.250,50 -> 1250.50)
            if "," in clean_value:
                clean_value = clean_value.replace(".", "").replace(",", ".")

            try:
                val = float(clean_value)
            except ValueError:
                raise ValueError("Valor de custo inválido.")

        if val < 0:
            raise ValueError("O valor do custo não pode ser negativo.")

        return round(val, 2)

    @staticmethod
    def mask_personal_data(name: str) -> str:
        """
        Anonimiza o nome de pessoas físicas ao gerar relatórios públicos (LGPD).
        Exemplo: 'Carlos Eduardo' -> 'C***** E******'
        """
        if not name:
            return ""
        
        parts = SecurityValidator.sanitizar_texto(name).split()
        masked_parts = [
            p[0] + "*" * (len(p) - 1) if len(p) > 1 else p for p in parts
        ]
        return " ".join(masked_parts)