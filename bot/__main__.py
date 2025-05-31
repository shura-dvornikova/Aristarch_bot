import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import List

from aiogram import Bot, Dispatcher, Router
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

# ─── НАСТРОЙКИ ───
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

BASE_DIR = Path(__file__).parent
IMG_DIR = BASE_DIR.parent / "images"
QUIZ_PATH = BASE_DIR / "data" / "quizzes.json"
FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

# ЗАГРУЗКА ВИКТОРИН
try:
    with QUIZ_PATH.open(encoding="utf-8") as f:
        QUIZZES: dict[str, list[dict]] = json.load(f)
except FileNotFoundError:
    raise RuntimeError(f"Файл вопросов не найден: {QUIZ_PATH}")
except json.JSONDecodeError as e:
    raise RuntimeError(f"Ошибка синтаксиса в quizzes.json: {e}") from e

# ТЕЛЕГРАМ БОТ
bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# FSM
class QuizState(StatesGroup):
    waiting_for_answer = State()

class ReportChoice(StatesGroup):
    waiting = State()

# PDF

def build_pdf(title: str, results: List[dict]) -> str:
    pdf = FPDF(format="A4")
    font_ok = False
    if FONT_PATH.exists():
        try:
            pdf.add_font("DejaVu", "", str(FONT_PATH))
            font_ok = True
        except Exception as e:
            logger.warning("Шрифт повреждён (%s) — ASCII-fallback", e)

    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    if font_ok:
        pdf.set_font("DejaVu", size=18)
    else:
        pdf.set_font("Helvetica", size=18, style="B")
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT", align="C")

    if font_ok:
        pdf.set_font("DejaVu", size=12)
    else:
        pdf.set_font("Helvetica", size=12)

    items_on_page = 0
    for item in results:
        if items_on_page >= 5:
            pdf.add_page()
            items_on_page = 0

        text = f"{item['index'] + 1}. {item['question']} — верный ответ: {item['correct_answer']}"
        try:
            pdf.multi_cell(0, 10, text)
        except FPDFUnicodeEncodingException:
            pdf.multi_cell(0, 10, text.encode("latin-1", "replace").decode("latin-1"))

        if item["image_path"] and Path(item["image_path"]).exists():
            pdf.ln(2)
            pdf.image(item["image_path"], w=170)
            pdf.ln(10)

        items_on_page += 1

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp.name)
    return tmp.name

# HANDLERS
@router.message(Command("start"))
async def cmd_start(msg: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=f"topic:{t}")]
            for t in QUIZZES.keys()
        ]
    )
    await msg.answer("*\u041f\u0440\u0438\u0432\u0435\u0442!*\n\u0412\u044b\u0431\u0435\u0440\u0438 \u0442\u0435\u043c\u0443 \u0432\u0438\u043a\u0442\u043e\u0440\u0438\u043d\u044b:", reply_markup=kb)

@router.callback_query(lambda cb: cb.data.startswith("topic:"))
async def choose_topic(cb: CallbackQuery, state: FSMContext) -> None:
    topic = cb.data.split(":", 1)[1]
    await state.set_state(QuizState.waiting_for_answer)
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
    caption = f"\u2753 _\u0412\u043e\u043f\u0440\u043e\u0441 {idx + 1} \u0438\u0437 {total}_\n\n*{q['question']}*"
    if fname := q.get("image_file"):
        file_path = IMG_DIR / fname
        if file_path.exists():
            await msg.answer_photo(
                FSInputFile(file_path), caption=caption, reply_markup=kb
            )
            return
    await msg.answer(caption, reply_markup=kb)

@router.callback_query(QuizState.waiting_for_answer)
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
    await cb.answer("\u0412\u0415\u0420\u041d\u041e" if correct else "\u041d\u0415\u0412\u0415\u0420\u041d\u041e")

    if next_idx < len(QUIZZES[topic]):
        await state.update_data(idx=next_idx, score=score, results=d["results"])
        await ask_question(cb.message, state)
        return

    await cb.message.answer(f"\U0001F3C1 Конец!\nПравильных ответов: *{score}* из *{len(d['results'])}*")
    await state.update_data(score=score)
    await state.set_state(ReportChoice.waiting)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="\ud83d\udcdd \u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u043e\u0442\u0447\u0451\u0442", callback_data="report:text"),
                InlineKeyboardButton(text="\ud83d\udcc4 PDF \u0441 \u043a\u0430\u0440\u0442\u0438\u043d\u043a\u0430\u043c\u0438", callback_data="report:pdf")
            ]
        ]
    )
    await cb.message.answer("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0444\u043e\u0440\u043c\u0430\u0442 \u043e\u0442\u0447\u0451\u0442\u0430:", reply_markup=kb)

@router.callback_query(lambda c: c.data == "report:text", ReportChoice.waiting)
async def send_text_report(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    lines = []
    for i, item in enumerate(d["results"], 1):
        mark = "✅" if item["correct"] else "❌"
        lines.append(f"{mark} *Вопрос {i}:* {item['question']}\n\u2003*\u0412ерный ответ:* _{item['correct_answer']}_")
    await cb.message.answer("\n\n".join(lines))
    await state.clear()

@router.callback_query(lambda c: c.data == "report:pdf", ReportChoice.waiting)
async def send_pdf_report(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    pdf_path = build_pdf(d["topic"], d["results"])
    await cb.message.answer_document(FSInputFile(pdf_path), caption="PDF-отчёт")
    os.remove(pdf_path)
    await state.clear()

# MAIN
async def main():
    if not config.bot_token:
        raise RuntimeError("BOT_TOKEN не найден в .env")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
