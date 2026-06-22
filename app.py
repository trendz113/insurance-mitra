"""
app.py — Insurance Mitra Flask backend.

The database schema is initialized automatically on startup (see
_init_schema_with_retry below) -- there is no manual `psql -f schema.sql`
step needed. CREATE TABLE IF NOT EXISTS means this is safe to run on every
deploy; it only creates what's missing and never touches existing data.

Routes:
  GET  /                          -> landing/redirect to dashboard or login
  GET  /signup, POST /signup      -> create account
  GET  /login, POST /login        -> session login
  GET  /logout                    -> clear session
  GET  /dashboard                 -> tabs: analyzer / checklist (requires login)

  POST /api/analyze               -> classify rejection text, return rule + questions
  POST /api/score                 -> compute score from answers, save case, return verdict
  POST /api/generate-letter       -> call Claude API server-side, save + return letter
  GET  /api/cases                 -> list user's saved cases

  POST /api/checklist/summary     -> build + save disclosure summary text
  GET  /api/checklist/summaries   -> list user's saved summaries
"""
import os
import time
import logging
import functools
import requests
from flask import Flask, request, jsonify, session, redirect, url_for, render_template
from werkzeug.security import generate_password_hash, check_password_hash

import db
import rules
import sheets
import payments

logger = logging.getLogger("app")

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_MODEL = "claude-sonnet-4-6"


def _init_schema_with_retry(max_attempts=5, delay_seconds=3):
    """
    Runs schema.sql against the database on startup, so there is no manual
    SQL step. CREATE TABLE IF NOT EXISTS makes this safe to run on every
    boot -- it only creates what's missing, never touches existing data.
    Retries a few times since Postgres can take a moment to become
    reachable right after a fresh deploy.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            db.init_schema()
            logger.info("Database schema initialized successfully.")
            return
        except Exception as e:
            logger.warning(f"Schema init attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                time.sleep(delay_seconds)
    logger.error("Schema initialization failed after all retries. "
                 "The app will still start, but database operations will likely fail "
                 "until this is resolved.")


_init_schema_with_retry()


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not authenticated"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ---------- Pages ----------

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    name = request.form.get("name", "").strip()

    if not email or not password:
        return render_template("signup.html", error="Email and password are required.")
    if len(password) < 8:
        return render_template("signup.html", error="Password must be at least 8 characters.")
    if db.get_user_by_email(email):
        return render_template("signup.html", error="An account with this email already exists.")

    password_hash = generate_password_hash(password)
    user = db.create_user(email, password_hash, name)
    session["user_id"] = user["id"]
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    user = db.get_user_by_email(email)

    if not user or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Invalid email or password.")

    session["user_id"] = user["id"]
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = db.get_user_by_id(session["user_id"])
    return render_template(
        "dashboard.html",
        user=user,
        checklist_groups=rules.CHECKLIST_GROUPS,
    )


# ---------- API: Claim Rejection Analyzer ----------

@app.route("/api/analyze", methods=["POST"])
@login_required
def api_analyze():
    data = request.get_json(force=True)
    reason_text = (data.get("rejectionReason") or "").strip()
    if not reason_text:
        return jsonify({"error": "rejectionReason is required"}), 400

    matched, secondary = rules.classify(reason_text)
    return jsonify({
        "matched": {
            "id": matched["id"],
            "label": matched["label"],
            "explain": matched["explain"],
            "asks": matched["asks"],
        },
        "secondary": [{"id": r["id"], "label": r["label"]} for r in secondary],
    })


@app.route("/api/score", methods=["POST"])
@login_required
def api_score():
    data = request.get_json(force=True)
    rule_id = data.get("ruleId")
    answers = data.get("answers") or {}
    form = data.get("form") or {}

    if rule_id not in rules.RULES_BY_ID:
        return jsonify({"error": "Unknown ruleId"}), 400

    score = rules.compute_score(rule_id, answers)
    b = rules.band(score)
    secondary_ids = data.get("secondaryIds") or []

    case = db.create_claim_case(
        user_id=session["user_id"],
        insurer=form.get("insurer"),
        policy_name=form.get("policyName"),
        claim_amount=form.get("claimAmount") or None,
        hospital=form.get("hospital"),
        diagnosis=form.get("diagnosis"),
        rejection_reason=form.get("rejectionReason"),
        matched_rule_id=rule_id,
        secondary_rule_ids=secondary_ids,
        answers=answers,
        score=score,
    )
    sheets.upsert_case(case, "health")

    return jsonify({
        "caseRef": case["case_ref"],
        "score": score,
        "band": b,
        "ruleLabel": rules.RULES_BY_ID[rule_id]["label"],
        "ruleExplain": rules.RULES_BY_ID[rule_id]["explain"],
    })


@app.route("/api/generate-letter", methods=["POST"])
@login_required
def api_generate_letter():
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    if not case_ref:
        return jsonify({"error": "caseRef is required"}), 400

    case = db.get_case_by_ref(case_ref, session["user_id"])
    if not case:
        return jsonify({"error": "Case not found"}), 404

    rule = rules.RULES_BY_ID.get(case["matched_rule_id"])
    secondary_text = ""
    if case["secondary_rule_ids"]:
        labels = [rules.RULES_BY_ID[rid]["label"] for rid in case["secondary_rule_ids"]
                  if rid in rules.RULES_BY_ID]
        if labels:
            secondary_text = f"\n- Additional issue also worth raising: {', '.join(labels)}"

    b = rules.band(case["score"])

    prompt = f"""You are drafting a formal but plain-language grievance letter for an Indian health insurance policyholder whose claim was rejected, to send to the insurer's Grievance Redressal Officer.

