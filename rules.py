"""
rules.py — Insurance Mitra deterministic rule engine.

This is intentionally kept separate from any AI call. The AI (Claude) only
narrates a verdict this module already computed — it never decides the
classification or the score on its own. That separation is what keeps the
tool's legal claims auditable rather than opaque.
"""

RULES = [
    {
        "id": "ped",
        "label": "Pre-existing disease / non-disclosure",
        "match": ["pre-existing", "non-disclosure", "concealment", "suppress",
                   "hidden condition", "didn't disclose", "did not disclose",
                   "withheld", "misstatement", "material information", "material fact"],
        "explain": (
            "Under IRDAI's moratorium rule, once a policy has been continuously renewed "
            "for 5 years (60 months), the insurer cannot reject a claim for non-disclosure "
            "or misrepresentation except in cases of proven fraud — not just suspicion. "
            "Even under 5 years, insurers must prove non-disclosure was both material AND "
            "deliberate. Courts have also overturned repudiations where the undisclosed "
            "condition had no actual connection to the illness being claimed for. "
            "Critically: if the insurer required a pre-policy medical check-up and issued "
            "the policy after reviewing it, they generally cannot later repudiate a claim "
            "by calling the same condition 'pre-existing' — that risk was theirs to catch "
            "at underwriting, not yours to confess twice."
        ),
        "asks": [
            {"key": "policyAge", "q": "How old is your policy (in years, continuous, including via portability)?", "type": "number"},
            {"key": "namedRecord", "q": "Has the insurer named a specific medical record you allegedly hid, or just said it's 'likely' or 'potential'?", "type": "yesno"},
            {"key": "related", "q": "Is the condition they say you hid actually related to what you're claiming for now?", "type": "yesno"},
            {"key": "preMedicalDone", "q": "Did the insurer ask you to do a medical check-up before issuing the policy?", "type": "yesno"},
        ],
    },
    {
        "id": "waiting",
        "label": "Waiting period applied",
        "match": ["waiting period", "30 days", "24 months", "initial waiting"],
        "explain": (
            "Initial 30-day waiting periods don't apply to accidents. If you ported your "
            "policy from another insurer without a break, your waiting period clock doesn't "
            "reset. IRDAI rules cap pre-existing disease waiting periods — check your policy "
            "schedule against the cap. If the insurer cites a specific exclusion code with a "
            "named list of diseases, check that your actual diagnosis is genuinely on that "
            "list — Ombudsmen have overturned cases where the cited clause was real but "
            "simply didn't apply to the diagnosis."
        ),
        "asks": [
            {"key": "isAccident", "q": "Was this claim for an accident (not illness)?", "type": "yesno"},
            {"key": "ported", "q": "Did you port this policy from another insurer with no coverage gap?", "type": "yesno"},
            {"key": "diagnosisMatches", "q": "If the insurer named a specific exclusion list or code, does your actual diagnosis genuinely match a disease on that list?", "type": "yesno"},
        ],
    },
    {
        "id": "roomrent",
        "label": "Room rent limit / proportionate deduction",
        "match": ["room rent", "proportionate", "sub-limit", "sublimit", "room category"],
        "explain": (
            "If your policy has no explicit room-rent cap clause, the insurer cannot apply "
            "a proportionate deduction at all — this is one of the most commonly misapplied "
            "rejection reasons."
        ),
        "asks": [
            {"key": "hasCap", "q": "Does your policy document explicitly mention a room rent cap?", "type": "yesno"},
        ],
    },
    {
        "id": "cashless",
        "label": "Cashless claim denied or delayed",
        "match": ["cashless", "tpa", "network hospital", "pre-auth", "preauth"],
        "explain": (
            "IRDAI's 2024 master circular requires a pre-authorization decision within 1 "
            "hour and final discharge authorization within 3 hours. A delay beyond this is "
            "a regulatory violation on its own, separate from the medical merit of your claim."
        ),
        "asks": [
            {"key": "delayed", "q": "Did the decision take longer than a few hours?", "type": "yesno"},
        ],
    },
    {
        "id": "docs",
        "label": "Documents incomplete / query raised",
        "match": ["incomplete document", "missing document", "query raised",
                   "discharge summary missing", "resubmit", "claim is incomplete",
                   "kindly furnish", "kindly submit", "please furnish", "furnish the",
                   "additional documents", "documents required", "awaiting documents"],
        "explain": (
            "This is usually a deficiency notice, not a final rejection. You almost always "
            "have the right to simply resubmit with the missing document — your case hasn't "
            "actually been decided yet."
        ),
        "asks": [],
    },
    {
        "id": "exclusion",
        "label": "Treatment excluded by policy",
        "match": ["excluded", "cosmetic", "infertility", "opd", "dental", "not covered"],
        "explain": (
            "If the treatment clearly matches a named exclusion in your policy wording, "
            "this is often a valid rejection. It's still worth checking the exact wording "
            "for ambiguity before giving up."
        ),
        "asks": [
            {"key": "wordingClear", "q": "Is the exclusion wording in your policy clear and specific (not vague)?", "type": "yesno"},
        ],
    },
    {
        "id": "underwriting",
        "label": "You disclosed it, but they issued the policy anyway",
        "match": ["loading", "underwriting", "they knew", "i told them", "i disclosed",
                   "i informed", "agent said", "agent told", "declared at the time",
                   "mentioned in the proposal form", "medical test", "medical checkup",
                   "medical check-up", "health checkup", "never asked", "no checkup",
                   "without any checkup", "no medical test"],
        "explain": (
            "Under IRDAI's Health Insurance Regulations 2016, if an insurer charges extra "
            "premium (a 'loading') because of a declared health condition, they must inform "
            "you of this in writing and get your specific signed consent before issuing the "
            "policy. If they issued your policy at a normal premium after you disclosed a "
            "condition, that is their underwriting decision to accept the risk — they cannot "
            "use that same disclosed condition against you later. Ask them to produce the "
            "loading-consent letter or medical underwriting note from when you bought the "
            "policy. If they can't, that itself supports your case."
        ),
        "asks": [
            {"key": "disclosedAtPurchase", "q": "Did you mention this condition in the proposal form or to the agent when buying the policy?", "type": "yesno"},
            {"key": "extraPremiumDocumented", "q": "If they charged extra premium for it, did you receive anything in writing about this loading at the time?", "type": "yesno"},
        ],
    },
    {
        "id": "vague",
        "label": "No specific reason given",
        "match": ["cannot be processed", "as per terms and conditions", "not payable",
                   "does not fall under", "not admissible", "unable to settle your claim",
                   "we hereby repudiate", "decision has been taken as per"],
        "explain": (
            "IRDAI rules require insurers to give a specific, written reason for rejection "
            "— not just a generic reference to 'policy terms.' If your rejection letter "
            "doesn't name an exact clause, exclusion, or waiting period with a clause number, "
            "you can demand this in writing before anything else. A vague rejection is "
            "itself a procedural gap you can use."
        ),
        "asks": [
            {"key": "clauseNamed", "q": "Does the rejection letter quote a specific clause number or name an exact exclusion?", "type": "yesno"},
        ],
    },
    {
        "id": "unclear",
        "label": "Reason unclear from your description",
        "match": [],
        "explain": (
            "We couldn't confidently match this to a specific IRDAI rule category from your "
            "description alone. That's common — insurer rejection letters are often "
            "deliberately vague. This doesn't mean your case is weak, it means you need the "
            "exact written rejection letter (not a verbal explanation) before anyone, "
            "including a human expert, can assess it properly."
        ),
        "asks": [
            {"key": "hasWrittenLetter", "q": "Do you have the insurer's written rejection letter (not just a phone call)?", "type": "yesno"},
        ],
    },
]

