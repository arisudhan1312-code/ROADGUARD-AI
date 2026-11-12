"""Web Push subscription and delivery for ROADGUARD AI."""
import json
import os
import mysql.connector
from flask import Blueprint, current_app, jsonify, request, session
from access import require_role
from database import get_db_connection

push_blueprint = Blueprint("push", __name__, url_prefix="/api/push")

def _error(msg, code): return jsonify({"error": msg}), code

@push_blueprint.get("/vapid-public-key")
def get_vapid_public_key():
    key = os.getenv("VAPID_PUBLIC_KEY", "")
    if not key:
        return _error("Push not configured. Add VAPID_PUBLIC_KEY to backend/.env", 503)
    return jsonify({"public_key": key}), 200

@push_blueprint.post("/subscribe")
@require_role("driver")
def subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "")
    keys = data.get("keys", {})
    p256dh = keys.get("p256dh", "")
    auth = keys.get("auth", "")
    if not endpoint or not p256dh or not auth:
        return _error("Subscription must include endpoint, p256dh and auth.", 400)
    user_id = session["user_id"]
    conn = None; cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, is_active)
               VALUES (%s,%s,%s,%s,1)
               ON DUPLICATE KEY UPDATE p256dh=VALUES(p256dh), auth=VALUES(auth),
               is_active=1, updated_at=CURRENT_TIMESTAMP""",
            (user_id, endpoint, p256dh, auth)
        )
        conn.commit()
    except mysql.connector.Error:
        if conn: conn.rollback()
        current_app.logger.exception("Could not save push subscription.")
        return _error("Subscription could not be saved.", 503)
    finally:
        if cur: cur.close()
        if conn: conn.close()
    return jsonify({"message": "Push subscription saved."}), 201

@push_blueprint.post("/unsubscribe")
@require_role("driver")
def unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "")
    if not endpoint: return _error("endpoint is required.", 400)
    user_id = session["user_id"]
    conn = None; cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE push_subscriptions SET is_active=0 WHERE user_id=%s AND endpoint=%s", (user_id, endpoint))
        conn.commit()
    except mysql.connector.Error:
        if conn: conn.rollback()
        return _error("Could not unsubscribe.", 503)
    finally:
        if cur: cur.close()
        if conn: conn.close()
    return jsonify({"message": "Unsubscribed."}), 200

def send_push_to_user(user_id, title, body, url="/frontend/driver.html#notifications", notification_type="general", tag=None):
    """Send a Web Push to all active subscriptions for a user. Returns (sent, failed)."""
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        current_app.logger.warning("pywebpush not installed — push skipped.")
        return 0, 0
    private_key = os.getenv("VAPID_PRIVATE_KEY", "")
    public_key = os.getenv("VAPID_PUBLIC_KEY", "")
    claim_email = os.getenv("VAPID_CLAIM_EMAIL", "admin@roadguard.ai")
    if not private_key or not public_key:
        current_app.logger.warning("VAPID keys not set — push skipped.")
        return 0, 0
    payload = json.dumps({"title": title, "body": body, "url": url,
                          "notification_type": notification_type, "tag": tag or f"roadguard-{notification_type}"})
    conn = None; cur = None; subs = []
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=%s AND is_active=1", (user_id,))
        subs = cur.fetchall()
    except mysql.connector.Error:
        current_app.logger.exception("Could not load push subscriptions.")
        return 0, 0
    finally:
        if cur: cur.close()
        if conn: conn.close()
    sent = failed = 0
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": f"mailto:{claim_email}"},
            )
            sent += 1
        except Exception as exc:
            failed += 1
            current_app.logger.warning("Push failed sub %s: %s", sub["id"], exc)
            if hasattr(exc, "response") and exc.response and getattr(exc.response, "status_code", 0) == 410:
                try:
                    c2 = get_db_connection(); x = c2.cursor()
                    x.execute("UPDATE push_subscriptions SET is_active=0 WHERE id=%s", (sub["id"],))
                    c2.commit(); x.close(); c2.close()
                except Exception: pass
    return sent, failed
