"""Assign word speakers from a single stateful encoder pass over the whole recording."""
import bisect
import math
from pathlib import Path

import httpx

MAX_SPEAKER_SECONDS = 3600


def diarize_file(chunks, destination: Path, base_url):
    # Chunks are already mono PCM16 at 16 kHz. Stream-copy them into one WAV.
    import wave
    with wave.open(str(destination),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
        for chunk in chunks:
            with wave.open(str(chunk),'rb') as source:
                while frames:=source.readframes(16000*30):output.writeframesraw(frames)
    with destination.open('rb') as audio, httpx.Client(timeout=httpx.Timeout(connect=15,read=3600,write=180,pool=15)) as client:
        response=client.post(base_url+'/v1/audio/diarizations',data={'mode':'streaming'},files={'file':('recording.wav',audio,'audio/wav')})
        response.raise_for_status()
        result=response.json()
    turns=result.get('segments')
    if not isinstance(turns,list):raise RuntimeError('Invalid file-wide speaker response from model')
    for turn in turns:
        if (not isinstance(turn,dict) or any(isinstance(turn.get(k),bool) or not isinstance(turn.get(k),(int,float)) or not math.isfinite(turn[k]) for k in ('start','end'))
            or not 0<=turn['start']<turn['end'] or isinstance(turn.get('speaker'),bool) or not isinstance(turn.get('speaker'),int) or turn['speaker']<1):
            raise RuntimeError('Invalid speaker turn from model')
    return sorted(turns,key=lambda turn:turn['start'])


def assign_speakers(segments, turns):
    """Choose greatest actual overlap; unvoiced/unmatched words have no speaker."""
    starts=[turn['start'] for turn in turns]
    max_duration=max((t['end']-t['start'] for t in turns),default=0)
    for segment in segments:
        offset=segment['start_seconds']
        for word in segment.get('words',[]):
            word.pop('speaker',None)
            word.pop('speaker_slot',None)
            word.pop('speaker_confidence',None)
            start,end=word.get('start'),word.get('end')
            if not isinstance(start,(int,float)) or not isinstance(end,(int,float)) or end<=start:continue
            start+=offset;end+=offset
            scores={}
            for n in range(bisect.bisect_left(starts,start-max_duration),bisect.bisect_left(starts,end)):
                turn=turns[n];overlap=min(end,turn['end'])-max(start,turn['start'])
                if overlap>0:scores[turn['speaker']]=scores.get(turn['speaker'],0)+overlap
            if scores:word['speaker']=max(scores,key=scores.get)
