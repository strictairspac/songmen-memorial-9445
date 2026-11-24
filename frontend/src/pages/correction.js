import {heading, panel} from '../components/common/heading.js';
import {entries, records} from '../components/common/records.js';
import {supplementStatusLabels} from '../constants/workflow.js';
import {topic} from '../constants/project.js';
import {date, esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';
import {go} from '../router/hash.js';

export function correctionPage(state, identity) {
  const selectedCase = state.cases.find(row=>row.id===identity) || state.cases[0];
  if (!selectedCase) return heading(topic.modules[4]) + '<p class="empty">尚无档案，无法发起选月更正。</p>';
  const isOperator = state.session.role==='operator';
  const isReviewer = state.session.role==='reviewer';
  const adoption = state.adoption?.case_id===selectedCase.id ? state.adoption : null;
  const caseOrders = state.supplements.filter(row=>row.case_id===selectedCase.id);
  const evidenceRows = state.evidence.filter(row=>{
    const run = state.runs.find(r=>r.id===row.run_id);
    return run && run.case_id===selectedCase.id;
  });
  const basis = evidenceRows[0];

  const monthOptions = (selectedCase.payload.partitions || []).map(row=>{
    const label = row.month.slice(0, 7);
    const current = adoption?.adoption.find(a=>a['分区月份']===label);
    const hint = current ? `（当前采用：${current['当前采用证据']}）` : '';
    return `<option value="${esc(row.month)}">${esc(label)} · 原登记「${esc(row.state)}」${esc(hint)}</option>`;
  }).join('');

  const form = basis && isOperator ? `<form id="supplement-form" class="brief">
      <h2>选月更正登记</h2>
      <p class="note">沿用凭条账簿与保留截止日，仅复算选中月份；依据为回收核验证据 #${basis.id}。</p>
      <label>更正月份<select name="month" required>${monthOptions}</select></label>
      <label>更正后分区登记状态<select name="corrected_state" required>
        <option value="在册">在册</option><option value="待挂载">待挂载</option>
      </select></label>
      <input type="hidden" name="basis_evidence_id" value="${basis.id}">
      <div class="actions form-actions"><button>发起补核并复算</button></div>
    </form>` : `<aside class="brief"><h2>选月更正登记</h2><p class="note">${basis ? '仅经办员（档案岗）可发起更正。' : '该档案还没有回收核验证据，需先完成一次生命周期执行。'}</p></aside>`;

  const adoptionPanel = adoption ? panel('各月当前采用证据', entries(adoption.adoption))
    : '<p class="empty">加载各月证据采用情况…</p>';

  const orderRows = caseOrders.map(row=>{
    const signed = row.reviewer
      ? `${esc(row.reviewer)} · ${esc(date(row.reviewed_at))}` : (isReviewer ? '待签署' : '待复核员签署');
    const action = isReviewer && !row.reviewer
      ? `<button type="button" class="secondary supplement-review" data-id="${row.id}">签署补核</button>` : '';
    return [esc(row.month), esc(row.corrected_state), esc(supplementStatusLabels[row.status] || row.status),
            `#${row.basis_evidence_id}`, esc(row.owner), signed,
            `<a href="#evidence/supplement/${row.id}">查看</a> ${action}`];
  });
  const reviewDialog = isReviewer ? `<dialog id="supplement-dialog">
      <form id="supplement-review-form">
        <h2>签署补核单</h2>
        <label>复核意见<textarea name="note" maxlength="500" required></textarea></label>
        <div class="actions form-actions"><button type="button" id="supplement-dialog-confirm">确认签署</button>
        <button type="button" class="secondary" id="supplement-dialog-cancel">取消</button></div>
      </form></dialog>` : '';

  const caseLinks = state.cases.map(row=>[`<a href="#correction/${row.id}" ${row.id===selectedCase.id?'aria-current="true"':''}>${esc(row.title)}</a>`, esc(row.author)]);
  return `${heading(topic.modules[4], '档案岗选月更正分区登记，沿用原凭条与截止日，只复算选中月份。')}
    <div class="spread"><section aria-label="档案目录">${records(['档案','经办'], caseLinks)}
      ${panel('补核单台账', records(['月份','更正后状态','补核单状态','依据证据','经办','签署','操作'], orderRows, '该档案还没有补核单。'))}
    </section>${form}</div>
    ${adoptionPanel}
    ${reviewDialog}`;
}

export function bindCorrection(api, update, fail, state) {
  submit('supplement-form', async data=>{
    const caseId = (state.cases.find(row=>row.id===(Number(location.hash.split('/')[1])||0)) || state.cases[0]).id;
    const body = Object.fromEntries(data);
    body.basis_evidence_id = Number(body.basis_evidence_id);
    const result = await api(`/cases/${caseId}/supplements`, {method:'POST', data:body});
    await update();
    if (result.created) go(`evidence/supplement/${result.order.id}`);
  }, fail);

  const dialog = document.getElementById('supplement-dialog');
  let activeOrderId = 0;
  document.querySelectorAll('.supplement-review').forEach(button=>{
    button.addEventListener('click', ()=>{ activeOrderId = Number(button.dataset.id); dialog.returnValue=''; dialog.showModal(); });
  });
  document.getElementById('supplement-dialog-cancel')?.addEventListener('click', ()=>dialog.close());
  document.getElementById('supplement-dialog-confirm')?.addEventListener('click', async event=>{
    const form = document.getElementById('supplement-review-form');
    if (!form.reportValidity()) return;
    const note = new FormData(form).get('note');
    dialog.close('confirm');
    try {
      await api(`/supplements/${activeOrderId}/review`, {method:'POST', data:{note}});
      await update();
    } catch (error) { fail(error); }
  });
}
