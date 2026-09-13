"""Reproducible visual fixtures, entirely synthetic; never opens the user database."""
import base64
import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PIL import Image, ImageDraw
from services.cv_export import render

OUTPUT = Path(__file__).resolve().parent


def portrait():
    # Deliberately illustrated stand-in, not a real person's photograph.
    image = Image.new('RGB', (500, 650), '#D9E4EC')
    draw = ImageDraw.Draw(image)
    draw.ellipse((25, 335, 475, 820), fill='#3A5269')
    draw.rectangle((218, 285, 284, 410), fill='#C09D80')
    draw.ellipse((146, 105, 355, 365), fill='#D4B397')
    draw.pieslice((137, 83, 363, 265), 175, 355, fill='#33404A')
    draw.polygon([(196,380),(250,450),(218,590),(132,407)],fill='#F5F7FA')
    draw.polygon([(303,380),(250,450),(285,590),(373,407)],fill='#F5F7FA')
    stream=io.BytesIO(); image.save(stream,'PNG')
    return base64.b64encode(stream.getvalue()).decode()


def sample(language='en', long=False):
    ru = language == 'ru'
    data = dict(
        full_name='Анна Соколова' if ru else 'Anna Sokolova',
        target_role='Аналитик данных и бизнес-процессов' if ru else 'Data & Operations Analyst',
        current_location='Дубай, ОАЭ' if ru else 'Dubai, UAE',
        email='anna.sokolova@example.com',
        linkedin='https://www.linkedin.com/in/demo-anna-sokolova',
        summary=('Аналитик с опытом подготовки управленческой отчётности и работы с операционными данными. '
                 'Помогаю командам проверять качество данных и находить причины изменений показателей.' if ru else
                 'Data analyst focused on operational reporting and reliable business metrics. '
                 'Experienced in turning reporting requests into clear dashboards and documented data checks.'),
        skills='SQL\nPython\nPower BI\nExcel\n' + ('Качество данных\nВизуализация данных' if ru else 'Data quality\nData visualization'),
        languages='Русский - родной\nАнглийский - C1' if ru else 'English - C1\nRussian - native',
        certifications='Microsoft Power BI Data Analyst, 2023',
        additional='Доступна для работы в офисе и гибридно.' if ru else 'Available for onsite and hybrid roles.',
        experience=[
            dict(company='Example Operations', role='Старший аналитик данных' if ru else 'Senior Data Analyst',
                 location='Dubai',start_date='2022-06',end_date='Present',
                 description=('Разработала набор Power BI отчётов для операционной команды.\n'
                              'Согласовала определения показателей с руководителями подразделений.\n'
                              'Настроила проверки качества данных и документировала SQL-запросы.' if ru else
                              'Built Power BI reporting for the operations team.\n'
                              'Agreed metric definitions with department leads.\n'
                              'Introduced data quality checks and documented SQL queries.')),
            dict(company='Example Retail',role='Бизнес-аналитик' if ru else 'Business Analyst',
                 location='Abu Dhabi',start_date='2019-09',end_date='2022-05',
                 description=('Готовила еженедельные отчёты о продажах и запасах.\n'
                              'Собирала требования пользователей и проверяла обновления отчётности.\n'
                              'Обучала коллег работе с отчётами и справочниками.' if ru else
                              'Prepared weekly sales and inventory reporting.\n'
                              'Gathered user requirements and verified reporting updates.\n'
                              'Helped colleagues use reporting tools and reference data.')),
        ],
        education=[dict(institution='Example University',qualification='Бакалавр' if ru else 'BSc',
                        field='Информационные системы' if ru else 'Information Systems',dates='2015 - 2019',location='Moscow')])
    if language == 'en':
        data['photo']=portrait()
    if long:
        data['email']='anna.sokolova.operational.reporting.and.business.intelligence@example.com'
        data['linkedin']='https://www.linkedin.com/in/anna-sokolova-operational-reporting-business-intelligence-demonstration'
        data['experience'] += [dict(company=f'Example Project {i}',role='Аналитик операционных процессов',
            location='Dubai',start_date='2015',end_date='2018',
            description=('Подготовка отчётности и проверка данных для операционных команд. '
                         'Согласование требований с пользователями и документирование решений.\n')*5)
                              for i in range(1,6)]
    return data


if __name__ == '__main__':
    for template,name in [('template_1','Modern_Blue'),('template_2','Professional'),('template_3','Classic_ATS')]:
        for language,long in [('en',False),('ru',False),('ru',True)]:
            suffix=language.upper()+('_long' if long else '')
            for extension in ('pdf','docx'):
                path=OUTPUT/f'{name}_{suffix}.{extension}'
                path.write_bytes(render(sample(language,long),extension,language,template))
                print(path.name)
