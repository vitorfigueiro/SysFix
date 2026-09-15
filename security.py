from datetime import date, datetime
import re
from typing import Union


class SecurityValidator:
    # Whitelist de localizações permitidas
    LOCALIZACOES_PERMITIDAS = [
        "Bancada TI",
        "PlayLan",
        "Central Informática",
        "Aguardando Peça",
        "Bancada",
        "Sede",
    ]

    # Termos comuns que não devem ser mascarados como nome de pessoa física
    _TERMOS_IGNORAR_MASCARA = {
        "-",
        "s/n",
        "não informado",
        "nao informado",
        "geral",
        "bancada ti",
        "padrão",
    }

    # Pré-compilação de Regex e mapas de busca para alta performance
    _REGEX_ESPACOS = re.compile(r"\s+")
    _MAPA_LOCALIZACOES = {
        loc.lower(): loc for loc in LOCALIZACOES_PERMITIDAS
    }

    @staticmethod
    def sanitizar_texto(texto: Union[str, int, float, None]) -> str:
        """Limpa espaços nas extremidades e consolida múltiplos espaços em branco."""
        if texto is None:
            return ""

        texto_str = str(texto).strip()
        if not texto_str:
            return ""

        return SecurityValidator._REGEX_ESPACOS.sub(" ", texto_str).strip()

    @staticmethod
    def validate_location(location: Union[str, None]) -> str:
        """Valida a localização contra a whitelist (insensível a maiúsculas/minúsculas)."""
        if not location or not str(location).strip():
            return "Bancada TI"

        clean_loc = SecurityValidator.sanitizar_texto(location)
        clean_loc_lower = clean_loc.lower()

        if clean_loc_lower in SecurityValidator._MAPA_LOCALIZACOES:
            return SecurityValidator._MAPA_LOCALIZACOES[clean_loc_lower]

        options_str = ", ".join(SecurityValidator.LOCALIZACOES_PERMITIDAS)
        raise ValueError(
            f"Localização inválida ('{clean_loc}'). Opções permitidas: {options_str}"
        )

    @staticmethod
    def validar_data(data_input: Union[str, date, datetime, None]) -> str:
        """Converte a data de entrada para o formato ISO padrão (YYYY-MM-DD)."""
        if not data_input:
            return datetime.now().strftime("%Y-%m-%d")

        if isinstance(data_input, (date, datetime)):
            return data_input.strftime("%Y-%m-%d")

        data_str = str(data_input).strip()
        if not data_str:
            return datetime.now().strftime("%Y-%m-%d")

        # Separa a parte de data caso venha no formato datetime ISO (ex: "2026-09-15T14:30:00")
        data_limpa = data_str.replace("T", " ").split(" ")[0]

        formatos = (
            "%Y-%m-%d",  # ISO / HTML5 date input
            "%d/%m/%Y",  # Formato BR
            "%d-%m-%Y",
            "%Y/%m/%d",
        )

        for formato in formatos:
            try:
                dt = datetime.strptime(data_limpa, formato)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        raise ValueError(
            f"Data '{data_str}' em formato inválido. Use AAAA-MM-DD ou DD/MM/AAAA."
        )

    @staticmethod
    def validate_cost(value_input: Union[str, int, float, None]) -> float:
        """Normaliza e valida valores monetários (aceita R$, ponto ou vírgula decimal)."""
        if value_input is None or value_input == "":
            return 0.0

        if isinstance(value_input, (int, float)):
            val = float(value_input)
        else:
            clean_value = (
                str(value_input).replace("R$", "").replace(" ", "").strip()
            )

            # Trata notação PT-BR (ex: 1.250,50 -> 1250.50)
            if "," in clean_value:
                clean_value = clean_value.replace(".", "").replace(",", ".")

            try:
                val = float(clean_value)
            except ValueError:
                raise ValueError("Valor de custo numérico inválido.")

        if val < 0:
            raise ValueError("O valor do custo não pode ser negativo.")

        return round(val, 2)

    @staticmethod
    def mask_personal_data(name: str) -> str:
        """Anonimiza nomes próprios mantendo a inicial (ex: 'Carlos Silva' -> 'C***** S****')."""
        if not name:
            return ""

        clean_name = SecurityValidator.sanitizar_texto(name)

        # Não anonimiza placeholders do sistema
        if clean_name.lower() in SecurityValidator._TERMOS_IGNORAR_MASCARA:
            return clean_name

        parts = clean_name.split()
        masked_parts = [
            p[0] + "*" * (len(p) - 1) if len(p) > 1 else p for p in parts
        ]
        return " ".join(masked_parts)