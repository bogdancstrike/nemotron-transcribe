"""Unicode document/subtitle exports from preserved source or saved analyst text."""
import io
import json
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

router = APIRouter()
TYPES = {'txt':'text/plain; charset=utf-8','json':'application/json','vtt':'text/vtt; charset=utf-8',
         'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','pdf':'application/pdf'}


def result_for(id):
    import db, main
    job=db.get_job(id)
    if not job:raise HTTPException(404,'Recording not found')
    if job['status']!='done':raise HTTPException(409,'Complete this recording before exporting')
    path=main.OUTPUT/id/'transcript.json'
    if not path.is_file():raise HTTPException(404,'Transcript not available')
    from transcript import present_result
    try:result=present_result(json.loads(path.read_text()))
    except (OSError,ValueError):raise HTTPException(409,'Transcript cannot be read. Retry this recording to rebuild it.')
    try:
        from review_tools import annotate_result, get_annotations
        result=annotate_result(result,get_annotations(id)['value'])
    except ImportError:pass
    return job,result


def normalize(text):
    return ''.join(char.casefold() for char in text if char.isalnum())


def u16slice(text,start,end=None):
    return text.encode('utf-16-le')[start*2:end*2 if end is not None else None].decode('utf-16-le')


def speaker_text(result):
    """Preserve every character, including unaligned text, when splitting speaker turns."""
    from worker import playback_words
    sections=[]
    names=result.get('speaker_names',{})
    for segment in result.get('segments',[]):
        text=segment.get('text','');words=segment.get('playback_words') or playback_words(segment)
        if not words:
            if text.strip():sections.append(('Unassigned',text.strip()))
            continue
        start=0;speaker=words[0].get('speaker');length=len(text.encode('utf-16-le'))//2
        for word in words[1:]:
            if word.get('speaker')!=speaker:
                label=names.get(str(speaker),f'Speaker {speaker}') if speaker is not None else 'Unassigned'
                sections.append((label,u16slice(text,start,word['char_start']).strip()))
                speaker=word.get('speaker');start=word['char_start']
        label=names.get(str(speaker),f'Speaker {speaker}') if speaker is not None else 'Unassigned'
        sections.append((label,u16slice(text,start,length).strip()))
    return '\n\n'.join(f'{label}\n{text}' for label,text in sections if text)


def vtt(words,text,names=None,include_speakers=False):
    import math
    from worker import fmt_time
    valid=[w for w in words if isinstance(w.get('word'),str) and all(isinstance(w.get(k),(float,int)) and math.isfinite(w[k]) for k in ('start','end')) and 0<=w['start']<w['end']]
    if not valid or normalize(' '.join(w['word'] for w in valid))!=normalize(text):
        raise HTTPException(409,'Complete, valid word alignment is required for WebVTT. Realign reviewed text first or export TXT/DOCX/PDF.')
    cues=[];group=[]
    for word in valid:
        if group and (len(group)>=9 or word['end']-group[0]['start']>5 or (include_speakers and word.get('speaker')!=group[0].get('speaker')) or word.get('_segment')!=group[0].get('_segment') or word['start']<group[0]['start']):cues.append(group);group=[]
        group.append(word)
    if group:cues.append(group)
    cues.sort(key=lambda group:group[0]['start'])
    def cue_text(group):
        content=escape(' '.join(w['word'] for w in group))
        speaker=group[0].get('speaker')
        if include_speakers and speaker is not None:
            name=(names or {}).get(str(speaker),f'Speaker {speaker}')
            content=f'<v {escape(name)}>{content}</v>'
        return content
    return 'WEBVTT\n\n'+'\n\n'.join(f'{fmt_time(g[0]["start"],".")} --> {fmt_time(max(w["end"] for w in g),".")}\n{cue_text(g)}' for g in cues)+'\n'


def document(format,title,text):
    text=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',text)
    if format=='txt':return text.encode('utf-8')
    stream=io.BytesIO()
    if format=='docx':
        from docx import Document
        from docx.shared import Pt
        doc=Document();doc.core_properties.title=title
        doc.styles['Normal'].font.name='Calibri';doc.styles['Normal'].font.size=Pt(11)
        doc.add_heading(title,0)
        for paragraph in text.splitlines():doc.add_paragraph(paragraph)
        doc.save(stream)
    elif format=='pdf':
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
        font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        if not Path(font).is_file():raise HTTPException(503,'PDF font unavailable. Rebuild the application image with DejaVu fonts.')
        if 'TranscriptUnicode' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('TranscriptUnicode',font))
        body=ParagraphStyle('Transcript',fontName='TranscriptUnicode',fontSize=10,leading=16,spaceAfter=8,splitLongWords=True)
        heading=ParagraphStyle('Title',parent=body,fontSize=16,leading=22,spaceAfter=20)
        story=[Paragraph(escape(title),heading)]
        story.extend(Paragraph(escape(line),body) if line.strip() else Spacer(1,8) for line in text.splitlines())
        SimpleDocTemplate(stream,pagesize=A4,title=title,leftMargin=48,rightMargin=48,topMargin=48,bottomMargin=48).build(story)
    return stream.getvalue()