RULES_BY_ID = {r["id"]: r for r in RULES}

# Underwriting and documentation stories are more specific and more favorable
# than a bare keyword overlap with "ped" or "vague" — so they win ties.
PRIORITY = {"underwriting": 2, "docs": 1.5, "vague": 1}


def classify(reason_text: str):
    """Returns (matched_rule_dict, [secondary_rule_dicts])."""
    lower = reason_text.lower()
    candidates = [r for r in RULES if r["id"] != "unclear"]
    scored = []
    for rule in candidates:
        hits = sum(1 for m in rule["match"] if m in lower)
        weighted = hits * PRIORITY.get(rule["id"], 1)
        scored.append({"rule": rule, "hits": hits, "weighted": weighted})

    top_weighted = max(s["weighted"] for s in scored)
    if top_weighted == 0:
        return RULES_BY_ID["unclear"], []

    tied = [s for s in scored if s["weighted"] == top_weighted]
    matched = tied[0]["rule"]
    secondary = [s["rule"] for s in tied[1:]] + [
        s["rule"] for s in scored if 0 < s["weighted"] < top_weighted
    ]
    return matched, secondary


# ===== Life Insurance Death Claim Rules =====
# Distinct from health-claim rules: different moratorium period (3 years, not
# 5), different document requirements, and the claimant is often a grieving
# family member rather than the policyholder — tone matters here.

