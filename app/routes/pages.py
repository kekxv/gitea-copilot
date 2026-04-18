from fastapi import APIRouter, Request, Response, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db, SessionLocal
from ..models import Admin, GiteaInstance, GiteaAccount, ProcessedEvent, SystemConfig
from ..auth.admin import (
    verify_admin_credentials, verify_admin_totp,
    change_admin_password, enable_admin_totp, disable_admin_totp,
    is_totp_enabled
)
from ..auth.utils import hash_password, verify_password
from jose import jwt
import os
import secrets
import logging
from datetime import datetime, timedelta

router = APIRouter()
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key")
ALGORITHM = "HS256"

templates = Jinja2Templates(directory="app/templates")


def render(request: Request, template: str, context: dict) -> HTMLResponse:
    context["is_admin"] = request.cookies.get("admin_token") is not None
    return templates.TemplateResponse(request, template, context)


def get_admin_from_token(request: Request, db: Session) -> Optional[Admin]:
    """Get admin from JWT token in cookie."""
    token = request.cookies.get("admin_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        return db.query(Admin).filter(Admin.username == username).first()
    except:
        return None


def get_system_config(db: Session) -> SystemConfig:
    """Get or create system config."""
    config = db.query(SystemConfig).first()
    if not config:
        config = SystemConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def has_admin(db: Session) -> bool:
    """Check if any admin exists."""
    return db.query(Admin).count() > 0


# ============ Index Page ============
@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    show_register = not has_admin(db)
    config = get_system_config(db)

    # Check system status
    status = {
        "database": True,
        "llm_configured": config.llm_api_key is not None,
        "instances": db.query(GiteaInstance).count(),
        "accounts": db.query(GiteaAccount).count()
    }

    return render(request, "index.html", {
        "show_register": show_register,
        "status": status
    })


# ============ Admin Registration (First User) ============
@router.get("/admin/register", response_class=HTMLResponse)
async def admin_register_page(request: Request, db: Session = Depends(get_db)):
    if has_admin(db):
        return render(request, "admin/login.html", {"error": "管理员已存在，无法继续注册"})
    return render(request, "admin/register.html", {})


@router.post("/admin/register", response_class=HTMLResponse)
async def admin_register_submit(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    if has_admin(db):
        return render(request, "admin/login.html", {"error": "管理员已存在，无法继续注册"})

    if len(username) < 3 or len(password) < 6:
        return render(request, "admin/register.html", {"error": "用户名至少3个字符，密码至少6个字符"})

    admin = Admin(
        username=username,
        password_hash=hash_password(password),
        totp_enabled=False
    )
    db.add(admin)
    db.commit()

    # Auto login after registration
    token_data = {"sub": admin.username}
    expire = datetime.utcnow() + timedelta(hours=8)
    token_data["exp"] = expire
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)

    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    response.set_cookie(key="admin_token", value=token, httponly=True, max_age=86400 * 8, samesite="strict", secure=False)
    return response


# ============ Admin Login ============
@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, db: Session = Depends(get_db)):
    # If already logged in, redirect to dashboard
    admin = get_admin_from_token(request, db)
    if admin:
        return RedirectResponse(url="/admin/dashboard")

    if not has_admin(db):
        return RedirectResponse(url="/admin/register")
    return render(request, "admin/login.html", {})


@router.post("/admin/login", response_class=HTMLResponse)
async def admin_login_submit(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    otp_code: str = Form(default=""),
    db: Session = Depends(get_db)
):
    try:
        admin = verify_admin_credentials(db, username, password)

        # Always check TOTP if provided or if TOTP is enabled
        if is_totp_enabled(admin):
            if not otp_code or not verify_admin_totp(admin, otp_code):
                return render(request, "admin/login.html", {
                    "error": "TOTP 验证码错误或未填写",
                    "username": username
                })
        elif otp_code and not is_totp_enabled(admin):
            # TOTP not enabled but code provided - ignore it
            pass

        token_data = {"sub": admin.username}
        expire = datetime.utcnow() + timedelta(hours=8)
        token_data["exp"] = expire
        token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)

        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(key="admin_token", value=token, httponly=True, max_age=86400 * 8, samesite="strict", secure=False)
        return response

    except HTTPException:
        return render(request, "admin/login.html", {"error": "用户名或密码错误"})


