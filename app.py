"""
app.py — Insurance Mitra Flask backend.

The database schema is initialized automatically on startup (see
_init_schema_with_retry below) -- there is no manual `psql -f schema.sql`
step needed. CREATE TABLE IF NOT EXISTS means this is safe to run on every
deploy; it only creates what's missing and never touches existing data.

No login/accounts. Every visitor gets an anonymous session id (a random
UUID stored in the Flask session cookie) the first time they hit the site.
This anonymous id is used everywhere the old code used an authenticated
user_id, purely to scope a case to "whoever created it" -- there is no
password, no account, and nothing is shared across browsers/devices.

Monetization: analysis (rejection classification + score/verdict) is free.
The Claude-generated grievance letter is the one paid feature (₹99),
because it's the only step that actually calls the Claude API and costs
us money. Payment is verified via Razorpay before the letter is generated.

Routes:
  GET  /                          -> landing page (claim form)
  POST /api/analyze               -> classify rejection text, return rule + questions
  POST /api/score                 -> compute score from answers, save case, return verdict
  POST /api/payments/create-order -> create Razorpay order for a case's letter or a policy recommendation
  POST /api/payments/verify       -> verify Razorpay payment signature
  POST /api/generate-letter       -> call Claude API server-side, save + return letter (requires verified payment)
  GET  /api/cases                 -> list cases created in this browser session
  POST /api/checklist/summary     -> build + save disclosure summary text
  GET  /api/checklist/summaries   -> list summaries created in this browser session
  POST /api/life/analyze          -> classify a life/death claim rejection text
  POST /api/life/score            -> compute score for a life claim, save case, return verdict
  POST /api/life/generate-letter  -> build life claim letter from local template (free, no Claude call)
  GET  /api/life/cases            -> list life cases created in this browser session
  POST /api/policy/recommend-request -> save recommendation inputs, return a recommendationRef to pay against
  POST /api/policy/recommend      -> call Claude API server-side, return recommendation (requires verified payment)
"""
import os
import time
import uuid
import logging
import functools
import requests
from flask import Flask, request, jsonify, session, render_template
from flask_cors import CORS

import db
import rules
import sheets
import payments

logger = logging.getLogger("app")

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

# CORS for the trial period: insurance-mitra.html is hosted as a static file
# on salarybit.in, but all API calls go to this Railway-hosted backend.
# supports_credentials=True is required so the browser sends/receives the
# anon_id session cookie across the salarybit.in -> Railway boundary --
# without it, every request would look like a brand-new anonymous visitor.
#
# Also need SESSION_COOKIE_SAMESITE="None" + SESSION_COOKIE_SECURE=True
# below, since modern browsers refuse to send a session cookie cross-site
# unless it's explicitly marked SameSite=None and Secure (HTTPS only,
# which Railway already gives us).
CORS(app, supports_credentials=True, origins=[
    "https://salarybit.in",
    "https://www.salarybit.in",
])
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = True

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_MODEL = "claude-sonnet-4-6"

LETTER_PRICE_PAISE = 9900  # ₹99


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


def get_anon_user_id():
    """
    Returns a stable anonymous id for this browser session, creating one
    on first visit. No password, no account -- just a random UUID stored
    in the signed Flask session cookie, used purely to scope a case to
    whoever created it.
    """
    if "anon_id" not in session:
        session["anon_id"] = str(uuid.uuid4())
    return session["anon_id"]


# ---------- Pages ----------

@app.route("/")
def index():
    return render_template(
        "dashboard.html",
        checklist_groups=rules.CHECKLIST_GROUPS,
    )


# ---------- API: Claim Rejection Analyzer ----------

