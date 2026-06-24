// ===== API base URL =====
// This page is hosted as a static file on salarybit.in (trial period),
// but the actual Flask backend runs on Railway. Every API call must go to
// the absolute Railway URL -- a relative '/api/...' would try to hit
// salarybit.in/api/... which doesn't exist.
//
// credentials: 'include' is required so the browser sends/receives the
// anon_id session cookie across this cross-origin boundary. Without it,
// every request looks like a brand-new anonymous visitor and case lookups
// fail right after creation.
const API_BASE = 'https://web-production-b0a7.up.railway.app';

function apiFetch(path, options = {}) {
  return fetch(API_BASE + path, { ...options, credentials: 'include' });
}

// ===== Top-level tab switching =====
function switchTab(tab) {
  ['analyzer', 'life', 'checklist', 'schemes'].forEach((t) => {
    document.getElementById('tab-' + t).classList.toggle('active', t === tab);
  });
  ['analyzer', 'life', 'checklist', 'schemes'].forEach((t) => {
    const btn = document.getElementById('tab-' + t + '-btn');
    if (btn) btn.classList.toggle('active', t === tab);
  });

  const titles = {
    analyzer: 'Claim rejection case file',
    life: 'Life / death claim case file',
    checklist: "Buying a new policy",
    schemes: 'Government health schemes',
  };
  document.getElementById('page-title').textContent = titles[tab] || '';
}

// ===== Sub-tab switching (within "buying a new policy") =====
function switchSubTab(sub) {
  ['disclosure', 'recommend'].forEach((s) => {
    document.getElementById('subtab-' + s).classList.toggle('active', s === sub);
    document.getElementById('subtab-' + s).style.display = s === sub ? 'block' : 'none';
    const btn = document.getElementById('subtab-' + s + '-btn');
    if (btn) btn.classList.toggle('active', s === sub);
  });
}

function val(id) {
  const el = document.getElementById(id);
  return el ? el.value : '';
}

// ======================================================================
// ===== HEALTH CLAIM ANALYZER (free analysis + score, ₹99 letter) =====
// ======================================================================

let an = {
  form: {},
  matched: null,
  secondary: [],
  answers: {},
  caseRef: null,
  score: null,
};

function showStep(prefix, stepId, allSteps) {
  allSteps.forEach((id) => {
    document.getElementById(id).style.display = id === stepId ? 'block' : 'none';
  });
}

const AN_STEPS = ['an-step-form', 'an-step-questions', 'an-step-verdict', 'an-step-letter'];

document.addEventListener('DOMContentLoaded', () => {
  const reasonEl = document.getElementById('an-rejectionReason');
  if (reasonEl) {
    reasonEl.addEventListener('input', () => {
      document.getElementById('an-open-case-btn').disabled = !reasonEl.value.trim();
    });
  }

  const lfReasonEl = document.getElementById('lf-rejectionReason');
  if (lfReasonEl) {
    lfReasonEl.addEventListener('input', () => {
      document.getElementById('lf-open-case-btn').disabled = !lfReasonEl.value.trim();
    });
  }

  const prAgeEl = document.getElementById('pr-age');
  if (prAgeEl) {
    prAgeEl.addEventListener('input', updatePrButtonState);
  }
});

async function openCase() {
  an.form = {
    insurer: val('an-insurer'),
    policyName: val('an-policyName'),
    claimAmount: val('an-claimAmount'),
    hospital: val('an-hospital'),
    diagnosis: val('an-diagnosis'),
    rejectionReason: val('an-rejectionReason'),
  };

  const res = await apiFetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rejectionReason: an.form.rejectionReason }),
  });
  if (!res.ok) { alert('Something went wrong analyzing your case. Please try again.'); return; }
  const data = await res.json();
  an.matched = data.matched;
  an.secondary = data.secondary;
  an.answers = {};

  if (an.matched.asks.length === 0) {
    await submitScore();
  } else {
    renderQuestions();
    showStep('an', 'an-step-questions', AN_STEPS);
  }
}

