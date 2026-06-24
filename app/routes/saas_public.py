from __future__ import annotations

import logging
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import (
    audit,
    billing,
    config,
    signup,
    ui_sessions,
)
from ..auth import resolve_auth
from .deps import (
    templates,
    ui_context,
)

log = logging.getLogger("plutus")
router = APIRouter()

@router.get("/ui/saas", response_class=HTMLResponse)
def ui_saas_landing(request: Request):
    if not config.SAAS_MODE:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request,
        "saas_landing.html",
        ui_context(request, title="Plutus"),
    )



@router.get("/ui/saas/login", response_class=HTMLResponse)
def ui_saas_login(request: Request):
    if not config.SAAS_MODE:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "saas_login.html", ui_context(request, title="Sign in")
    )



@router.post("/ui/saas/login")
def ui_saas_login_post(request: Request, api_token: str = Form(...)):
    if not config.SAAS_MODE:
        return RedirectResponse("/", status_code=302)
    try:
        ctx = resolve_auth(request, form_token=api_token)
    except HTTPException:
        return templates.TemplateResponse(
            request,
            "saas_login.html",
            ui_context(request, title="Sign in", login_error="Invalid API key or admin token"),
            status_code=401,
        )
    dest = "/ui/saas/app/admin" if ctx.is_admin else "/ui/saas/app"
    response = RedirectResponse(dest, status_code=303)
    ui_sessions.attach_session_cookie(
        response,
        is_admin=ctx.is_admin,
        tenant_id=ctx.tenant_id if ctx.tenant else None,
        api_key_id=ctx.api_key_id,
    )
    return response



@router.get("/ui/saas/signup", response_class=HTMLResponse)
def ui_saas_signup(request: Request):
    if not config.SAAS_MODE:
        return RedirectResponse("/", status_code=302)
    if not signup.signup_enabled():
        return RedirectResponse("/ui/saas/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "saas_signup.html",
        ui_context(request, 
            title="Start free trial",
            trial_days=config.SIGNUP_TRIAL_DAYS,
            trial_cap=config.SIGNUP_TRIAL_RECOMMEND_CAP,
        ),
    )



@router.post("/ui/saas/signup")
def ui_saas_signup_post(
    request: Request,
    studio_name: str = Form(...),
    email: str = Form(...),
    store_slug: str | None = Form(None),
):
    if not signup.signup_enabled():
        return RedirectResponse("/ui/saas/login", status_code=302)
    try:
        result = signup.register_studio(
            studio_name=studio_name,
            email=email,
            store_slug=store_slug,
        )
    except signup.SignupError as exc:
        return templates.TemplateResponse(
            request,
            "saas_signup.html",
            ui_context(request, 
                title="Start free trial",
                trial_days=config.SIGNUP_TRIAL_DAYS,
                trial_cap=config.SIGNUP_TRIAL_RECOMMEND_CAP,
                signup_error=str(exc),
                studio_name=studio_name,
                email=email,
                store_slug=store_slug or "",
            ),
            status_code=400,
        )
    if result.get("verification_required"):
        return templates.TemplateResponse(
            request,
            "saas_signup_pending.html",
            ui_context(request, 
                title="Confirm your email",
                verify_email=result.get("verify_email"),
                verify_token_hours=config.SIGNUP_VERIFY_TOKEN_HOURS,
                tenant_name=result["tenant"]["name"],
            ),
            status_code=200,
        )
    api_key = result["api_key"]
    audit.record(
        "tenant.signup",
        request=request,
        tenant_id=result["tenant"]["id"],
        resource=result["tenant"]["id"],
    )
    if config.SIGNUP_REDIRECT_BILLING and billing.billing_enabled():
        try:
            session = billing.create_checkout_session(result["tenant"]["id"])
            response = RedirectResponse(session["checkout_url"], status_code=303)
            ui_sessions.attach_session_cookie(
                response,
                is_admin=False,
                tenant_id=result["tenant"]["id"],
                api_key_id=result.get("key_id"),
            )
            return response
        except billing.BillingError:
            pass
    response = templates.TemplateResponse(
        request,
        "saas_signup_welcome.html",
        ui_context(request, 
            title="Welcome",
            tenant=result["tenant"],
            api_key=api_key,
            store_url=result["store_url"],
            trial_days=config.SIGNUP_TRIAL_DAYS,
            trial_cap=config.SIGNUP_TRIAL_RECOMMEND_CAP,
        ),
    )
    ui_sessions.attach_session_cookie(
        response,
        is_admin=False,
        tenant_id=result["tenant"]["id"],
        api_key_id=result.get("key_id"),
    )
    return response



