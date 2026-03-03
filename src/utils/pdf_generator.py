from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from src.models.schemas import DueDiligenceReport
import io

def generate_pdf(report: DueDiligenceReport) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            topMargin=20*mm, bottomMargin=20*mm,
                            leftMargin=20*mm, rightMargin=20*mm)
    styles = getSampleStyleSheet()
    story = []

    # Title
    title_style = ParagraphStyle('Title', parent=styles['Title'],
                                  fontSize=18, spaceAfter=6)
    story.append(Paragraph(f"FinSight AI: {report.company_name}", title_style))
    story.append(Paragraph(f"Report Date: {report.report_date}", styles['Normal']))
    story.append(Spacer(1, 8*mm))

    # Signal + Confidence
    signal_color = {"STRONG_BUY": "green", "BUY": "green",
                    "HOLD": "orange", "SELL": "red", "STRONG_SELL": "red"}
    col = signal_color.get(report.investment_signal.value, "black")
    story.append(Paragraph(
        f'<b>Signal:</b> <font color="{col}">{report.investment_signal.value}</font>  '
        f'&nbsp;&nbsp; <b>Confidence:</b> {report.confidence_score:.0%}',
        styles['Normal']
    ))
    story.append(Spacer(1, 6*mm))

    # Executive Summary
    story.append(Paragraph("Executive Summary", styles['Heading2']))
    story.append(Paragraph(report.executive_summary, styles['Normal']))
    story.append(Spacer(1, 6*mm))

    # Financial Snapshot
    snap = report.financial_snapshot
    story.append(Paragraph("Financial Snapshot", styles['Heading2']))
    fin_data = [
        ["Revenue Trend", snap.revenue_trend],
        ["Profitability", snap.profitability_summary],
        ["Debt", snap.debt_assessment],
    ] + [[k, v] for k, v in snap.key_metrics.items()]
    t = Table(fin_data, colWidths=[60*mm, 110*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1f4e79')),
        ('TEXTCOLOR', (0,0), (0,-1), colors.white),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#2e74b5')),
        ('TEXTCOLOR', (0,0), (0,-1), colors.white),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 6*mm))

    # Risk Factors
    story.append(Paragraph("Risk Factors", styles['Heading2']))
    sev_colors = {'CRITICAL': '#c00000', 'HIGH': '#e36c09',
                  'MEDIUM': '#3f6ebf', 'LOW': '#375623'}
    for risk in report.risk_factors:
        col = sev_colors.get(risk.severity.value, '#000000')
        story.append(Paragraph(
            f'<font color="{col}"><b>[{risk.severity.value}]</b></font> '
            f'<b>{risk.category}</b>: {risk.description}',
            styles['Normal']
        ))
        story.append(Paragraph(f'<i>Source: {risk.source_citation}</i>',
                                styles['Normal']))
        story.append(Spacer(1, 2*mm))

    # Competitive Position
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("Competitive Position", styles['Heading2']))
    story.append(Paragraph(report.competitive_position, styles['Normal']))

    # Recent Developments
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("Recent Developments", styles['Heading2']))
    for dev in report.recent_developments:
        story.append(Paragraph(f"• {dev}", styles['Normal']))

    # Disclaimer
    story.append(Spacer(1, 8*mm))
    disclaimer_style = ParagraphStyle('Disclaimer', parent=styles['Normal'],
                                       fontSize=8, textColor=colors.grey)
    story.append(Paragraph(report.disclaimer, disclaimer_style))

    doc.build(story)
    return buffer.getvalue()