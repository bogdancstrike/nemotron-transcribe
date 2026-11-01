"""Presentation cleanup; original model responses remain available for inspection."""
import re

SPEAKER_TAG = re.compile(r'<spk\s*:?\s*\d+\s*>', re.IGNORECASE)


def readable_text(text):
    return SPEAKER_TAG.sub('', text)


def present_result(result):
    """Rebuild character mappings when removing model control tokens from old exports."""
    from worker import playback_words, valid_alignment, timing_suspect
    for segment in result.get('segments', []):
        if timing_suspect(segment):
            result['srt_available']=False
            result['timestamp_source']='unavailable'
            segment['alignment_valid']=False
            segment['playback_words']=[]
            segment['alignment_warning']='Word timing ends substantially before detected speech. Retry this recording with current settings.'
        text = segment.get('text', '')
        cleaned = readable_text(text)
        if cleaned != text:
            segment.setdefault('model_text', text)
            segment['text'] = cleaned
            segment['alignment_valid'] = valid_alignment(segment)
            segment['playback_words'] = playback_words(segment)
        elif 'playback_words' not in segment:
            segment['playback_words'] = playback_words(segment)
    result['text'] = readable_text(result.get('text', ''))
    return result
