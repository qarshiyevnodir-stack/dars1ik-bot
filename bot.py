import io
import json
import logging
import os
import re
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


def load_legacy_content() -> dict:
    if not DATA_FILE.exists():
        return {"classes": []}
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


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "item"


def prettify_name(value: str) -> str:
    value = value.replace("_", " ")
    value = re.sub(r"\s+", " ", value).strip(" -_")
    return value or "Noma'lum"


def clean_subject_name(value: str) -> str:
    cleaned = re.sub(r"[_-]+", " ", value).strip()
    cleaned = re.sub(
        r"\b(kitobi|kitob|darsligi|darslik|qo'llanma|qollanma)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b\d+\s*qism\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b\d+\s*[- ]?qism\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip(" -_")
    return prettify_name(cleaned or value)


def infer_from_filename(file_name: str) -> tuple[str, str, str]:
    stem = prettify_name(Path(file_name).stem)
    stem_for_match = stem.lower()

    if stem_for_match.startswith("alifbe"):
        return "1-sinf", "Alifbe", stem

    match = re.match(r"^(\d{1,2})\s*[-_ ]?sinf\s+(.+)$", stem, flags=re.IGNORECASE)
    if match:
        class_name = f"{match.group(1)}-sinf"
        remainder = match.group(2).strip()
        subject_name = clean_subject_name(remainder)
        return class_name, subject_name, stem

    return "Boshqa", "Umumiy", stem


def parse_relative_pdf_path(relative_path: str) -> dict | None:
    parts = [prettify_name(part) for part in Path(relative_path).parts if part]
    if not parts:
        return None

    file_name = Path(parts[-1]).name
    textbook_name = prettify_name(Path(file_name).stem)
    inferred_class_name, inferred_subject_name, inferred_textbook_name = infer_from_filename(file_name)

    if len(parts) >= 3:
        class_name = parts[0]
        subject_name = parts[1]
        if inferred_class_name.lower() == class_name.lower() and inferred_subject_name:
            subject_name = inferred_subject_name
    elif len(parts) == 2:
        class_name = parts[0]
        subject_name = inferred_subject_name or clean_subject_name(Path(parts[1]).stem)
    else:
        class_name, subject_name, textbook_name = inferred_class_name, inferred_subject_name, inferred_textbook_name

    return {
        "class_name": class_name,
        "subject_name": subject_name,
        "textbook_name": textbook_name,
        "file_name": file_name,
        "relative_path": relative_path.replace("\\", "/"),
    }


def iter_local_entries() -> list[dict]:
    entries = []
    if not TEXTBOOKS_DIR.exists():
        return entries

    for path in sorted(TEXTBOOKS_DIR.rglob("*.pdf")):
        relative_path = path.relative_to(TEXTBOOKS_DIR).as_posix()
        parsed = parse_relative_pdf_path(relative_path)
        if not parsed:
            continue
        parsed["local_rel_path"] = relative_path
        entries.append(parsed)
    return entries


def iter_r2_entries() -> list[dict]:
    client = get_r2_client()
    if not client or not R2_BUCKET_NAME:
        return []

    prefix = f"{R2_KEY_PREFIX}/" if R2_KEY_PREFIX else ""
    entries = []

    continuation_token = None
    while True:
        kwargs = {"Bucket": R2_BUCKET_NAME, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)

        for obj in response.get("Contents", []):
            key = obj.get("Key", "")
            if not key.lower().endswith(".pdf"):
                continue
            relative_path = key[len(prefix):] if prefix and key.startswith(prefix) else key
            parsed = parse_relative_pdf_path(relative_path)
            if not parsed:
                continue
            parsed["r2_key"] = key
            entries.append(parsed)

        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")

    return entries


def iter_legacy_entries() -> list[dict]:
    legacy = load_legacy_content()
    entries = []
    for class_item in legacy.get("classes", []):
        class_name = class_item.get("name", "Noma'lum sinf")
        for subject in class_item.get("subjects", []):
            subject_name = subject.get("name", "Noma'lum fan")
            for textbook in subject.get("textbooks", []):
                file_name = textbook.get("file_name") or Path(textbook.get("r2_key", "textbook.pdf")).name
                if not file_name:
                    continue
                entries.append(
                    {
                        "class_name": class_name,
                        "subject_name": subject_name,
                        "textbook_name": textbook.get("name") or prettify_name(Path(file_name).stem),
                        "file_name": file_name,
                        "relative_path": textbook.get("relative_path") or file_name,
                        "local_rel_path": textbook.get("relative_path") or file_name,
                        "r2_key": textbook.get("r2_key") or build_textbook_key({"file_name": file_name}),
                    }
                )
    return entries


def class_sort_key(name: str) -> tuple[int, int | str]:
    match = re.match(r"^(\d{1,2})\s*[- ]?sinf$", name.strip(), flags=re.IGNORECASE)
    if match:
        return (0, int(match.group(1)))
    return (1, name.lower())


def build_content_tree(entries: list[dict]) -> dict:
    classes_map: dict[str, dict] = {}

    for entry in entries:
        class_name = entry["class_name"]
        subject_name = entry["subject_name"]
        file_name = entry["file_name"]

        class_key = class_name.lower()
        subject_key = subject_name.lower()
        textbook_key = file_name.lower()

        class_item = classes_map.setdefault(
            class_key,
            {
                "id": slugify(class_name),
                "name": class_name,
                "subjects_map": {},
            },
        )

        subject_item = class_item["subjects_map"].setdefault(
            subject_key,
            {
                "id": slugify(subject_name),
                "name": subject_name,
                "textbooks_map": {},
            },
        )

        textbook_item = subject_item["textbooks_map"].setdefault(
            textbook_key,
            {
                "id": slugify(Path(file_name).stem),
                "name": entry["textbook_name"],
                "file_name": file_name,
            },
        )

        if entry.get("local_rel_path") and not textbook_item.get("local_rel_path"):
            textbook_item["local_rel_path"] = entry["local_rel_path"]
        if entry.get("r2_key") and not textbook_item.get("r2_key"):
            textbook_item["r2_key"] = entry["r2_key"]

    classes = []
    for class_item in sorted(classes_map.values(), key=lambda item: class_sort_key(item["name"])):
        subjects = []
        for subject_item in sorted(class_item["subjects_map"].values(), key=lambda item: item["name"].lower()):
            textbooks = sorted(
                subject_item["textbooks_map"].values(),
                key=lambda item: item["name"].lower(),
            )
            subjects.append(
                {
                    "id": subject_item["id"],
                    "name": subject_item["name"],
                    "textbooks": textbooks,
                }
            )
        classes.append(
            {
                "id": class_item["id"],
                "name": class_item["name"],
                "subjects": subjects,
            }
        )

    return {"classes": classes}


def has_textbooks(content: dict) -> bool:
    for class_item in content.get("classes", []):
        for subject in class_item.get("subjects", []):
            if subject.get("textbooks"):
                return True
    return False


def load_content() -> dict:
    entries = []
    entries.extend(iter_local_entries())
    entries.extend(iter_r2_entries())
    entries.extend(iter_legacy_entries())

    content = build_content_tree(entries)
    if has_textbooks(content):
        return content

    return {"classes": []}


def build_textbook_key(textbook: dict) -> str | None:
    if textbook.get("r2_key"):
        return textbook["r2_key"].lstrip("/")

    relative_path = textbook.get("relative_path") or textbook.get("local_rel_path")
    if relative_path:
        relative_path = relative_path.lstrip("/")
        if R2_KEY_PREFIX:
            return f"{R2_KEY_PREFIX}/{relative_path}"
        return relative_path

    file_name = textbook.get("file_name")
    if not file_name:
        return None
    if R2_KEY_PREFIX:
        return f"{R2_KEY_PREFIX}/{file_name}"
    return file_name


def open_textbook_stream(textbook: dict):
    file_name = textbook.get("file_name") or Path(textbook.get("r2_key", "textbook.pdf")).name

    local_rel_path = textbook.get("local_rel_path") or textbook.get("relative_path") or file_name
    local_path = TEXTBOOKS_DIR / local_rel_path
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
    if not has_textbooks(content):
        await update.message.reply_text("Hozircha darsliklar topilmadi.")
        return

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
                f"{class_item['name']} uchun hali fanlar topilmadi.",
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
                f"{subject['name']} uchun hali darslik topilmadi.",
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
