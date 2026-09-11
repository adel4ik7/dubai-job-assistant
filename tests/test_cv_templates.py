import base64
import io
import re
import unittest

from docx import Document
from PIL import Image
from pypdf import PdfReader

from services.cv_export import TEMPLATE_IDS, render, photo_stream
from services.cv_builder import filename


def sample(language='en', count=1):
    return dict(full_name='Анна Example' if language == 'ru' else 'Anna Example',
                target_role='Data Analyst', email='anna@example.com', current_location='Dubai',
                summary='Prepared operational reports and documented data checks.', skills='SQL, Python, Power BI',
                languages='English C1; Русский — родной',
                experience=[dict(company=f'Example Company {i}', role='Data Analyst', location='Dubai',
                                 start_date='2020-01', end_date='Present',
                                 description='Prepared reports and documented SQL queries. ' * 8 + f' ItemEnd{i}') for i in range(count)],
                education=[dict(institution='Example University', qualification='BSc', field='Information Systems', dates='2016–2020')])


class CVTemplateTests(unittest.TestCase):
    def test_all_templates_ru_en_pdf_and_editable_docx(self):
        for template in TEMPLATE_IDS:
            for language in ('en', 'ru'):
                with self.subTest(template=template, language=language):
                    data = sample(language)
                    pdf = PdfReader(io.BytesIO(render(data, 'pdf', language, template)))
                    extracted = '\n'.join(p.extract_text() for p in pdf.pages)
                    doc = Document(io.BytesIO(render(data, 'docx', language, template)))
                    doc_text = '\n'.join(doc.element.xpath('//w:t/text()'))
                    for output in (extracted, doc_text):
                        self.assertIn(data['full_name'], output)
                        self.assertIn('Example Company', output)
                        self.assertIn('Русский', output)
                        self.assertIn('Навыки' if language == 'ru' else 'Skills', output)
                    self.assertAlmostEqual(float(pdf.pages[0].mediabox.width), 595.28, delta=1)
                    self.assertAlmostEqual(doc.sections[0].page_width.mm, 210, delta=0.1)
                    self.assertEqual(len(doc.inline_shapes), 0)
                    if template == 'template_3':
                        self.assertEqual(len(doc.tables), 0)
                        self.assertLess(extracted.index('Work experience') if language == 'en' else extracted.index('Опыт работы'), extracted.index('Skills') if language == 'en' else extracted.index('Навыки'))

    def test_long_content_multiple_pages_and_complete_extraction(self):
        for template in TEMPLATE_IDS:
            data = sample('ru', 7)
            data['email'] = 'long.' + 'x'*180 + '@example.com'
            data['linkedin'] = 'https://www.linkedin.com/in/' + 'a'*350
            data['experience'][0]['company'] = 'VeryLongCompany' * 20
            data['experience'][0]['role'] = 'ExtremelyLongRole' * 15
            data['education'] *= 3
            pdf = PdfReader(io.BytesIO(render(data, 'pdf', 'ru', template)))
            self.assertGreaterEqual(len(pdf.pages), 2)
            output = ''.join(p.extract_text() for p in pdf.pages)
            self.assertIn('ItemEnd6', output)
            compact = re.sub(r'\s', '', output)
            self.assertIn(data['email'], compact)
            self.assertIn(data['linkedin'], compact)
            self.assertIn(data['experience'][0]['company'], compact)
            doc = Document(io.BytesIO(render(data, 'docx', 'ru', template)))
            doc_text = ''.join(doc.element.xpath('//w:t/text()'))
            self.assertIn('ItemEnd6', doc_text)
            self.assertIn(data['linkedin'], doc_text)

    def test_empty_sections_missing_photo_and_invalid_photo(self):
        for template in TEMPLATE_IDS:
            data = {'full_name': 'Test Name', 'target_role': 'Cook', 'experience': [{}], 'education': [{}], 'photo': 'invalid'}
            pdf = PdfReader(io.BytesIO(render(data, 'pdf', 'en', template)))
            text = ''.join(p.extract_text() for p in pdf.pages)
            for heading in ('Education', 'Work experience', 'Contact', 'References', 'Certifications'):
                self.assertNotIn(heading, text)
            doc = Document(io.BytesIO(render(data, 'docx', 'en', template)))
            self.assertEqual(len(doc.inline_shapes), 0)

    def test_portrait_crop_and_classic_omits_photo(self):
        image = io.BytesIO()
        Image.new('RGB', (50, 140), 'blue').save(image, 'PNG')
        data = sample()
        data['photo'] = base64.b64encode(image.getvalue()).decode()
        with Image.open(photo_stream(data)) as cropped:
            self.assertEqual(cropped.size, (400, 400))
        for template in TEMPLATE_IDS:
            doc = Document(io.BytesIO(render(data, 'docx', 'en', template)))
            self.assertEqual(len(doc.inline_shapes), 0 if template == 'template_3' else 1)
            if doc.inline_shapes:
                self.assertEqual(doc.inline_shapes[0].width, doc.inline_shapes[0].height)

    def test_filename_sanitization_retained(self):
        self.assertEqual(filename('Anna Example', 'pdf'), 'Anna_Example_CV.pdf')
        self.assertNotIn('/', filename('../Анна:Example', 'docx'))
