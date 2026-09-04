import io
from datetime import datetime
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from models import ColetaModel

class PDFReportGenerator:

    @staticmethod
    def _gerar_pdf_buffer(titulo, dados):
        """Gera o arquivo PDF diretamente em um buffer de memória (BytesIO)."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, 
            pagesize=landscape(letter),
            rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20
        )
        elements = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Heading1'],
            fontSize=15,
            alignment=1,
            spaceAfter=15
        )

        elements.append(Paragraph(f"<b>{titulo}</b>", title_style))
        elements.append(Paragraph(f"Emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal']))
        elements.append(Spacer(1, 15))

        table_data = [["ID", "Equipamento", "Tomb.", "Téc. Coleta", "Data", "Origem", "Status", "Custo (R$)", "Resolução", "Téc. Entrega"]]
        
        cell_style = ParagraphStyle('CellStyle', parent=styles['Normal'], fontSize=8, leading=10)

        for reg in dados:
            # Formatação segura de valores financeiros
            raw_valor = reg.get('valor_custo') or 0.0
            try:
                val_num = float(raw_valor)
                val_fmt = f"R$ {val_num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            except (ValueError, TypeError):
                val_fmt = "R$ 0,00"

            # Formatação segura de datas (YYYY-MM-DD)
            raw_data = str(reg.get('data_coleta') or '')
            if len(raw_data) >= 10:
                try:
                    data_fmt = datetime.strptime(raw_data[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
                except ValueError:
                    data_fmt = raw_data
            else:
                data_fmt = raw_data or "-"
            
            table_data.append([
                str(reg.get('id', '')),
                Paragraph(str(reg.get('equipamento') or '-'), cell_style),
                Paragraph(str(reg.get('tombamento') or '-'), cell_style),
                Paragraph(str(reg.get('tecnico_coleta') or '-'), cell_style),
                data_fmt,
                Paragraph(str(reg.get('origem') or '-'), cell_style),
                str(reg.get('status') or '-'),
                val_fmt,
                Paragraph(str(reg.get('resolucao') or '-'), cell_style),
                Paragraph(str(reg.get('tecnico_entrega') or '-'), cell_style)
            ])

        t = Table(table_data, colWidths=[30, 110, 60, 85, 60, 95, 65, 65, 110, 85])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2C3E50")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#BDC3C7")),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ]))
        
        elements.append(t)
        doc.build(elements)
        buffer.seek(0)
        return buffer

    @classmethod
    def relatorio_por_mes(cls, mes: int, ano: int):
        nome_meses = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
        dados = ColetaModel.buscar_por_mes_ano(mes, ano)
        titulo = f"Relatório de Equipamentos - {nome_meses[mes]} / {ano}"
        return cls._gerar_pdf_buffer(titulo, dados)