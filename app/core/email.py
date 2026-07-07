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
