"""
db.py — thin Postgres access layer. No ORM, just psycopg2 with explicit SQL,
since the schema is small and stable. Uses Railway's DATABASE_URL env var.

No login/accounts: user_id is an anonymous UUID string generated per
browser session (see get_anon_user_id() in app.py), not a foreign key to
a users table. There is no users table anymore.
"""
import os
import json
import random
import string
import psycopg2
import psycopg2.extras


def get_conn():
    database_url = os.environ["DATABASE_URL"]
    return psycopg2.connect(database_url, sslmode="require")


def init_schema():
    with get_conn() as conn:
        with conn.cursor() as cur:
            with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
                cur.execute(f.read())
            conn.commit()


# ---- claim cases ----

def generate_case_ref():
    year = __import__("datetime").date.today().year
    n = "".join(random.choices(string.digits, k=4))
    return f"IM-{year}-{n}"


def create_claim_case(user_id, insurer, policy_name, claim_amount, hospital,
                       diagnosis, rejection_reason, matched_rule_id,
                       secondary_rule_ids, answers, score):
    case_ref = generate_case_ref()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO claim_cases
                    (case_ref, user_id, insurer, policy_name, claim_amount, hospital,
                     diagnosis, rejection_reason, matched_rule_id, secondary_rule_ids,
                     answers, score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (case_ref, user_id, insurer, policy_name, claim_amount, hospital,
                 diagnosis, rejection_reason, matched_rule_id, secondary_rule_ids,
                 json.dumps(answers), score),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def update_case_letter(case_ref, user_id, letter_text):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE claim_cases SET letter_text = %s, updated_at = NOW()
                WHERE case_ref = %s AND user_id = %s
                RETURNING *
                """,
                (letter_text, case_ref, user_id),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def get_case_by_ref(case_ref, user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM claim_cases WHERE case_ref = %s AND user_id = %s",
                (case_ref, user_id),
            )
            return cur.fetchone()


def list_cases_for_user(user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM claim_cases WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            return cur.fetchall()


# _log_event writes to case_events, used for a basic creation-event log
# (currently only called from create_life_claim_case below). Case-level
# tracking/follow-up deadlines were removed as a feature -- this helper
# stays only because that one call site still uses it.

def _log_event(cur, case_ref, case_type, user_id, event_type, event_note=None):
    cur.execute(
        """
        INSERT INTO case_events (case_ref, case_type, user_id, event_type, event_note)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (case_ref, case_type, user_id, event_type, event_note),
    )


# ---- life claim cases ----

def generate_life_case_ref():
    year = __import__("datetime").date.today().year
    n = "".join(random.choices(string.digits, k=4))
    return f"IL-{year}-{n}"


def create_life_claim_case(user_id, insurer, policy_name, deceased_name, date_of_death,
                            rejection_reason, matched_rule_id, secondary_rule_ids, answers, score):
    case_ref = generate_life_case_ref()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO life_claim_cases
                    (case_ref, user_id, insurer, policy_name, deceased_name, date_of_death,
                     rejection_reason, matched_rule_id, secondary_rule_ids, answers, score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (case_ref, user_id, insurer, policy_name, deceased_name, date_of_death,
                 rejection_reason, matched_rule_id, secondary_rule_ids, json.dumps(answers), score),
            )
            row = cur.fetchone()
            _log_event(cur, case_ref, "life", user_id, "letter_drafted", "Initial case opened and scored.")
            conn.commit()
            return row


def update_life_case_letter(case_ref, user_id, letter_text):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE life_claim_cases SET letter_text = %s, updated_at = NOW()
                WHERE case_ref = %s AND user_id = %s
                RETURNING *
                """,
                (letter_text, case_ref, user_id),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def get_life_case_by_ref(case_ref, user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM life_claim_cases WHERE case_ref = %s AND user_id = %s",
                (case_ref, user_id),
            )
            return cur.fetchone()


def list_life_cases_for_user(user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM life_claim_cases WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            return cur.fetchall()


# ---- payments ----

def create_payment_record(user_id, case_ref, case_type, razorpay_order_id, amount_paise):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO payments (user_id, case_ref, case_type, razorpay_order_id, amount_paise, status)
                VALUES (%s, %s, %s, %s, %s, 'created')
                RETURNING *
                """,
                (user_id, case_ref, case_type, razorpay_order_id, amount_paise),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def mark_payment_verified(razorpay_order_id, razorpay_payment_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE payments
                SET status = 'paid', razorpay_payment_id = %s, verified_at = NOW()
                WHERE razorpay_order_id = %s
                RETURNING *
                """,
                (razorpay_payment_id, razorpay_order_id),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def has_verified_payment(case_ref, user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT 1 FROM payments WHERE case_ref = %s AND user_id = %s AND status = 'paid' LIMIT 1",
                (case_ref, user_id),
            )
            return cur.fetchone() is not None


def get_payment_by_order_id(razorpay_order_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM payments WHERE razorpay_order_id = %s", (razorpay_order_id,))
            return cur.fetchone()


# ---- disclosure summaries ----

def save_disclosure_summary(user_id, insurer, profile_name, checked_items, notes, summary_text):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO disclosure_summaries
                    (user_id, insurer, profile_name, checked_items, notes, summary_text)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (user_id, insurer, profile_name, json.dumps(checked_items),
                 json.dumps(notes), summary_text),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def list_disclosure_summaries_for_user(user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM disclosure_summaries WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            return cur.fetchall()


# ---- policy recommendations ----

def generate_recommendation_ref():
    year = __import__("datetime").date.today().year
    n = "".join(random.choices(string.digits, k=4))
    return f"IR-{year}-{n}"


def create_policy_recommendation(user_id, inputs):
    recommendation_ref = generate_recommendation_ref()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO policy_recommendations (recommendation_ref, user_id, inputs)
                VALUES (%s, %s, %s)
                RETURNING *
                """,
                (recommendation_ref, user_id, json.dumps(inputs)),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def get_policy_recommendation_by_ref(recommendation_ref, user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM policy_recommendations WHERE recommendation_ref = %s AND user_id = %s",
                (recommendation_ref, user_id),
            )
            return cur.fetchone()


def update_policy_recommendation_text(recommendation_ref, user_id, recommendation_text):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE policy_recommendations SET recommendation_text = %s, updated_at = NOW()
                WHERE recommendation_ref = %s AND user_id = %s
                RETURNING *
                """,
                (recommendation_text, recommendation_ref, user_id),
            )
            row = cur.fetchone()
            conn.commit()
            return row


# ---- feedback ----

def save_feedback(user_id, message, page_context=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO feedback (user_id, message, page_context)
                VALUES (%s, %s, %s)
                RETURNING *
                """,
                (user_id, message, page_context),
            )
            row = cur.fetchone()
            conn.commit()
            return row


# ---- usage stats ----
# Used for an optional "X people helped so far" counter. Kept as a single
# combined count across the three case-producing tables, since site visits
# alone aren't a meaningful number -- a finished case/recommendation is.

def get_usage_count():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM claim_cases) +
                    (SELECT COUNT(*) FROM life_claim_cases) +
                    (SELECT COUNT(*) FROM policy_recommendations) AS total
                """
            )
            row = cur.fetchone()
            return row[0] if row else 0