function renderQuestions() {
  document.getElementById('an-match-tag').textContent = 'Matched category: ' + an.matched.label;

  const secNote = document.getElementById('an-secondary-note');
  if (an.secondary.length > 0) {
    secNote.style.display = 'block';
    secNote.textContent = 'Also worth raising separately: ' +
      an.secondary.map((r) => r.label).join(', ') + '. We\'ll focus the score on the primary issue, but mention this in your letter too.';
  } else {
    secNote.style.display = 'none';
  }

  const container = document.getElementById('an-questions-container');
  container.innerHTML = '';
  an.matched.asks.forEach((ask) => {
    const wrap = document.createElement('div');
    wrap.className = 'field';
    const label = document.createElement('label');
    label.textContent = ask.q;
    wrap.appendChild(label);

    if (ask.type === 'yesno') {
      const row = document.createElement('div');
      row.className = 'yesno-row';
      ['yes', 'no'].forEach((v) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'yesno-btn';
        btn.textContent = v.charAt(0).toUpperCase() + v.slice(1);
        btn.onclick = () => {
          an.answers[ask.key] = v;
          row.querySelectorAll('.yesno-btn').forEach((b) => b.classList.remove('active'));
          btn.classList.add('active');
          updateVerdictButtonState();
        };
        row.appendChild(btn);
      });
      wrap.appendChild(row);
    } else {
      const input = document.createElement('input');
      input.type = 'number';
      input.oninput = () => {
        an.answers[ask.key] = input.value;
        updateVerdictButtonState();
      };
      wrap.appendChild(input);
    }
    container.appendChild(wrap);
  });
  updateVerdictButtonState();
}

function updateVerdictButtonState() {
  const allAnswered = an.matched.asks.every((a) => an.answers[a.key] !== undefined && an.answers[a.key] !== '');
  document.getElementById('an-get-verdict-btn').disabled = !allAnswered;
}

async function getVerdict() {
  await submitScore();
}

async function submitScore() {
  const res = await apiFetch('/api/score', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ruleId: an.matched.id,
      answers: an.answers,
      form: an.form,
      secondaryIds: an.secondary.map((r) => r.id),
    }),
  });
  if (!res.ok) { alert('Something went wrong scoring your case. Please try again.'); return; }
  const data = await res.json();
  an.caseRef = data.caseRef;
  an.score = data.score;

  const secNote = document.getElementById('an-verdict-secondary-note');
  if (an.secondary.length > 0) {
    secNote.style.display = 'block';
    secNote.textContent = 'Also worth raising separately: ' + an.secondary.map((r) => r.label).join(', ') + '.';
  } else {
    secNote.style.display = 'none';
  }

  const seal = document.getElementById('an-seal');
  seal.style.borderColor = data.band.color;
  seal.style.color = data.band.color;
  document.getElementById('an-seal-score').textContent = data.score;
  document.getElementById('an-seal-label').textContent = data.band.label;
  document.getElementById('an-verdict-explain').textContent = data.ruleExplain;

  showStep('an', 'an-step-verdict', AN_STEPS);
}

// Letter generation is the paid step (₹99). If /api/generate-letter responds
// with 402 + payment_required, we open Razorpay checkout, verify, then retry.
// If the Claude call itself fails after payment, the backend does NOT burn
// the payment record, so retrying is always safe and never double-charges.

async function generateLetter() {
  showStep('an', 'an-step-letter', AN_STEPS);
  document.getElementById('an-letter-loading').textContent = 'Drafting your letter…';
  document.getElementById('an-letter-loading').style.display = 'block';
  document.getElementById('an-letter-box').style.display = 'none';
  document.getElementById('an-letter-actions').style.display = 'none';

  await requestLetter();
}