Facts:
- Insurer: {case['insurer']}
- Policy: {case['policy_name']}
- Claim amount: ₹{case['claim_amount']}
- Hospital: {case['hospital']}
- Diagnosis: {case['diagnosis']}
- Stated rejection reason: {case['rejection_reason']}
- Our assessment: {rule['label']}. {rule['explain']}{secondary_text}
- Case strength assessment: {b['label']} ({case['score']}/100)

Write a formal grievance letter (not an email with subject line, just the letter body) that:
1. States the facts plainly
2. Politely but firmly disputes the rejection using the specific regulatory/contractual point above
3. Requests reconsideration within 15 working days
4. Notes that escalation to IRDAI's Bima Bharosa portal and the Insurance Ombudsman will follow if unresolved
5. Is signed "Yours sincerely, [Policyholder Name]"

Keep it under 350 words, formal Indian business letter register, no markdown formatting, plain paragraphs only."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        letter_text = "".join(
            block.get("text", "") for block in result.get("content", [])
            if block.get("type") == "text"
        ).strip()
        if not letter_text:
            letter_text = "Could not generate letter. Please try again."
    except requests.RequestException:
        return jsonify({"error": "Letter generation failed. Please try again in a moment."}), 502

    case = db.update_case_letter(case_ref, session["user_id"], letter_text)
    if case:
        sheets.upsert_case(case, "health")
    return jsonify({"letter": letter_text})


@app.route("/api/cases", methods=["GET"])
@login_required
def api_list_cases():
    cases = db.list_cases_for_user(session["user_id"])
    return jsonify({"cases": cases})


# ---------- API: Pre-purchase Disclosure Checklist ----------

@app.route("/api/checklist/summary", methods=["POST"])
@login_required
def api_checklist_summary():
    data = request.get_json(force=True)
    checked = data.get("checked") or {}
    notes = data.get("notes") or {}
    profile = data.get("profile") or {}

    summary_text = rules.build_disclosure_summary(checked, notes, profile)

    db.save_disclosure_summary(
        user_id=session["user_id"],
        insurer=profile.get("insurer"),
        profile_name=profile.get("name"),
        checked_items=checked,
        notes=notes,
        summary_text=summary_text,
    )

    return jsonify({"summary": summary_text})


@app.route("/api/checklist/summaries", methods=["GET"])
@login_required
def api_list_summaries():
    summaries = db.list_disclosure_summaries_for_user(session["user_id"])
    return jsonify({"summaries": summaries})


# ---------- API: Life/Death Claim Analyzer ----------

@app.route("/api/life/analyze", methods=["POST"])
@login_required
def api_life_analyze():
    data = request.get_json(force=True)
    reason_text = (data.get("rejectionReason") or "").strip()
    if not reason_text:
        return jsonify({"error": "rejectionReason is required"}), 400

    matched, secondary = rules.classify_life(reason_text)
    return jsonify({
        "matched": {
            "id": matched["id"],
            "label": matched["label"],
            "explain": matched["explain"],
            "asks": matched["asks"],
        },
        "secondary": [{"id": r["id"], "label": r["label"]} for r in secondary],
    })


