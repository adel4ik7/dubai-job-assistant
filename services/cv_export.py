"""Factual sections with interchangeable local PDF/DOCX layouts."""
import base64
import io
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

from locales import text
from services.cv_builder import EDUCATION, EXPERIENCE

TEMPLATES = Path(__file__).resolve().parent.parent / 'templates'
TEMPLATE_IDS = ('template_1', 'template_2', 'template_3')
CLASSIC_ORDER = ('identity', 'contact', 'summary', 'experience', 'skills', 'education',
                 'languages', 'certifications', 'additional', 'references')


def template_style(template):
    if template not in TEMPLATE_IDS:
        raise ValueError('cb_invalid')
    return json.loads((TEMPLATES / template / 'style.json').read_text(encoding='utf-8'))


def label(language, key):
    return text(language, 'cb_' + key).replace(' (optional)', '').replace(' (необязательно)', '')


def document_sections(data, language):
    sections = {}
    sections['identity'] = [('name' if key == 'full_name' else 'body', data[key])
                            for key in ('full_name', 'target_role') if data.get(key)]
    contact = [label(language, key) + ': ' + data[key] for key in
               ('phone', 'email', 'current_location', 'linkedin', 'website', 'telegram') if data.get(key)]
    sections['contact'] = [('heading', label(language, 'contact'))] + [('body', line) for line in contact] if contact else []
    for section in ('summary', 'experience', 'education', 'skills', 'languages', 'certifications', 'references'):
        entries = []
        if section in {'experience', 'education'}:
            for entry in data.get(section, []):
                keys = EXPERIENCE[:-1] if section == 'experience' else EDUCATION
                lines = [label(language, key) + ': ' +
                         (text(language, 'cb_present') if key == 'end_date' and entry[key] == 'Present' else entry[key])
                         for key in keys if entry.get(key)]
                if lines:
                    entries.append(('entry', '\n'.join(lines)))
                if entry.get('description'):
                    entries.append(('body', entry['description']))
        elif data.get(section):
            entries = [('body', data[section])]
        sections[section] = [('heading', label(language, section)), *entries] if entries else []
    additional = [label(language, key) + ': ' + data[key] for key in ('nationality', 'visa_status') if data.get(key)]
    if data.get('additional'):
        additional.append(data['additional'])
    sections['additional'] = [('heading', label(language, 'additional')), *[('body', s) for s in additional]] if additional else []
    return sections


def blocks(data, language):
    sections = document_sections(data, language)
    return [block for section in CLASSIC_ORDER for block in sections[section]]


def plain_text(data, language='en'):
    return '\n\n'.join(value for _, value in blocks(data, language))


def columns(data, language, style):
    sections = document_sections(data, language)
    if style['layout'] == 'modern':
        left = ('identity', 'summary', 'skills', 'languages', 'additional')
        right = ('contact', 'experience', 'education', 'certifications', 'references')
    else:
        left = ('identity', 'summary', 'skills', 'contact', 'languages', 'additional')
        right = ('experience', 'education', 'certifications', 'references')
    return ([block for section in left for block in sections[section]],
            [block for section in right for block in sections[section]])


def photo_stream(data):
    if not data.get('photo'):
        return None
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(base64.b64decode(data['photo']))) as original:
            image = ImageOps.fit(ImageOps.exif_transpose(original).convert('RGB'), (400, 400))
            output = io.BytesIO()
            image.save(output, 'JPEG', quality=85)
            output.seek(0)
            return output
    except (ValueError, OSError):
        return None


def pdf_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if 'CVUnicode' not in pdfmetrics.getRegisteredFontNames():
        candidates = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arial.ttf',
                      Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                      Path('/System/Library/Fonts/Supplemental/Arial.ttf')]
        font = next((p for p in candidates if p.is_file()), None)
        if font is None:
            raise ValueError('cb_font_error')
        pdfmetrics.registerFont(TTFont('CVUnicode', str(font)))
    return 'CVUnicode'


def pdf_column(content, style, x_mm, width_mm, photo=None):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
    font = pdf_font()
    styles = {kind: ParagraphStyle(kind, fontName=font, fontSize=size, leading=size * 1.35,
              spaceBefore=7 if kind == 'heading' else 0, spaceAfter=6,
              textColor=HexColor(style['accent']) if kind in {'heading', 'name'} else HexColor('#111111'),
              keepWithNext=kind in {'heading', 'name'}, splitLongWords=True,
              allowWidows=0, allowOrphans=0)
              for kind, size in [('name', 20), ('heading', style['heading_size']),
                                 ('body', style['font_size']), ('entry', style['font_size'])]}
    story = []
    if photo:
        story.extend([Image(photo, width=30*mm, height=30*mm, hAlign='LEFT'), Spacer(1, 10)])
    for kind, value in content:
        story.append(Paragraph(escape(value).replace('\n', '<br/>'), styles[kind]))
    if not story:
        story.append(Spacer(1, 1))
    output = io.BytesIO()
    margin = style['margin_mm'] * mm
    SimpleDocTemplate(output, pagesize=A4, leftMargin=x_mm*mm,
                      rightMargin=(210-x_mm-width_mm)*mm, topMargin=margin,
                      bottomMargin=margin, author='').build(story)
    return output.getvalue()


