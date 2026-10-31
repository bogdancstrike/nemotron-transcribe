"""Settings and language codes from the pinned NeMo SALM language table."""
import os

LANGUAGES = {'en': 'English', 'ar': 'Arabic', 'zh': 'Chinese', 'fr': 'French', 'de': 'German', 'hi': 'Hindi', 'it': 'Italian', 'ja': 'Japanese', 'es': 'Spanish', 'ko': 'Korean', 'pt': 'Portuguese', 'ru': 'Russian', 'th': 'Thai', 'hy': 'Armenian', 'nn': 'Norwegian', 'nb': 'Norwegian', 'no': 'Norwegian', 'bg': 'Bulgarian', 'hr': 'Croatian', 'cs': 'Czech', 'da': 'Danish', 'nl': 'Dutch', 'et': 'Estonian', 'fi': 'Finnish', 'el': 'Greek', 'he': 'Hebrew', 'hu': 'Hungarian', 'id': 'Indonesian', 'lv': 'Latvian', 'lt': 'Lithuanian', 'mt': 'Maltese', 'ta': 'Tamil', 'bn': 'Bengali', 'pl': 'Polish', 'ro': 'Romanian', 'sk': 'Slovak', 'sl': 'Slovenian', 'sv': 'Swedish', 'tr': 'Turkish', 'tl': 'Filipino', 'fil': 'Filipino', 'uk': 'Ukrainian', 'vi': 'Vietnamese'}
CAPACITY = min(64, max(1, int(os.getenv("ENGINE_MAX_PARALLEL", "16"))))
DEFAULTS = {"parallel_jobs": 1, "segment_seconds": 120, "input_language": "ro", "output_language": "source", "diarize": False, "number_style": "digits"}
