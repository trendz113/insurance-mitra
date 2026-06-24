// ===== Tab switching =====
function switchTab(tab) {
  document.getElementById('tab-analyzer').classList.toggle('active', tab === 'analyzer');
  document.getElementById('tab-checklist').classList.toggle('active', tab === 'checklist');
  document.getElementById('tab-analyzer-btn').classList.toggle('active', tab === 'analyzer');
  document.getElementById('tab-checklist-btn').classList.toggle('active', tab === 'checklist');
  document.getElementById('page-title').textContent =
    tab === 'analyzer' ? 'Claim rejection case file' : 'Pre-purchase disclosure checklist';
}

// ===== Analyzer state =====
let an = {
  form: {},
  matched: null,
  secondary: [],
  answers: {},
  caseRef: null,
  score: null,
};

function showStep(stepId) {
  ['an-step-form', 'an-step-questions', 'an-step-verdict', 'an-step-letter'].forEach((id) => {
    document.getElementById(id).style.display = id === stepId ? 'block' : 'none';
  });
}

// enable "Open case file" only once rejection reason has content
document.addEventListener('DOMContentLoaded', () => {
  const reasonEl = document.getElementById('an-rejectionReason');
  if (reasonEl) {
    reasonEl.addEventListener('input', () => {
      document.getElementById('an-open-case-btn').disabled = !reasonEl.value.trim();
    });
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

  const res = await fetch('/api/analyze', {
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
    showStep('an-step-questions');
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
      ['yes', 'no'].forEach((val) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'yesno-btn';
        btn.textContent = val.charAt(0).toUpperCase() + val.slice(1);
        btn.onclick = () => {
          an.answers[ask.key] = val;
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
  const res = await fetch('/api/score', {
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

  showStep('an-step-verdict');
}

// ===== Letter generation (paid step — ₹99) =====
//
// /api/generate-letter now requires a verified Razorpay payment for this
// case. If payment hasn't happened yet, the backend responds with
// 402 + {"error": "payment_required"}. In that case we open Razorpay
// checkout first, verify the payment, then call generate-letter again.
// If the Claude call itself fails after payment, the backend does NOT
// burn the payment record, so simply retrying generateLetter() is safe
// and will not charge the user a second time.

async function generateLetter() {
  showStep('an-step-letter');
  document.getElementById('an-letter-loading').textContent = 'Drafting your letter…';
  document.getElementById('an-letter-loading').style.display = 'block';
  document.getElementById('an-letter-box').style.display = 'none';
  document.getElementById('an-letter-actions').style.display = 'none';

  await requestLetter();
}

async function requestLetter() {
  const res = await fetch('/api/generate-letter', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseRef: an.caseRef }),
  });
  const data = await res.json();

  if (res.status === 402 && data.error === 'payment_required') {
    // Payment needed before we can generate the letter. Kick off
    // Razorpay checkout; on success, retry requestLetter().
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
  const res = await fetch('/api/payments/create-order', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseRef: an.caseRef, caseType: 'health' }),
  });
  const data = await res.json();

  if (data.alreadyPaid) {
    // Edge case: payment was verified between our first attempt and now.
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
      const verifyRes = await fetch('/api/payments/verify', {
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
        // User closed the Razorpay popup without paying.
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
  showStep('an-step-verdict');
}

// ===== Case tracking (free — no payment required) =====

async function markGroSent() {
  const res = await fetch('/api/track/gro-sent', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseRef: an.caseRef, caseType: 'health' }),
  });
  const data = await res.json();
  if (res.ok) {
    showTrackingStartedView();
    alert('Tracking started. We will track your 15-day GRO follow-up deadline for this case.');
  } else {
    alert(data.error || 'Something went wrong starting tracking.');
  }
}

function showTrackingStartedView() {
  document.getElementById('an-pay-btn').style.display = 'none';
  document.getElementById('an-tracking-pitch').style.display = 'none';
  document.getElementById('an-tracking-paid-view').style.display = 'block';
}

function resetAnalyzer() {
  an = { form: {}, matched: null, secondary: [], answers: {}, caseRef: null, score: null };
  ['an-insurer', 'an-policyName', 'an-claimAmount', 'an-hospital', 'an-diagnosis', 'an-rejectionReason'].forEach((id) => {
    document.getElementById(id).value = '';
  });
  document.getElementById('an-open-case-btn').disabled = true;
  showStep('an-step-form');
}

function val(id) {
  const el = document.getElementById(id);
  return el ? el.value : '';
}

// ===== Checklist state =====
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
  const res = await fetch('/api/checklist/summary', {
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
