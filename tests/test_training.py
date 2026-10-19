import json
import sqlite3
from pathlib import Path
import pytest
from maintainer_copilot.training import collect, prepare, encode_example, train


def feedback_db(path, count=1):
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE skill_feedback (id TEXT, repo TEXT, decision TEXT, payload TEXT, ts TEXT)')
        for i in range(count):
            payload={'messages':[{'role':'user','content':f'How to configure setting {i}?'}],
                     'draft':'Use documented setting safely.', 'task_type':'triage'}
            c.execute('INSERT INTO skill_feedback VALUES (?,?,?,?,?)',(str(i),'a/b','approved',json.dumps(payload),str(i)))


def test_quality_gate_and_read_only_source(tmp_path):
    db=tmp_path/'feedback.sqlite'; feedback_db(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO skill_feedback SELECT 'duplicate',repo,decision,payload,ts FROM skill_feedback")
        c.execute("INSERT INTO skill_feedback VALUES ('bad','a/b','edited','{}','0')")
        c.execute("INSERT INTO skill_feedback VALUES ('reject','a/b','rejected','{}','0')")
    data,stats=collect(db)
    assert len(data)==1 and stats['duplicate']==1 and stats['invalid']==1 and stats['rejected_draft']==1
    assert collect(db,['How to configure setting 0?'])[0]==[]
    report=prepare(db,tmp_path/'version1')
    assert report['ready'] is False
    with pytest.raises(FileExistsError): prepare(db,tmp_path/'version1')
    with pytest.raises(ValueError,match='Insufficient'): train(tmp_path/'version1',tmp_path/'model',tmp_path/'out')


def test_real_cpu_training_smoke(tmp_path):
    """Synthetic tiny random model: validates plumbing only, NOT business quality."""
    import torch
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast
    torch.set_num_threads(1)
    db=tmp_path/'feedback.sqlite'; feedback_db(db,300)
    report=prepare(db,tmp_path/'dataset')
    assert report['ready']
    engine=Tokenizer(models.WordLevel({'[UNK]':0,'[EOS]':1,'Use':2,'documented':3,'setting':4,'safely':5,'.':6},unk_token='[UNK]'))
    engine.pre_tokenizer=pre_tokenizers.Whitespace()
    tokenizer=PreTrainedTokenizerFast(tokenizer_object=engine,unk_token='[UNK]',eos_token='[EOS]')
    model=GPT2LMHeadModel(GPT2Config(vocab_size=7,n_positions=128,n_embd=16,n_layer=1,n_head=2,bos_token_id=1,eos_token_id=1))
    base=tmp_path/'base'; model.save_pretrained(base); tokenizer.save_pretrained(base)
    row=json.loads((tmp_path/'dataset/train.jsonl').read_text(encoding='utf-8').splitlines()[0])
    encoded=encode_example(tokenizer,row,128)
    assert encoded['labels'][0]==-100 and encoded['labels'][-1]==1
    with pytest.raises(ValueError,match='context'): encode_example(tokenizer,row,3)
    result=train(tmp_path/'dataset',base,tmp_path/'candidate',max_length=128,lr=.001)
    candidate=GPT2LMHeadModel.from_pretrained(tmp_path/'candidate',local_files_only=True)
    assert not torch.equal(model.transformer.wte.weight,candidate.transformer.wte.weight)
    assert result['auto_deploy'] is False and result['candidate_generation']['samples']>=5
    assert (tmp_path/'candidate/predictions.jsonl').exists()
    manifest=tmp_path/'dataset/train.jsonl'
    manifest.write_text(manifest.read_text(encoding='utf-8')+'\n',encoding='utf-8')
    with pytest.raises(ValueError,match='changed'): train(tmp_path/'dataset',base,tmp_path/'other')


def test_credentials_grounding_and_issue_grouping(tmp_path):
    db=tmp_path/'feedback.sqlite'; feedback_db(db)
    with sqlite3.connect(db) as c:
        base={'messages':[{'role':'user','content':'How should this setting be used?'}],
              'draft':'Use the documented setting [3].','task_type':'solve',
              'citations':[{'path':'guide.md','snippet':'setting'}]}
        c.execute('INSERT INTO skill_feedback VALUES (?,?,?,?,?)',('ground','a/b','approved',json.dumps(base),'a'))
        base['task_type']='triage'; base['draft']='password=abcdefghijklmnop'
        c.execute('INSERT INTO skill_feedback VALUES (?,?,?,?,?)',('secret','a/b','approved',json.dumps(base),'b'))
        for i in range(2):
            base['draft']=f'Apply documented change {i} safely.'
            base['issue']={'number':123}
            c.execute('INSERT INTO skill_feedback VALUES (?,?,?,?,?)',(f'edit{i}','a/b','approved',json.dumps(base),'c'))
    examples,stats=collect(db)
    assert stats['unrecoverable_grounding']==1 and stats['possible_secret']==1
    edits=[r for r in examples if r['feedback_id'].startswith('edit')]
    assert len(edits)==2 and edits[0]['group']==edits[1]['group']
