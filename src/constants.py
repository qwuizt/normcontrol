import re


DPI_LIST = [50, 150]

# titul_checker module
#: Путь по умолчанию, куда сохраняются аннотированные PDF,
#: если переменная окружения SAVE_DIR не задана.
PATH_SAVE_DEFAULT: str = 'checked_pdf'

#: Максимальное допустимое евклидово расстояние(px) между центрами
#: спанов для объединения их в один кластер (BFS).
DISTANCE_THRESHOLD_DEFAULT: float = 65.0

#: Максимальное горизонтальное расстояние между спанами
#: (между x‑координатой правой границы одного спана и левой границей следующего),
#: позволяющее объединить их в один кластер.
OVERLAP_THRESHOLD_DEFAULT: float = 3.0

#: Порог "короткого" спана (количество символов),
#: который будет слит с предыдущим спаном при кластеризации.
SHORT_SPAN_LIMIT_DEFAULT: int = 4

#: Порог количества слов в блоке отчёта, ниже которого
#: мы пытаемся склеить блок с последующим.
TITLE_BLOCK_WORDS_THRESHOLD: int = 6

#: Смещение окна аннотации для избегания наложения на текст
ANNOTATION_OFFSET: int = 10

#: Цвет оформления рамки аннотации (Базово - жёлтый)
COLOR_HIGHLIGHT: tuple[float, float, float] = (1.0, 1.0, 0.0)

#: Значение прозрачности рамки аннотации
OPACITY_DEFAULT: float = 0.3

#: Аппроксимированное значение пикселей на мм страницы А4
PT_TO_MM: float = 72 / 25.4  # Считать дальше нужно как left * PT_TO_MM
MM_TO_PX_APPROX: float = 2.83

#: Значение левого страничного отступа по ГОСТ
LEFT_MARGIN_DEFAULT: int = 30  # Левая граница (поле)
LEFT_MARGIN_MM: int = 30  # 3 сантиметра или 30 миллиметров

LEFT_OFFSET_MM: float = 12.5  # 1.25 сантиметра или 12.5 миллиметров

#: Значение правого страничного отступа по ГОСТ
RIGHT_MARGIN_DEFAULT: int = 20  # Для титула, не понял почему так
RIGHT_MARGIN_MM: int = 15

#: Значение отношения ширины страницы к её высоте листа А4
W_H_RATIO_A4: float = 0.71

#: Значения ширины и высоты страницы формата А4 в мм
SHAPE_A4_MM: list[int] = [210, 297]

#: Значение пункта - основная единица измерения, с помощью которой определяются размер шрифта, размер абзаца и отступ
PT: float = 0.3528

#: Допустимый процент расхождения между отступами слева и справа блока
#: относительно ширины страницы (5%).
TOLERANCE_RATIO_DEFAULT: float = 0.05

#: Максимальное вертикальное отклонение (в пикселях) между центрами спанов,
#: чтобы считать их принадлежащими одной строке.
BFS_VERTICAL_TOLERANCE: float = 5.0

#: Допустимые варианты подписи проверяющего
LEADER_KEYWORDS: list[str] = ['руководитель нир', 'руководитель проекта', 'руководитель темы']

#: Корректирующие значения размера аннотационного fake_block в случае отсутствия блоков
#: 1 - сверху, 2 - слева, 3 - снизу, 4 - справа
FAKE_BLOCK_POSITION_CORRECTION: list[float] = [50, 50, 80, 300]

#: Минимальный размер шрифта на титульной странице
MIN_FONT_SIZE: int = 12

#: Слова для проверки на наличие в центральном блоке с названием
TYPE_KEYWORDS: set[str] = {'этап', 'промежуточный', 'заключительный', 'шифр'}

#: Токены эталонной фразы "Министерство науки и высшего образования Российской Федерации"
MINISTRY_PHRASE_TOKENS: list[str] = [
    'министерство',
    'науки',
    'и',
    'высшего',
    'образования',
    'российской',
    'федерации',
]

#: Слова для игнорирования при проверке внутреннего блока
IGNORE_KEYWORDS: set[str] = {'заключительный', 'этап', 'итоговый', 'шифр', 'по', 'теме'}

#: Слова для проверки наличие лишней информации в Рег. номере (Титульный лист)
BAD_REG_TAIL_WORDS: list[str] = ['от', 'дата', 'число', 'приказ']

#: Ищем дату только в верхней части листа (доля от высоты страницы)
UTVERJ_DATE_SEARCH_UPPER_RATIO: float = 0.5
#: Максимальный вертикальный зазор от низа блока «УТВЕРЖДАЮ» до блока с датой (в доле от высоты)
UTVERJ_DATE_MAX_DY_RATIO: float = 0.05
#: Допустимое вертикальное смещение (в пикселях) между блоком «УТВЕРЖДАЮ» и датой
UTVERJ_DATE_OVERLAP_TOLERANCE_PX: float = 6.0
#: Регулярное выражение для поиска доменов в тексте
DOMAIN_RE: re.Pattern = re.compile(
    r'\b(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}\b',
    re.IGNORECASE,
)
#: Регулярное выражение для поиска email-адресов в тексте
EMAIL_RE: re.Pattern = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
#: Типы блоков текста, которые мы ищем в PDF
TEXT_BLOCK_TYPE: int = 0
#: Типы сигналов для водяных знаков
SIGNAL_URL_TEXT: str = 'URL_TEXT'
SIGNAL_EMAIL_TEXT: str = 'EMAIL_TEXT'
SIGNAL_LINK_HIT: str = 'LINK_HIT'
SIGNAL_NEAR_WHITE: str = 'NEAR_WHITE'

#: Цвет, близкий к белому, для определения водяных знаков
NEAR_WHITE_DEFAULT: int = 0xF0_F0_F0
#: Паддинг для определения водяных знаков
PAD_DEFAULT: float = 0.5
#: Вес для сильных сигналов водяных знаков
SCORE_STRONG_WEIGHT: int = 3

# TEXT_CLUSTERING MODULE
DRAWING_Y_TOLERANCE_PX: float = 4.0
DRAWING_MIN_LENGTH_PX: float = 30.0
UNDERLINE_MIN_SEGMENTS: int = 4
SIGN_SAME_LINE_Y_TOL_PX: int = 16
SIGN_LINE_RIGHT_MIN_GAP_PX: int = 5
SIGN_LOOKAHEAD_BLOCKS: int = 5
UNDERLINE_PIXELS_PER_CHAR: int = 7
UNDERLINE_MIN_CHARS: int = 5
FAKE_UNDERLINE_Y_PAD_PX: int = 1
FAKE_UNDERLINE_FONT_NAME: str = 'Times New Roman'
FAKE_UNDERLINE_FONT_SIZE_PT: float = 14.0
