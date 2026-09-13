# Новое оформление CV

Все данные в этих примерах вымышлены и используются только для демонстрации.
Условный нарисованный портрет проверяет размещение фото; это не фото пользователя.
Рендерер не добавляет эти данные в настоящий CV.

| Шаблон | EN с фото, 1 страница | RU без фото, 1 страница | RU с большим опытом, 3 страницы |
| --- | --- | --- | --- |
| Modern Blue | [PDF](Modern_Blue_EN.pdf) / [DOCX](Modern_Blue_EN.docx) | [PDF](Modern_Blue_RU.pdf) / [DOCX](Modern_Blue_RU.docx) | [PDF](Modern_Blue_RU_long.pdf) / [DOCX](Modern_Blue_RU_long.docx) |
| Professional | [PDF](Professional_EN.pdf) / [DOCX](Professional_EN.docx) | [PDF](Professional_RU.pdf) / [DOCX](Professional_RU.docx) | [PDF](Professional_RU_long.pdf) / [DOCX](Professional_RU_long.docx) |
| Classic ATS | [PDF](Classic_ATS_EN.pdf) / [DOCX](Classic_ATS_EN.docx) | [PDF](Classic_ATS_RU.pdf) / [DOCX](Classic_ATS_RU.docx) | [PDF](Classic_ATS_RU_long.pdf) / [DOCX](Classic_ATS_RU_long.docx) |

PDF содержит выделяемый текст, DOCX редактируется в Word. Все документы — A4.
Classic ATS намеренно сохраняет одну колонку текста и не использует таблицы
для расположения содержимого. Наличие фото не влияет на извлечение текста.

Проверены все страницы PDF и DOCX после экспорта через Word, кириллица,
переносы длинных ссылок, отсутствие пустых разделов и выходов текста за страницу.
Переносы PDF и Word могут различаться; другой редактор DOCX также может иначе
распределить страницы. Работа конструктора, пользовательские данные и анализ CV
не изменялись.

Повторная генерация из корня проекта:

```powershell
.\.venv\Scripts\python.exe examples/cv_redesign/generate.py
```
