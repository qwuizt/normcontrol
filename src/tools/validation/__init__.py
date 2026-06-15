from src.tools.validation.schemas import (
    CaptionAlignmentRule,
    CaptionCapitalizationRule,
    CaptionEndPunctuationRule,
    CaptionExistenceRule,
    CaptionIntervalRule,
    CaptionNumberRule,
    CaptionOrderRule,
    CaptionSectionRule,
    CaptionSeparatorRule,
    CaptionStartRule,
    CaptionTitleRequiredRule,
    CaptionValidationRules,
)


TABLE_CAPTION_VALIDATION_RULES = CaptionValidationRules(
    rules=(
        CaptionExistenceRule(kind='table'),
        CaptionStartRule(allowed=['Таблица', 'Продолжение таблицы'], similarity_cutoff=0.8),
        CaptionTitleRequiredRule(similarity_cutoff=0.8),
        CaptionNumberRule(has_section=True),
        CaptionSeparatorRule(allowed=['-', '–', '—'], similarity_cutoff=0.7),
        CaptionCapitalizationRule(similarity_cutoff=0.7, validate_title_after_separator=True),
        CaptionEndPunctuationRule(forbidden=True),
        CaptionAlignmentRule(alignment='left'),
        CaptionIntervalRule(spacing_tolerance=0.1),
        CaptionOrderRule(kind='table'),
    ),
)


FIGURE_CAPTION_VALIDATION_RULES = CaptionValidationRules(
    rules=(
        CaptionExistenceRule(kind='figure'),
        CaptionNumberRule(has_section=True, suffix_forbidden=True),
        CaptionSeparatorRule(allowed=['-', '—', '–', '―']),
        CaptionCapitalizationRule(),
        CaptionEndPunctuationRule(forbidden=True),
        CaptionAlignmentRule(alignment='center'),
        CaptionIntervalRule(spacing_tolerance=0.14),
        CaptionSectionRule(),
        CaptionOrderRule(kind='figure'),
    ),
)
