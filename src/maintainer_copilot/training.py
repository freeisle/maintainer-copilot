"""Local supervised-training pipeline, gated on reviewed, non-leaking data.

This module never downloads weights, changes the serving model or uploads data.
Rejected drafts are feedback, not supervised targets.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize(text):
    return " ".join(text.casefold().split())


def collect(db_path, reserved_questions=()):
    path = Path(db_path).resolve()
    stats = Counter()
    if not path.exists():
        return [], {"missing_feedback_database":1}
    with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='skill_feedback'").fetchone():
            return [], {"missing_feedback_table":1}
        rows = conn.execute("SELECT id,repo,decision,payload FROM skill_feedback ORDER BY ts,id").fetchall()
    reserved = {normalize(q) for q in reserved_questions}
    seen = set()
    examples = []
    for fid,repo,decision,raw in rows:
        stats["total_feedback"] += 1
        if decision not in ("approved","edited"):
            stats["rejected_draft"] += 1
            continue
        try:
            payload = json.loads(raw)
            answer = payload.get("final_draft") if decision == "edited" else payload.get("draft")
            messages = payload.get("messages") or []
            question = next((m["content"] for m in reversed(messages) if m.get("role")=="user"), "")
            source = payload.get("issue") or payload.get("pr") or {}
            question = question or "\n".join(str(source.get(k) or "") for k in ("title","body"))
            if not isinstance(question,str) or not isinstance(answer,str) or len(question.strip())<8 or len(answer.strip())<16:
                raise ValueError("missing text")
            citations = payload.get("citations") or []
            context = "\n".join(f"[{i}] {c.get('repo',repo)} {c.get('path','')}: {c.get('snippet','')}" for i,c in enumerate(citations,1))
            # Feedback contains filtered citations, not the original context order.
            # Only use contiguous markers; otherwise the original grounding is unrecoverable.
            refs = list(dict.fromkeys(int(v) for v in re.findall(r"\[(\d+)\]",answer)))
            if payload.get("task_type") == "solve" and (not refs or refs != list(range(1,len(citations)+1))):
                stats["unrecoverable_grounding"] += 1
                continue
            text = question+answer+context
            if re.search(r"(?i)(sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9]{20,}|-----BEGIN .*PRIVATE KEY|(?:api[_-]?key|password|token)\s*[:=]\s*\S{8,})",text):
                stats["possible_secret"] += 1
                continue
            if len(text)>16000:
                stats["oversize"] += 1
                continue
            if normalize(question) in reserved:
                stats["reserved_eval"] += 1
                continue
            group = fingerprint(repo+":"+str(source.get("number") or normalize(question)))
            identity = fingerprint(normalize(question)+"\n"+normalize(answer))
            if identity in seen:
                stats["duplicate"] += 1
                continue
            seen.add(identity)
            examples.append({"feedback_id":fid,"repo":repo,"group":group,
                "messages":[{"role":"system","content":"依据提供的资料回答，保留引用；资料不足应明确说明。"},
                            {"role":"user","content":question+"\n\n资料:\n"+context},
                            {"role":"assistant","content":answer}]})
        except (ValueError,TypeError,KeyError,AttributeError):
            stats["invalid"] += 1
    stats["eligible"] = len(examples)
    return examples,dict(stats)


def prepare(db_path, output, reserved_questions=()):
    output = Path(output)
    if output.exists():
        raise FileExistsError("Use a new dataset version directory")
    examples,stats = collect(db_path,reserved_questions)
    # Keep all revisions of one issue/question in one split, stable across exports.
    splits = {"train":[],"validation":[],"test":[]}
    for row in examples:
        bucket = int(row["group"][:8],16)%10
        splits["test" if bucket==0 else "validation" if bucket==1 else "train"].append(row)
    output.mkdir(parents=True)
    hashes = {}
    for name,rows in splits.items():
        text = "".join(json.dumps(row,ensure_ascii=False)+"\n" for row in rows)
        (output/f"{name}.jsonl").write_text(text,encoding="utf-8")
        hashes[name] = fingerprint(text)
    counts = {k:len(v) for k,v in splits.items()}
    report = {"quality":stats,"counts":counts,"sha256":hashes,
              "ready":counts["train"]>=40 and counts["validation"]>=5 and counts["test"]>=5,
              "gate":"minimum 40 train / 5 validation / 5 test; all human-reviewed; semantic review still required"}
    (output/"manifest.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report


def encode_example(tokenizer, row, max_length=1024):
    messages = row["messages"]
    if getattr(tokenizer,"chat_template",None):
        prefix = tokenizer.apply_chat_template(messages[:-1],tokenize=True,add_generation_prompt=True)
        full = tokenizer.apply_chat_template(messages,tokenize=True)
        if full[:len(prefix)] != prefix:
            raise ValueError("Chat template does not preserve assistant prefix")
    else:
        prompt = "".join(f"### {m['role']}\n{m['content']}\n" for m in messages[:-1])+"### assistant\n"
        prefix = tokenizer.encode(prompt,add_special_tokens=False)
        full = prefix+tokenizer.encode(messages[-1]["content"],add_special_tokens=False)
        if tokenizer.eos_token_id is not None:
            full.append(tokenizer.eos_token_id)
    if len(full)>max_length or len(prefix)>=len(full) or not prefix:
        raise ValueError("Training example exceeds context or has no target; no silent truncation")
    return {"input_ids":full,"attention_mask":[1]*len(full),"labels":[-100]*len(prefix)+full[len(prefix):]}


def loss_pass(model, encoded, optimizer=None):
    import torch
    model.train(optimizer is not None)
    total, count = 0.,0
    with torch.set_grad_enabled(optimizer is not None):
        for item in encoded:
            batch = {k:torch.tensor([v],device=model.device) for k,v in item.items()}
            if optimizer is not None:
                optimizer.zero_grad()
            loss = model(**batch).loss
            if not torch.isfinite(loss):
                raise ValueError("Non-finite training loss")
            if optimizer is not None:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
                optimizer.step()
            n=sum(v!=-100 for v in item["labels"][1:])
            total += loss.item()*n; count += n
    if not count:
        raise ValueError("No supervised tokens")
    return total/count


def generation_metrics(model, tokenizer, encoded):
    import torch
    model.eval()
    predictions=[]; scores=[]; format_ok=[]
    started=time.monotonic()
    with torch.no_grad():
        for item in encoded:
            prefix_len=next(i for i,v in enumerate(item['labels']) if v!=-100)
            inputs=torch.tensor([item['input_ids'][:prefix_len]],device=model.device)
            remaining=getattr(model.config,'max_position_embeddings',2048)-prefix_len
            if remaining<=0:
                raise ValueError('No generation context remaining')
            output=model.generate(inputs,attention_mask=torch.ones_like(inputs),do_sample=False,
                                  max_new_tokens=min(64,remaining),pad_token_id=tokenizer.eos_token_id)
            prediction=tokenizer.decode(output[0,prefix_len:],skip_special_tokens=True)
            target=tokenizer.decode(item['input_ids'][prefix_len:],skip_special_tokens=True)
            # Surface-format and token overlap checks do not establish factual correctness.
            expected=set(re.findall(r'\[\d+\]',target)); actual=set(re.findall(r'\[\d+\]',prediction))
            format_ok.append(bool(prediction.strip()) and expected==actual)
            a,b=Counter(prediction.split()),Counter(target.split())
            scores.append(2*sum((a&b).values())/max(1,sum(a.values())+sum(b.values())))
            predictions.append(prediction)
    return {'citation_format_exact':sum(format_ok)/len(format_ok),'token_f1':sum(scores)/len(scores),
            'seconds_per_sample':(time.monotonic()-started)/len(encoded),'samples':len(encoded)},predictions


def train(dataset, model_path, output, epochs=1, max_length=1024, lr=2e-5):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    dataset,model_path,output=map(Path,(dataset,model_path,output))
    manifest=json.loads((dataset/"manifest.json").read_text(encoding="utf-8"))
    if not manifest["ready"]:
        raise ValueError("Insufficient reviewed samples; training gate closed")
    if not model_path.is_dir() or output.exists() or not 1<=epochs<=10 or not 0<lr<=.01:
        raise ValueError("Existing local base model, new output directory and bounded training settings required")
    rows={}
    for name in ("train","validation","test"):
        text=(dataset/f"{name}.jsonl").read_text(encoding="utf-8")
        if fingerprint(text)!=manifest["sha256"][name]:
            raise ValueError("Dataset changed since validation")
        rows[name]=[json.loads(line) for line in text.splitlines() if line.strip()]
    groups=[{r['group'] for r in rows[k]} for k in rows]
    if any(groups[i]&groups[j] for i in range(3) for j in range(i+1,3)):
        raise ValueError("Cross-split source leakage")
    if len(rows['train'])<40 or len(rows['validation'])<5 or len(rows['test'])<5:
        raise ValueError("Insufficient reviewed samples")
    torch.manual_seed(42)
    tokenizer=AutoTokenizer.from_pretrained(model_path,local_files_only=True,trust_remote_code=False)
    model=AutoModelForCausalLM.from_pretrained(model_path,local_files_only=True,trust_remote_code=False)
    encoded={k:[encode_example(tokenizer,row,max_length) for row in v] for k,v in rows.items()}
    baseline={k:loss_pass(model,encoded[k]) for k in ('validation','test')}
    baseline_format,base_outputs=generation_metrics(model,tokenizer,encoded['test'])
    optimizer=torch.optim.AdamW(model.parameters(),lr=lr)
    history=[]; started=time.monotonic()
    for epoch in range(epochs):
        history.append({"epoch":epoch+1,"train_loss":loss_pass(model,encoded['train'],optimizer),
                        "validation_loss":loss_pass(model,encoded['validation'])})
    after={k:loss_pass(model,encoded[k]) for k in ('validation','test')}
    candidate_format,candidate_outputs=generation_metrics(model,tokenizer,encoded['test'])
    output.mkdir(parents=True)
    model.save_pretrained(output,safe_serialization=True); tokenizer.save_pretrained(output)
    report={"base_model":str(model_path.resolve()),"dataset":manifest,"seed":42,"epochs":history,
            "baseline_nll":baseline,"candidate_nll":after,"seconds":time.monotonic()-started,
            "baseline_generation":baseline_format,"candidate_generation":candidate_format,
            "torch":torch.__version__,"device":str(model.device),"auto_deploy":False,
            "note":"NLL is not answer quality. Run fixed QA/triage/format evaluations and human review before serving."}
    (output/'training-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (output/'predictions.jsonl').write_text(''.join(json.dumps({'feedback_id':row['feedback_id'],
        'baseline':base,'candidate':candidate},ensure_ascii=False)+'\n'
        for row,base,candidate in zip(rows['test'],base_outputs,candidate_outputs)),encoding='utf-8')
    return report