@app.route("/api/life/score", methods=["POST"])
@login_required
def api_life_score():
    data = request.get_json(force=True)
    rule_id = data.get("ruleId")
    answers = data.get("answers") or {}
    form = data.get("form") or {}

    if rule_id not in rules.LIFE_RULES_BY_ID:
        return jsonify({"error": "Unknown ruleId"}), 400

    score = rules.compute_life_score(rule_id, answers)
    b = rules.band(score)
    secondary_ids = data.get("secondaryIds") or []

    case = db.create_life_claim_case(
        user_id=session["user_id"],
        insurer=form.get("insurer"),
        policy_name=form.get("policyName"),
        deceased_name=form.get("deceasedName"),
        date_of_death=form.get("dateOfDeath") or None,
        rejection_reason=form.get("rejectionReason"),
        matched_rule_id=rule_id,
        secondary_rule_ids=secondary_ids,
        answers=answers,
        score=score,
    )
    sheets.upsert_case(case, "life")

    return jsonify({
        "caseRef": case["case_ref"],
        "score": score,
        "band": b,
        "ruleLabel": rules.LIFE_RULES_BY_ID[rule_id]["label"],
        "ruleExplain": rules.LIFE_RULES_BY_ID[rule_id]["explain"],
    })


@app.route("/api/life/generate-letter", methods=["POST"])
@login_required
def api_life_generate_letter():
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    if not case_ref:
        return jsonify({"error": "caseRef is required"}), 400

    case = db.get_life_case_by_ref(case_ref, session["user_id"])
    if not case:
        return jsonify({"error": "Case not found"}), 404

    rule = rules.LIFE_RULES_BY_ID.get(case["matched_rule_id"])
    secondary = [rules.LIFE_RULES_BY_ID[rid] for rid in (case["secondary_rule_ids"] or [])
                 if rid in rules.LIFE_RULES_BY_ID]

    form = {
        "insurer": case["insurer"],
        "policyName": case["policy_name"],
        "deceasedName": case["deceased_name"],
        "dateOfDeath": str(case["date_of_death"]) if case["date_of_death"] else None,
        "rejectionReason": case["rejection_reason"],
    }

    letter_text = rules.build_life_letter_template(form, rule, secondary, case["score"])

    updated_case = db.update_life_case_letter(case_ref, session["user_id"], letter_text)
    if updated_case:
        sheets.upsert_case(updated_case, "life")

    return jsonify({"letter": letter_text})


@app.route("/api/life/cases", methods=["GET"])
@login_required
def api_list_life_cases():
    cases = db.list_life_cases_for_user(session["user_id"])
    return jsonify({"cases": cases})


# ---------- API: Case Tracking (works for both health and life cases) ----------

def _table_for_type(case_type):
    return "life_claim_cases" if case_type == "life" else "claim_cases"


def _get_case_for_sync(case_ref, user_id, case_type):
    if case_type == "life":
        return db.get_life_case_by_ref(case_ref, user_id)
    return db.get_case_by_ref(case_ref, user_id)


