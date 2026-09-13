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


def photo_stream(data):
    if not data.get('photo'):
        return None
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(base64.b64decode(data['photo']))) as original:
            image = ImageOps.fit(ImageOps.exif_transpose(original).convert('RGB'), (400, 400), centering=(0.5, 0.35))
            output = io.BytesIO()
            image.save(output, 'JPEG', quality=85)
            output.seek(0)
            return output
    except (ValueError, OSError):
        return None



def visual_sections(data, language):
    """Presentation-only hierarchy; plain_text/active-CV evidence stays unchanged."""
    sections = document_sections(data, language)
    sections['identity'] = [('name', data['full_name'])] if data.get('full_name') else []
    if data.get('target_role'):
        sections['identity'].append(('role', data['target_role']))
    for section in ('experience', 'education'):
        entries = []
        for entry in data.get(section, []):
            if section == 'experience':
                title, organization = entry.get('role'), entry.get('company')
                dates = ' - '.join(text(language, 'cb_present') if entry.get(k) == 'Present' else entry[k]
                                   for k in ('start_date', 'end_date') if entry.get(k))
                detail = entry.get('description')
            else:
                title, organization = entry.get('qualification'), entry.get('institution')
                dates, detail = entry.get('dates'), entry.get('field')
            item = []
            if title:
                item.append(('entry_title', title))
            if organization:
                item.append(('organization', organization))
            meta = ' | '.join(v for v in (dates, entry.get('location')) if v)
            if meta:
                item.append(('meta', meta))
            if detail:
                item.append(('body', detail))
            if item:
                entries.extend(item + [('space', '')])
        sections[section] = [('heading', label(language, section)), *entries] if entries else []
    return sections



def classic_content(data, language):
    sections = visual_sections(data, language)
    # Compact factual lists, not a second data model or rewritten user content.
    for key in ('contact', 'skills', 'languages'):
        if sections[key]:
            values = [value.replace('\n', '; ') for kind, value in sections[key] if kind != 'heading']
            sections[key] = [sections[key][0], ('body', ' | '.join(values))]
    return [b for key in CLASSIC_ORDER if key != 'identity' for b in sections[key]]


def visual_columns(data, language, style):
    sections = visual_sections(data, language)
    if style['layout'] == 'modern':
        left = ('summary', 'skills', 'languages', 'additional')
        right = ('contact', 'experience', 'education', 'certifications', 'references')
    else:
        left = ('identity', 'summary', 'skills', 'contact', 'languages', 'additional')
        right = ('experience', 'education', 'certifications', 'references')
    return ([b for key in left for b in sections[key]], [b for key in right for b in sections[key]])


def pdf_font(bold=False):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    name = 'CVUnicodeBold' if bold else 'CVUnicode'
    if name not in pdfmetrics.getRegisteredFontNames():
        candidates = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / ('arialbd.ttf' if bold else 'arial.ttf'),
                      Path('/usr/share/fonts/truetype/dejavu') / ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'),
                      Path('/System/Library/Fonts/Supplemental') / ('Arial Bold.ttf' if bold else 'Arial.ttf')]
        font = next((p for p in candidates if p.is_file()), None)
        if font is None:
            raise ValueError('cb_font_error')
        pdfmetrics.registerFont(TTFont(name, str(font)))
    return name


def pdf_styles(style):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.colors import HexColor
    styles = {}
    for kind, size in [('name', 25), ('role', 11), ('heading', style['heading_size']),
                       ('body', style['font_size']), ('entry_title', 11), ('organization', 10), ('meta', 9)]:
        styles[kind] = ParagraphStyle(kind, fontName=pdf_font(kind in {'name', 'heading', 'entry_title', 'organization'}),
            fontSize=size, leading=size*1.35, spaceAfter=5 if kind == 'body' else 4,
            spaceBefore=(8 if style['layout'] == 'classic' else 12) if kind == 'heading' else 0,
            textColor=HexColor(style['accent'] if kind in {'name', 'role', 'heading', 'entry_title'} else '#303A43'),
            keepWithNext=kind in {'heading', 'name', 'entry_title', 'organization', 'meta'},
            splitLongWords=True, allowWidows=0, allowOrphans=0)
    heading = styles['heading']
    heading.borderWidth = 0
    heading.borderColor = HexColor(style['accent'])
    heading.borderPadding = (5, 5, 5, 5)
    heading.spaceAfter = 8 if style['layout'] == 'classic' else 10
    if style['layout'] == 'professional':
        heading.backColor = HexColor('#E5EBEF')
        heading.borderWidth = 0
    return styles


