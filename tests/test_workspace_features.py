import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
import main
import workspace
import workspace_store as store


@pytest.fixture
def client(isolated):
    main.INPUT.mkdir(exist_ok=True);main.OUTPUT.mkdir(exist_ok=True)
    application=FastAPI();application.include_router(workspace.router)
    with TestClient(application) as client:yield client


def completed(isolated,id='source',text='Salut lume',wall=2):
    path=main.INPUT/(id+'.wav');path.parent.mkdir(exist_ok=True);path.write_bytes(b'media')
    db.create_job(id,id+'.wav',str(path),'ro',60,False,duration_s=10)
    db.update_job(id,status='done',wall_s=wall,inference_s=wall,rtfx=10/wall)
    directory=main.OUTPUT/id;directory.mkdir(parents=True,exist_ok=True)
    document={'text':text,'duration_s':10,'wall_s':wall,'inference_s':wall,'rtfx':10/wall,'segments':[],'guidance':'Actual saved instruction'}
    (directory/'transcript.json').write_text(json.dumps(document,ensure_ascii=False))
    (directory/'transcript.txt').write_text(text)
    return path


def test_profiles_and_vocabulary_are_validated_without_mutating_defaults(client):
    vocabulary=client.post('/api/vocabulary',json={'name':'Names','terms':['Ștefan','CUDA','Ștefan']}).json()
    assert vocabulary['terms']==['Ștefan','CUDA']
    original=db.get_settings()
    response=client.post('/api/profiles',json={'name':'Technical','settings':{'input_language':'ro','vocabulary_ids':[vocabulary['id']],'temperature':.15,'chunk_mode':'silence'}})
    assert response.status_code==201,response.text
    profile=response.json()
    assert profile['settings']['chunk_mode']=='silence'
    assert db.get_settings()==original
    options=store.snapshot_options(profile['settings'])
    assert options['vocabulary']==['Ștefan','CUDA']
    assert 'Ștefan' in store.guidance_for(profile['settings'])
    assert client.delete('/api/vocabulary/'+vocabulary['id']).status_code==409
    assert client.post('/api/profiles',json={'name':'bad','settings':{'temperature':2}}).status_code==400
    assert client.post('/api/vocabulary',json={'name':'bad','terms':['<|im_end|>']}).status_code==400
    db.save_settings({'queue_paused':True})
    assert client.post('/api/profiles/'+profile['id']+'/apply').status_code==200
    assert db.get_settings()['queue_paused'] is True
    assert db.get_settings()['temperature']==.15


def test_vocabulary_guidance_is_not_silently_truncated(client):
    vocabulary=store.save_vocabulary({'name':'Long','terms':['term'+str(n)+'x'*93 for n in range(45)]})
    response=client.post('/api/profiles',json={'name':'Too long','settings':{'vocabulary_ids':[vocabulary['id']]}})
    assert response.status_code==400 and '4000' in response.json()['detail']


def test_project_metadata_and_bulk_partial_errors(client,isolated):
    completed(isolated,'one');completed(isolated,'two')
    project=client.post('/api/projects',json={'name':'Interviews'}).json()
    response=client.patch('/api/jobs/one/metadata',json={'project':project['id'],'tags':['verified','verified'],'archived':True})
    assert response.status_code==200
    job=db.get_job('one')
    assert job['project']==project['id'] and job['tags']==['verified'] and job['archived']
    result=client.post('/api/jobs/bulk',json={'ids':['one','missing','two'],'action':'archive'}).json()
    assert result['succeeded']==2 and result['failed']==1
    assert client.delete('/api/projects/'+project['id']).status_code==204
    assert db.get_job('one')['project']==''
    assert client.patch('/api/jobs/one/metadata',json={'project':'does-not-exist'}).status_code==400


def test_individual_control_preserves_chunk_progress(client,isolated):
    source=main.INPUT/'active.wav';source.write_bytes(b'x')
    db.create_job('active','active.wav',str(source),'ro',60,False,duration_s=180)
    db.claim_job();db.update_job('active',status='processing',completed_segments=2,total_segments=3,progress=66)
    response=client.post('/api/jobs/active/control',json={'action':'pause'})
    assert response.status_code==200 and db.get_job('active')['control_action']=='pause'
    assert db.get_job('active')['status']=='processing'
    # Model worker acknowledges at a checkpoint, then the API can resume it.
    db.update_job('active',status='paused')
    with db.connection() as connection:connection.execute('UPDATE jobs SET control_action=NULL WHERE id=?',('active',))
    assert client.post('/api/jobs/active/control',json={'action':'resume'}).status_code==200
    assert db.get_job('active')['status']=='queued'
    assert db.get_job('active')['completed_segments']==2
    assert client.post('/api/jobs/active/control',json={'action':'cancel'}).status_code==200
    assert db.get_job('active')['status']=='cancelled'
    assert client.post('/api/jobs/active/control',json={'action':'resume'}).status_code==200
    assert client.post('/api/jobs/active/control',json={'action':'destroy'}).status_code==400