@app.route("/api/track/gro-sent", methods=["POST"])
@login_required
def api_track_gro_sent():
    import datetime
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")
    sent_date_str = data.get("sentDate")

    # Health claims require a verified payment before tracking unlocks.
    # Life/death claims stay free, deliberately, at this stage.
    if case_type == "health" and not db.has_verified_payment(case_ref, session["user_id"]):
        return jsonify({
            "error": "payment_required",
            "message": "Tracking for this case requires a one-time ₹199 payment. Create a payment order via /api/payments/create-order first.",
        }), 402

    try:
        sent_date = datetime.date.fromisoformat(sent_date_str) if sent_date_str else datetime.date.today()
    except ValueError:
        return jsonify({"error": "Invalid sentDate format, expected YYYY-MM-DD"}), 400

    case = db.mark_gro_sent(case_ref, session["user_id"], sent_date, table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/irdai-filed", methods=["POST"])
@login_required
def api_track_irdai_filed():
    import datetime
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")
    filed_date_str = data.get("filedDate")
    try:
        filed_date = datetime.date.fromisoformat(filed_date_str) if filed_date_str else datetime.date.today()
    except ValueError:
        return jsonify({"error": "Invalid filedDate format, expected YYYY-MM-DD"}), 400

    case = db.mark_irdai_filed(case_ref, session["user_id"], filed_date, table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/ombudsman-filed", methods=["POST"])
@login_required
def api_track_ombudsman_filed():
    import datetime
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")
    filed_date_str = data.get("filedDate")
    try:
        filed_date = datetime.date.fromisoformat(filed_date_str) if filed_date_str else datetime.date.today()
    except ValueError:
        return jsonify({"error": "Invalid filedDate format, expected YYYY-MM-DD"}), 400

    case = db.mark_ombudsman_filed(case_ref, session["user_id"], filed_date, table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/resolved", methods=["POST"])
@login_required
def api_track_resolved():
    import datetime
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")
    outcome = data.get("outcome")  # "won" or "lost"
    resolution_amount = data.get("resolutionAmount")
    resolved_date_str = data.get("resolvedDate")

    if outcome not in ("won", "lost"):
        return jsonify({"error": "outcome must be 'won' or 'lost'"}), 400
    try:
        resolved_date = datetime.date.fromisoformat(resolved_date_str) if resolved_date_str else datetime.date.today()
    except ValueError:
        return jsonify({"error": "Invalid resolvedDate format, expected YYYY-MM-DD"}), 400

    case = db.mark_resolved(case_ref, session["user_id"], resolved_date, outcome, resolution_amount,
                             table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/abandoned", methods=["POST"])
@login_required
def api_track_abandoned():
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")

    case = db.mark_abandoned(case_ref, session["user_id"], table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/note", methods=["POST"])
@login_required
def api_track_note():
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")
    note_text = (data.get("note") or "").strip()
    if not note_text:
        return jsonify({"error": "note is required"}), 400

    case = db.add_case_note(case_ref, session["user_id"], note_text, table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/events/<case_ref>", methods=["GET"])
@login_required
def api_track_events(case_ref):
    events = db.get_case_events(case_ref)
    return jsonify({"events": events})


@app.route("/api/track/open-cases", methods=["GET"])
@login_required
def api_track_open_cases():
    """Combined health + life open cases, with overdue flags, for the tracker dashboard."""
    import datetime
    cases = db.list_all_open_cases_for_user(session["user_id"])
    today = datetime.date.today()

    def is_overdue(case):
        for field in ("gro_followup_due", "irdai_followup_due"):
            due = case.get(field)
            if due and due < today and case.get("status") == "open":
                return True
        return False

    for case in cases:
        case["is_overdue"] = is_overdue(case)

    return jsonify({"cases": cases, "sheetsEnabled": sheets.is_enabled()})


# ---------- API: Payments (Razorpay) ----------

@app.route("/api/payments/create-order", methods=["POST"])
@login_required
def api_create_payment_order():
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")

    if not case_ref:
        return jsonify({"error": "caseRef is required"}), 400

    case = _get_case_for_sync(case_ref, session["user_id"], case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404

    if case_type != "health":
        return jsonify({"error": "Tracking is free for this case type, no payment needed"}), 400

    if db.has_verified_payment(case_ref, session["user_id"]):
        return jsonify({"alreadyPaid": True})

    try:
        order = payments.create_order(case_ref, case_type)
    except Exception as e:
        return jsonify({"error": f"Could not create payment order: {e}"}), 502

    db.create_payment_record(
        user_id=session["user_id"],
        case_ref=case_ref,
        case_type=case_type,
        razorpay_order_id=order["id"],
        amount_paise=order["amount"],
    )

    return jsonify({
        "orderId": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "keyId": os.environ["RAZORPAY_KEY_ID"],  # public key, safe to expose to the browser
    })


@app.route("/api/payments/verify", methods=["POST"])
@login_required
def api_verify_payment():
    data = request.get_json(force=True)
    order_id = data.get("razorpay_order_id")
    payment_id = data.get("razorpay_payment_id")
    signature = data.get("razorpay_signature")

    if not all([order_id, payment_id, signature]):
        return jsonify({"error": "Missing payment verification fields"}), 400

    payment_record = db.get_payment_by_order_id(order_id)
    if not payment_record or payment_record["user_id"] != session["user_id"]:
        return jsonify({"error": "Order not found"}), 404

    if not payments.verify_payment(order_id, payment_id, signature):
        return jsonify({"error": "Payment signature verification failed"}), 400

    db.mark_payment_verified(order_id, payment_id)
    return jsonify({"verified": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
