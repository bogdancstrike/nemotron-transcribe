import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
import db
import main
import exports


def ready(tmp):
    db.create_job('export','Știință.wav',str(tmp/'uploads/export.wav'),'ro',60,False)
    (tmp/'uploads').mkdir(exist_ok=True);(tmp/'uploads/export.wav').write_bytes(b'x')
    db.update_job('export',status='done',duration_s=2,wall_s=1)
    directory=tmp/'results/export';directory.mkdir(parents=True)
    text='Știință în România: 10 000 lei & <text>.'
    words=[{'word':w,'start':n*.1,'end':n*.1+.1,'speaker':1 if n<3 else 2} for n,w in enumerate(text.split())]
    result={'text':text,'speaker_scope':'file','segments':[{'index':0,'start_seconds':0,'duration_s':2,'text':text,'words':words,'alignment_valid':True}]}
    (directory/'transcript.json').write_text(json.dumps(result,ensure_ascii=False))
    (directory/'transcript.txt').write_text(text)
    return text


def test_docx_keeps_unicode_and_escapes_markup(isolated):
    text=ready(isolated)
    with TestClient(main.app) as client:
        response=client.get('/api/jobs/export/export/docx')
        assert response.status_code==200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            xml=archive.read('word/document.xml').decode()
        assert 'Știință în România' in xml and '&amp;' in xml and '&lt;text&gt;' in xml
        assert response.headers['content-type'].startswith('application/vnd.openxmlformats')


def test_pdf_is_real_font_embedded_unicode_document(isolated):
    ready(isolated)
    with TestClient(main.app) as client:
        response=client.get('/api/jobs/export/export/pdf')
        assert response.status_code==200 and response.content.startswith(b'%PDF-')
        assert b'/FontFile2' in response.content and b'/ToUnicode' in response.content


def test_vtt_requires_complete_review_alignment(isolated):
    ready(isolated)
    with TestClient(main.app) as client:
        original=client.get('/api/jobs/export/export/vtt')
        assert original.status_code==200 and original.text.startswith('WEBVTT') and ' --> ' in original.text
        assert client.get('/api/jobs/export/export/vtt?source=review').status_code==200
        current=client.get('/api/jobs/export/correction').json()
        client.put('/api/jobs/export/correction',json={'text':current['text']+' Extra words','revision':current['revision']}).raise_for_status()
        assert client.get('/api/jobs/export/export/vtt?source=review').status_code==409
        selected=client.post('/api/jobs/export/export/docx',json={'source':'review','text':'Corectat: șapte lei.'})
        with zipfile.ZipFile(io.BytesIO(selected.content)) as archive:assert 'Corectat: șapte lei.' in archive.read('word/document.xml').decode()


def test_bulk_zip_explains_failed_exports_and_preserves_good_ones(isolated):
    ready(isolated)
    with TestClient(main.app) as client:
        result=client.post('/api/jobs/export',json={'ids':['export','missing'],'format':'txt'})
        assert result.status_code==200
        with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
            assert len(archive.namelist())==2
            assert json.loads(archive.read('export-errors.json'))[0]['id']=='missing'
            assert 'Știință în România' in archive.read(next(n for n in archive.namelist() if n.endswith('.txt'))).decode()


def test_speaker_exports_use_names_and_keep_unaligned_review_edits(isolated):
    text=ready(isolated)
    with TestClient(main.app) as client:
        annotations=client.get('/api/jobs/export/review-tools').json()
        client.put('/api/jobs/export/review-tools',json={'revision':annotations['revision'],'value':{'names':{'1':'Ștefan & Maria'},'merges':{},'overrides':[],'reviewed':[]}}).raise_for_status()
        subtitles=client.get('/api/jobs/export/export/vtt?speakers=true')
        assert subtitles.status_code==200
        assert '<v Ștefan &amp; Maria>' in subtitles.text
        review=client.get('/api/jobs/export/correction').json()
        client.put('/api/jobs/export/correction',json={'revision':review['revision'],'text':text+' Unaligned analyst note.'}).raise_for_status()
        result=client.get('/api/jobs/export/export/txt?source=review&speakers=true')
        assert 'Ștefan & Maria' in result.text and 'Unaligned analyst note.' in result.text
