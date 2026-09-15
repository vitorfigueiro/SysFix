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
        clean_text = html.escape(str(text if text is not None else "-"))
        return Paragraph(clean_text, style)

    @staticmethod
    def _gerar_pdf_buffer(
        titulo: str, dados: list, anonimizar: bool = False
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
            textColor=colors.HexColor("#1E293B"),
            spaceAfter=10,
        )

        meta_style = ParagraphStyle(
            "MetaStyle",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#64748B"),
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
        elements.append(
            Paragraph(
                f"Emitido em: {datetime.now().strftime('%d/%m/%Y às %H:%M')}",
                meta_style,
            )
        )
        elements.append(Spacer(1, 10))

        headers = [
            "ID",
            "Equipamento",
            "Tomb.",
            "Téc. Coleta",
            "Data",
            "Origem",
            "Status",
            "Custo",
            "Resolução",
            "Téc. Entrega",
        ]
        table_data = [
            [
                PDFReportGenerator._safe_paragraph(h, cell_header_style)
                for h in headers
            ]
        ]

        for reg in dados:
            # Formatação de valores financeiros
            raw_valor = reg.get("valor") or reg.get("valor_custo") or 0.0
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
                except Exception:
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

            table_data.append([
                PDFReportGenerator._safe_paragraph(
                    reg.get("id"), cell_style
                ),
                PDFReportGenerator._safe_paragraph(
                    reg.get("equipamento"), cell_style
                ),
                PDFReportGenerator._safe_paragraph(
                    reg.get("tombamento"), cell_style
                ),
                PDFReportGenerator._safe_paragraph(tec_coleta, cell_style),
                PDFReportGenerator._safe_paragraph(data_fmt, cell_style),
                PDFReportGenerator._safe_paragraph(
                    reg.get("origem"), cell_style
                ),
                PDFReportGenerator._safe_paragraph(
                    reg.get("status"), cell_style
                ),
                PDFReportGenerator._safe_paragraph(val_fmt, cell_style),
                PDFReportGenerator._safe_paragraph(
                    resolucao_txt, cell_style
                ),
                PDFReportGenerator._safe_paragraph(tec_entrega, cell_style),
            ])

        # Larguras totalizam 752pt
        col_widths = [28, 100, 55, 80, 55, 90, 60, 60, 144, 80]
        t = Table(table_data, colWidths=col_widths, repeatRows=1)

        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
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
                    colors.HexColor("#F8FAFC"),
                ))

        t.setStyle(TableStyle(t_style))
        elements.append(t)

        doc.build(elements)
        buffer.seek(0)
        return buffer

    @classmethod
    def relatorio_por_mes(
        cls, mes: int, ano: int, anonimizar: bool = False
    ) -> io.BytesIO:
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
        dados = ColetaModel.buscar_por_mes_ano(mes, ano)
        titulo = f"Relatório de Equipamentos - {nome_meses[mes]} / {ano}"
        return cls._gerar_pdf_buffer(titulo, dados, anonimizar=anonimizar)

    @classmethod
    def relatorio_pendentes(cls, anonimizar: bool = False) -> io.BytesIO:
        dados = ColetaModel.buscar_nao_finalizados_mes_atual()
        titulo = "Relatório de Equipamentos Pendentes / Em Manutenção"
        return cls._gerar_pdf_buffer(titulo, dados, anonimizar=anonimizar)

    @classmethod
    def relatorio_geral(cls, anonimizar: bool = False) -> io.BytesIO:
        dados = ColetaModel.buscar_todos()
        titulo = "Relatório Geral de Coletas e Manutenções"
        return cls._gerar_pdf_buffer(titulo, dados, anonimizar=anonimizar)