LIFE_RULES = [
    {
        "id": "noNominee",
        "label": "No nominee mentioned / nominee details wrong",
        "match": ["no nominee", "nominee not mentioned", "nominee missing", "nominee details",
                   "nominee incorrect", "wrong nominee", "without nominee", "nominee was not",
                   "no nominee was", "nominee name was not", "nominee not named", "nominee not registered"],
        "explain": (
            "A missing or incorrect nominee does NOT void a life insurance claim. Under "
            "Section 39(11) of the Insurance Act, if no nominee is registered, the payout "
            "simply goes to the policyholder's legal heirs instead — it is not forfeited. "
            "The family will need to additionally provide a Succession Certificate, Legal "
            "Heir Certificate, or a court-issued probate of will to prove who should receive "
            "the money. This takes longer and needs more paperwork, but the insurer cannot "
            "refuse to pay just because no nominee was named."
        ),
        "asks": [
            {"key": "hasLegalHeirDocs", "q": "Do you have (or can you get) a Legal Heir Certificate, Succession Certificate, or similar proof of relationship to the deceased?", "type": "yesno"},
        ],
    },
    {
        "id": "claimOverdue",
        "label": "Claim filed late / delay in informing insurer",
        "match": ["claim overdue", "delay in", "delayed intimation", "late claim",
                   "filed late", "informed late", "claim is overdue", "claim is late"],
        "explain": (
            "A delay in informing the insurer is rarely, by itself, a valid reason to reject "
            "a genuine death claim — IRDAI rules focus on whether the claim is genuine, not "
            "just on timing. If there was a real reason for the delay (family was dealing "
            "with the death, didn't know about the policy, documents took time), explain "
            "this clearly in writing. Insurers must still investigate the actual claim on "
            "its merits."
        ),
        "asks": [
            {"key": "hasReasonForDelay", "q": "Was there a genuine reason for the delay (grief, didn't know about the policy, gathering documents, etc.)?", "type": "yesno"},
        ],
    },
    {
        "id": "lifePed",
        "label": "Non-disclosure of medical history",
        "match": ["non-disclosure", "pre-existing", "withheld", "misstatement", "concealment",
                   "suppress", "did not disclose", "didn't disclose", "material fact", "material information"],
        "explain": (
            "Under IRDAI's rules, the contestability period for life insurance is 3 years "
            "from the policy start date — after 3 years of continuous coverage, the insurer "
            "generally cannot reject a death claim for non-disclosure or misrepresentation, "
            "except in cases of clear, proven fraud. Even within 3 years, the insurer must "
            "show the non-disclosed condition was both material to the risk AND actually "
            "connected to the cause of death — an unrelated old condition usually cannot be "
            "used to deny a claim for an unconnected cause of death (for example, a sudden "
            "accident)."
        ),
        "asks": [
            {"key": "policyAge", "q": "How old was the policy at the time of death (in years, continuous)?", "type": "number"},
            {"key": "causeRelated", "q": "Is the condition they're citing actually related to the cause of death?", "type": "yesno"},
        ],
    },
    {
        "id": "policyLapsed",
        "label": "Policy had lapsed / premium not paid",
        "match": ["policy lapsed", "premium not paid", "premium unpaid", "lapsed policy",
                   "grace period", "policy had lapsed"],
        "explain": (
            "If a premium was missed but death occurred within the grace period (usually "
            "15-30 days depending on payment frequency), the policy is still considered "
            "active and the claim should be paid. Check the exact date of death against the "
            "grace period end date and the policy's payment frequency. If the policy had a "
            "'reduced paid-up' or revival option that was active, that may also still apply."
        ),
        "asks": [
            {"key": "withinGracePeriod", "q": "Did the death occur within the grace period after the missed premium (usually 15-30 days)?", "type": "yesno"},
        ],
    },
    {
        "id": "suicideClause",
        "label": "Death due to suicide",
        "match": ["suicide"],
        "explain": (
            "Indian life insurance policies generally only exclude suicide if it occurs "
            "within the first 1 year of the policy. After 1 year of continuous coverage, "
            "suicide is covered like any other cause of death. If the exclusion is being "
            "applied and the policy is more than 1 year old, this is a strong ground to "
            "challenge. If it happened within the first year, the premiums paid (excluding "
            "taxes) are still usually owed back to the nominee, even though the death "
            "benefit itself isn't payable."
        ),
        "asks": [
            {"key": "policyOverOneYear", "q": "Was the policy more than 1 year old (continuous) at the time of death?", "type": "yesno"},
        ],
    },
    {
        "id": "lifeDocs",
        "label": "Documents incomplete / additional documents requested",
        "match": ["incomplete document", "missing document", "kindly furnish",
                   "additional documents", "documents required", "furnish the", "resubmit"],
        "explain": (
            "This usually means the claim hasn't actually been decided yet — it's a "
            "request, not a rejection. IRDAI rules require insurers to ask for all "
            "necessary documents upfront, within 15 days of being notified of the death, "
            "and to settle or reject within 30 days of receiving everything (or up to 90 "
            "days if a special investigation is genuinely needed, with the final payment "
            "due within 30 days after that). Keep a written record of when you submitted "
            "each document."
        ),
        "asks": [],
    },
    {
        "id": "lifeVague",
        "label": "No specific reason given",
        "match": ["cannot be processed", "as per terms and conditions", "not payable",
                   "not admissible", "we hereby repudiate", "unable to settle"],
        "explain": (
            "IRDAI requires insurers to give a specific, written reason for any claim "
            "rejection. A vague reference to 'policy terms' without naming an exact clause "
            "or exclusion is itself something you can challenge — write back asking for the "
            "specific clause number and the evidence behind their decision."
        ),
        "asks": [
            {"key": "clauseNamed", "q": "Does the rejection letter name a specific clause or exclusion?", "type": "yesno"},
        ],
    },
    {
        "id": "lifeUnclear",
        "label": "Reason unclear from your description",
        "match": [],
        "explain": (
            "We couldn't confidently match this to a specific category from your "
            "description alone. This is common with life insurance rejection letters, "
            "which are often brief. Get the exact written rejection letter if you don't "
            "already have it — that's the document a Grievance Officer or Ombudsman will "
            "actually look at."
        ),
        "asks": [
            {"key": "hasWrittenLetter", "q": "Do you have the insurer's written rejection letter?", "type": "yesno"},
        ],
    },
]