async function requestLetter() {
  const res = await apiFetch('/api/generate-letter', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseRef: an.caseRef }),
  });
  const data = await res.json();

  if (res.status === 402 && data.error === 'payment_required') {
    document.getElementById('an-letter-loading').textContent =
      'This letter is a one-time ₹99 — complete payment to continue.';
    await startLetterPayment();
    return;
  }

  document.getElementById('an-letter-loading').style.display = 'none';

  const box = document.getElementById('an-letter-box');
  box.textContent = data.letter || data.error || 'Something went wrong.';
  box.style.display = 'block';
  document.getElementById('an-letter-actions').style.display = 'flex';
  an.lastLetter = data.letter || '';
  document.getElementById('an-tracking-box').style.display = 'block';
}

async function startLetterPayment() {
  const res = await apiFetch('/api/payments/create-order', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseRef: an.caseRef, caseType: 'health' }),
  });
  const data = await res.json();

  if (data.alreadyPaid) {
    await requestLetter();
    return;
  }
  if (!res.ok) {
    document.getElementById('an-letter-loading').style.display = 'none';
    const box = document.getElementById('an-letter-box');
    box.textContent = data.error || 'Could not start payment. Please try again.';
    box.style.display = 'block';
    return;
  }

  const options = {
    key: data.keyId,
    amount: data.amount,
    currency: data.currency,
    name: 'Insurance Mitra',
    description: 'Grievance letter — ' + an.caseRef,
    order_id: data.orderId,
    handler: async function (response) {
      const verifyRes = await apiFetch('/api/payments/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          razorpay_order_id: response.razorpay_order_id,
          razorpay_payment_id: response.razorpay_payment_id,
          razorpay_signature: response.razorpay_signature,
        }),
      });
      const verifyData = await verifyRes.json();
      if (verifyRes.ok && verifyData.verified) {
        document.getElementById('an-letter-loading').textContent = 'Payment received. Drafting your letter…';
        await requestLetter();
      } else {
        document.getElementById('an-letter-loading').style.display = 'none';
        const box = document.getElementById('an-letter-box');
        box.textContent = 'Payment could not be verified. If money was deducted, please contact support with your case reference: ' + an.caseRef;
        box.style.display = 'block';
      }
    },
    modal: {
      ondismiss: function () {
        document.getElementById('an-letter-loading').style.display = 'none';
        const box = document.getElementById('an-letter-box');
        box.textContent = 'Payment was not completed. Click "Draft my grievance letter" again whenever you\'re ready.';
        box.style.display = 'block';
      },
    },
    theme: { color: '#0F1B2E' },
  };

  const rzp = new Razorpay(options);
  rzp.open();
}

function copyLetter() {
  navigator.clipboard.writeText(an.lastLetter || '');
  const btn = document.getElementById('an-copy-btn');
  const orig = btn.textContent;
  btn.textContent = 'Copied ✓';
  setTimeout(() => { btn.textContent = orig; }, 2000);
}

function backToVerdict() {
  showStep('an', 'an-step-verdict', AN_STEPS);
}

async function markGroSent() {
  const res = await apiFetch('/api/track/gro-sent', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseRef: an.caseRef, caseType: 'health' }),
  });
  const data = await res.json();
  if (res.ok) {
    document.getElementById('an-pay-btn').style.display = 'none';
    document.getElementById('an-tracking-pitch').style.display = 'none';
    document.getElementById('an-tracking-paid-view').style.display = 'block';
    alert('Tracking started. We will track your 15-day GRO follow-up deadline for this case.');
  } else {
    alert(data.error || 'Something went wrong starting tracking.');
  }
}

function resetAnalyzer() {
  an = { form: {}, matched: null, secondary: [], answers: {}, caseRef: null, score: null };
  ['an-insurer', 'an-policyName', 'an-claimAmount', 'an-hospital', 'an-diagnosis', 'an-rejectionReason'].forEach((id) => {
    document.getElementById(id).value = '';
  });
  document.getElementById('an-open-case-btn').disabled = true;
  showStep('an', 'an-step-form', AN_STEPS);
}

// ======================================================================
// ===== LIFE / DEATH CLAIM ANALYZER (entirely free, template letter) ===
// ======================================================================

let lf = {
  form: {},
  matched: null,
  secondary: [],
  answers: {},
  caseRef: null,
  score: null,
};

const LF_STEPS = ['lf-step-form', 'lf-step-questions', 'lf-step-verdict', 'lf-step-letter'];