def pdf_story(content, style, photo=None):
    from reportlab.platypus import Paragraph, Spacer, Image
    from reportlab.lib.units import mm
    styles = pdf_styles(style)
    class SectionHeading(Paragraph):
        def draw(self):
            super().draw()
            if style['layout'] != 'professional':
                from reportlab.lib.colors import HexColor
                self.canv.saveState()
                self.canv.setStrokeColor(HexColor(style['accent']))
                self.canv.setLineWidth(0.5)
                self.canv.line(0, -4, self.width, -4)
                self.canv.restoreState()
    story = []
    if photo:
        story += [Image(photo, width=style['sidebar_mm']*mm, height=style['sidebar_mm']*mm), Spacer(1, 12)]
    for i, (kind, value) in enumerate(content):
        if kind == 'space':
            story.append(Spacer(1, 6))
            continue
        fmt = styles[kind]
        # Only keep metadata with the next block when that block belongs to this item.
        if kind in {'entry_title', 'organization', 'meta'} and (i+1 == len(content) or content[i+1][0] == 'space'):
            fmt = fmt.clone(kind + '_last', keepWithNext=False)
        paragraph = SectionHeading if kind == 'heading' else Paragraph
        story.append(paragraph(escape(value).replace('\n', '<br/>'), fmt))
    return story or [Spacer(1, 1)]