def test_runs_are_immutable_idempotent_and_comparable(client,isolated):
    completed(isolated,text='Salut <script> lume',wall=2)
    first=store.archive_run('source',main.OUTPUT)
    assert store.archive_run('source',main.OUTPUT)==first
    path=main.OUTPUT/'source'/'transcript.json';document=json.loads(path.read_text())
    document.update(text='Salut lume!',wall_s=1,rtfx=10);path.write_text(json.dumps(document))
    response=client.get('/api/jobs/source/compare',params={'left':first,'right':'current'})
    assert response.status_code==200
    result=response.json()
    assert result['left']['text']=='Salut <script> lume' and result['right']['text']=='Salut lume!'
    assert result['left']['metrics']['wall_s']==2 and result['right']['metrics']['wall_s']==1
    assert any(item['operation']!='equal' for item in result['diff'])
    assert len(client.get('/api/jobs/source/runs').json()['runs'])==2
    # Job ownership is checked for every snapshot ID.
    completed(isolated,'other')
    assert client.get('/api/jobs/other/compare',params={'left':first}).status_code==404


def test_benchmark_counts_real_reference_edits_and_bounds_computation(client,isolated):
    completed(isolated,text='Salut frumoasă lume')
    response=client.post('/api/benchmarks',json={'job_id':'source','reference':'Salut lume','reference_source':'human'})
    assert response.status_code==201,response.text
    report=response.json()
    assert report['wer']['insertions']==1 and report['wer']['rate']==.5
    assert report['cer']['edits']>0 and report['metrics']['rtfx']==5
    assert client.get('/api/benchmarks').json()['benchmarks'][0]['id']==report['id']
    assert client.post('/api/benchmarks',json={'job_id':'source','reference':'hello','reference_source':'model'}).status_code==400
    assert client.post('/api/benchmarks',json={'job_id':'source','reference':' '}).status_code==400
    with pytest.raises(ValueError,match='too large'):store.edit_counts('a'*6000,'b'*6000)
    assert store.edit_counts('a'*10000,'a'*9999+'b')['edits']==1


def test_variant_has_own_input_path_and_snapshots_new_settings(client,isolated):
    source=completed(isolated)
    response=client.post('/api/jobs/source/variant',json={'settings':{'temperature':.2,'chunk_mode':'silence'}})
    assert response.status_code==201,response.text
    id=response.json()['id'];job=db.get_job(id)
    assert job['group_id']=='source' and job['temperature']==.2
    assert job['processing_options']['chunk_mode']=='silence'
    target=main.INPUT/(id+'.wav')
    assert target!=source and target.read_bytes()==source.read_bytes()
    source.unlink()
    assert target.read_bytes()==b'media'


def start(client,content,**values):
    response=client.post('/api/uploads',json={'filename':'sample.wav','size':len(content),**values})
    assert response.status_code==201,response.text
    return response.json()['id']


def test_upload_resumes_at_verified_offset_and_completion_is_idempotent(client,wav_bytes):
    id=start(client,wav_bytes,fingerprint='same-file-identity')
    assert start(client,wav_bytes,fingerprint='same-file-identity')==id
    first=wav_bytes[:137]
    response=client.patch('/api/uploads/'+id+'?offset=0',content=first,headers={'upload-checksum':hashlib.sha256(first).hexdigest()})
    assert response.status_code==200 and response.json()['offset']==137
    assert client.get('/api/uploads/'+id).json()['offset']==137
    assert client.patch('/api/uploads/'+id+'?offset=0',content=b'x').status_code==409
    assert client.post('/api/uploads/'+id+'/complete').status_code==409
    assert client.patch('/api/uploads/'+id+'?offset=137',content=wav_bytes[137:]).status_code==200
    result=client.post('/api/uploads/'+id+'/complete');assert result.status_code==201,result.text
    assert result.json()['id']==id
    assert client.post('/api/uploads/'+id+'/complete').json()['id']==id
    assert (main.INPUT/(id+'.wav')).read_bytes()==wav_bytes
    with db.connection() as connection:assert connection.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
    assert client.patch('/api/uploads/'+id+'?offset=0',content=b'x').status_code==409


def test_upload_rejects_checksum_boundary_and_recovers_uncommitted_tail(client,wav_bytes):
    id=start(client,wav_bytes)
    assert client.patch('/api/uploads/'+id+'?offset=0',content=b'x',headers={'upload-checksum':'0'*64}).status_code==400
    assert client.get('/api/uploads/'+id).json()['offset']==0
    assert client.patch('/api/uploads/'+id+'?offset=0',content=wav_bytes+b'x').status_code==413
    part=db.DATA/'incoming'/(id+'.part');part.write_bytes(b'crash-before-offset-commit')
    assert client.patch('/api/uploads/'+id+'?offset=0',content=wav_bytes).status_code==200
    assert part.read_bytes()==wav_bytes
    assert client.post('/api/uploads/../../bad/complete').status_code in {404,405}
    assert client.delete('/api/uploads/'+id).status_code==204
    assert not part.exists()