async function openLifeCase() {
  lf.form = {
    insurer: val('lf-insurer'),
    policyName: val('lf-policyName'),
    deceasedName: val('lf-deceasedName'),
    dateOfDeath: val('lf-dateOfDeath'),
    rejectionReason: val('lf-rejectionReason'),
  };

  const res = await apiFetch('/api/life/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rejectionReason: lf.form.rejectionReason }),
  });
  if (!res.ok) { alert('Something went wrong analyzing your case. Please try again.'); return; }
  const data = await res.json();
  lf.matched = data.matched;
  lf.secondary = data.secondary;
  lf.answers = {};

  if (lf.matched.asks.length === 0) {
    await submitLifeScore();
  } else {
    renderLifeQuestions();
    showStep('lf', 'lf-step-questions', LF_STEPS);
  }
}

function renderLifeQuestions() {
  document.getElementById('lf-match-tag').textContent = 'Matched category: ' + lf.matched.label;

  const secNote = document.getElementById('lf-secondary-note');
  if (lf.secondary.length > 0) {
    secNote.style.display = 'block';
    secNote.textContent = 'Also worth raising separately: ' +
      lf.secondary.map((r) => r.label).join(', ') + '.';
  } else {
    secNote.style.display = 'none';
  }

  const container = document.getElementById('lf-questions-container');
  container.innerHTML = '';
  lf.matched.asks.forEach((ask) => {
    const wrap = document.createElement('div');
    wrap.className = 'field';
    const label = document.createElement('label');
    label.textContent = ask.q;
    wrap.appendChild(label);

    if (ask.type === 'yesno') {
      const row = document.createElement('div');
      row.className = 'yesno-row';
      ['yes', 'no'].forEach((v) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'yesno-btn';
        btn.textContent = v.charAt(0).toUpperCase() + v.slice(1);
        btn.onclick = () => {
          lf.answers[ask.key] = v;
          row.querySelectorAll('.yesno-btn').forEach((b) => b.classList.remove('active'));
          btn.classList.add('active');
          updateLifeVerdictButtonState();
        };
        row.appendChild(btn);
      });
      wrap.appendChild(row);
    } else {
      const input = document.createElement('input');
      input.type = 'number';
      input.oninput = () => {
        lf.answers[ask.key] = input.value;
        updateLifeVerdictButtonState();
      };
      wrap.appendChild(input);
    }
    container.appendChild(wrap);
  });
  updateLifeVerdictButtonState();
}

function updateLifeVerdictButtonState() {
  const allAnswered = lf.matched.asks.every((a) => lf.answers[a.key] !== undefined && lf.answers[a.key] !== '');
  document.getElementById('lf-get-verdict-btn').disabled = !allAnswered;
}

async function getLifeVerdict() {
  await submitLifeScore();
}

async function submitLifeScore() {
  const res = await apiFetch('/api/life/score', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ruleId: lf.matched.id,
      answers: lf.answers,
      form: lf.form,
      secondaryIds: lf.secondary.map((r) => r.id),
    }),
  });
  if (!res.ok) { alert('Something went wrong scoring your case. Please try again.'); return; }
  const data = await res.json();
  lf.caseRef = data.caseRef;
  lf.score = data.score;

  const secNote = document.getElementById('lf-verdict-secondary-note');
  if (lf.secondary.length > 0) {
    secNote.style.display = 'block';
    secNote.textContent = 'Also worth raising separately: ' + lf.secondary.map((r) => r.label).join(', ') + '.';
  } else {
    secNote.style.display = 'none';
  }

  const seal = document.getElementById('lf-seal');
  seal.style.borderColor = data.band.color;
  seal.style.color = data.band.color;
  document.getElementById('lf-seal-score').textContent = data.score;
  document.getElementById('lf-seal-label').textContent = data.band.label;
  document.getElementById('lf-verdict-explain').textContent = data.ruleExplain;

  showStep('lf', 'lf-step-verdict', LF_STEPS);
}

