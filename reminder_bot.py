import os
import logging
from datetime import datetime, timedelta
import pytz

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ===== Logging =====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ===== Config =====
TOKEN = os.getenv("BOT_TOKEN")
VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")   # múi giờ Việt Nam
REM_STORE = {}  # {chat_id: {job_id: (time, text)}}

# ===== Helper =====
def now_vn():
    return datetime.now(VN_TZ)

def utc_from_vn(dt_vn: datetime):
    """Chuyển giờ VN sang UTC (dùng cho job queue)."""
    return dt_vn.astimezone(pytz.UTC)

def human_dt_local(dt_utc: datetime):
    """Hiển thị giờ UTC sang giờ VN cho dễ đọc."""
    return dt_utc.astimezone(VN_TZ).strftime("%H:%M:%S %d-%m-%Y")

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Xin chào 👋. Gõ /help để xem cú pháp.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Cú pháp nhắc việc:\n"
        "/remind in <s|m|h> <nội_dung>\n"
        "VD: /remind in 10s uống nước\n\n"
        "/list — xem danh sách nhắc\n"
    )
    await update.message.reply_text(msg)

async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3 or context.args[0] != "in":
        await update.message.reply_text("Sai cú pháp. Dùng: /remind in <s|m|h> <nội_dung>")
        return

    unit = context.args[1][-1]
    try:
        value = int(context.args[1][:-1])
    except ValueError:
        await update.message.reply_text("Sai số phút/giây/giờ.")
        return

    text = " ".join(context.args[2:])

    if unit == "s":
        delta = timedelta(seconds=value)
    elif unit == "m":
        delta = timedelta(minutes=value)
    elif unit == "h":
        delta = timedelta(hours=value)
    else:
        await update.message.reply_text("Chỉ hỗ trợ s, m, h.")
        return

    run_at_vn = now_vn() + delta
    run_at_utc = utc_from_vn(run_at_vn)

    job_id = f"rem-{int(run_at_utc.timestamp())}-{update.message.message_id}"

    # 1. Gửi tin nhắn xác nhận → lấy message_id
    confirm = await update.message.reply_text(
        f"Đã đặt nhắc lúc {human_dt_local(run_at_utc)} với id {job_id}"
    )

    # 2. Lưu data cho job
    job_data = {
        "text": text,
        "confirm_mid": confirm.message_id,
        "confirm_chat_id": confirm.chat_id,
    }

    delay_seconds = (run_at_utc - datetime.now(pytz.UTC)).total_seconds()

    context.application.job_queue.run_once(
        send_reminder,
        when=delay_seconds,
        name=job_id,
        chat_id=update.effective_chat.id,
        data=job_data,
    )

    # 3. Lưu vào REM_STORE
    REM_STORE.setdefault(update.effective_chat.id, {})[job_id] = (run_at_utc, text)


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data or {}
    chat_id = job.chat_id
    text = data.get("text", "Nhắc việc.")

    # 1. Gửi tin nhắc chính
    await context.bot.send_message(chat_id=chat_id, text=f"🔔 Nhắc việc: {text}")

    # 2. Xóa tin nhắn xác nhận
    confirm_mid = data.get("confirm_mid")
    confirm_chat = data.get("confirm_chat_id", chat_id)
    if confirm_mid:
        try:
            await context.bot.delete_message(chat_id=confirm_chat, message_id=confirm_mid)
        except Exception as e:
            logging.warning(f"Không xóa được tin nhắn xác nhận: {e}")

    # 3. Xóa khỏi REM_STORE
    if chat_id in REM_STORE and job.name in REM_STORE[chat_id]:
        del REM_STORE[chat_id][job.name]


async def list_rem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in REM_STORE or not REM_STORE[chat_id]:
        await update.message.reply_text("Không có nhắc nào.")
        return

    lines = []
    for job_id, (t, txt) in REM_STORE[chat_id].items():
        lines.append(f"{job_id}: {human_dt_local(t)} → {txt}")

    await update.message.reply_text("\n".join(lines))


# ===== Main =====
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(CommandHandler("list", list_rem))

    app.run_polling()


if __name__ == "__main__":
    main()