def test_upload_finalization_survives_queue_pressure(client,wav_bytes,monkeypatch):
    monkeypatch.setenv('MAX_PENDING_JOBS','1')
    path=main.INPUT/'pending.wav';path.write_bytes(wav_bytes)
    db.create_job('pending','pending.wav',str(path),'ro',60,False)
    id=start(client,wav_bytes)
    client.patch('/api/uploads/'+id+'?offset=0',content=wav_bytes).raise_for_status()
    assert client.post('/api/uploads/'+id+'/complete').status_code==429
    assert (main.INPUT/(id+'.wav')).is_file()
    db.update_job('pending',status='done')
    assert client.post('/api/uploads/'+id+'/complete').status_code==201
    assert (main.INPUT/(id+'.wav')).read_bytes()==wav_bytes


def test_queue_eta_is_derived_and_hides_when_offline_or_paused(client,isolated):
    completed(isolated,wall=2)
    path=main.INPUT/'pending.wav';path.write_bytes(b'media')
    db.create_job('pending','pending.wav',str(path),'ro',60,False,duration_s=100)
    assert not store.queue_estimate()['available']
    db.worker_heartbeat([],True)
    estimate=store.queue_estimate()
    assert estimate['available'] and estimate['remaining_s']==20
    assert estimate['files'][0]['start_in_s']==0 and estimate['files'][0]['finish_in_s']==20
    db.save_settings({'queue_paused':True})
    assert store.queue_estimate()['remaining_s'] is None


def test_comparison_can_include_variants_but_not_unrelated_recordings(client,isolated):
    completed(isolated)
    response=client.post('/api/jobs/source/variant',json={'settings':{'temperature':.2}})
    sibling=response.json()['id']
    db.update_job(sibling,status='done',wall_s=3,inference_s=3,rtfx=10/3)
    directory=main.OUTPUT/sibling;directory.mkdir()
    (directory/'transcript.json').write_text(json.dumps({'text':'Salut altă lume','wall_s':3,'duration_s':10,'segments':[]}))
    runs=client.get('/api/jobs/source/runs').json()['runs']
    assert {row['id'] for row in runs}=={'current','current:'+sibling}
    response=client.get('/api/jobs/source/compare',params={'left':'current','right':'current:'+sibling})
    assert response.status_code==200 and response.json()['right']['text']=='Salut altă lume'
    completed(isolated,'unrelated')
    assert client.get('/api/jobs/unrelated/compare',params={'right':'current:'+sibling}).status_code==404


def test_damaged_previous_result_does_not_block_retry_and_is_preserved(client,isolated):
    completed(isolated)
    original=main.OUTPUT/'source'/'transcript.json';original.write_bytes(b'{damaged json')
    id=store.archive_run('source',main.OUTPUT)
    saved=store.read_run('source',id,main.OUTPUT)
    assert saved['text']=='Salut lume'
    assert 'damaged' in saved['document']['result_warning']
    import base64
    assert base64.b64decode(saved['document']['original_json_base64'])==b'{damaged json'


def test_upload_snapshots_vocabulary_even_if_list_changes_before_completion(client,wav_bytes):
    vocabulary=store.save_vocabulary({'name':'Names','terms':['OriginalName']})
    id=start(client,wav_bytes,settings={'vocabulary_ids':[vocabulary['id']]})
    store.save_vocabulary({'name':'Names','terms':['UpdatedName']},vocabulary['id'])
    client.patch('/api/uploads/'+id+'?offset=0',content=wav_bytes).raise_for_status()
    response=client.post('/api/uploads/'+id+'/complete')
    assert response.status_code==201,response.text
    job=db.get_job(id)
    assert 'OriginalName' in job['guidance'] and 'UpdatedName' not in job['guidance']
    assert job['processing_options']['vocabulary']==['OriginalName']


def test_variant_rejects_queue_pressure_without_orphaning_source(client,isolated,monkeypatch):
    completed(isolated)
    pending=main.INPUT/'pending.wav';pending.write_bytes(b'media')
    db.create_job('pending','pending.wav',str(pending),'ro',60,False)
    monkeypatch.setenv('MAX_PENDING_JOBS','1')
    before=set(main.INPUT.iterdir())
    response=client.post('/api/jobs/source/variant',json={'settings':{'temperature':.2}})
    assert response.status_code==429 and 'Queue is full' in response.json()['detail']
    assert set(main.INPUT.iterdir())==before


def test_variant_prevents_unsupported_long_recording_speaker_request(client,isolated):
    completed(isolated)
    db.update_job('source',duration_s=7200)
    response=client.post('/api/jobs/source/variant',json={'settings':{'diarize':True}})
    assert response.status_code==400 and '60 minutes' in response.json()['detail']


def test_recording_deletion_removes_completed_upload_metadata(client,wav_bytes):
    id=start(client,wav_bytes)
    client.patch('/api/uploads/'+id+'?offset=0',content=wav_bytes).raise_for_status()
    client.post('/api/uploads/'+id+'/complete').raise_for_status()
    main.delete(id)
    assert client.get('/api/uploads/'+id).status_code==404
    assert not (db.DATA/'incoming'/(id+'.lock')).exists()