def render_pdf(data, language, style):
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    margin = style['margin_mm']
    if style['layout'] == 'classic':
        return pdf_column(blocks(data, language), style, margin, 210-2*margin)
    left, right = columns(data, language, style)
    side, gap = style['sidebar_mm'], style['gap_mm']
    streams = [pdf_column(left, style, margin, side, photo_stream(data)),
               pdf_column(right, style, margin+side+gap, 210-2*margin-side-gap)]
    readers = [PdfReader(io.BytesIO(stream)) for stream in streams]
    writer = PdfWriter()
    # Independently flowing columns, merged as selectable text/vector pages.
    for index in range(max(len(reader.pages) for reader in readers)):
        background = io.BytesIO()
        drawing = canvas.Canvas(background, pagesize=A4)
        if style['layout'] == 'modern':
            drawing.setFillColor(HexColor(style['sidebar_background']))
            drawing.rect((margin-3)*mm, margin*mm, (side+6)*mm, (297-2*margin)*mm, fill=1, stroke=0)
            drawing.setFillColor(HexColor(style['accent']))
            drawing.rect(margin*mm, (297-margin+3)*mm, (210-2*margin)*mm, 2*mm, fill=1, stroke=0)
        else:
            drawing.setStrokeColor(HexColor('#BBBBBB'))
            drawing.line((margin+side+gap/2)*mm, margin*mm, (margin+side+gap/2)*mm, (297-margin)*mm)
        drawing.showPage()
        drawing.save()
        page = writer.add_page(PdfReader(io.BytesIO(background.getvalue())).pages[0])
        for reader in readers:
            if index < len(reader.pages):
                page.merge_page(reader.pages[index])
    writer.add_metadata({'/Title': data.get('full_name', ''), '/Author': ''})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def render_docx(data, language, style):
    from docx import Document
    from docx.shared import Mm, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    document = Document()
    document.core_properties.author = ''
    document.core_properties.title = data.get('full_name', '')
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Mm(style['margin_mm'])
    for name, size in [('Normal', style['font_size']), ('Title', 20), ('Heading 1', style['heading_size'])]:
        fmt = document.styles[name]
        fmt.font.name, fmt.font.size = 'Arial', Pt(size)
        fmt.font.color.rgb = RGBColor(0, 0, 0)
        fmt.paragraph_format.space_after = Pt(6)
        fmt.paragraph_format.space_before = Pt(7 if name == 'Heading 1' else 0)
    for border in document.styles.element.xpath('.//w:pBdr'):
        border.getparent().remove(border)

    def fill(container, content, photo=None):
        if photo:
            container.add_paragraph().add_run().add_picture(photo, width=Mm(30), height=Mm(30))
        for kind, value in content:
            paragraph = container.add_paragraph(value, 'Title' if kind == 'name' else 'Heading 1' if kind == 'heading' else 'Normal')
            paragraph.paragraph_format.widow_control = True
            paragraph.paragraph_format.keep_with_next = kind in {'name', 'heading'}
            paragraph.paragraph_format.keep_together = kind == 'entry'
    if style['layout'] == 'classic':
        fill(document, blocks(data, language))
    else:
        table = document.add_table(rows=1, cols=3)
        table.autofit = False
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        width = 210 - 2*style['margin_mm']
        widths = (style['sidebar_mm'], style['gap_mm'], width-style['sidebar_mm']-style['gap_mm'])
        for column, cell, size in zip(table.columns, table.rows[0].cells, widths):
            column.width = cell.width = Mm(size)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.space_before = Pt(0)
                paragraph.paragraph_format.line_spacing = Pt(1)
                paragraph.add_run().font.size = Pt(1)
        borders = OxmlElement('w:tblBorders')
        for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            item = OxmlElement('w:' + edge)
            item.set(qn('w:val'), 'nil')
            borders.append(item)
        table._tbl.tblPr.append(borders)
        if style['layout'] == 'modern':
            shade = OxmlElement('w:shd')
            shade.set(qn('w:fill'), style['sidebar_background'].lstrip('#'))
            table.cell(0, 0)._tc.get_or_add_tcPr().append(shade)
        left, right = columns(data, language, style)
        fill(table.cell(0, 0), left, photo_stream(data))
        fill(table.cell(0, 2), right)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def render(data, extension, language='en', template='template_3'):
    style = template_style(template)
    if extension == 'pdf':
        return render_pdf(data, language, style)
    if extension == 'docx':
        return render_docx(data, language, style)
    raise ValueError('cb_invalid')
