import io
import json
import logging
import os
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "content.json"
TEXTBOOKS_DIR = BASE_DIR / "textbooks"
TOKEN = os.getenv("BOT_TOKEN")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_KEY_PREFIX = os.getenv("R2_KEY_PREFIX", "textbooks").strip("/")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
_r2_client = None


def load_content() -> dict:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_r2_client():
    global _r2_client
    if _r2_client is not None:
        return _r2_client

    required = [R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME]
    if not all(required):
        logger.info("R2 muhit o‘zgaruvchilari to‘liq emas. Lokal fayl rejimi ishlatiladi.")
        return None

    _r2_client = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    return _r2_client


def build_textbook_key(textbook: dict) -> str | None:
    if textbook.get("r2_key"):
        return textbook["r2_key"].lstrip("/")
    file_name = textbook.get("file_name")
    if not file_name:
        return None
    if R2_KEY_PREFIX:
        return f"{R2_KEY_PREFIX}/{file_name}"
    return file_name


def open_textbook_stream(textbook: dict):
    file_name = textbook.get("file_name") or Path(textbook.get("r2_key", "textbook.pdf")).name
    local_path = TEXTBOOKS_DIR / file_name
    if local_path.exists():
        logger.info("Darslik lokal papkadan yuboriladi: %s", local_path)
        return local_path.open("rb"), file_name

    client = get_r2_client()
    key = build_textbook_key(textbook)
    if not client or not key:
        return None, file_name

    try:
        logger.info("Darslik R2 dan olinmoqda: bucket=%s key=%s", R2_BUCKET_NAME, key)
        obj = client.get_object(Bucket=R2_BUCKET_NAME, Key=key)
        data = obj["Body"].read()
        stream = io.BytesIO(data)
        stream.name = file_name
        return stream, file_name
    except (ClientError, BotoCoreError) as exc:
        logger.exception("R2 dan darslikni olishda xato: %s", exc)
        return None, file_name


def get_main_keyboard(content: dict) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for item in content["classes"]:
        row.append(InlineKeyboardButton(item["name"], callback_data=f"class:{item['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def get_class_item(class_id: str, content: dict) -> dict | None:
    for item in content["classes"]:
        if item["id"] == class_id:
            return item
    return None


def get_subject_item(class_item: dict, subject_id: str) -> dict | None:
    for item in class_item.get("subjects", []):
        if item["id"] == subject_id:
            return item
    return None


def get_subjects_keyboard(class_item: dict) -> InlineKeyboardMarkup:
    buttons = []
    for subject in class_item.get("subjects", []):
        buttons.append([
            InlineKeyboardButton(
                subject["name"],
                callback_data=f"subject:{class_item['id']}:{subject['id']}",
            )
        ])
    buttons.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="back:classes")])
    return InlineKeyboardMarkup(buttons)


def get_textbooks_keyboard(class_id: str, subject: dict) -> InlineKeyboardMarkup:
    buttons = []
    for textbook in subject.get("textbooks", []):
        buttons.append([
            InlineKeyboardButton(
                textbook["name"],
                callback_data=f"textbook:{class_id}:{subject['id']}:{textbook['id']}",
            )
        ])
    buttons.append([
        InlineKeyboardButton("⬅️ Orqaga", callback_data=f"back:subjects:{class_id}")
    ])
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    content = load_content()
    text = (
        "Assalomu alaykum.\n\n"
        "Kerakli darslikni olish uchun sinfni tanlang."
    )
    await update.message.reply_text(text, reply_markup=get_main_keyboard(content))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    content = load_content()
    data = query.data.split(":")

    if data[0] == "class":
        class_id = data[1]
        class_item = get_class_item(class_id, content)
        if not class_item:
            await query.edit_message_text("Sinf topilmadi.")
            return

        if not class_item.get("subjects"):
            await query.edit_message_text(
                f"{class_item['name']} uchun hali fanlar qo‘shilmagan.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Orqaga", callback_data="back:classes")]
                ]),
            )
            return

        await query.edit_message_text(
            f"{class_item['name']} uchun fanni tanlang.",
            reply_markup=get_subjects_keyboard(class_item),
        )
        return

    if data[0] == "subject":
        class_id, subject_id = data[1], data[2]
        class_item = get_class_item(class_id, content)
        if not class_item:
            await query.edit_message_text("Sinf topilmadi.")
            return
        subject = get_subject_item(class_item, subject_id)
        if not subject:
            await query.edit_message_text("Fan topilmadi.")
            return
        if not subject.get("textbooks"):
            await query.edit_message_text(
                f"{subject['name']} uchun hali darslik qo‘shilmagan.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Orqaga", callback_data=f"back:subjects:{class_id}")]
                ]),
            )
            return
        await query.edit_message_text(
            f"{subject['name']} fanidan darslikni tanlang.",
            reply_markup=get_textbooks_keyboard(class_id, subject),
        )
        return

    if data[0] == "textbook":
        class_id, subject_id, textbook_id = data[1], data[2], data[3]
        class_item = get_class_item(class_id, content)
        if not class_item:
            await query.edit_message_text("Sinf topilmadi.")
            return
        subject = get_subject_item(class_item, subject_id)
        if not subject:
            await query.edit_message_text("Fan topilmadi.")
            return
        textbook = next((x for x in subject.get("textbooks", []) if x["id"] == textbook_id), None)
        if not textbook:
            await query.edit_message_text("Darslik topilmadi.")
            return

        document_stream, file_name = open_textbook_stream(textbook)
        if not document_stream:
            await query.message.reply_text("Darslik fayli topilmadi. Iltimos, admin bilan bog‘laning.")
            return

        try:
            await query.message.reply_document(
                document=InputFile(document_stream, filename=file_name),
                caption=f"{textbook['name']}",
            )
        finally:
            document_stream.close()

        await query.message.reply_text(
            "Yana davom etish uchun sinfni tanlang.",
            reply_markup=get_main_keyboard(content),
        )
        return

    if data[0] == "back":
        if data[1] == "classes":
            await query.edit_message_text(
                "Kerakli darslikni olish uchun sinfni tanlang.",
                reply_markup=get_main_keyboard(content),
            )
            return
        if data[1] == "subjects":
            class_id = data[2]
            class_item = get_class_item(class_id, content)
            if not class_item:
                await query.edit_message_text("Sinf topilmadi.")
                return
            await query.edit_message_text(
                f"{class_item['name']} uchun fanni tanlang.",
                reply_markup=get_subjects_keyboard(class_item),
            )
            return


def main() -> None:
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN o‘rnatilmagan. Muhit o‘zgaruvchisiga BOT_TOKEN qo‘ying.")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.run_polling()


if __name__ == "__main__":
    main()
