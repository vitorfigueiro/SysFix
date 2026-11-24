from datetime import datetime
import html
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models import ColetaModel
from security import SecurityValidator


class PDFReportGenerator:

    @staticmethod
    def _safe_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
        """Sanitiza caracteres especiais de XML/HTML para evitar quebras no ReportLab."""
        texto = "" if text is None else str(text).strip()
        clean_text = html.escape(texto or "-")
        return Paragraph(clean_text, style)

    @staticmethod
    def _gerar_pdf_buffer(
        titulo: str,
        dados: list,
        anonimizar: bool = False,
        ocultar_custos: bool = False,
    ) -> io.BytesIO:
        """Gera o arquivo PDF diretamente em um buffer de memória (BytesIO)."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(letter),
            rightMargin=20,
            leftMargin=20,
            topMargin=20,
            bottomMargin=20,
        )
        elements = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "TitleStyle",
            parent=styles["Heading1"],
            fontSize=14,
            leading=18,
            alignment=1,
            textColor=colors.HexColor("#2E1065"),
            spaceAfter=10,
        )

        meta_style = ParagraphStyle(
            "MetaStyle",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#6B5E8E"),
            alignment=2,
        )

        cell_header_style = ParagraphStyle(
            "CellHeader",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            fontName="Helvetica-Bold",
            textColor=colors.whitesmoke,
        )

        cell_style = ParagraphStyle(
            "CellStyle",
            parent=styles["Normal"],
            fontSize=7.5,
            leading=9.5,
            textColor=colors.HexColor("#1E293B"),
        )

        elements.append(Paragraph(f"<b>{html.escape(titulo)}</b>", title_style))

        total_registros = len(dados)
        total_entregues = sum(
            1
            for reg in dados
            if str(reg.get("status") or "").strip().lower() in {"entregue", "finalizado"}
        )
        total_pendentes = total_registros - total_entregues
        custo_total = 0.0
        for reg in dados:
            raw_valor_resumo = reg.get("valor_custo")
            if raw_valor_resumo is None or raw_valor_resumo == "":
                raw_valor_resumo = reg.get("valor", 0.0)
            try:
                custo_total += float(raw_valor_resumo)
            except (ValueError, TypeError):
                pass
        custo_total_fmt = (
            f"R$ {custo_total:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

        resumo_texto = (
            f"Emitido em: {datetime.now().strftime('%d/%m/%Y às %H:%M')} | "
            f"Total: {total_registros} | Entregues: {total_entregues} | "
            f"Pendentes: {total_pendentes}"
        )
        if not ocultar_custos:
            resumo_texto += f" | Custo Total: {custo_total_fmt}"

        elements.append(Paragraph(resumo_texto, meta_style))
        elements.append(Spacer(1, 10))

        if not dados:
            empty_style = ParagraphStyle(
                "EmptyStyle",
                parent=styles["Normal"],
                fontSize=10,
                leading=14,
                alignment=1,
                textColor=colors.HexColor("#64748B"),
                spaceBefore=24,
            )
            elements.append(
                Paragraph(
                    "Nenhum registro encontrado para o filtro selecionado.",
                    empty_style,
                )
            )
            doc.build(elements)
            buffer.seek(0)
            return buffer

        headers = [
            "ID",
            "Equipamento",
            "Tomb.",
            "Téc. Coleta",
            "Data",
            "Origem",
            "Status",
        ]
        if not ocultar_custos:
            headers.append("Custo")
        headers.extend(["Resolução", "Téc. Entrega"])
        table_data = [
            [
                PDFReportGenerator._safe_paragraph(h, cell_header_style)
                for h in headers
            ]
        ]

        for reg in dados:
            # Formatação de valores financeiros
            raw_valor = reg.get("valor_custo")
            if raw_valor is None or raw_valor == "":
                raw_valor = reg.get("valor", 0.0)
            try:
                val_num = float(raw_valor)
                val_fmt = (
                    f"R$ {val_num:,.2f}"
                    .replace(",", "X")
                    .replace(".", ",")
                    .replace("X", ".")
                )
            except (ValueError, TypeError):
                val_fmt = "R$ 0,00"

            # Formatação de datas
            raw_data = str(reg.get("data_coleta") or "")
            data_fmt = "-"
            if raw_data:
                try:
                    iso_date = SecurityValidator.validar_data(raw_data)
                    data_fmt = datetime.strptime(
                        iso_date, "%Y-%m-%d"
                    ).strftime("%d/%m/%Y")
                except ValueError:
                    data_fmt = raw_data

            # Trata nomes para exibição/anonimização
            tec_coleta = str(
                reg.get("tecnico_coleta") or reg.get("tecnico") or "-"
            )
            tec_entrega = str(reg.get("tecnico_entrega") or "-")

            if anonimizar:
                tec_coleta = SecurityValidator.mask_personal_data(tec_coleta)
                tec_entrega = SecurityValidator.mask_personal_data(tec_entrega)

            resolucao_txt = (
                reg.get("resolucao") or reg.get("servico_realizado") or "-"
            )

            row_cells = [
                PDFReportGenerator._safe_paragraph(reg.get("id"), cell_style),
                PDFReportGenerator._safe_paragraph(
                    reg.get("equipamento"), cell_style
                ),
                PDFReportGenerator._safe_paragraph(
                    reg.get("tombamento"), cell_style
                ),
                PDFReportGenerator._safe_paragraph(tec_coleta, cell_style),
                PDFReportGenerator._safe_paragraph(data_fmt, cell_style),
                PDFReportGenerator._safe_paragraph(reg.get("origem"), cell_style),
                PDFReportGenerator._safe_paragraph(reg.get("status"), cell_style),
            ]
            if not ocultar_custos:
                row_cells.append(
                    PDFReportGenerator._safe_paragraph(val_fmt, cell_style)
                )
            row_cells.extend([
                PDFReportGenerator._safe_paragraph(resolucao_txt, cell_style),
                PDFReportGenerator._safe_paragraph(tec_entrega, cell_style),
            ])
            table_data.append(row_cells)

        # Larguras totalizam 752pt
        col_widths = (
            [28, 100, 55, 80, 55, 90, 60, 204, 80]
            if ocultar_custos
            else [28, 100, 55, 80, 55, 90, 60, 60, 144, 80]
        )
        t = Table(table_data, colWidths=col_widths, repeatRows=1)

        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4C1D95")),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD6FE")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]

        for i in range(1, len(table_data)):
            if i % 2 == 0:
                t_style.append((
                    "BACKGROUND",
                    (0, i),
                    (-1, i),
                    colors.HexColor("#F5F3FF"),
                ))

        t.setStyle(TableStyle(t_style))
        elements.append(t)

        doc.build(elements)
        buffer.seek(0)
        return buffer

    @classmethod
    def relatorio_por_mes(
        cls,
        mes: int,
        ano: int,
        anonimizar: bool = False,
        tenant_id: str = "default",
        nome_empresa: str = "SysFix",
        ocultar_custos: bool = False,
    ) -> io.BytesIO:
        if isinstance(mes, bool) or not isinstance(mes, int) or not (1 <= mes <= 12):
            raise ValueError("Mês inválido. Informe um valor entre 1 e 12.")
        if isinstance(ano, bool) or not isinstance(ano, int) or not (2000 <= ano <= 2100):
            raise ValueError("Ano inválido. Informe um valor entre 2000 e 2100.")

        nome_meses = [
            "",
            "Janeiro",
            "Fevereiro",
            "Março",
            "Abril",
            "Maio",
            "Junho",
            "Julho",
            "Agosto",
            "Setembro",
            "Outubro",
            "Novembro",
            "Dezembro",
        ]
        dados = ColetaModel.buscar_por_mes_ano(mes, ano, tenant_id=tenant_id)
        prefixo = f"{nome_empresa} — " if nome_empresa else ""
        titulo = f"{prefixo}Relatório de Equipamentos - {nome_meses[mes]} / {ano}"
        return cls._gerar_pdf_buffer(
            titulo,
            dados,
            anonimizar=anonimizar,
            ocultar_custos=ocultar_custos,
        )

    @classmethod
    def relatorio_pendentes(
        cls,
        anonimizar: bool = False,
        tenant_id: str = "default",
        nome_empresa: str = "SysFix",
        ocultar_custos: bool = False,
    ) -> io.BytesIO:
        dados = ColetaModel.buscar_nao_finalizados_mes_atual(tenant_id=tenant_id)
        prefixo = f"{nome_empresa} — " if nome_empresa else ""
        titulo = f"{prefixo}Relatório de Equipamentos Pendentes / Em Manutenção"
        return cls._gerar_pdf_buffer(
            titulo,
            dados,
            anonimizar=anonimizar,
            ocultar_custos=ocultar_custos,
        )

    @classmethod
    def relatorio_geral(
        cls,
        anonimizar: bool = False,
        tenant_id: str = "default",
        nome_empresa: str = "SysFix",
        ocultar_custos: bool = False,
    ) -> io.BytesIO:
        dados = ColetaModel.buscar_todos(tenant_id=tenant_id)
        prefixo = f"{nome_empresa} — " if nome_empresa else ""
        titulo = f"{prefixo}Relatório Geral de Coletas e Manutenções"
        return cls._gerar_pdf_buffer(
            titulo,
            dados,
            anonimizar=anonimizar,
            ocultar_custos=ocultar_custos,
        )