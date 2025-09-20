import os
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ===== reminder_bot.py (PTB 22.x, Windows OK) — One-off + Daily repeat adjustable =====
import logging
import os
import re
import datetime as dt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Tuple

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

# ---------- Timezone ----------
USE_FIXED_TZ = False
VN_TZ = timezone(timedelta(hours=7))  # Asia/Ho_Chi_Minh

def now_tz() -> datetime:
    # Dùng VN_TZ nếu muốn cố định, còn không thì dùng UTC rồi as local tz
    return datetime.now(VN_TZ if USE_FIXED_TZ else timezone.utc).astimezone()

def tzinfo() -> timezone:
    return now_tz().tzinfo  # tz hiện tại (VN nếu bật, ngược lại theo hệ thống)

# ---------- Token ----------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Thiếu BOT_TOKEN. Tạo .env với dòng: BOT_TOKEN=xxxxx")

HELP_TEXT = (
    "Cách dùng:\n"
    "• Một lần:\n"
    "  /remind in <s|m|h|d> <nội_dung>\n"
    "    Ví dụ: /remind in 10m uống nước\n"
    "  /remind at YYYY-MM-DD HH:MM <nội_dung>\n"
    "    Ví dụ: /remind at 2025-12-01 14:30 họp team\n"
    "  /list  → xem các nhắc một lần đang chờ\n"
    "  /cancel <id>  → hủy nhắc một lần theo id\n"
    "\n"
    "• Lặp lại hằng ngày:\n"
    "  /repeat new HH:MM <nội_dung>\n"
    "    Ví dụ: /repeat new 08:00 uống nước\n"
    "  /repeat list  → liệt kê các nhắc lặp ngày\n"
    "  /repeat edit <id> HH:MM [nội_dung_mới]\n"
    "    Ví dụ: /repeat edit rep-2 09:15 uống nước ấm\n"
    "  /repeat cancel <id>  → hủy nhắc lặp theo id\n"
)

# ---------- Stores ----------
# One-off: { chat_id: { job_id: (run_at, text) } }
REM_STORE: Dict[int, Dict[str, Tuple[datetime, str]]] = {}
# Daily repeat: { chat_id: { job_id: (dt.time with tz, text) } }
REP_STORE: Dict[int, Dict[str, Tuple[dt.time, str]]] = {}

def ensure_oneoff(chat_id: int):
    if chat_id not in REM_STORE:
        REM_STORE[chat_id] = {}

def ensure_repeat(chat_id: int):
    if chat_id not in REP_STORE:
        REP_STORE[chat_id] = {}