@router.get("/ui/saas/verify-email", response_class=HTMLResponse)
def ui_saas_verify_email(request: Request, token: str | None = Query(None)):
    from .. import signup_verify

    if not token:
        return templates.TemplateResponse(
            request,
            "saas_verify_email.html",
            ui_context(
                request,
                title="Email verification",
                verify_error="missing verification token",
            ),
            status_code=400,
        )
    try:
        result = signup_verify.verify_token(token)
    except signup_verify.SignupVerifyError as exc:
        return templates.TemplateResponse(
            request,
            "saas_verify_email.html",
            ui_context(request, title="Email verification", verify_error=str(exc)),
            status_code=400,
        )
    response = templates.TemplateResponse(
        request,
        "saas_signup_welcome.html",
        ui_context(request, 
            title="Welcome",
            tenant=result["tenant"],
            api_key=result["api_key"],
            store_url=result["store_url"],
            trial_days=config.SIGNUP_TRIAL_DAYS,
            trial_cap=config.SIGNUP_TRIAL_RECOMMEND_CAP,
        ),
    )
    ui_sessions.attach_session_cookie(
        response,
        is_admin=False,
        tenant_id=result["tenant"]["id"],
        api_key_id=result.get("key_id"),
    )
    audit.record(
        "tenant.signup.verified",
        request=request,
        tenant_id=result["tenant"]["id"],
        resource=result["tenant"]["id"],
    )
    return response



@router.get("/ui/saas/claim-invite", response_class=HTMLResponse)
def ui_saas_claim_invite(request: Request, token: str | None = Query(None)):
    from .. import tenant_invite

    if not token:
        return templates.TemplateResponse(
            request,
            "saas_claim_invite.html",
            ui_context(
                request,
                title="Accept invite",
                invite_error="missing invite token",
            ),
            status_code=400,
        )
    try:
        result = tenant_invite.claim_token(token)
    except tenant_invite.TenantInviteError as exc:
        return templates.TemplateResponse(
            request,
            "saas_claim_invite.html",
            ui_context(request, title="Accept invite", invite_error=str(exc)),
            status_code=400,
        )
    response = templates.TemplateResponse(
        request,
        "saas_invite_welcome.html",
        ui_context(
            request,
            title="Welcome",
            tenant=result["tenant"],
            api_key=result["api_key"],
            store_url=result["store_url"],
        ),
    )
    ui_sessions.attach_session_cookie(
        response,
        is_admin=False,
        tenant_id=result["tenant"]["id"],
        api_key_id=result.get("key_id"),
    )
    audit.record(
        "tenant.invite.claimed",
        request=request,
        tenant_id=result["tenant"]["id"],
        resource=result["tenant"]["id"],
    )
    return response



@router.get("/ui/saas/signup/pending", response_class=HTMLResponse)
def ui_saas_signup_pending(request: Request, email: str | None = None):
    if not signup.signup_enabled():
        return RedirectResponse("/ui/saas/login", status_code=302)
    addr = (email or request.query_params.get("email") or "").strip()
    if not addr:
        return RedirectResponse("/ui/saas/signup", status_code=302)
    return templates.TemplateResponse(
        request,
        "saas_signup_pending.html",
        ui_context(request, 
            title="Confirm your email",
            verify_email=addr,
            verify_token_hours=config.SIGNUP_VERIFY_TOKEN_HOURS,
            resent=request.query_params.get("resent"),
        ),
    )



@router.post("/ui/saas/resend-verification")
def ui_saas_resend_verification(
    email: str = Form(...),
    return_to: str | None = Form(None),
):
    from .. import signup_verify

    addr = email.strip()
    signup_verify.resend_for_email(addr)
    if return_to == "pending":
        return RedirectResponse(
            f"/ui/saas/signup/pending?email={quote_plus(addr)}&resent=1",
            status_code=303,
        )
    return RedirectResponse(
        f"/ui/saas/login?resent=1&email={quote_plus(addr)}",
        status_code=303,
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_policy(request: Request):
    back = "/ui/saas" if config.SAAS_MODE else "/"
    return templates.TemplateResponse(
        request,
        "trust_privacy.html",
        ui_context(request, title="Privacy", back_url=back),
    )


@router.get("/terms", response_class=HTMLResponse)
def terms_of_service(request: Request):
    back = "/ui/saas" if config.SAAS_MODE else "/"
    return templates.TemplateResponse(
        request,
        "trust_terms.html",
        ui_context(request, title="Terms", back_url=back),
    )