# ============ Admin Dashboard ============
@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    admin = get_admin_from_token(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login")

    stats = {
        "instances": db.query(GiteaInstance).count(),
        "accounts": db.query(GiteaAccount).count(),
        "events": db.query(ProcessedEvent).count()
    }

    config = get_system_config(db)
    instances = db.query(GiteaInstance).all()
    accounts = db.query(GiteaAccount).all()

    # Build instance data with accounts
    instance_data = []
    for instance in instances:
        instance_accounts = db.query(GiteaAccount).filter(
            GiteaAccount.instance_id == instance.id
        ).all()
        instance_data.append({
            "id": instance.id,
            "url": instance.url,
            "client_id": instance.client_id,
            "accounts": [a.gitea_username for a in instance_accounts],
            "account_count": len(instance_accounts)
        })

    account_data = []
    for account in accounts:
        instance = db.query(GiteaInstance).filter(GiteaInstance.id == account.instance_id).first()
        account_data.append({
            "id": account.id,
            "gitea_username": account.gitea_username,
            "instance_url": instance.url if instance else "Unknown",
            "instance_id": instance.id if instance else 0,
            "webhook_id": account.webhook_id
        })

    # Construct callback URL
    scheme = request.url.scheme
    host = request.url.hostname or "localhost"
    port = request.url.port
    if port and port not in (80, 443):
        base_url = f"{scheme}://{host}:{port}"
    else:
        base_url = f"{scheme}://{host}"
    callback_url = base_url + "/oauth/callback"

    return render(request, "admin/dashboard.html", {
        "admin_username": admin.username,
        "admin_totp_enabled": is_totp_enabled(admin),
        "stats": stats,
        "config": config,
        "instances": instance_data,
        "accounts": account_data,
        "callback_url": callback_url,
        "bot_username": accounts[0].gitea_username if accounts else "your-account"
    })


# ============ Admin Password Change ============
@router.get("/admin/password", response_class=HTMLResponse)
async def admin_password_page(request: Request, db: Session = Depends(get_db)):
    admin = get_admin_from_token(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login")
    return render(request, "admin/password.html", {"admin_username": admin.username})


@router.post("/admin/password", response_class=HTMLResponse)
async def admin_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db)
):
    admin = get_admin_from_token(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login")

    if not verify_password(current_password, admin.password_hash):
        return render(request, "admin/password.html", {
            "admin_username": admin.username,
            "error": "当前密码错误"
        })

    if len(new_password) < 6:
        return render(request, "admin/password.html", {
            "admin_username": admin.username,
            "error": "新密码至少6个字符"
        })

    change_admin_password(db, admin, new_password)
    return render(request, "admin/password.html", {
        "admin_username": admin.username,
        "message": "密码已修改成功"
    })


# ============ Admin TOTP Management ============
@router.get("/admin/totp", response_class=HTMLResponse)
async def admin_totp_page(request: Request, db: Session = Depends(get_db)):
    admin = get_admin_from_token(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login")

    if is_totp_enabled(admin):
        return render(request, "admin/totp_status.html", {
            "admin_username": admin.username,
            "totp_enabled": True
        })
    else:
        # Generate secret but don't enable yet - require verification first
        from ..auth.admin import generate_totp_secret, get_totp_uri
        from ..utils.qrcode import generate_qr_code_data_uri

        secret = generate_totp_secret()
        uri = get_totp_uri(admin.username, secret)
        qr_data_uri = generate_qr_code_data_uri(uri)

        return render(request, "admin/totp_setup.html", {
            "admin_username": admin.username,
            "totp_secret": secret,
            "totp_uri": uri,
            "qr_data_uri": qr_data_uri,
            "totp_enabled": False
        })


@router.post("/admin/totp/enable", response_class=HTMLResponse)
async def admin_totp_enable(
    request: Request,
    totp_secret: str = Form(...),
    otp_code: str = Form(...),
    db: Session = Depends(get_db)
):
    admin = get_admin_from_token(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login")

    # Verify the code before enabling
    import pyotp
    totp = pyotp.TOTP(totp_secret)
    if not totp.verify(otp_code, valid_window=1):
        from ..auth.admin import get_totp_uri
        from ..utils.qrcode import generate_qr_code_data_uri

        uri = get_totp_uri(admin.username, totp_secret)
        qr_data_uri = generate_qr_code_data_uri(uri)

        return render(request, "admin/totp_setup.html", {
            "admin_username": admin.username,
            "totp_secret": totp_secret,
            "totp_uri": uri,
            "qr_data_uri": qr_data_uri,
            "totp_enabled": False,
            "error": "验证码错误，请重新输入"
        })

    # Enable TOTP
    admin.totp_secret = totp_secret
    admin.totp_enabled = True
    db.commit()

    return RedirectResponse(url="/admin/dashboard", status_code=303)


@router.post("/admin/totp/disable", response_class=HTMLResponse)
async def admin_totp_disable(
    request: Request,
    otp_code: str = Form(...),
    db: Session = Depends(get_db)
):
    admin = get_admin_from_token(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login")

    if not verify_admin_totp(admin, otp_code):
        return render(request, "admin/totp_status.html", {
            "admin_username": admin.username,
            "totp_enabled": True,
            "error": "TOTP 验证码错误"
        })

    disable_admin_totp(db, admin)
    return RedirectResponse(url="/admin/dashboard", status_code=303)


# ============ System Config ============
@router.post("/admin/config")
async def admin_config_update(
    request: Request,
    llm_base_url: str = Form(default="https://api.openai.com/v1"),
    llm_api_key: str = Form(default=""),
    llm_model: str = Form(default="gpt-4o-mini"),
    copilot_docs_limit: int = Form(default=10),
    copilot_docs_size_limit: int = Form(default=25),
    clear_api_key: str = Form(default=""),
    db: Session = Depends(get_db)
):
    admin = get_admin_from_token(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login")

    config = get_system_config(db)
    config.llm_base_url = llm_base_url
    config.llm_model = llm_model
    config.copilot_docs_limit = copilot_docs_limit
    config.copilot_docs_size_limit = copilot_docs_size_limit

    # Handle API key: clear if requested, update if provided
    if clear_api_key:
        config.llm_api_key = None
    elif llm_api_key:
        config.llm_api_key = llm_api_key

    db.commit()

    return RedirectResponse(url="/admin/dashboard", status_code=303)


@router.get("/admin/logout")
async def admin_logout():
    response = RedirectResponse(url="/")
    response.delete_cookie("admin_token")
    return response


# ============ Gitea Instance Management ============
@router.post("/admin/instances")
async def create_instance(
    request: Request,
    url: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    db: Session = Depends(get_db)
):
    admin = get_admin_from_token(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login")

    instance = GiteaInstance(
        url=url,
        client_id=client_id,
        client_secret_encrypted=client_secret
    )
    db.add(instance)
    db.commit()
    return RedirectResponse(url="/admin/dashboard", status_code=303)


@router.delete("/admin/instances/{instance_id}")
async def delete_instance(
    request: Request,
    instance_id: int,
    db: Session = Depends(get_db)
):
    admin = get_admin_from_token(request, db)
    if not admin:
        return {"error": "Unauthorized"}

    instance = db.query(GiteaInstance).filter(GiteaInstance.id == instance_id).first()
    if instance:
        db.delete(instance)
        db.commit()
    return {"message": "Instance deleted"}


@router.delete("/admin/accounts/{account_id}")
async def delete_account(
    request: Request,
    account_id: int,
    db: Session = Depends(get_db)
):
    admin = get_admin_from_token(request, db)
    if not admin:
        return {"error": "Unauthorized"}

    account = db.query(GiteaAccount).filter(GiteaAccount.id == account_id).first()
    if account:
        # Delete user webhook from Gitea if exists
        if account.webhook_id:
            try:
                from ..gitea import GiteaClient
                instance = account.instance
                client = GiteaClient(instance.url, account.access_token)
                await client.delete_user_hook(account.webhook_id)
            except Exception as e:
                logging.warning(f"Failed to delete user webhook: {e}")

        db.delete(account)
        db.commit()
    return {"message": "Account deleted"}


# ============ OAuth Flow ============
@router.get("/oauth/{instance_id}/redirect")
async def oauth_redirect(
    request: Request,
    instance_id: int,
    db: Session = Depends(get_db)
):
    from ..auth import gitea as gitea_auth

    admin = get_admin_from_token(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login")

    instance = db.query(GiteaInstance).filter(GiteaInstance.id == instance_id).first()
    if not instance:
        return RedirectResponse(url="/admin/dashboard")

    # Use fixed callback URL
    scheme = request.url.scheme
    host = request.url.hostname or "localhost"
    port = request.url.port
    if port and port not in (80, 443):
        base_url = f"{scheme}://{host}:{port}"
    else:
        base_url = f"{scheme}://{host}"
    redirect_uri = base_url + "/oauth/callback"

    state = gitea_auth.create_oauth_state(instance_id, redirect_uri)
    redirect_url = gitea_auth.get_oauth_redirect_url(instance, state, redirect_uri)
    return {"redirect_url": redirect_url, "state": state}


@router.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    request: Request,
    response: Response,
    code: str,
    state: str,
    db: Session = Depends(get_db)
):
    from ..auth import gitea as gitea_auth
    from ..gitea import GiteaClient, generate_webhook_secret, encode_user_context

    # OAuth callback doesn't require admin login - the state validates the request
    state_data = gitea_auth.validate_oauth_state(state)
    if not state_data:
        return render(request, "admin/oauth_result.html", {
            "success": False,
            "error": "无效的 OAuth 状态",
            "is_admin": request.cookies.get("admin_token") is not None
        })

    instance_id = state_data["instance_id"]
    gitea_auth.oauth_states.pop(state, None)
    instance = db.query(GiteaInstance).filter(GiteaInstance.id == instance_id).first()

    if not instance:
        return render(request, "admin/oauth_result.html", {
            "success": False,
            "error": "实例不存在",
            "is_admin": request.cookies.get("admin_token") is not None
        })

    try:
        # Use fixed callback URL for token exchange
        scheme = request.url.scheme
        host = request.url.hostname or "localhost"
        port = request.url.port
        if port and port not in (80, 443):
            base_url = f"{scheme}://{host}:{port}"
        else:
            base_url = f"{scheme}://{host}"
        redirect_uri = base_url + "/oauth/callback"

        token_data = await gitea_auth.exchange_code_for_token(instance, code, redirect_uri)

        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")

        gitea_user = await gitea_auth.get_gitea_user_info(instance, access_token)

        account = gitea_auth.create_or_update_account(
            db, instance,
            str(gitea_user["id"]),
            gitea_user["login"],
            access_token,
            refresh_token,
            expires_in
        )

        # Create user-level webhook (receives events from all repos)
        client = GiteaClient(instance.url, access_token)
        webhook_url = base_url + "/webhook/gitea"
        auth_header = encode_user_context(instance.id, account.id)

        webhook_created = False
        if not account.webhook_id:
            try:
                secret = generate_webhook_secret()
                webhook = await client.create_user_hook(webhook_url, secret, auth_header)
                account.webhook_id = webhook.get("id")
                account.webhook_secret = secret
                db.commit()
                webhook_created = True
            except Exception as e:
                logging.error(f"Failed to create user webhook: {e}")

        return render(request, "admin/oauth_result.html", {
            "success": True,
            "gitea_username": gitea_user["login"],
            "account_id": account.id,
            "webhook_created": webhook_created,
            "webhook_exists": account.webhook_id is not None,
            "is_admin": request.cookies.get("admin_token") is not None
        })

    except Exception as e:
        logging.error(f"OAuth callback error: {e}")
        return render(request, "admin/oauth_result.html", {
            "success": False,
            "error": "OAuth 授权失败，请重试",
            "is_admin": request.cookies.get("admin_token") is not None
        })