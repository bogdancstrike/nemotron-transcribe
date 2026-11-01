"""Settings and language codes from the pinned NeMo SALM language table."""
import os

LANGUAGES = {'en': 'English', 'ar': 'Arabic', 'zh': 'Chinese', 'fr': 'French', 'de': 'German', 'hi': 'Hindi', 'it': 'Italian', 'ja': 'Japanese', 'es': 'Spanish', 'ko': 'Korean', 'pt': 'Portuguese', 'ru': 'Russian', 'th': 'Thai', 'hy': 'Armenian', 'nn': 'Norwegian', 'nb': 'Norwegian', 'no': 'Norwegian', 'bg': 'Bulgarian', 'hr': 'Croatian', 'cs': 'Czech', 'da': 'Danish', 'nl': 'Dutch', 'et': 'Estonian', 'fi': 'Finnish', 'el': 'Greek', 'he': 'Hebrew', 'hu': 'Hungarian', 'id': 'Indonesian', 'lv': 'Latvian', 'lt': 'Lithuanian', 'mt': 'Maltese', 'ta': 'Tamil', 'bn': 'Bengali', 'pl': 'Polish', 'ro': 'Romanian', 'sk': 'Slovak', 'sl': 'Slovenian', 'sv': 'Swedish', 'tr': 'Turkish', 'tl': 'Filipino', 'fil': 'Filipino', 'uk': 'Ukrainian', 'vi': 'Vietnamese'}
CAPACITY = min(64, max(1, int(os.getenv("ENGINE_MAX_PARALLEL", "16"))))
DEFAULTS = {"parallel_jobs": 1, "segment_seconds": 120, "input_language": "ro", "output_language": "source", "diarize": False, "number_style": "digits", "guidance": "", "queue_paused": False, "temperature": 0.0, "seed": None, "chunk_mode": "fixed", "overlap_seconds": 0, "channel_mode": "mix", "vocabulary_ids": []}

GUIDANCE_EXAMPLES = [
    {"title":"Verbatim", "text":"Keep fillers, repetitions, hesitations and false starts. Do not improve the speaker’s grammar."},
    {"title":"Readable transcript", "text":"Omit verbal fillers and accidental repetitions. Keep the original meaning and all factual details. Use punctuation and paragraphs."},
    {"title":"Names and places", "text":"Context names: București, Cluj-Napoca, Timișoara, Ștefan. Use these spellings only when supported by the audio."},
    {"title":"Technical vocabulary", "text":"Context terms: Nemotron, CUDA, Docker Compose, RTX 3080, PostgreSQL. Preserve technical names and acronyms."},
    {"title":"Amounts and dates", "text":"Write numeric quantities, percentages and dates in conventional written form. Preserve the exact value and units. For example, zece mii de lei is 10 000 lei."},
    {"title":"Mixed languages", "text":"Keep each spoken language in its original script. Preserve code-switching, proper names and English technical terms. Do not translate unless a target output language is selected."},
]