async function generateLifeLetter() {
  const res = await apiFetch('/api/life/generate-letter', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseRef: lf.caseRef }),
  });
  const data = await res.json();
  const box = document.getElementById('lf-letter-box');
  box.textContent = data.letter || data.error || 'Something went wrong.';
  lf.lastLetter = data.letter || '';
  showStep('lf', 'lf-step-letter', LF_STEPS);
}

function copyLifeLetter() {
  navigator.clipboard.writeText(lf.lastLetter || '');
  const btn = document.getElementById('lf-copy-btn');
  const orig = btn.textContent;
  btn.textContent = 'Copied ✓';
  setTimeout(() => { btn.textContent = orig; }, 2000);
}

function backToLifeVerdict() {
  showStep('lf', 'lf-step-verdict', LF_STEPS);
}

async function markLifeGroSent() {
  const res = await apiFetch('/api/track/gro-sent', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseRef: lf.caseRef, caseType: 'life' }),
  });
  const data = await res.json();
  if (res.ok) {
    document.getElementById('lf-track-btn').style.display = 'none';
    document.getElementById('lf-tracking-pitch').style.display = 'none';
    document.getElementById('lf-tracking-started-view').style.display = 'block';
    alert('Tracking started. We will track your 15-day GRO follow-up deadline for this case.');
  } else {
    alert(data.error || 'Something went wrong starting tracking.');
  }
}

function resetLifeAnalyzer() {
  lf = { form: {}, matched: null, secondary: [], answers: {}, caseRef: null, score: null };
  ['lf-insurer', 'lf-policyName', 'lf-deceasedName', 'lf-dateOfDeath', 'lf-rejectionReason'].forEach((id) => {
    document.getElementById(id).value = '';
  });
  document.getElementById('lf-open-case-btn').disabled = true;
  showStep('lf', 'lf-step-form', LF_STEPS);
}

// ======================================================================
// ===== DISCLOSURE CHECKLIST (free, unchanged) =====
// ======================================================================

let ck = { checked: {}, notes: {} };

function toggleChecklistItem(itemId, isChecked) {
  ck.checked[itemId] = isChecked;
  const noteInput = document.getElementById('note-' + itemId);
  noteInput.style.display = isChecked ? 'block' : 'none';
  if (!isChecked) {
    noteInput.value = '';
    delete ck.notes[itemId];
  } else {
    noteInput.oninput = () => { ck.notes[itemId] = noteInput.value; };
  }
}

async function generateChecklistSummary() {
  const profile = { name: val('ck-name'), insurer: val('ck-insurer') };
  const res = await apiFetch('/api/checklist/summary', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ checked: ck.checked, notes: ck.notes, profile }),
  });
  if (!res.ok) { alert('Something went wrong generating your summary. Please try again.'); return; }
  const data = await res.json();
  document.getElementById('ck-summary-box').textContent = data.summary;
  document.getElementById('ck-step-form').style.display = 'none';
  document.getElementById('ck-step-summary').style.display = 'block';
  ck.lastSummary = data.summary;
}

function copySummary() {
  navigator.clipboard.writeText(ck.lastSummary || '');
  const btn = document.getElementById('ck-copy-btn');
  const orig = btn.textContent;
  btn.textContent = 'Copied ✓';
  setTimeout(() => { btn.textContent = orig; }, 2000);
}

function backToChecklist() {
  document.getElementById('ck-step-summary').style.display = 'none';
  document.getElementById('ck-step-form').style.display = 'block';
}

// ======================================================================
// ===== POLICY RECOMMENDATION (paid, ₹99, AI-generated) =====
// ======================================================================

let pr = { recommendationRef: null, lastRecommendation: '' };

function togglePrConditionDetail() {
  const hasConditions = val('pr-hasConditions') === 'yes';
  document.getElementById('pr-condition-detail-field').style.display = hasConditions ? 'block' : 'none';
}

function updatePrButtonState() {
  const age = val('pr-age');
  document.getElementById('pr-submit-btn').disabled = !age || Number(age) < 18 || Number(age) > 100;
}