@app.route("/api/analyze", methods=["POST"])
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
def api_score():
    user_id = get_anon_user_id()
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
        user_id=user_id,
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
def api_generate_letter():
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    if not case_ref:
        return jsonify({"error": "caseRef is required"}), 400

    case = db.get_case_by_ref(case_ref, user_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404

    # The letter is the paid feature -- it's the only step that calls the
    # Claude API and costs us money, so it's the one thing gated on payment.
    if not db.has_verified_payment(case_ref, user_id):
        return jsonify({
            "error": "payment_required",
            "message": "Generating the formal letter requires a one-time ₹99 payment. "
                       "Create a payment order via /api/payments/create-order first.",
        }), 402

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
        # Fail-safe: the payment is already verified at this point, so we
        # do NOT burn the credit -- the person can retry generate-letter
        # again since has_verified_payment will still return True.
        return jsonify({"error": "Letter generation failed. Please try again in a moment."}), 502

    case = db.update_case_letter(case_ref, user_id, letter_text)
    if case:
        sheets.upsert_case(case, "health")

    return jsonify({"letter": letter_text})


@app.route("/api/cases", methods=["GET"])
def api_list_cases():
    user_id = get_anon_user_id()
    cases = db.list_cases_for_user(user_id)
    return jsonify({"cases": cases})


# ---------- API: Pre-purchase Disclosure Checklist ----------

@app.route("/api/checklist/summary", methods=["POST"])
def api_checklist_summary():
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    checked = data.get("checked") or {}
    notes = data.get("notes") or {}
    profile = data.get("profile") or {}

    summary_text = rules.build_disclosure_summary(checked, notes, profile)
    db.save_disclosure_summary(
        user_id=user_id,
        insurer=profile.get("insurer"),
        profile_name=profile.get("name"),
        checked_items=checked,
        notes=notes,
        summary_text=summary_text,
    )
    return jsonify({"summary": summary_text})


@app.route("/api/checklist/summaries", methods=["GET"])
def api_list_summaries():
    user_id = get_anon_user_id()
    summaries = db.list_disclosure_summaries_for_user(user_id)
    return jsonify({"summaries": summaries})


# ---------- API: Life/Death Claim Analyzer ----------

@app.route("/api/life/analyze", methods=["POST"])
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
def api_life_score():
    user_id = get_anon_user_id()
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
        user_id=user_id,
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
def api_life_generate_letter():
    # Local template, not a Claude call -- stays free, no payment check.
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    if not case_ref:
        return jsonify({"error": "caseRef is required"}), 400

    case = db.get_life_case_by_ref(case_ref, user_id)
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
    updated_case = db.update_life_case_letter(case_ref, user_id, letter_text)
    if updated_case:
        sheets.upsert_case(updated_case, "life")

    return jsonify({"letter": letter_text})


@app.route("/api/life/cases", methods=["GET"])
def api_list_life_cases():
    user_id = get_anon_user_id()
    cases = db.list_life_cases_for_user(user_id)
    return jsonify({"cases": cases})


# ---------- API: Age-based Policy Recommendation (paid, ₹99 -- calls Claude) ----------
#
# Two-step flow, same shape as the claim letter: first save the inputs and
# get back a recommendation_ref, then (after a verified payment against
# that ref) call Claude to generate the actual recommendation. This mirrors
# create_claim_case + generate-letter, just for a "case" that isn't a
# rejection at all -- it's a forward-looking purchase question.

@app.route("/api/policy/recommend-request", methods=["POST"])
def api_policy_recommend_request():
    user_id = get_anon_user_id()
    data = request.get_json(force=True)

    age = data.get("age")
    try:
        age = int(age)
    except (TypeError, ValueError):
        return jsonify({"error": "age is required and must be a number"}), 400
    if age < 18 or age > 100:
        return jsonify({"error": "age must be between 18 and 100"}), 400

    inputs = {
        "age": age,
        "dependents": data.get("dependents"),
        "hasExistingConditions": data.get("hasExistingConditions"),  # "yes" | "no"
        "existingConditionsDetail": data.get("existingConditionsDetail", ""),
        "monthlyBudget": data.get("monthlyBudget"),
        "city": data.get("city", ""),
    }

    rec = db.create_policy_recommendation(user_id=user_id, inputs=inputs)
    return jsonify({"recommendationRef": rec["recommendation_ref"]})


@app.route("/api/policy/recommend", methods=["POST"])
def api_policy_recommend():
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    recommendation_ref = data.get("recommendationRef")
    if not recommendation_ref:
        return jsonify({"error": "recommendationRef is required"}), 400

    rec = db.get_policy_recommendation_by_ref(recommendation_ref, user_id)
    if not rec:
        return jsonify({"error": "Recommendation request not found"}), 404

    if not db.has_verified_payment(recommendation_ref, user_id):
        return jsonify({
            "error": "payment_required",
            "message": "Generating a personalized policy recommendation requires a one-time ₹99 payment. "
                       "Create a payment order via /api/payments/create-order first.",
        }), 402

    inputs = rec["inputs"]
    conditions_line = "No pre-existing conditions disclosed."
    if inputs.get("hasExistingConditions") == "yes":
        detail = inputs.get("existingConditionsDetail") or "details not specified"
        conditions_line = f"Has pre-existing condition(s): {detail}."

    prompt = f"""You are an Indian health/life insurance advisor recommending what TYPE of policy and coverage level a person should look for -- not a specific insurer's product, since you must stay neutral and not endorse a commercial brand.

Person's details:
- Age: {inputs['age']}
- Number of dependents: {inputs.get('dependents') or 'not specified'}
- {conditions_line}
- Comfortable monthly premium budget: ₹{inputs.get('monthlyBudget') or 'not specified'}
- City: {inputs.get('city') or 'not specified'}

Give a personalized recommendation covering:
1. What type of health policy fits this age/life-stage best (individual vs family floater, and why)
2. A reasonable sum insured range for their age and city tier (mention that metro cities need higher cover due to treatment costs)
3. Whether they should also consider a term life policy given their dependents, and a rough cover multiple of annual income to consider (do not assume their income; phrase as "a common rule of thumb is roughly 10-15x annual income" type guidance)
4. Riders worth considering given their profile (e.g. critical illness rider, no-claim bonus, restoration benefit)
5. One or two practical next steps (e.g. compare on official comparison tools, check waiting periods, read the exclusions list)

Important: Do not name or recommend specific insurance companies or product names. Speak in terms of policy types and features only. Keep it under 350 words, plain paragraphs, no markdown formatting, warm but professional tone in plain English suitable for an Indian consumer."""

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
        recommendation_text = "".join(
            block.get("text", "") for block in result.get("content", [])
            if block.get("type") == "text"
        ).strip()
        if not recommendation_text:
            recommendation_text = "Could not generate a recommendation. Please try again."
    except requests.RequestException:
        # Fail-safe: payment is already verified at this point, so we do
        # NOT burn the credit -- has_verified_payment will still return
        # True, so the person can simply retry this endpoint.
        return jsonify({"error": "Recommendation generation failed. Please try again in a moment."}), 502

    db.update_policy_recommendation_text(recommendation_ref, user_id, recommendation_text)
    return jsonify({"recommendation": recommendation_text})


# ---------- API: Case Tracking (works for both health and life cases, free) ----------

def _table_for_type(case_type):
    return "life_claim_cases" if case_type == "life" else "claim_cases"


def _get_case_for_sync(case_ref, user_id, case_type):
    if case_type == "life":
        return db.get_life_case_by_ref(case_ref, user_id)
    return db.get_case_by_ref(case_ref, user_id)


@app.route("/api/track/gro-sent", methods=["POST"])
def api_track_gro_sent():
    import datetime
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")
    sent_date_str = data.get("sentDate")

    # Tracking is free for everyone now -- no payment gate here.
    try:
        sent_date = datetime.date.fromisoformat(sent_date_str) if sent_date_str else datetime.date.today()
    except ValueError:
        return jsonify({"error": "Invalid sentDate format, expected YYYY-MM-DD"}), 400

    case = db.mark_gro_sent(case_ref, user_id, sent_date, table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/irdai-filed", methods=["POST"])
def api_track_irdai_filed():
    import datetime
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")
    filed_date_str = data.get("filedDate")
    try:
        filed_date = datetime.date.fromisoformat(filed_date_str) if filed_date_str else datetime.date.today()
    except ValueError:
        return jsonify({"error": "Invalid filedDate format, expected YYYY-MM-DD"}), 400

    case = db.mark_irdai_filed(case_ref, user_id, filed_date, table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/ombudsman-filed", methods=["POST"])
def api_track_ombudsman_filed():
    import datetime
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")
    filed_date_str = data.get("filedDate")
    try:
        filed_date = datetime.date.fromisoformat(filed_date_str) if filed_date_str else datetime.date.today()
    except ValueError:
        return jsonify({"error": "Invalid filedDate format, expected YYYY-MM-DD"}), 400

    case = db.mark_ombudsman_filed(case_ref, user_id, filed_date, table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/resolved", methods=["POST"])
def api_track_resolved():
    import datetime
    user_id = get_anon_user_id()
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

    case = db.mark_resolved(case_ref, user_id, resolved_date, outcome, resolution_amount,
                             table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/abandoned", methods=["POST"])
def api_track_abandoned():
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")

    case = db.mark_abandoned(case_ref, user_id, table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/note", methods=["POST"])
def api_track_note():
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    case_type = data.get("caseType", "health")
    note_text = (data.get("note") or "").strip()

    if not note_text:
        return jsonify({"error": "note is required"}), 400

    case = db.add_case_note(case_ref, user_id, note_text, table=_table_for_type(case_type), case_type=case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sheets.upsert_case(case, case_type)
    return jsonify({"case": case})


@app.route("/api/track/events/<case_ref>", methods=["GET"])
def api_track_events(case_ref):
    events = db.get_case_events(case_ref)
    return jsonify({"events": events})


@app.route("/api/track/open-cases", methods=["GET"])
def api_track_open_cases():
    """Combined health + life open cases, with overdue flags, for the tracker dashboard."""
    import datetime
    user_id = get_anon_user_id()
    cases = db.list_all_open_cases_for_user(user_id)
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


# ---------- API: Payments (Razorpay) -- gates the Claude letter and the policy recommendation ----------

RECOMMENDATION_PRICE_PAISE = 9900  # ₹99, same as the letter


@app.route("/api/payments/create-order", methods=["POST"])
def api_create_payment_order():
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    case_ref = data.get("caseRef")
    recommendation_ref = data.get("recommendationRef")
    case_type = data.get("caseType", "health")

    if recommendation_ref:
        # ----- Paying for an age-based policy recommendation -----
        rec = db.get_policy_recommendation_by_ref(recommendation_ref, user_id)
        if not rec:
            return jsonify({"error": "Recommendation request not found"}), 404

        if db.has_verified_payment(recommendation_ref, user_id):
            return jsonify({"alreadyPaid": True})

        try:
            order = payments.create_order(recommendation_ref, "policy_recommendation",
                                           amount_paise=RECOMMENDATION_PRICE_PAISE)
        except Exception as e:
            return jsonify({"error": f"Could not create payment order: {e}"}), 502

        db.create_payment_record(
            user_id=user_id,
            case_ref=recommendation_ref,
            case_type="policy_recommendation",
            razorpay_order_id=order["id"],
            amount_paise=order["amount"],
        )
        return jsonify({
            "orderId": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "keyId": os.environ["RAZORPAY_KEY_ID"],
        })

    # ----- Paying for a health claim's grievance letter -----
    if not case_ref:
        return jsonify({"error": "caseRef or recommendationRef is required"}), 400
    if case_type != "health":
        return jsonify({"error": "The letter is free for this case type, no payment needed"}), 400

    case = _get_case_for_sync(case_ref, user_id, case_type)
    if not case:
        return jsonify({"error": "Case not found"}), 404

    if db.has_verified_payment(case_ref, user_id):
        return jsonify({"alreadyPaid": True})

    try:
        order = payments.create_order(case_ref, case_type, amount_paise=LETTER_PRICE_PAISE)
    except Exception as e:
        return jsonify({"error": f"Could not create payment order: {e}"}), 502

    db.create_payment_record(
        user_id=user_id,
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
def api_verify_payment():
    user_id = get_anon_user_id()
    data = request.get_json(force=True)
    order_id = data.get("razorpay_order_id")
    payment_id = data.get("razorpay_payment_id")
    signature = data.get("razorpay_signature")

    if not all([order_id, payment_id, signature]):
        return jsonify({"error": "Missing payment verification fields"}), 400

    payment_record = db.get_payment_by_order_id(order_id)
    if not payment_record or payment_record["user_id"] != user_id:
        return jsonify({"error": "Order not found"}), 404

    if not payments.verify_payment(order_id, payment_id, signature):
        return jsonify({"error": "Payment signature verification failed"}), 400

    db.mark_payment_verified(order_id, payment_id)
    return jsonify({"verified": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