LIFE_RULES_BY_ID = {r["id"]: r for r in LIFE_RULES}


def classify_life(reason_text: str):
    """Returns (matched_rule_dict, [secondary_rule_dicts]) for life/death claims."""
    lower = reason_text.lower()
    candidates = [r for r in LIFE_RULES if r["id"] != "lifeUnclear"]
    scored = []
    for rule in candidates:
        hits = sum(1 for m in rule["match"] if m in lower)
        scored.append({"rule": rule, "hits": hits})

    top_hits = max(s["hits"] for s in scored)
    if top_hits == 0:
        return LIFE_RULES_BY_ID["lifeUnclear"], []

    tied = [s for s in scored if s["hits"] == top_hits]
    matched = tied[0]["rule"]
    secondary = [s["rule"] for s in tied[1:]] + [
        s["rule"] for s in scored if 0 < s["hits"] < top_hits
    ]
    return matched, secondary


def score_no_nominee(a):
    s = 65
    if a.get("hasLegalHeirDocs") == "yes":
        s += 30
    return s


def score_claim_overdue(a):
    s = 50
    if a.get("hasReasonForDelay") == "yes":
        s += 35
    return s


def score_life_ped(a):
    s = 25
    try:
        age = float(a.get("policyAge", 0))
    except (TypeError, ValueError):
        age = 0
    if age >= 3:
        s += 55
    elif age >= 1:
        s += 15
    if a.get("causeRelated") == "no":
        s += 35
    return s


def score_policy_lapsed(a):
    s = 20
    if a.get("withinGracePeriod") == "yes":
        s += 60
    return s


def score_suicide_clause(a):
    s = 10
    if a.get("policyOverOneYear") == "yes":
        s += 80
    return s


def score_life_docs(a):
    return 65


def score_life_vague(a):
    s = 55
    if a.get("clauseNamed") == "no":
        s += 20
    else:
        s -= 20
    return s


def score_life_unclear(a):
    s = 50
    if a.get("hasWrittenLetter") == "no":
        s -= 15
    return s