def human_dt(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M %Z")

def human_time(t: dt.time) -> str:
    hh = f"{t.hour:02d}"
    mm = f"{t.minute:02d}"
    return f"{hh}:{mm} {t.tzname() or ''}".strip()

# ---------- Parsers ----------
def parse_remind_args(text: str) -> Optional[Tuple[datetime, str]]:
    """
    Hỗ trợ:
      - in 10m nội dung
      - in 2h nội dung
      - in 3d nội dung
      - at YYYY-MM-DD HH:MM nội dung
    """
    s = text.strip()

    m = re.match(r"^in\s+(\d+)\s*([smhd])\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        message = m.group(3).strip()
        if unit == "s":
            delta = timedelta(seconds=amount)
        elif unit == "m":
            delta = timedelta(minutes=amount)
        elif unit == "h":
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(days=amount)
        return now_tz() + delta, message

    m = re.match(r"^at\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        date_str, time_str, message = m.group(1), m.group(2), m.group(3).strip()
        try:
            naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            run_at = naive.replace(tzinfo=tzinfo())
            return run_at, message
        except ValueError:
            return None

    return None

def parse_hhmm(text: str) -> Optional[dt.time]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", text.strip())
    if not m:
        return None
    h = int(m.group(1)); mnt = int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mnt <= 59):
        return None
    return dt.time(hour=h, minute=mnt, tzinfo=tzinfo())

# ---------- Notify callbacks ----------
async def _notify_oneoff(ctx: ContextTypes.DEFAULT_TYPE):
    job = ctx.job
    chat_id = job.chat_id
    job_id = job.name
    text = (job.data or {}).get("text", "")
    # Gửi CHỈ nội dung
    if text:
        await ctx.bot.send_message(chat_id=chat_id, text=text)
    # dọn store
    if chat_id in REM_STORE and job_id in REM_STORE[chat_id]:
        del REM_STORE[chat_id][job_id]

async def _notify_daily(ctx: ContextTypes.DEFAULT_TYPE):
    job = ctx.job
    chat_id = job.chat_id
    text = (job.data or {}).get("text", "")
    if text:
        await ctx.bot.send_message(chat_id=chat_id, text=text)
    # repeat: KHÔNG xóa khỏi store, vì là job lặp

# ---------- Handlers ----------
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Có mặt. Gõ /help để xem cú pháp. Làm việc cho gọn, đừng bày biện.")

async def help_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

# One-off
async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_oneoff(chat_id)

    if not context.args:
        await update.message.reply_text("Sai cú pháp. Ví dụ: /remind in 15m uống nước\n" + HELP_TEXT)
        return

    parsed = parse_remind_args(" ".join(context.args))
    if not parsed:
        await update.message.reply_text("Không hiểu thời gian. Xem /help cho đúng cú pháp.")
        return

    run_at, msg_text = parsed
    now = now_tz()
    if run_at <= now + timedelta(seconds=2):
        await update.message.reply_text("Thời gian phải ở tương lai.")
        return

    job_id = f"rem-{int(now.timestamp())}-{len(REM_STORE[chat_id]) + 1}"
    delay_seconds = (run_at - now).total_seconds()

    context.job_queue.run_once(
        _notify_oneoff,
        when=delay_seconds,
        name=job_id,
        chat_id=chat_id,
        data={"text": msg_text},
    )
    REM_STORE[chat_id][job_id] = (run_at, msg_text)
    await update.message.reply_text(
        f"Đã đặt nhắc lúc {human_dt(run_at)} với id `{job_id}`",
        parse_mode="Markdown"
    )

async def list_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_oneoff(chat_id)
    if not REM_STORE[chat_id]:
        await update.message.reply_text("Không có nhắc một lần nào.")
        return
    lines = ["Các nhắc một lần đang chờ:"]
    for jid, (dt_, txt) in REM_STORE[chat_id].items():
        lines.append(f"- {jid}: {human_dt(dt_)} → {txt}")
    await update.message.reply_text("\n".join(lines))

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_oneoff(chat_id)
    if not context.args:
        await update.message.reply_text("Gõ: /cancel <id> (xem id trong /list).")
        return
    jid = context.args[0].strip()
    if jid not in REM_STORE[chat_id]:
        await update.message.reply_text("Không thấy id đó.")
        return

    # gỡ job trong queue
    removed = False
    jq = context.job_queue
    if jq:
        for job in jq.jobs():
            if job.name == jid and job.chat_id == chat_id:
                job.schedule_removal()
                removed = True
                break
    if removed:
        del REM_STORE[chat_id][jid]
        await update.message.reply_text(f"Đã hủy `{jid}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("Job đã chạy hoặc không tồn tại.")

# Daily repeat
async def repeat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /repeat new HH:MM <text>
    /repeat list
    /repeat edit <id> HH:MM [new text]
    /repeat cancel <id>
    """
    chat_id = update.effective_chat.id
    ensure_repeat(chat_id)

    if not context.args:
        await update.message.reply_text(
            "Cú pháp:\n"
            "/repeat new HH:MM <nội_dung>\n"
            "/repeat list\n"
            "/repeat edit <id> HH:MM [nội_dung_mới]\n"
            "/repeat cancel <id>"
        )
        return

    sub = context.args[0].lower()

    # /repeat list
    if sub == "list":
        if not REP_STORE[chat_id]:
            await update.message.reply_text("Không có nhắc lặp ngày nào.")
            return
        lines = ["Nhắc lặp hằng ngày:"]
        for jid, (t, txt) in REP_STORE[chat_id].items():
            lines.append(f"- {jid}: {human_time(t)} → {txt}")
        await update.message.reply_text("\n".join(lines))
        return

    # /repeat new HH:MM <text>
    if sub == "new":
        if len(context.args) < 3:
            await update.message.reply_text("Cú pháp: /repeat new HH:MM <nội_dung>")
            return
        time_str = context.args[1]
        t = parse_hhmm(time_str)
        if not t:
            await update.message.reply_text("Giờ không hợp lệ. Dùng HH:MM, ví dụ 08:00")
            return
        msg_text = " ".join(context.args[2:]).strip()
        if not msg_text:
            await update.message.reply_text("Thiếu nội dung nhắc.")
            return

        # tạo id
        jid = f"rep-{len(REP_STORE[chat_id]) + 1}"

        context.job_queue.run_daily(
            _notify_daily,
            time=t,  # dt.time có tzinfo
            name=jid,
            chat_id=chat_id,
            data={"text": msg_text},
        )
        REP_STORE[chat_id][jid] = (t, msg_text)
        await update.message.reply_text(f"Đã tạo nhắc lặp mỗi ngày {human_time(t)} với id `{jid}`", parse_mode="Markdown")
        return

    # /repeat cancel <id>
    if sub == "cancel":
        if len(context.args) < 2:
            await update.message.reply_text("Cú pháp: /repeat cancel <id>")
            return
        jid = context.args[1].strip()
        if jid not in REP_STORE[chat_id]:
            await update.message.reply_text("Không thấy id đó.")
            return
        removed = False
        for job in context.job_queue.jobs():
            if job.name == jid and job.chat_id == chat_id:
                job.schedule_removal()
                removed = True
                break
        if removed:
            del REP_STORE[chat_id][jid]
            await update.message.reply_text(f"Đã hủy `{jid}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("Job không tồn tại hoặc đã bị gỡ.")
        return

    # /repeat edit <id> HH:MM [new text]
    if sub == "edit":
        if len(context.args) < 3:
            await update.message.reply_text("Cú pháp: /repeat edit <id> HH:MM [nội_dung_mới]")
            return
        jid = context.args[1].strip()
        if jid not in REP_STORE[chat_id]:
            await update.message.reply_text("Không thấy id đó.")
            return
        t_new = parse_hhmm(context.args[2])
        if not t_new:
            await update.message.reply_text("Giờ không hợp lệ. Dùng HH:MM, ví dụ 09:30")
            return
        new_text = " ".join(context.args[3:]).strip()
        if not new_text:
            # nếu không cung cấp nội dung mới thì giữ nguyên nội dung cũ
            new_text = REP_STORE[chat_id][jid][1]

        # gỡ job cũ
        for job in context.job_queue.jobs():
            if job.name == jid and job.chat_id == chat_id:
                job.schedule_removal()
                break

        # tạo job mới cùng id
        context.job_queue.run_daily(
            _notify_daily,
            time=t_new,
            name=jid,
            chat_id=chat_id,
            data={"text": new_text},
        )
        REP_STORE[chat_id][jid] = (t_new, new_text)
        await update.message.reply_text(
            f"Đã chỉnh `{jid}` → {human_time(t_new)}: {new_text}",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text("Cú pháp không hợp lệ. Dùng /repeat list để xem hướng dẫn.")

async def fallback_text(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Không hiểu. Đây là bot nhắc việc. Gõ /help.")

# ---------- Main (đồng bộ, không asyncio.run) ----------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Lệnh
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("repeat", repeat_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), fallback_text))

    # Blocking, để PTB tự quản lý event loop
    app.run_polling()

if __name__ == "__main__":
    # Cho chắc trên Windows 10 + Python 3.13
    try:
        import sys, asyncio
        if sys.platform.startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass
    main()
# ===== END =====
