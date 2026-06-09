# src/osrs_ge_quant/notifications.py

from __future__ import annotations

import os
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

from .config import load_settings
from .db import get_session
from .models import Recommendation, Item


def send_discord_webhook(content: str, embed: dict = None) -> bool:
    """
    Send a ping to a Discord channel via Webhook.
    """
    settings = load_settings()
    notif_settings = settings.get("notifications", {})
    
    # Priority: Env variable > Settings file
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL") or notif_settings.get("discord_webhook_url")
    
    if not webhook_url:
        # Silently skip if webhook URL is not configured
        return False
        
    payload = {"content": content}
    if embed:
        payload["embeds"] = [embed]
        
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        r.raise_for_status()
        print("[Notifications] Discord ping sent successfully.")
        return True
    except Exception as e:
        print(f"[Error] Failed to send Discord webhook: {e}")
        return False


def send_email_notification(subject: str, html_content: str) -> bool:
    """
    Send an HTML email notification via SMTP using settings and env variables.
    """
    settings = load_settings()
    notif_settings = settings.get("notifications", {})

    if not notif_settings.get("enabled", False):
        print("[Notifications] Emails disabled in settings.")
        return False

    smtp_server = notif_settings.get("smtp_server", "smtp.gmail.com")
    smtp_port = notif_settings.get("smtp_port", 587)
    recipient = notif_settings.get("recipient_email", "london.thomson.merriman@gmail.com")

    # Load credentials from environment
    sender_email = os.getenv("SMTP_USER")
    sender_password = os.getenv("SMTP_PASSWORD")

    if not sender_email or not sender_password:
        print("[Warning] SMTP_USER or SMTP_PASSWORD not set in environment. Skipping email.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = recipient

    # Inject default OSRS styles into the HTML wrapper
    styled_html = f"""
    <html>
    <head>
        <style>
            body {{
                background-color: #0e0d0c;
                color: #d1c2a5;
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
            }}
            .container {{
                background-color: #1a1613;
                border: 2px solid #3c352e;
                border-radius: 4px;
                padding: 25px;
                max-width: 650px;
                margin: 0 auto;
                box-shadow: 0 4px 10px rgba(0,0,0,0.5);
            }}
            h2 {{
                color: #ffb900;
                font-size: 22px;
                border-bottom: 2px solid #5a4f43;
                padding-bottom: 10px;
                margin-top: 0;
            }}
            .alert-banner {{
                background-color: #7b1113;
                color: #ffffff;
                border: 1px solid #c92a2a;
                border-radius: 3px;
                padding: 15px;
                margin-bottom: 20px;
                font-weight: bold;
                text-align: center;
                font-size: 16px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
            }}
            th {{
                background-color: #2d2621;
                color: #ffb900;
                border: 1px solid #5a4f43;
                padding: 10px;
                text-align: left;
                font-size: 13px;
            }}
            td {{
                border: 1px solid #3c352e;
                padding: 10px;
                font-size: 13px;
            }}
            tr:nth-child(even) {{
                background-color: #201b17;
            }}
            .badge-up {{
                color: #2f9e44;
                font-weight: bold;
            }}
            .badge-down {{
                color: #e03131;
                font-weight: bold;
            }}
            .footer {{
                margin-top: 25px;
                font-size: 11px;
                color: #7a6e5d;
                text-align: center;
                border-top: 1px solid #3c352e;
                padding-top: 15px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {html_content}
            <div class="footer">
                OSRS GE Quant Bloomberg Terminal Alerting System • {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}
            </div>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(styled_html, "html"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, [recipient], msg.as_string())
        print(f"[Notifications] Email sent successfully to {recipient} (Subject: {subject})")
        return True
    except Exception as e:
        print(f"[Error] Failed to send email via SMTP: {e}")
        return False


def send_trade_digest() -> bool:
    """
    Query the latest recommendations and send a daily trade digest.
    """
    from sqlalchemy import func
    session = get_session()
    
    # Find latest recommendation timestamp
    latest_ts = session.query(func.max(Recommendation.created_at)).scalar()
    if not latest_ts:
        session.close()
        print("[Notifications] No recommendations found in DB.")
        return False
        
    # Threshold for latest run (within 1 minute)
    threshold = latest_ts - timedelta(minutes=1)
    
    # Fetch top 15 flips from the latest run
    flips = (
        session.query(Recommendation, Item.name)
        .join(Item, Recommendation.item_id == Item.id)
        .filter(Recommendation.signal_type == "pure_flip")
        .filter(Recommendation.created_at >= threshold)
        .order_by(Recommendation.expected_profit_gp.desc())
        .limit(15)
        .all()
    )

    # Fetch top 10 skilling opportunities from the latest run
    skilling = (
        session.query(Recommendation)
        .filter(Recommendation.signal_type == "processing")
        .filter(Recommendation.created_at >= threshold)
        .order_by(Recommendation.expected_profit_gp.desc())
        .limit(10)
        .all()
    )

    session.close()

    if not flips and not skilling:
        print("[Notifications] No outstanding recommendations to email.")
        return False

    html = "<h2>OSRS GE Quant - Daily Trade Recommendations</h2>"
    html += "<p>Here are your top high-margin trade setups on the Grand Exchange:</p>"

    if flips:
        html += "<h3>Top Margin Flips</h3>"
        html += """
        <table>
            <thead>
                <tr>
                    <th>Item</th>
                    <th>Buy Trigger</th>
                    <th>Target Qty</th>
                    <th>Expected Profit</th>
                    <th>Expected Return</th>
                </tr>
            </thead>
            <tbody>
        """
        for r, name in flips:
            ret_pct = f"{r.expected_return_pct * 100:.2f}%" if r.expected_return_pct else "N/A"
            html += f"""
                <tr>
                    <td><b>{name}</b> (ID: {r.item_id})</td>
                    <td>{r.price_each:,.0f} gp</td>
                    <td>{r.qty:,.0f}</td>
                    <td style="color: #40c057;">+{r.expected_profit_gp:,.0f} gp</td>
                    <td>{ret_pct}</td>
                </tr>
            """
        html += "</tbody></table>"

    if skilling:
        html += "<h3>Top Skilling Processing Opportunities</h3>"
        html += """
        <table>
            <thead>
                <tr>
                    <th>Skilling Method</th>
                    <th>Required Stat</th>
                    <th>Profit / Batch</th>
                </tr>
            </thead>
            <tbody>
        """
        for r in skilling:
            html += f"""
                <tr>
                    <td><b>{r.reason}</b></td>
                    <td>{r.strategy_name.replace('_processing', '').title()} (Lvl {r.price_each or 1})</td>
                    <td style="color: #40c057;">+{r.expected_profit_gp:,.0f} gp</td>
                </tr>
            """
        html += "</tbody></table>"

    subject = f"OSRS GE Quant Trade Digest - {datetime.utcnow().strftime('%b %d, %Y')}"
    
    # Send Discord notification alongside email
    discord_lines = [f"**{subject}**", "----------------------------------------"]
    if flips:
        discord_lines.append("**Top Flips:**")
        for r, name in flips[:5]:
            ret = f"{r.expected_return_pct*100:.2f}%" if r.expected_return_pct else "N/A"
            discord_lines.append(f"• **{name}** (Buy @ {r.price_each:,.0f} gp): profit **+{r.expected_profit_gp:,.0f} gp** (Return: {ret})")
    if skilling:
        discord_lines.append("\n**Top Skilling Opportunities:**")
        for r in skilling[:5]:
            discord_lines.append(f"• **{r.reason}** ({r.strategy_name.replace('_processing', '').title()} Lvl {r.price_each or 1}): **+{r.expected_profit_gp:,.0f} gp/batch**")
            
    send_discord_webhook("\n".join(discord_lines))

    return send_email_notification(subject, html)


def send_urgent_news_alert(
    news_title: str,
    item_keywords: str,
    direction: str,
    expected_move: float,
    confidence: float,
    reasoning: str,
    in_portfolio: bool = False
) -> bool:
    """
    Send an urgent sentiment market warning.
    """
    subject = "[ALERT] OSRS Market Sentiment Move Detected"
    
    if in_portfolio:
        subject = f"[CRITICAL PORTFOLIO ALERT] Sell Suggestion: {item_keywords}!"
        banner = f"""
        <div class="alert-banner">
            WARNING: A market update is expected to cause a CRITICAL move in {item_keywords}! <br/>
            Your portfolio holds this item. Action recommended: Review and secure profits.
        </div>
        """
    else:
        banner = ""

    dir_class = "badge-up" if direction == "up" else "badge-down"
    dir_text = "SURGE (UP)" if direction == "up" else "CRASH (DOWN)"
    move_sign = "+" if direction == "up" else "-"

    html = f"""
    {banner}
    <h2>OSRS Market Sentiment Warning</h2>
    <p>A recent game update has triggered a sentiment event:</p>
    
    <table>
        <tr>
            <td width="30%"><b>Game Update:</b></td>
            <td>{news_title}</td>
        </tr>
        <tr>
            <td><b>Affected Items:</b></td>
            <td><b>{item_keywords}</b></td>
        </tr>
        <tr>
            <td><b>Sentiment Direction:</b></td>
            <td><span class="{dir_class}">{dir_text}</span></td>
        </tr>
        <tr>
            <td><b>Inferred Move:</b></td>
            <td><span class="{dir_class}">{move_sign}{expected_move * 100:.1f}%</span></td>
        </tr>
        <tr>
            <td><b>Confidence Level:</b></td>
            <td>{confidence * 100:.0f}%</td>
        </tr>
        <tr>
            <td><b>Model Reasoning:</b></td>
            <td>{reasoning}</td>
        </tr>
    </table>
    """

    # Send Discord notification
    ping_content = "@here ⚠️ **CRITICAL PORTFOLIO ALERT!**" if in_portfolio else "🔔 **OSRS Market Sentiment Alert**"
    color_val = 65382 if direction == "up" else 16724787  # Green vs Red
    
    embed = {
        "title": "OSRS Grand Exchange Sentiment Sentry Warning",
        "description": f"A recent game update has triggered a high-impact sentiment model move.",
        "color": color_val,
        "fields": [
            { "name": "Game Update", "value": news_title, "inline": False },
            { "name": "Affected Items", "value": item_keywords, "inline": True },
            { "name": "Direction", "value": dir_text, "inline": True },
            { "name": "Expected Move", "value": f"{move_sign}{expected_move * 100:.1f}%", "inline": True },
            { "name": "Confidence", "value": f"{confidence * 100:.0f}%", "inline": True },
            { "name": "Reasoning", "value": reasoning[:1000], "inline": False }
        ],
        "footer": { "text": "OSRS GE Quant Bloomberg Terminal" },
        "timestamp": datetime.utcnow().isoformat()
    }
    send_discord_webhook(ping_content, embed)

    return send_email_notification(subject, html)


def send_hot_flip_alert(
    item_name: str,
    item_id: int,
    buy_price: float,
    margin: float,
    qty: int,
    profit: float,
    return_pct: float
) -> bool:
    """
    Send a Discord notification for a high-profit "Hot Flip".
    """
    ping_content = f"🔥 **OSRS HOT FLIP ALERT: {item_name}!**"
    
    # Wiki link for the item
    wiki_url = f"https://prices.runescape.wiki/osrs/item/{item_id}"
    
    embed = {
        "title": f"🔥 Hot Flip Opportunity: {item_name}",
        "url": wiki_url,
        "description": "A high-profit Grand Exchange day-trading setup has been identified.",
        "color": 16758784,  # Gold (#ffb900)
        "fields": [
            { "name": "Item Name", "value": f"**{item_name}** (ID: {item_id})", "inline": True },
            { "name": "Buy Trigger Price", "value": f"{buy_price:,.0f} gp", "inline": True },
            { "name": "Expected Margin", "value": f"+{margin:,.0f} gp (after tax)", "inline": True },
            { "name": "Suggested Quantity", "value": f"{qty:,.0f}", "inline": True },
            { "name": "Total Expected Profit", "value": f"**+{profit:,.0f} gp**", "inline": True },
            { "name": "Return Percentage", "value": f"{return_pct * 100:.2f}%", "inline": True }
        ],
        "footer": { "text": "OSRS Live Day-Trading Sentinel" },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    return send_discord_webhook(ping_content, embed)
