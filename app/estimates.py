"""Conservative remaining-time estimates from observed segment performance."""
import statistics


def estimate_remaining(segments, remaining_audio_s):
    samples=[s['inference_s']/s['duration_s'] for s in segments[-5:]
             if s.get('duration_s',0)>0 and s.get('inference_s',0)>0]
    if not samples or remaining_audio_s<=0:
        return None
    median=statistics.median(samples)
    return {'seconds':median*remaining_audio_s,
            'low':min(samples)*remaining_audio_s,
            'high':max(samples)*remaining_audio_s,
            'samples':len(samples)}
