"""Shared factual document blocks, separate template rendering. No network calls."""
import base64
import io
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

from locales import text
from services.cv_builder import BASIC, EDUCATION, EXPERIENCE

TEMPLATES = Path(__file__).resolve().parent.parent / 'templates'


def blocks(data, language):
    result = []
    for key in ('full_name', 'target_role'):
        if data.get(key):
            result.append(('name' if key == 'full_name' else 'body', data[key]))
    for key in (*BASIC[2:], 'linkedin', 'website', 'telegram'):
        if data.get(key):
            result.append(('body', text(language, 'cb_' + key) + ': ' + data[key]))
    for section in ('summary', 'experience', 'education', 'skills', 'languages', 'certifications'):
        if not data.get(section):
            continue
        entries = []
        if section in {'experience', 'education'}:
            for entry in data[section]:
                lines = [text(language, 'cb_' + key) + ': ' +
                         (text(language, 'cb_present') if key == 'end_date' and entry[key] == 'Present' else entry[key])
                         for key in (EXPERIENCE if section == 'experience' else EDUCATION) if entry.get(key)]
                if lines:
                    entries.append('\n'.join(lines))
        else:
            entries = [data[section]]
        if entries:
            result.append(('heading', text(language, 'cb_' + section)))
            result.extend(('body', entry) for entry in entries)
    return result


def plain_text(data, language='en'):
    return '\n\n'.join(value for _, value in blocks(data, language))


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


def render(data, extension, language='en', template='template_1'):
    # Template ID is never treated as a filesystem path supplied by the user.
    if template not in {'template_1', 'template_2', 'template_3'}:
        raise ValueError('cb_invalid')
    style = json.loads((TEMPLATES / template / 'style.json').read_text(encoding='utf-8'))
    if not style['enabled']:
        raise ValueError('cb_template_unavailable')
    output = io.BytesIO()
    content = blocks(data, language)
    photo = io.BytesIO(base64.b64decode(data['photo'])) if data.get('photo') else None
    if extension == 'docx':
        from docx import Document
        from docx.shared import Mm, Pt, RGBColor
        document = Document()
        document.core_properties.author = ''
        document.core_properties.title = data.get('full_name', '')
        section = document.sections[0]
        section.page_width, section.page_height = Mm(210), Mm(297)
        section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Mm(style['margin_mm'])
        normal = document.styles['Normal']
        normal.font.name, normal.font.size = 'Arial', Pt(style['font_size'])
        for kind in ('Title', 'Heading 1'):
            document.styles[kind].font.color.rgb = RGBColor(0, 0, 0)
            document.styles[kind].font.name = 'Arial'
        # Some python-docx distributions ship decorated default styles.
        for border in document.styles.element.xpath('.//w:pBdr'):
            border.getparent().remove(border)
        if photo:
            from PIL import Image as PILImage
            with PILImage.open(photo) as image:
                width, height = image.size
            photo.seek(0)
            scale = 28 / max(width, height)
            document.add_picture(photo, width=Mm(width * scale), height=Mm(height * scale))
        for kind, value in content:
            paragraph = document.add_paragraph(value, 'Title' if kind == 'name' else 'Heading 1' if kind == 'heading' else 'Normal')
            paragraph.paragraph_format.widow_control = True
        document.save(output)
    elif extension == 'pdf':
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
        font = pdf_font()
        styles = {kind: ParagraphStyle(kind, fontName=font, fontSize=size,
                                      leading=size * 1.35, spaceAfter=8,
                                      keepWithNext=kind in {'name', 'heading'})
                  for kind, size in [('name', 22), ('heading', style['heading_size']), ('body', style['font_size'])]}
        story = []
        if photo:
            from PIL import Image as PILImage
            with PILImage.open(photo) as image:
                width, height = image.size
            photo.seek(0)
            scale = 28 * mm / max(width, height)
            story.extend([Image(photo, width=width * scale, height=height * scale), Spacer(1, 8)])
        for kind, value in content:
            story.append(Paragraph(escape(value).replace('\n', '<br/>'), styles[kind]))
        margin = style['margin_mm'] * mm
        SimpleDocTemplate(output, pagesize=A4, leftMargin=margin, rightMargin=margin,
                          topMargin=margin, bottomMargin=margin, title=data.get('full_name', ''), author='').build(story)
    else:
        raise ValueError('cb_invalid')
    return output.getvalue()