def build(id,format,source='original',speakers=False,selection=None):
    import main
    if format not in TYPES:raise HTTPException(400,'Choose TXT, JSON, DOCX, PDF or WebVTT')
    if source not in {'original','review'}:raise HTTPException(400,'Choose original or review')
    job,result=result_for(id);text=result.get('text','');words=[]
    if source=='review':
        review=main.correction(id);text=review['text']
        from alignment import current_alignment
        aligned=current_alignment(id,text,review['revision'])
        if not aligned:
            from review import align_review,source_words
            path=main.OUTPUT/id/'transcript.json';stat=path.stat()
            aligned=align_review(text,source_words(str(path),stat.st_mtime_ns,stat.st_size))
        mapped=[{**word,'word':u16slice(text,word['char_start'],word['char_end'])} for word in aligned.get('words',[])]
        from review_tools import annotate_result,get_annotations
        reviewed=annotate_result({'text':text,'segments':[{'index':0,'start_seconds':0,'duration_s':job.get('duration_s') or 0,'text':text,'words':mapped,'playback_words':mapped}]},get_annotations(id))
        words=reviewed['segments'][0]['words']
        result['speaker_names']=reviewed.get('speaker_names',{})
        if format=='vtt' and aligned.get('unmatched_words',1):
            raise HTTPException(409,'Complete genuine word timing is required. Realign reviewed text first.')
        if speakers and format!='vtt':text=speaker_text(reviewed)
    else:
        from worker import valid_alignment
        if all(valid_alignment(s) for s in result.get('segments',[])):
            words=[{**word,'_segment':s.get('index'),'start':s['start_seconds']+word['start'],'end':s['start_seconds']+word['end']} for s in result.get('segments',[]) for word in s.get('words',[])]
        if speakers and format!='vtt':text=speaker_text(result)
    if selection is not None:
        if not isinstance(selection,str) or not selection.strip() or len(selection)>2_000_000:raise HTTPException(400,'Select text to export (at most two million characters)')
        text=selection
        if format=='vtt':raise HTTPException(409,'Export selection subtitles through a fully aligned reviewed transcript')
    title=job['filename']+(' — reviewed' if source=='review' else '')
    if format=='vtt':data=vtt(words,text,result.get('speaker_names',{}),speakers).encode('utf-8')
    elif format=='json':data=json.dumps({'filename':job['filename'],'source':source,'text':text},ensure_ascii=False,indent=2).encode()
    else:data=document(format,title,text)
    name=f'{Path(job["filename"]).stem[:90]}'+('-reviewed' if source=='review' else '')+('-selection' if selection else '')+'.'+format
    return data,name


def response(data,name,format):
    headers={'Content-Disposition':FileResponse('unused',filename=name).headers['content-disposition']}
    return Response(data,media_type=TYPES[format],headers=headers)


@router.get('/api/jobs/{id}/export/{format}')
def export(id:str,format:str,source:str='original',speakers:bool=False):
    data,name=build(id,format,source,speakers)
    return response(data,name,format)


@router.post('/api/jobs/{id}/export/{format}')
def selection_export(id:str,format:str,values:dict):
    data,name=build(id,format,values.get('source','review'),False,values.get('text'))
    return response(data,name,format)


@router.post('/api/jobs/export')
def bulk_export(values:dict):
    ids=values.get('ids');format=values.get('format','txt');source=values.get('source','original')
    if not isinstance(ids,list) or not 1<=len(ids)<=50 or any(not isinstance(id,str) for id in ids):raise HTTPException(400,'Choose between 1 and 50 recordings')
    if format not in TYPES:raise HTTPException(400,'Unsupported export format')
    buffer=io.BytesIO();failures=[]
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for n,id in enumerate(dict.fromkeys(ids)):
            try:
                data,name=build(id,format,source,values.get('speakers',False))
                name=re.sub(r'[/\\\x00-\x1f]','_',name)
                archive.writestr(f'{n+1:02}-{name}',data)
            except HTTPException as exc:failures.append({'id':id,'error':exc.detail})
        if failures:archive.writestr('export-errors.json',json.dumps(failures,ensure_ascii=False,indent=2))
    return Response(buffer.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="transcripts.zip"'})