LIFE_SCORERS = {
    "noNominee": score_no_nominee,
    "claimOverdue": score_claim_overdue,
    "lifePed": score_life_ped,
    "policyLapsed": score_policy_lapsed,
    "suicideClause": score_suicide_clause,
    "lifeDocs": score_life_docs,
    "lifeVague": score_life_vague,
    "lifeUnclear": score_life_unclear,
}


def compute_life_score(rule_id: str, answers: dict) -> int:
    scorer = LIFE_SCORERS.get(rule_id, lambda a: 50)
    raw = scorer(answers or {})
    return max(0, min(100, raw))


def build_life_letter_template(form: dict, rule: dict, secondary: list, score: int) -> str:
    import datetime
    b = band(score)
    today = datetime.date.today().strftime("%d %B %Y")
    secondary_line = ""
    if secondary:
        labels = ", ".join(r["label"] for r in secondary)
        secondary_line = f"\n\nI would also like to separately raise: {labels}."

    return f"""To,
The Grievance Redressal Officer
{form.get('insurer') or '[Insurer Name]'}

Date: {today}

Subject: Request for reconsideration of death claim rejection — Policy: {form.get('policyName') or '[Policy Number]'}

Dear Sir/Madam,

I am writing as the claimant on the above policy, held by {form.get('deceasedName') or '[Name of the deceased]'}, who passed away on {form.get('dateOfDeath') or '[Date of death]'}. I am writing regarding the rejection of the death claim under this policy.

The reason given for rejection was: "{form.get('rejectionReason') or '[paste their exact wording here]'}"

I would like to bring to your attention that: {rule['explain']}{secondary_line}

I request that this claim be reconsidered within 15 working days of receipt of this letter. If the matter is not resolved to my satisfaction, I will be escalating this to IRDAI's Bima Bharosa portal, followed by the Insurance Ombudsman if necessary, who can resolve disputes up to ₹50 lakh free of charge.

I have attached all relevant documents in support of this claim, including the death certificate and other documents requested. Please confirm receipt of this letter and provide a reference number for tracking.

Yours sincerely,
[Your Name]
[Relationship to the deceased]
[Contact Number]

---
Note: This is a starting template (case strength: {b['label']}, {score}/100). Please review and personalize the bracketed sections, and add specific dates or document references before sending. If you're not comfortable handling this alone, the Insurance Ombudsman's office can also guide you through the process at no cost."""


def score_ped(a):
    s = 25
    try:
        age = float(a.get("policyAge", 0))
    except (TypeError, ValueError):
        age = 0
    if age >= 5:
        s += 55
    elif age >= 3:
        s += 25
    elif age >= 1:
        s += 10
    if a.get("namedRecord") == "no":
        s += 25
    if a.get("related") == "no":
        s += 30
    if a.get("preMedicalDone") == "yes":
        s += 35
    return s


def score_waiting(a):
    s = 30
    if a.get("isAccident") == "yes":
        s += 35
    if a.get("ported") == "yes":
        s += 20
    if a.get("diagnosisMatches") == "no":
        s += 35
    return s


def score_roomrent(a):
    s = 30
    if a.get("hasCap") == "no":
        s += 45
    else:
        s -= 10
    return s


def score_cashless(a):
    s = 30
    if a.get("delayed") == "yes":
        s += 30
    return s


def score_docs(a):
    return 70


def score_exclusion(a):
    s = 10
    if a.get("wordingClear") == "no":
        s += 25
    return s


def score_underwriting(a):
    s = 50
    if a.get("disclosedAtPurchase") == "yes":
        s += 35
    if a.get("extraPremiumDocumented") == "no":
        s += 15
    return s


def score_vague(a):
    s = 55
    if a.get("clauseNamed") == "no":
        s += 20
    else:
        s -= 25
    return s


def score_unclear(a):
    s = 50
    if a.get("hasWrittenLetter") == "no":
        s -= 15
    return s


SCORERS = {
    "ped": score_ped,
    "waiting": score_waiting,
    "roomrent": score_roomrent,
    "cashless": score_cashless,
    "docs": score_docs,
    "exclusion": score_exclusion,
    "underwriting": score_underwriting,
    "vague": score_vague,
    "unclear": score_unclear,
}


def compute_score(rule_id: str, answers: dict) -> int:
    scorer = SCORERS.get(rule_id, lambda a: 50)
    raw = scorer(answers or {})
    return max(0, min(100, raw))


