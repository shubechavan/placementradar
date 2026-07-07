"""
email_sender.py — Gmail SMTP outreach for PlacementRadar
Uses Gmail App Password (2FA required). Never stores passwords in plaintext beyond settings.db.
"""
import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime


def send_outreach_email(to_email, subject, body, settings):
    """Send a single outreach email via Gmail SMTP."""
    gmail_user = settings.get('gmail_email','').strip()
    gmail_pass = settings.get('gmail_app_password','').strip()

    if not gmail_user or not gmail_pass:
        return {'success': False, 'error': 'Gmail credentials not configured in Settings'}
    if not to_email:
        return {'success': False, 'error': 'No recipient email'}

    try:
        msg = MIMEMultipart('alternative')
        msg['From']    = f"{settings.get('committee_name','Placement Committee')} <{gmail_user}>"
        msg['To']      = to_email
        msg['Subject'] = subject
        msg['Reply-To']= settings.get('committee_email', gmail_user)

        # Plain text version
        msg.attach(MIMEText(body, 'plain'))

        # HTML version with nice formatting
        html_body = _to_html(body, settings)
        msg.attach(MIMEText(html_body, 'html'))

        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2   # reject legacy TLS 1.0/1.1
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to_email, msg.as_string())

        return {'success': True}

    except smtplib.SMTPAuthenticationError:
        return {'success': False, 'error': 'Gmail authentication failed. Check your App Password (not regular password).'}
    except smtplib.SMTPException as e:
        return {'success': False, 'error': f'SMTP error: {str(e)}'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def send_bulk_emails(recipients, subject_template, body_template, settings):
    """
    Send bulk emails.
    recipients: list of dicts with keys: email, company, job_title, job_id
    Returns: list of result dicts
    """
    results = []
    for r in recipients:
        subject = _fill_template(subject_template, r, settings)
        body    = _fill_template(body_template,    r, settings)
        result  = send_outreach_email(r['email'], subject, body, settings)
        result['email']   = r['email']
        result['company'] = r.get('company','')
        result['job_id']  = r.get('job_id','')
        results.append(result)
        import time; time.sleep(1.5)  # avoid rate limiting
    return results


def build_subject(company, settings):
    college = settings.get('college_name','Our College')
    return f"Campus Hiring Opportunity — {college} × {company}"


def _fill_template(template, job_data, settings):
    """Replace {{placeholders}} in template."""
    vals = {
        'company':        job_data.get('company','the company'),
        'job_title':      job_data.get('title',''),
        'college_name':   settings.get('college_name','Our College'),
        'committee_name': settings.get('committee_name','Placement Committee'),
        'committee_email':settings.get('committee_email',
                          settings.get('gmail_email','')),
        'date':           datetime.now().strftime('%B %d, %Y'),
    }
    for k, v in vals.items():
        template = template.replace('{{'+k+'}}', v)
    return template


def _to_html(plain_text, settings):
    college = settings.get('college_name','Our College')
    paras   = ''.join(f'<p style="margin:0 0 14px">{p}</p>'
                      for p in plain_text.split('\n') if p.strip())
    return f"""
    <html><body style="font-family:Georgia,serif;color:#222;max-width:600px;margin:0 auto;padding:20px">
    <div style="border-top:3px solid #f5a623;padding-top:16px;margin-bottom:24px">
      <strong style="font-size:1.1rem;color:#f5a623">{college}</strong>
      <span style="color:#888;font-size:0.85rem;margin-left:10px">Placement Committee</span>
    </div>
    {paras}
    <div style="border-top:1px solid #eee;margin-top:24px;padding-top:12px;
                color:#888;font-size:0.8rem">
      Sent via PlacementRadar · {datetime.now().strftime('%B %Y')}
    </div>
    </body></html>"""


def test_smtp(settings):
    """Test if Gmail credentials work."""
    gmail_user = settings.get('gmail_email','').strip()
    gmail_pass = settings.get('gmail_app_password','').strip()
    if not gmail_user or not gmail_pass:
        return {'success':False,'error':'No credentials set'}
    try:
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2   # reject legacy TLS 1.0/1.1
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx) as s:
            s.login(gmail_user, gmail_pass)
        return {'success':True,'message':f'Connected as {gmail_user}'}
    except smtplib.SMTPAuthenticationError:
        return {'success':False,'error':'Wrong email/App Password. Use App Password, not your regular Gmail password.'}
    except Exception as e:
        return {'success':False,'error':str(e)}