def pdf_column(content, style, first_top, x, width, photo=None, narrow_pages=None):
    """First-page header clearance; full-width continuation after sidebar ends."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame
    output = io.BytesIO()
    margin = style['margin_mm']
    def frame(left, top, w):
        return Frame(left*mm, margin*mm, w*mm, (297-top-margin)*mm,
                     leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    templates = [PageTemplate(id='first', frames=[frame(x, first_top, width)])]
    if narrow_pages is None:
        templates.append(PageTemplate(id='rest', frames=[frame(x, margin, width)]))
        templates[0].autoNextPageTemplate = 'rest'
    else:
        last = templates[0]
        for page in range(2, max(2, narrow_pages+1)):
            item = PageTemplate(id=f'column{page}', frames=[frame(x, margin, width)])
            last.autoNextPageTemplate = item.id
            templates.append(item)
            last = item
        last.autoNextPageTemplate = 'full'
        templates.append(PageTemplate(id='full', frames=[frame(margin, margin, 210-2*margin)]))
    document = BaseDocTemplate(output, pagesize=A4, pageTemplates=templates, author='')
    document.build(pdf_story(content, style, photo))
    return output.getvalue()


def pdf_identity(data, style, photo, modern):
    """Measured first-page identity: long names grow the header instead of clipping."""
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    margin = style['margin_mm']
    x = margin + (style.get('sidebar_mm', 34) + style.get('gap_mm', 8) if photo else 0)
    width = (210-margin-x)*mm
    paragraphs = []
    height = 0
    for key, size in (('full_name', 28 if modern else 27), ('target_role', 11)):
        if data.get(key):
            fmt = ParagraphStyle(key, fontName=pdf_font(key == 'full_name'), fontSize=size, leading=size*1.15,
                                 textColor=HexColor('#FFFFFF' if modern else style['accent']), splitLongWords=True)
            p = Paragraph(escape(data[key]).replace('\n', '<br/>'), fmt)
            _, h = p.wrap(width, 10000)
            paragraphs.append((p, h))
            height += h + 7
    top = max(42 if modern else (48 if photo else 20), margin+height/mm+(7 if modern else 2))
    return x, width, paragraphs, top


def render_pdf(data, language, style):
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader
    margin, layout = style['margin_mm'], style['layout']
    photo = photo_stream(data)
    identity = None
    if layout in {'modern', 'classic'}:
        identity = pdf_identity(data, style, photo, layout == 'modern')
        first_top = identity[3] + (18 if photo and layout == 'modern' else (4 if layout == 'classic' else 9))
    else:
        first_top = margin
    if layout == 'classic':
        content = classic_content(data, language)
        streams = [pdf_column(content, style, first_top, margin, 210-2*margin)]
    else:
        left, right = visual_columns(data, language, style)
        side, gap = style['sidebar_mm'], style['gap_mm']
        side_stream = pdf_column(left, style, first_top, margin, side, photo if layout == 'professional' else None)
        side_pages = len(PdfReader(io.BytesIO(side_stream)).pages)
        streams = [side_stream, pdf_column(right, style, first_top, margin+side+gap,
                                           210-2*margin-side-gap, narrow_pages=side_pages)]
    readers = [PdfReader(io.BytesIO(stream)) for stream in streams]
    writer = PdfWriter()
    for index in range(max(len(r.pages) for r in readers)):
        background = io.BytesIO()
        drawing = canvas.Canvas(background, pagesize=A4)
        if index == 0 and identity:
            x, width, paragraphs, header = identity
            if layout == 'modern':
                drawing.setFillColor(HexColor(style['accent']))
                drawing.rect(0, (297-header)*mm, 210*mm, header*mm, fill=1, stroke=0)
            y = (297-margin)*mm
            for p, height in paragraphs:
                y -= height
                p.drawOn(drawing, x*mm, y)
                y -= 7
            if photo:
                size = 42 if layout == 'modern' else 32
                bottom = (297-header-10)*mm if layout == 'modern' else (297-margin-size)*mm
                drawing.setFillColor(HexColor('#FFFFFF'))
                drawing.rect((margin-1)*mm, bottom-mm, (size+2)*mm, (size+2)*mm, fill=1, stroke=0)
                drawing.drawImage(ImageReader(photo), margin*mm, bottom, size*mm, size*mm)
            if layout == 'classic':
                drawing.setStrokeColor(HexColor('#A6B2B9'))
                drawing.line(margin*mm, (297-first_top+4)*mm, (210-margin)*mm, (297-first_top+4)*mm)
        if layout == 'professional' and index < len(readers[0].pages):
            drawing.setStrokeColor(HexColor('#BCC8D0'))
            drawing.setLineWidth(0.65)
            x = (margin+style['sidebar_mm']+style['gap_mm']/2)*mm
            drawing.line(x, margin*mm, x, (297-margin)*mm)
        drawing.setFont(pdf_font(), 8)
        drawing.setFillColor(HexColor('#65717A'))
        drawing.drawRightString((210-margin)*mm, 8*mm, str(index+1))
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



def docx_first_column(content, style, width, height):
    """Move complete items to full-width continuation, using conservative metrics."""
    from reportlab.lib.units import mm
    groups, group = [], []
    for block in content:
        if block[0] == 'heading' and group:
            groups.append(group)
            group = []
        group.append(block)
        if block[0] == 'space':
            groups.append(group)
            group = []
    if group:
        groups.append(group)
    used, first = 0, []
    for index, group in enumerate(groups):
        measured = sum(f.wrap(width*mm, 100000)[1] + f.getSpaceBefore() + f.getSpaceAfter()
                       for f in pdf_story(group, style))
        if first and used + measured > height*mm:
            return first, [b for rest in groups[index:] for b in rest]
        first.extend(group)
        used += measured
    return first, []


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
    for name, size in [('Normal', style['font_size']), ('Title', 25), ('Subtitle', 11),
                       ('Heading 1', style['heading_size']), ('Heading 2', 11)]:
        fmt = document.styles[name]
        fmt.font.name, fmt.font.size = 'Arial', Pt(size)
        fmt.font.color.rgb = RGBColor.from_string(style['accent'].lstrip('#') if name != 'Normal' else '303A43')
        fmt.font.bold = name in {'Title', 'Heading 1', 'Heading 2'}
        fmt.font.italic = False
        fmt.paragraph_format.space_after = Pt(5)
        fmt.paragraph_format.space_before = Pt((8 if style['layout'] == 'classic' else 12) if name == 'Heading 1' else 0)
        fmt.paragraph_format.line_spacing = 1.15
    for border in document.styles.element.xpath('.//w:pBdr'):
        border.getparent().remove(border)

    def shade(element, color):
        node = OxmlElement('w:shd'); node.set(qn('w:fill'), color.lstrip('#')); element.append(node)

    def rule(paragraph):
        props = paragraph._p.get_or_add_pPr()
        if style['layout'] == 'professional':
            shade(props, '#E5EBEF')
        else:
            borders = OxmlElement('w:pBdr')
            edge = OxmlElement('w:bottom')
            for key, value in [('val', 'single'), ('sz', '4'), ('space', '4'), ('color', style['accent'].lstrip('#'))]:
                edge.set(qn('w:'+key), value)
            borders.append(edge); props.append(borders)

    def fill(container, content, photo=None, white=False):
        if photo:
            size = style.get('sidebar_mm', 32)
            p = container.add_paragraph()
            p.paragraph_format.keep_with_next = True
            p.add_run().add_picture(photo, width=Mm(size), height=Mm(size))
        for i, (kind, value) in enumerate(content):
            if kind == 'space':
                continue
            paragraph = container.add_paragraph(value, {'name':'Title', 'role':'Subtitle',
                'heading':'Heading 1', 'entry_title':'Heading 2'}.get(kind, 'Normal'))
            fmt = paragraph.paragraph_format
            fmt.widow_control = True
            fmt.keep_with_next = kind in {'name', 'heading', 'entry_title', 'organization', 'meta'}
            if kind in {'entry_title', 'organization', 'meta'} and (i+1 == len(content) or content[i+1][0] == 'space'):
                fmt.keep_with_next = False
            fmt.keep_together = kind in {'name', 'heading', 'entry_title', 'organization', 'meta'} or (kind == 'body' and len(value) < 1600)
            if kind == 'heading':
                rule(paragraph)
            if kind in {'organization', 'meta'}:
                fmt.space_after = Pt(3)
                for run in paragraph.runs:
                    run.bold = kind == 'organization'
                    run.font.size = Pt(10 if kind == 'organization' else 9)
            if kind == 'entry_title':
                fmt.space_before = Pt(7)
            if white:
                for run in paragraph.runs:
                    run.font.color.rgb = RGBColor(255,255,255)
            # Allow Word to wrap unbroken URLs without altering their literal text.
            wrap = OxmlElement('w:wordWrap'); wrap.set(qn('w:val'), '1')
            paragraph._p.get_or_add_pPr().append(wrap)

    def table(widths):
        result = document.add_table(rows=1, cols=len(widths))
        result.autofit = False
        result.alignment = WD_TABLE_ALIGNMENT.CENTER
        for column, cell, size in zip(result.columns, result.rows[0].cells, widths):
            column.width = cell.width = Mm(size)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.line_spacing = Pt(1)
            p.add_run().font.size = Pt(1)
            margins = OxmlElement('w:tcMar')
            for edge in ('top','left','bottom','right'):
                node = OxmlElement('w:'+edge); node.set(qn('w:w'),'0'); node.set(qn('w:type'),'dxa'); margins.append(node)
            cell._tc.get_or_add_tcPr().append(margins)
        borders = OxmlElement('w:tblBorders')
        for edge in ('top','left','bottom','right','insideH','insideV'):
            item = OxmlElement('w:'+edge); item.set(qn('w:val'),'nil'); borders.append(item)
        result._tbl.tblPr.append(borders)
        return result

    layout, width = style['layout'], 210-2*style['margin_mm']
    sections = visual_sections(data, language)
    photo = photo_stream(data)
    if layout == 'classic':
        if photo:
            # Anchored photo only. Essential text stays in the ordinary reading order.
            p = document.add_paragraph()
            p.paragraph_format.keep_with_next = True
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = Pt(1)
            picture = p.add_run().add_picture(photo, width=Mm(30), height=Mm(30))
            inline = picture._inline
            anchor = OxmlElement('wp:anchor')
            for key, value in [('distT','0'),('distB','0'),('distL','0'),('distR','0'),
                               ('simplePos','0'),('relativeHeight','0'),('behindDoc','0'),
                               ('locked','0'),('layoutInCell','1'),('allowOverlap','1')]:
                anchor.set(key,value)
            origin = OxmlElement('wp:simplePos'); origin.set('x','0'); origin.set('y','0'); anchor.append(origin)
            for axis, relative in [('H','column'),('V','paragraph')]:
                pos = OxmlElement('wp:position'+axis); pos.set('relativeFrom',relative)
                offset = OxmlElement('wp:posOffset'); offset.text='0'; pos.append(offset); anchor.append(pos)
            anchor.append(inline.extent)
            wrap = OxmlElement('wp:wrapNone'); anchor.append(wrap)
            anchor.append(inline.docPr)
            anchor.append(inline.graphic)
            inline.getparent().replace(inline,anchor)
        start = len(document.paragraphs)
        fill(document, sections['identity'])
        if photo:
            for p in document.paragraphs[start:]:
                p.paragraph_format.left_indent = Mm(40)
            document.paragraphs[-1].paragraph_format.space_after = Mm(18)
        fill(document, classic_content(data, language))
    else:
        side, gap = style['sidebar_mm'], style['gap_mm']
        if layout == 'modern':
            header = table((side, gap, width-side-gap))
            for cell in header.rows[0].cells:
                shade(cell._tc.get_or_add_tcPr(), style['accent'])
            if photo:
                p = header.cell(0,0).add_paragraph()
                p.add_run().add_picture(photo, width=Mm(42), height=Mm(42))
            identity_cell = header.cell(0,2) if photo else header.cell(0,0).merge(header.cell(0,2))
            margins = identity_cell._tc.get_or_add_tcPr().find(qn('w:tcMar'))
            for edge in margins:
                edge.set(qn('w:w'), '160' if edge.tag == qn('w:top') else '100')
            fill(identity_cell, sections['identity'], white=True)
            document.add_paragraph().paragraph_format.space_after = Pt(0)
        body = table((side, gap, width-side-gap))
        left, right = visual_columns(data, language, style)
        fill(body.cell(0,0), left, photo if layout == 'professional' else None)
        top = 16 + (50 if layout == 'modern' and photo else 30 if layout == 'modern' else 0)
        first, continuation = docx_first_column(right, style, width-side-gap, 297-top-style['margin_mm']-8)
        fill(body.cell(0,2), first)
        if continuation:
            fill(document, continuation)
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