def band(score: int):
    if score >= 70:
        return {"label": "Strong case", "color": "#3D6B4F"}
    if score >= 40:
        return {"label": "Worth fighting", "color": "#C9962C"}
    return {"label": "Weak case — but not nothing", "color": "#B3402A"}


# ---- Pre-purchase disclosure checklist data ----

CHECKLIST_GROUPS = [
    {
        "group": "Standard chronic conditions (always ask about these)",
        "items": [
            {"id": "diabetes", "label": "Diabetes / high blood sugar (HbA1c, fasting/PP sugar)"},
            {"id": "bp", "label": "High blood pressure (BP readings, any medication)"},
            {"id": "thyroid", "label": "Thyroid disorder (TSH levels, medication)"},
            {"id": "cholesterol", "label": "High cholesterol / lipid issues"},
            {"id": "asthma", "label": "Asthma or breathing-related conditions"},
            {"id": "heart", "label": "Any heart-related diagnosis, ECG abnormality, or chest pain history"},
        ],
    },
    {
        "group": "Often-missed findings insurers use later (the ones that get weaponized)",
        "items": [
            {"id": "fattyLiver", "label": "Fatty liver (NAFLD) — even if found incidentally on an ultrasound for something else"},
            {"id": "b12", "label": "Vitamin B12 / Vitamin D deficiency"},
            {"id": "kidneyStone", "label": "Kidney stones or gallbladder stones (even old, asymptomatic ones)"},
            {"id": "anemia", "label": "Anemia or low hemoglobin found in any past blood test"},
            {"id": "liverEnzymes", "label": "Mildly elevated liver enzymes (SGPT/SGOT) on any past report"},
        ],
    },
    {
        "group": "Post-COVID / recent symptoms (frequently undisclosed because people don't think of them as conditions)",
        "items": [
            {"id": "legCramps", "label": "Leg cramps, muscle pain, or unexplained body ache (ongoing)"},
            {"id": "fatigue", "label": "Persistent fatigue or breathlessness after COVID recovery"},
            {"id": "palpitations", "label": "Heart palpitations or irregular heartbeat noticed after COVID"},
            {"id": "covidHistory", "label": "Any COVID-19 hospitalization, oxygen support, or steroid treatment"},
        ],
    },
    {
        "group": "History & records to gather (insurer cannot claim ignorance of these)",
        "items": [
            {"id": "pastHospitalization", "label": "Any hospitalization in the last 4 years, for any reason"},
            {"id": "pastSurgery", "label": "Any past surgery, however minor"},
            {"id": "familyHistory", "label": "Family history of diabetes, heart disease, or cancer (some proposal forms ask this)"},
            {"id": "currentMeds", "label": "Any medication you take regularly, even over-the-counter"},
        ],
    },
]

CHECKLIST_ITEM_LABELS = {
    item["id"]: item["label"]
    for group in CHECKLIST_GROUPS
    for item in group["items"]
}


def build_disclosure_summary(checked: dict, notes: dict, profile: dict) -> str:
    import datetime
    lines = []
    lines.append("HEALTH DISCLOSURE SUMMARY")
    lines.append(f"Prepared by: {profile.get('name') or '[Your name]'}")
    lines.append(f"Date: {datetime.date.today().strftime('%d %B %Y')}")
    lines.append(f"For policy proposal with: {profile.get('insurer') or '[Insurer name]'}")
    lines.append("")
    lines.append("I am disclosing the following health information at the time of "
                  "proposing for this policy, so that it is on record before the policy "
                  "is issued:")
    lines.append("")
    any_checked = False
    for group in CHECKLIST_GROUPS:
        checked_in_group = [item for item in group["items"] if checked.get(item["id"])]
        if checked_in_group:
            any_checked = True
            lines.append(f"{group['group']}:")
            for item in checked_in_group:
                note = notes.get(item["id"])
                suffix = f" — {note}" if note else ""
                lines.append(f"- {item['label']}{suffix}")
            lines.append("")
    if not any_checked:
        lines.append("(No conditions checked — this confirms a clean disclosure with nothing to report.)")
        lines.append("")
    lines.append(
        "I am requesting that this disclosure be recorded as part of my proposal, and "
        "that I be informed in writing of any premium loading, waiting period, or "
        "exclusion applied as a result, as required under IRDAI's Health Insurance "
        "Regulations 2016. I am keeping a copy of this summary for my own records."
    )
    lines.append("")
    lines.append("Signed: ____________________  Date: ____________")
    return "\n".join(lines)
