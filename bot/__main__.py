import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    FSInputFile,
)
from fpdf import FPDF
from fpdf.errors import FPDFUnicodeEncodingException

from .config import config

# ─────────────────── настройки ─────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

BASE_DIR = Path(__file__).parent
IMG_DIR = BASE_DIR.parent / "images"
QUIZ_PATH = BASE_DIR / "data" / "quizzes.json"
FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

# ─────────────────── загрузка викторин ─────────────────────
try:
    with QUIZ_PATH.open(encoding="utf-8") as f:
        QUIZZES: dict[str, list[dict]] = json.load(f)
except FileNotFoundError:
    raise RuntimeError(f"Файл вопросов не найден: {QUIZ_PATH}")
except json.JSONDecodeError as e:
    raise RuntimeError(f"Ошибка синтаксиса в quizzes.json: {e}") from e

# ─────────────────── Telegram bot ──────────────────────────
bot = Bot(
    token=config.bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
)
dp = Dispatcher()

# ─────────────────── FSM ───────────────────────────────────
class QuizState(StatesGroup):
    waiting_for_answer = State()

class ReportChoice(StatesGroup):
    waiting = State()

# ─────────────────── PDF генерация ─────────────────────────
def build_pdf(title: str, results: List[dict]) -> str:
    pdf = FPDF(format="A4")
    font_ok = False

    if FONT_PATH.exists():
        try:
            pdf.add_font("DejaVu", "", str(FONT_PATH))
            font_ok = True
            logger.info(f"Использую Unicode-шрифт: {FONT_PATH}")
        except Exception as e:
            logger.warning(f"Шрифт повреждён ({e}) — ASCII fallback")

    pdf.set_auto_page_break(auto=True, margin=15)

    def set_main_font(size=12):
        if font_ok:
            pdf.set_font("DejaVu", size=size)
        else:
            pdf.set_font("Helvetica", size=size)

    # ─ Заголовок ─
    pdf.add_page()
    if font_ok:
        pdf.set_font("DejaVu", size=18)
    else:
        pdf.set_font("Helvetica", size=18)

    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)
    set_main_font()

    for i, item in enumerate(results, 1):
        if (i - 1) % 5 == 0 and i != 1:
            pdf.add_page()
            set_main_font()

        text = f"{i}. {item['question']} — верный ответ: {item['correct_answer']}"
        try:
            pdf.multi_cell(0, 10, text)
        except FPDFUnicodeEncodingException:
            pdf.multi_cell(0, 10, text.encode("latin-1", "replace").decode("latin-1"))

        if item["image_path"] and Path(item["image_path"]).exists():
            pdf.ln(2)
            try:
                pdf.image(item["image_path"], h=45)  # высота картинки ~45 мм
            except RuntimeError:
                pdf.cell(0, 10, "[Ошибка изображения]")
            pdf.ln(5)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp.name)
    return tmp.name

# ─────────────────── handlers ──────────────────────────────
@dp.message(Command("start"))
async def cmd_start(msg: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=f"topic:{t}")]
            for t in QUIZZES.keys()
        ]
    )
    await msg.answer("*Привет!*\nВыбери тему викторины:", reply_markup=kb)

@dp.callback_query(lambda cb: cb.data.startswith("topic:"))
async def choose_topic(cb: CallbackQuery, state: FSMContext) -> None:
    topic = cb.data.split(":", 1)[1]
    await state.update_data(topic=topic, idx=0, score=0, results=[])
    await ask_question(cb.message, state)

async def ask_question(msg: Message, state: FSMContext) -> None:
    d = await state.get_data()
    topic, idx = d["topic"], d["idx"]
    total = len(QUIZZES[topic])
    q = QUIZZES[topic][idx]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=opt, callback_data=f"ans:{i}")]
            for i, opt in enumerate(q["options"])
        ]
    )
    caption = f"❓ _Вопрос {idx + 1} из {total}_\n\n*{q['question']}*"
    if fname := q.get("image_file"):
        file_path = IMG_DIR / fname
        if file_path.exists():
            await msg.answer_photo(
                FSInputFile(file_path),
                caption=caption,
                reply_markup=kb,
            )
            return

    await msg.answer(caption, reply_markup=kb)

@dp.callback_query(lambda cb: cb.data.startswith("ans:"))
async def handle_answer(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    topic, idx = d["topic"], d["idx"]
    q = QUIZZES[topic][idx]
    chosen = int(cb.data.split(":", 1)[1])
    correct = chosen == q["correct"]

    d["results"].append({
        "index": idx,
        "question": q["question"],
        "correct_answer": q["options"][q["correct"]],
        "image_path": str(IMG_DIR / q["image_file"]) if q.get("image_file") else None,
        "correct": correct,
    })

    score = d["score"] + int(correct)
    next_idx = idx + 1
    await cb.answer("✅ Верно!" if correct else "❌ Неверно")

    if next_idx < len(QUIZZES[topic]):
        await state.update_data(idx=next_idx, score=score, results=d["results"])
        await ask_question(cb.message, state)
        return

    await cb.message.answer(
        f"🏁 Конец!\nПравильных ответов: *{score}* из *{len(d['results'])}*"
    )
    await state.update_data(score=score)
    await state.set_state(ReportChoice.waiting)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 Показать отчёт", callback_data="report:text"),
                InlineKeyboardButton(text="📄 PDF с картинками", callback_data="report:pdf"),
            ]
        ]
    )
    await cb.message.answer("Выберите формат отчёта:", reply_markup=kb)

@dp.callback_query(lambda c: c.data == "report:text", ReportChoice.waiting)
async def send_text_report(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    lines = []
    for i, item in enumerate(d["results"], 1):
        mark = "✅" if item["correct"] else "❌"
        lines.append(
            f"{mark} *Вопрос {i}:* {item['question']}\n *Верный ответ:* _{item['correct_answer']}_"
        )

    # Telegram: max 4096 characters per message — отправляем кусками
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) > 3500:
            await cb.message.answer(chunk)
            chunk = ""
        chunk += line + "\n\n"

    if chunk:
        await cb.message.answer(chunk)

    await state.clear()


@dp.callback_query(lambda c: c.data == "report:pdf", ReportChoice.waiting)
async def send_pdf_report(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    pdf_path = build_pdf(d["topic"], d["results"])
    await cb.message.answer_document(FSInputFile(pdf_path), caption="PDF-отчёт")
    os.remove(pdf_path)
    await state.clear()

# ─────────────────── main ──────────────────────────────────
async def main():
    if not config.bot_token:
        raise RuntimeError("BOT_TOKEN не найден в .env")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
