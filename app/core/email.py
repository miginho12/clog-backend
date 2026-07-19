"""이메일 발송 유틸 (SMTP Gmail).

이메일 인증 메일을 aiosmtplib 으로 비동기 발송.
- Gmail SMTP (STARTTLS, 587)
- 인증 실패는 예외로 전파 (호출측에서 정책 결정)
"""
from email.message import EmailMessage

import aiosmtplib

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _verification_html(nickname: str, verify_url: str) -> str:
    """인증 메일 HTML 본문 (Clog 브랜딩)."""
    return f"""\
<!DOCTYPE html>
<html lang="ko">
<body style="margin:0;padding:0;background:#FAECE7;font-family:sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#fff;border-radius:16px;overflow:hidden;">
    <div style="background:#D85A30;padding:24px;text-align:center;">
      <span style="font-size:28px;">🧗</span>
      <h1 style="color:#fff;margin:8px 0 0;font-size:20px;">Clog</h1>
    </div>
    <div style="padding:32px 28px;">
      <p style="font-size:15px;color:#333;">
        {nickname}님, 반가워요!
      </p>
      <p style="font-size:14px;color:#666;line-height:1.6;">
        아래 버튼을 눌러 이메일 인증을 완료하면<br>
        Clog 로그인이 가능해요.
      </p>
      <div style="text-align:center;margin:28px 0;">
        <a href="{verify_url}"
           style="display:inline-block;background:#D85A30;color:#fff;
                  text-decoration:none;padding:12px 32px;border-radius:10px;
                  font-size:15px;font-weight:600;">
          이메일 인증하기
        </a>
      </div>
      <p style="font-size:12px;color:#999;line-height:1.5;">
        버튼이 안 되면 아래 주소를 브라우저에 붙여넣어 주세요:<br>
        <span style="color:#7C5CD8;word-break:break-all;">{verify_url}</span>
      </p>
      <p style="font-size:12px;color:#bbb;margin-top:20px;">
        이 링크는 24시간 후 만료돼요. 본인이 요청하지 않았다면 무시하셔도 됩니다.
      </p>
    </div>
  </div>
</body>
</html>"""


async def send_verification_email(
    *, to_email: str, nickname: str, verify_url: str
) -> None:
    """인증 메일 발송. 실패 시 예외 전파."""
    settings = get_settings()

    msg = EmailMessage()
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
    msg["To"] = to_email
    msg["Subject"] = "[Clog] 이메일 인증을 완료해 주세요"
    msg.set_content(
        f"{nickname}님, 아래 주소로 이메일 인증을 완료해 주세요:\n{verify_url}\n\n"
        "이 링크는 24시간 후 만료됩니다."
    )
    msg.add_alternative(
        _verification_html(nickname, verify_url), subtype="html"
    )

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        start_tls=True,
    )
    logger.info("verification_email_sent", to=to_email)


def _password_reset_html(nickname: str, code: str) -> str:
    """비밀번호 재설정 코드 메일 HTML 본문 (새 브랜드 컬러 — 보라/코랄)."""
    return f"""\
<!DOCTYPE html>
<html lang="ko">
<body style="margin:0;padding:0;background:#F1ECFB;font-family:sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#fff;border-radius:16px;overflow:hidden;">
    <div style="background:linear-gradient(135deg,#7C5CD8,#E86A5C);padding:24px;text-align:center;">
      <h1 style="color:#fff;margin:0;font-size:20px;">Clog</h1>
    </div>
    <div style="padding:32px 28px;">
      <p style="font-size:15px;color:#3A3450;">
        {nickname}님, 비밀번호 재설정을 요청하셨네요.
      </p>
      <p style="font-size:14px;color:#5C5478;line-height:1.6;">
        아래 6자리 코드를 앱에 입력해 주세요.
      </p>
      <div style="text-align:center;margin:28px 0;">
        <span style="display:inline-block;background:#F1ECFB;color:#7C5CD8;
                     letter-spacing:6px;padding:14px 28px;border-radius:12px;
                     font-size:26px;font-weight:800;">
          {code}
        </span>
      </div>
      <p style="font-size:12px;color:#9C93B5;line-height:1.5;">
        이 코드는 3분 후 만료돼요. 본인이 요청하지 않았다면 무시하셔도 됩니다.
      </p>
    </div>
  </div>
</body>
</html>"""


async def send_password_reset_email(
    *, to_email: str, nickname: str, code: str
) -> None:
    """비밀번호 재설정 코드 메일 발송. 실패 시 예외 전파."""
    settings = get_settings()

    msg = EmailMessage()
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
    msg["To"] = to_email
    msg["Subject"] = "[Clog] 비밀번호 재설정 코드"
    msg.set_content(
        f"{nickname}님, 비밀번호 재설정 코드는 {code} 입니다.\n"
        "이 코드는 3분 후 만료됩니다."
    )
    msg.add_alternative(
        _password_reset_html(nickname, code), subtype="html"
    )

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        start_tls=True,
    )
    logger.info("password_reset_email_sent", to=to_email)
