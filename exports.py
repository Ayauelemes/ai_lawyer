"""Generate reports and filing drafts from saved analysis, never from fresh LLM text."""
from html import escape
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.shared import Pt
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

TITLES = {
    "ru": {"opinion": "Юридическое заключение", "demand": "Претензия", "complaint": "Жалоба",
           "application": "Заявление", "lawsuit": "Исковое заявление"},
    "kk": {"opinion": "Құқықтық қорытынды", "demand": "Талап хат", "complaint": "Шағым",
           "application": "Өтініш", "lawsuit": "Талап қою арызы"},
}
FIELDS = [
    ("facts", "Обстоятельства", "Мән-жайлар"),
    ("issues", "Правовые вопросы", "Құқықтық мәселелер"),
    ("legal", "Правовые основания", "Құқықтық негіздер"),
    ("evidence", "Анализ доказательств", "Дәлелдемелерді талдау"),
    ("applicant", "Позиция заявителя", "Өтініш берушінің ұстанымы"),
    ("respondent", "Возможная позиция другой стороны", "Екінші тараптың ықтимал ұстанымы"),
    ("practice", "Судебная практика", "Сот практикасы"),
    ("civil", "Гражданско-правовые последствия", "Азаматтық-құқықтық салдар"),
    ("administrative", "Административная ответственность", "Әкімшілік жауаптылық"),
    ("conclusion", "Предварительный вывод", "Алдын ала қорытынды"),
    ("questions", "Недостающие сведения", "Жетіспейтін мәліметтер"),
    ("next_actions", "Дальнейшие действия", "Кейінгі қадамдар"),
]


def document_content(result, request, approved):
    case = result["case"]
    kk = case["language"] == "kk"
    opinion = result["opinion"]
    sources = {item["id"]: item for item in result["sources"]}
    used = {}
    title = TITLES[case["language"]][request.kind]
    is_filing = request.kind != "opinion"
    status = ("ЖОБА" if kk else "ПРОЕКТ") if is_filing or not approved else (
        "БЕКІТІЛГЕН" if kk else "УТВЕРЖДЕНО")
    info = [case["title"], case["region"], case["event_on"] or ("Күні белгісіз" if kk else "Дата не указана")]
    sections = [("", info)]
    missing = "Толтыру қажет" if kk else "Требуется заполнить"
    if is_filing:
        sections.append(("Тараптар" if kk else "Реквизиты", [
            f'{"Кімге" if kk else "Кому"} — {request.addressee or missing}',
            f'{"Өтініш беруші" if kk else "Заявитель"} — {request.applicant or missing}',
            f'{"Екінші тарап" if kk else "Другая сторона"} — {request.respondent or missing}',
        ]))
    sections.append(("Өтініштің сипаттамасы" if kk else "Описание обращения", [case["narrative"]]))
    for key, ru, kaz in FIELDS:
        if is_filing and key not in {"facts", "legal", "civil", "administrative"}:
            continue
        entries = []
        for value in opinion[key]:
            if isinstance(value, str):
                entries.append(value)
            elif key == "evidence":
                entries.append(value["assessment"] + " «" + value["excerpt"] + "»")
            else:
                source_id = value["source_id"]
                source = sources[source_id]
                number = used.setdefault(source_id, len(used) + 1)
                entry = f'{value["text"]} [{number}] «{value["quote"]}»'
                if key == "practice":
                    entry += f'\n{value["similarities"]}\n{value["differences"]}'
                entries.append(entry)
        sections.append((kaz if kk else ru, entries or ["Дерек жеткіліксіз" if kk else "Нет подтверждённых данных"]))
    if is_filing:
        sections.append(("Талаптар" if kk else "Требования", request.requests or [missing]))
        sections.append(("Қосымшалар" if kk else "Приложения",
                         [item["filename"] for item in result["evidence"]] or [missing]))
        sections.append(("", ["Күні және қолы __________________" if kk else "Дата и подпись __________________"]))
    else:
        sections.append(("Санкцияны есептеу" if kk else "Расчёт санкции",
                         ["Жеке тексеріледі" if kk else "Проводится отдельно по проверенному правилу"]))
    references = []
    for source_id, number in used.items():
        source = sources[source_id]
        details = source["details"]
        if source["kind"] == "norm":
            labels = (("бап", "бөлік", "тармақ", "тармақша") if kk
                      else ("ст.", "ч.", "п.", "подп."))
            citation = ", ".join(f"{label} {details[key]}" for label, key in zip(
                labels, ("article", "part", "paragraph", "subparagraph")) if details.get(key))
        else:
            citation = f'{details["court"]}, № {details["case_number"]}, {details["decided_on"]}'
        references.append(f'[{number}] {source["title"]}, {citation}\n{source["url"]}')
    sections.append(("Дереккөздер" if kk else "Проверенные источники", references or [missing]))
    return f"{title} · {status}", sections


def render_docx(title, sections):
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(11)
    doc.add_heading(title, 0)
    for heading, entries in sections:
        if heading:
            doc.add_heading(heading, level=1)
        for entry in entries:
            doc.add_paragraph(entry)
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def render_pdf(title, sections, configured_font):
    candidates = (configured_font,) if configured_font else (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    )
    path = next((value for value in candidates if Path(value).is_file()), None)
    if not path:
        raise ValueError("pdf_font_unavailable")
    font = "LegalCyrillic"
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font, path))
    body = ParagraphStyle("Body", fontName=font, fontSize=10, leading=15, alignment=TA_LEFT,
                          spaceAfter=8, splitLongWords=True)
    heading = ParagraphStyle("Heading", parent=body, fontSize=13, leading=18,
                             textColor=colors.HexColor("#162b46"), keepWithNext=True)
    title_style = ParagraphStyle("Title", parent=heading, fontSize=19, leading=24)
    flow = [Paragraph(escape(title), title_style), Spacer(1, 12)]
    for section, entries in sections:
        if section:
            flow.append(Paragraph(escape(section), heading))
        for entry in entries:
            flow.append(Paragraph(escape(entry).replace("\n", "<br/>"), body))
    output = BytesIO()
    SimpleDocTemplate(output, title=title, leftMargin=48, rightMargin=48).build(flow)
    return output.getvalue()
