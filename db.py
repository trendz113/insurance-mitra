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


# ---- case tracking (health claims) ----

GRO_FOLLOWUP_DAYS = 15
IRDAI_FOLLOWUP_DAYS = 15  # rough heuristic; actual IRDAI/Ombudsman timelines vary by case


def _log_event(cur, case_ref, case_type, user_id, event_type, event_note=None):
    cur.execute(
        """
        INSERT INTO case_events (case_ref, case_type, user_id, event_type, event_note)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (case_ref, case_type, user_id, event_type, event_note),
    )


def mark_gro_sent(case_ref, user_id, sent_date, table="claim_cases", case_type="health"):
    import datetime
    followup_due = sent_date + datetime.timedelta(days=GRO_FOLLOWUP_DAYS)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET stage = 'gro_sent', gro_sent_date = %s, gro_followup_due = %s, updated_at = NOW()
                WHERE case_ref = %s AND user_id = %s
                RETURNING *
                """,
                (sent_date, followup_due, case_ref, user_id),
            )
            row = cur.fetchone()
            if row:
                _log_event(cur, case_ref, case_type, user_id, "gro_sent",
                           f"Letter sent to GRO on {sent_date}. Follow up by {followup_due} if no response.")
            conn.commit()
            return row


def mark_irdai_filed(case_ref, user_id, filed_date, table="claim_cases", case_type="health"):
    import datetime
    followup_due = filed_date + datetime.timedelta(days=IRDAI_FOLLOWUP_DAYS)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET stage = 'irdai_filed', irdai_filed_date = %s, irdai_followup_due = %s, updated_at = NOW()
                WHERE case_ref = %s AND user_id = %s
                RETURNING *
                """,
                (filed_date, followup_due, case_ref, user_id),
            )
            row = cur.fetchone()
            if row:
                _log_event(cur, case_ref, case_type, user_id, "irdai_filed",
                           f"Filed with IRDAI Bima Bharosa on {filed_date}.")
            conn.commit()
            return row


def mark_ombudsman_filed(case_ref, user_id, filed_date, table="claim_cases", case_type="health"):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET stage = 'ombudsman_filed', ombudsman_filed_date = %s, updated_at = NOW()
                WHERE case_ref = %s AND user_id = %s
                RETURNING *
                """,
                (filed_date, case_ref, user_id),
            )
            row = cur.fetchone()
            if row:
                _log_event(cur, case_ref, case_type, user_id, "ombudsman_filed",
                           f"Filed with Insurance Ombudsman on {filed_date}.")
            conn.commit()
            return row


def mark_resolved(case_ref, user_id, resolved_date, outcome, resolution_amount=None,
                   table="claim_cases", case_type="health"):
    """outcome: 'won' or 'lost'"""
    stage = "resolved_won" if outcome == "won" else "resolved_lost"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET stage = %s, status = 'resolved', resolved_date = %s, resolution_amount = %s, updated_at = NOW()
                WHERE case_ref = %s AND user_id = %s
                RETURNING *
                """,
                (stage, resolved_date, resolution_amount, case_ref, user_id),
            )
            row = cur.fetchone()
            if row:
                note = f"Resolved ({outcome}) on {resolved_date}."
                if resolution_amount:
                    note += f" Amount recovered: ₹{resolution_amount}"
                _log_event(cur, case_ref, case_type, user_id, stage, note)
            conn.commit()
            return row


def mark_abandoned(case_ref, user_id, table="claim_cases", case_type="health"):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET stage = 'abandoned', status = 'abandoned', updated_at = NOW()
                WHERE case_ref = %s AND user_id = %s
                RETURNING *
                """,
                (case_ref, user_id),
            )
            row = cur.fetchone()
            if row:
                _log_event(cur, case_ref, case_type, user_id, "abandoned", "Case marked as abandoned.")
            conn.commit()
            return row


def add_case_note(case_ref, user_id, note_text, table="claim_cases", case_type="health"):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE {table} SET notes = COALESCE(notes || E'\\n', '') || %s, updated_at = NOW()
                WHERE case_ref = %s AND user_id = %s
                RETURNING *
                """,
                (note_text, case_ref, user_id),
            )
            row = cur.fetchone()
            if row:
                _log_event(cur, case_ref, case_type, user_id, "note_added", note_text)
            conn.commit()
            return row


def get_case_events(case_ref):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM case_events WHERE case_ref = %s ORDER BY event_date ASC, created_at ASC",
                (case_ref,),
            )
            return cur.fetchall()


def list_all_open_cases_for_user(user_id):
    """Combined view of open health + life cases, for the tracker dashboard."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *, 'health' AS case_type FROM claim_cases WHERE user_id = %s AND status = 'open'
                UNION ALL
                SELECT id, case_ref, user_id, insurer, policy_name, NULL AS claim_amount,
                       NULL AS hospital, NULL AS diagnosis, rejection_reason, matched_rule_id,
                       secondary_rule_ids, answers, score, letter_text, stage, status,
                       gro_sent_date, gro_followup_due, irdai_filed_date, irdai_followup_due,
                       ombudsman_filed_date, resolved_date, resolution_amount, notes,
                       created_at, updated_at, 'life' AS case_type
                FROM life_claim_cases WHERE user_id = %s AND status = 'open'
                ORDER BY created_at DESC
                """,
                (user_id, user_id),
            )
            return cur.fetchall()


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
