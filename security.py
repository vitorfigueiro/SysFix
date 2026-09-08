import re
from datetime import datetime


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
    def sanitizar_texto(texto: str) -> str:
        """Limpa espaços extras nas extremidades de entradas de texto."""
        if not texto:
            return ""
        # Remove múltiplos espaços em branco consecutivos
        clean = re.sub(r"\s+", " ", str(texto))
        return clean.strip()

    @staticmethod
    def validate_location(location: str) -> str:
        """Valida se a localização pertence à Whitelist."""
        if not location:
            return "Bancada TI"  # Valor padrão amigável
            
        clean_loc = str(location).strip()
        if clean_loc not in SecurityValidator.LOCALIZACOES_PERMITIDAS:
            raise ValueError(
                f"Localização inválida ('{clean_loc}'). Opções permitidas: {', '.join(SecurityValidator.LOCALIZACOES_PERMITIDAS)}"
            )
        return clean_loc

    @staticmethod
    def validar_data(data_str: str) -> str:
        """
        Valida se a data enviada está correta e converte SEMPRE 
        para o formato ISO padrão do banco de dados (YYYY-MM-DD).
        Se a data for vazia ou nula, retorna a data atual.
        """
        if not data_str or not str(data_str).strip():
            return datetime.now().strftime("%Y-%m-%d")

        # Trata separadores de data/hora ISO (T ou espaço)
        data_limpa = str(data_str).strip().replace("T", " ").split(" ")[0]

        formatos = (
            "%Y-%m-%d",  # 2026-09-08 (ISO / HTML5 input)
            "%d/%m/%Y",  # 08/09/2026
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
    def validate_cost(value_str) -> float:
        """Valida e converte valores monetários sem permitir entradas negativas."""
        if value_str is None or value_str == "":
            return 0.0

        if isinstance(value_str, (int, float)):
            val = float(value_str)
        else:
            clean_value = str(value_str).replace("R$", "").strip()
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
        parts = str(name).strip().split()
        masked_parts = [
            p[0] + "*" * (len(p) - 1) if len(p) > 1 else p for p in parts
        ]
        return " ".join(masked_parts)