async function requestPolicyRecommendation() {
  const inputs = {
    age: val('pr-age'),
    dependents: val('pr-dependents'),
    hasExistingConditions: val('pr-hasConditions'),
    existingConditionsDetail: val('pr-conditionDetail'),
    monthlyBudget: val('pr-budget'),
    city: val('pr-city'),
  };

  document.getElementById('pr-step-form').style.display = 'none';
  document.getElementById('pr-step-result').style.display = 'block';
  document.getElementById('pr-loading').textContent = 'Working on your recommendation…';
  document.getElementById('pr-loading').style.display = 'block';
  document.getElementById('pr-result-box').style.display = 'none';
  document.getElementById('pr-result-actions').style.display = 'none';

  const reqRes = await apiFetch('/api/policy/recommend-request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(inputs),
  });
  if (!reqRes.ok) {
    const errData = await reqRes.json();
    showPrError(errData.error || 'Something went wrong. Please try again.');
    return;
  }
  const reqData = await reqRes.json();
  pr.recommendationRef = reqData.recommendationRef;

  await requestRecommendation();
}

async function requestRecommendation() {
  const res = await apiFetch('/api/policy/recommend', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ recommendationRef: pr.recommendationRef }),
  });
  const data = await res.json();

  if (res.status === 402 && data.error === 'payment_required') {
    document.getElementById('pr-loading').textContent =
      'This recommendation is a one-time ₹99 — complete payment to continue.';
    await startRecommendationPayment();
    return;
  }

  if (!res.ok) {
    showPrError(data.error || 'Something went wrong. Please try again.');
    return;
  }

  document.getElementById('pr-loading').style.display = 'none';
  const box = document.getElementById('pr-result-box');
  box.textContent = data.recommendation || 'Something went wrong.';
  box.style.display = 'block';
  document.getElementById('pr-result-actions').style.display = 'flex';
  pr.lastRecommendation = data.recommendation || '';
}

async function startRecommendationPayment() {
  const res = await apiFetch('/api/payments/create-order', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ recommendationRef: pr.recommendationRef }),
  });
  const data = await res.json();

  if (data.alreadyPaid) {
    await requestRecommendation();
    return;
  }
  if (!res.ok) {
    showPrError(data.error || 'Could not start payment. Please try again.');
    return;
  }

  const options = {
    key: data.keyId,
    amount: data.amount,
    currency: data.currency,
    name: 'Insurance Mitra',
    description: 'Policy recommendation — ' + pr.recommendationRef,
    order_id: data.orderId,
    handler: async function (response) {
      const verifyRes = await apiFetch('/api/payments/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          razorpay_order_id: response.razorpay_order_id,
          razorpay_payment_id: response.razorpay_payment_id,
          razorpay_signature: response.razorpay_signature,
        }),
      });
      const verifyData = await verifyRes.json();
      if (verifyRes.ok && verifyData.verified) {
        document.getElementById('pr-loading').textContent = 'Payment received. Working on your recommendation…';
        await requestRecommendation();
      } else {
        showPrError('Payment could not be verified. If money was deducted, please contact support with your reference: ' + pr.recommendationRef);
      }
    },
    modal: {
      ondismiss: function () {
        showPrError('Payment was not completed. Click "Get my recommendation" again whenever you\'re ready.');
      },
    },
    theme: { color: '#0F1B2E' },
  };

  const rzp = new Razorpay(options);
  rzp.open();
}

function showPrError(message) {
  document.getElementById('pr-loading').style.display = 'none';
  const box = document.getElementById('pr-result-box');
  box.textContent = message;
  box.style.display = 'block';
}

function copyRecommendation() {
  navigator.clipboard.writeText(pr.lastRecommendation || '');
  const btn = document.getElementById('pr-copy-btn');
  const orig = btn.textContent;
  btn.textContent = 'Copied ✓';
  setTimeout(() => { btn.textContent = orig; }, 2000);
}

function backToPrForm() {
  document.getElementById('pr-step-result').style.display = 'none';
  document.getElementById('pr-step-form').style.display = 'block';
}
