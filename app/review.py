"""Link edited text to existing model word times without assigning time to new words."""
import difflib
import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)


def canonical(value):
    return ''.join(c for c in unicodedata.normalize('NFC', value).casefold() if c.isalnum())


@lru_cache(maxsize=4)
def source_words(filename, modified_ns, size):
    result = json.loads(Path(filename).read_text(encoding='utf-8'))
    words = []
    from worker import timing_suspect
    for segment in result.get('segments', []):
        if timing_suspect(segment):continue
        offset = segment.get('start_seconds')
        if not isinstance(offset, (int, float)) or not math.isfinite(offset):
            continue
        for word in segment.get('words') or []:
            if not isinstance(word, dict) or not isinstance(word.get('word'), str):
                continue
            start, end = word.get('start'), word.get('end')
            if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (start, end)) or start < 0 or end <= start:
                continue
            words.append({'word':word['word'],'start':offset+start,'end':offset+end,
                          'speaker':word.get('speaker'),'segment':segment.get('index',0)})
    return words


def align_review(text, words):
    tokens = list(TOKEN.finditer(text))
    # Browser selection offsets count UTF-16 code units, including surrogate pairs.
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xffff else 1))
    a = [canonical(token.group()) for token in tokens]
    b = [canonical(word['word']) for word in words]
    mapped = []
    matcher = difflib.SequenceMatcher(a=a,b=b,autojunk=len(tokens)>5000)
    for block in matcher.get_matching_blocks():
        for index in range(block.size):
            token, word = tokens[block.a+index], words[block.b+index]
            mapped.append({'char_start':offsets[token.start()], 'char_end':offsets[token.end()],
                           'start':word['start'],'end':word['end'],'speaker':word.get('speaker')})
    return {'words':mapped,'matched_words':len(mapped),'total_words':len(tokens),
            'unmatched_words':len(tokens)-len(mapped),'timestamp_source':'original_model_alignment'}
