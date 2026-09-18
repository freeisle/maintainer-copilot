async function load() {
  const res = await fetch('/api/pending');
  const items = await res.json();
  const q = document.getElementById('queue');
  if (!items.length) { q.innerHTML = '<p class="meta">暂无待审草稿</p>'; return; }
  q.innerHTML = items.map(it => `
    <div class="card" id="item-${it.id}">
      <div class="meta">repo: ${it.repo || '-'} · task: ${it.task_type || '-'} · 状态: ${it.status || 'pending'}</div>
      <pre>${escapeHtml(it.draft || '')}</pre>
      <div class="meta">引用: ${(it.citations || []).length} 条</div>
      <button class="btn ok" onclick="decide('${it.id}','approved')">批准发布</button>
      <button class="btn" onclick="edit('${it.id}')">编辑</button>
      <button class="btn no" onclick="decide('${it.id}','rejected')">驳回</button>
    </div>`).join('');
}

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

async function decide(id, decision) {
  const edited = document.getElementById('edit-' + id);
  await fetch(`/api/approve/${id}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({decision, edited_draft: edited ? edited.value : null})
  });
  load();
}

function edit(id) {
  const card = document.getElementById('item-' + id);
  const pre = card.querySelector('pre');
  const old = pre.textContent;
  pre.innerHTML = `<textarea id="edit-${id}">${escapeHtml(old)}</textarea>`;
}

load();
