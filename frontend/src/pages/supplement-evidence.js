import {heading, panel} from '../components/common/heading.js';
import {entries, records} from '../components/common/records.js';
import {supplementStatusLabels} from '../constants/workflow.js';
import {topic} from '../constants/project.js';
import {date, el, esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';

function supplementView(current, state, isReviewer) {
  const {report} = current;
  const metrics = Object.entries(report.metrics).map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');
  const sections = report.sections.map(x=>panel(x.title, entries(x.rows))).join('');
  const review = current.reviewer
    ? `<p data-testid="supplement-reviewed">${esc(current.reviewer)} · ${esc(date(current.reviewed_at))}</p><p>${esc(current.review_note)}</p>`
    : isReviewer ? '<form id="supplement-review-form"><label>复核意见<textarea name="note" maxlength="500" required></textarea></label><button>签署补核单</button></form>'
      : '<p class="note">待复核员签署</p>';
  const sameCase = state.supplements.filter(row=>row.case_id===current.case_id && row.id!==current.id);
  const history = sameCase.map(row=>[`<a href="#evidence/supplement/${row.id}">补核单 #${row.id}</a>`,
    esc(row.month), esc(supplementStatusLabels[row.status] || row.status),
    esc(row.reviewer || '待签署'), esc(date(row.created_at))]);
  return `${heading(topic.modules[3], `选月更正补核单 #${current.id} · ${esc(report.correction.month)} · ${esc(report.summary)}`,
      `<button id="supplement-download" data-id="${current.id}">导出补核证据</button>`)}
    <div class="spread evidence-top"><dl class="metrics">${metrics}</dl>
      <aside class="brief"><h2>补核单签署</h2><p class="note">依据回收核验证据 #${current.basis_evidence_id}，状态：${esc(supplementStatusLabels[current.status] || current.status)}</p>
      ${review}<details><summary>报告校验码</summary><code class="digest">${esc(current.digest)}</code></details></aside></div>
    ${panel('更正口径', entries([report.correction]))}
    ${sections}
    <details open><summary>同档案其他补核单（旧证据照常可查）</summary>
    ${records(['补核单','月份','状态','签署','建立时间'], history, '没有其他补核单。')}</details>`;
}

export function supplementEvidencePage(state, identity) {
  const current = state.supplements.find(row=>row.id===identity);
  if (!current) return heading(topic.modules[3]) + '<p class="empty">找不到该补核单。</p>';
  return supplementView(current, state, state.session.role==='reviewer');
}

export function bindSupplementEvidence(api, update, fail, identity) {
  const button = document.getElementById('supplement-download');
  if (button) button.addEventListener('click', async()=>{
    try {
      const response = await api(`/supplements/${identity}/download`, {response:true});
      const url = URL.createObjectURL(await response.blob());
      const link = el('a');
      link.href = url;
      link.download = `supplement-${identity}.json`;
      link.click();
      setTimeout(()=>URL.revokeObjectURL(url), 1000);
    } catch (error) { fail(error); }
  });
  submit('supplement-review-form', async data=>{
    await api(`/supplements/${identity}/review`, {method:'POST', data:{note:data.get('note')}});
    await update();
  }, fail);
}
