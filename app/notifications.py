"""Order and ops notifications — SMTP email and optional webhook."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from . import config, db

log = logging.getLogger("plutus.notifications")


def smtp_ready() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_FROM)


def _smtp_ready() -> bool:
    return smtp_ready()


def _send_email(*, to: str, subject: str, body: str) -> bool:
    if not _smtp_ready() or not to:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = to
    msg.set_content(body)
    try:
        smtp = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
        try:
            if config.SMTP_USER and config.SMTP_PASSWORD:
                smtp.starttls()
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except smtplib.SMTPException:
                smtp.close()
        return True
    except Exception:
        log.exception("failed to send email to %s", to)
        return False


def _post_webhook(payload: dict[str, Any]) -> bool:
    if not config.ORDER_WEBHOOK_URL:
        return False
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(config.ORDER_WEBHOOK_URL, json=payload)
        return resp.status_code < 400
    except Exception:
        log.exception("order webhook failed")
        return False


def _order_recipients(tenant: dict) -> list[str]:
    recipients: list[str] = []
    if tenant.get("notify_email"):
        recipients.append(tenant["notify_email"])
    if config.ORDER_ALERT_EMAIL and config.ORDER_ALERT_EMAIL not in recipients:
        recipients.append(config.ORDER_ALERT_EMAIL)
    return recipients


def _bundle_title_for_order(order: dict) -> str | None:
    run = db.get_run(int(order["run_id"]), tenant_id=order["tenant_id"])
    if not run:
        return None
    bundles = (run.get("payload") or {}).get("bundles") or []
    idx = int(order.get("bundle_index") or 0)
    if 0 <= idx < len(bundles):
        title = bundles[idx].get("title")
        return title if title else None
    return None


def _client_confirmation_body(order: dict, tenant: dict) -> str:
    from .order_tracking import client_track_url

    studio = tenant.get("name") or order["tenant_id"]
    lines = [f"Thanks — your order with {studio} is confirmed.", ""]
    bundle_title = _bundle_title_for_order(order)
    if bundle_title:
        lines.append(f"Bundle: {bundle_title}")
        lines.append("")
    for item in order.get("items") or []:
        qty = int(item.get("quantity") or 1)
        unit = int(item["unit_cents"])
        lines.append(f"  - {item['label']} × {qty} — ${unit * qty / 100:,.2f}")
    lines.extend(
        [
            "",
            f"Total: ${order['total_cents'] / 100:,.2f} USD",
            "",
            "Track fulfillment anytime:",
            client_track_url(order["client_token"]),
        ]
    )
    return "\n".join(lines)


def send_test_email(*, to: str, tenant: dict) -> bool:
    """Send a dashboard test notification to verify SMTP delivery."""
    studio = tenant.get("name") or tenant.get("id") or "your studio"
    body = (
        f"This is a test notification from Plutus for {studio}.\n\n"
        "When clients pay, you'll receive order alerts at your notify email. "
        "Clients receive their own confirmation with bundle details, line items, "
        "total, and a tracking link.\n"
    )
    return _send_email(
        to=to,
        subject=f"[Plutus] Test notification — {studio}",
        body=body,
    )


def _order_summary_lines(order: dict, tenant: dict, *, headline: str) -> list[str]:
    lines = [
        headline,
        f"Tenant: {tenant.get('name') or order['tenant_id']}",
        f"Run: {order['run_id']} · bundle #{order['bundle_index']}",
    ]
    if order.get("client_email"):
        lines.append(f"Client: {order['client_email']}")
    if order.get("client_name"):
        lines.append(f"Name: {order['client_name']}")
    lines.append("")
    for item in order.get("items") or []:
        lines.append(
            f"  - {item['label']} × {item['quantity']} @ "
            f"${item['unit_cents'] / 100:,.2f}"
        )
    if order.get("lab_ref"):
        lines.append(f"\nLab ref: {order['lab_ref']} ({order.get('lab_status') or 'pending'})")
    if order.get("client_token"):
        from .order_tracking import client_track_url

        lines.append(f"\nTrack order: {client_track_url(order['client_token'])}")
    return lines


def notify_order_paid(order_id: int) -> dict[str, bool]:
    """Notify photographer and ops when a client order is paid."""
    order = db.get_order(order_id)
    if not order:
        return {"email": False, "webhook": False}
    tenant = db.get_tenant(order["tenant_id"]) or {}
    body = "\n".join(
        _order_summary_lines(
            order,
            tenant,
            headline=f"Order #{order['id']} paid — {order['total_cents'] / 100:,.2f} USD",
        )
    )
    subject = f"[Plutus] Order #{order['id']} paid — {tenant.get('name', order['tenant_id'])}"
    recipients = _order_recipients(tenant)
    emailed = any(_send_email(to=addr, subject=subject, body=body) for addr in recipients)
    client_emailed = False
    if config.NOTIFY_CLIENT_ON_PAID and order.get("client_email") and order.get("client_token"):
        studio = tenant.get("name") or order["tenant_id"]
        client_emailed = _send_email(
            to=order["client_email"],
            subject=f"Order confirmed — {studio}",
            body=_client_confirmation_body(order, tenant),
        )
    webhook = _post_webhook(
        {
            "event": "order.paid",
            "order_id": order["id"],
            "tenant_id": order["tenant_id"],
            "total_cents": order["total_cents"],
            "client_email": order.get("client_email"),
            "lab_ref": order.get("lab_ref"),
            "lab_status": order.get("lab_status"),
        }
    )
    return {"email": emailed, "webhook": webhook, "client_email": client_emailed}


def notify_lab_status(order_id: int, status: str) -> dict[str, bool]:
    """Notify when lab fulfillment reaches shipped (or complete)."""
    if not config.NOTIFY_LAB_SHIPPED or status not in {"shipped", "complete"}:
        return {"email": False, "webhook": False}
    order = db.get_order(order_id)
    if not order:
        return {"email": False, "webhook": False}
    tenant = db.get_tenant(order["tenant_id"]) or {}
    label = "shipped" if status == "shipped" else "delivered"
    body = "\n".join(
        _order_summary_lines(
            order,
            tenant,
            headline=f"Order #{order['id']} {label} — notify your client",
        )
    )
    subject = f"[Plutus] Order #{order['id']} {label} — {tenant.get('name', order['tenant_id'])}"
    recipients = _order_recipients(tenant)
    client = order.get("client_email")
    if client and client not in recipients:
        recipients.append(client)
    emailed = any(_send_email(to=addr, subject=subject, body=body) for addr in recipients)
    webhook = _post_webhook(
        {
            "event": f"order.lab.{status}",
            "order_id": order["id"],
            "tenant_id": order["tenant_id"],
            "client_email": order.get("client_email"),
            "lab_ref": order.get("lab_ref"),
            "lab_status": status,
        }
    )
    return {"email": emailed, "webhook": webhook}