import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import List

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
from .config import config

# ────────── инфраструктура ────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).parent          # …/bot
IMG_DIR  = BASE_DIR.parent / "images"     # …/images
QUIZ_PATH = BASE_DIR / "data" / "quizzes.json"

with QUIZ_PATH.open(encoding="utf-8") as f:
    QUIZZES: dict[str, List[dict]] = json.load(f)

bot = Bot(
    token=config.bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
)
dp = Dispatcher()

# ────────── FSM ────────────────────────────────────────────────────────────
class QuizState(StatesGroup):
    waiting_for_answer = State()

class ReportChoice(StatesGroup):
    waiting = State()

# ────────── PDF helper ─────────────────────────────────────────────────────
def build_pdf(lines: List[str], image_paths: List[str]) -> str:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=15)
    for text, img in zip(lines, image_paths):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0, 10, text)
        if img and Path(img).exists():
            pdf.ln(5)
            pdf.image(img, w=170)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp.name)
    return tmp.name

# ────────── хэндлеры ───────────────────────────────────────────────────────
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
    data   = await state.get_data()
    topic  = data["topic"]
    idx    = data["idx"]
    total  = len(QUIZZES[topic])
    q      = QUIZZES[topic][idx]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=opt, callback_data=f"ans:{i}")]
            for i, opt in enumerate(q["options"])
        ]
    )
    caption = f"❓_Вопрос {idx + 1} из {total}_\n\n*{q['question']}*"

    if fname := q.get("image_file"):
        file_path = IMG_DIR / fname
        if file_path.exists():
            await msg.answer_photo(
                FSInputFile(file_path),
                caption=caption,
                reply_markup=kb,
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            logging.warning("Картинка не найдена: %s", file_path)
            await msg.answer(caption, reply_markup=kb)
    else:
        await msg.answer(caption, reply_markup=kb)

    await state.set_state(QuizState.waiting_for_answer)

@dp.callback_query(QuizState.waiting_for_answer)
async def handle_answer(cb: CallbackQuery, state: FSMContext) -> None:
    data   = await state.get_data()
    topic, idx = data["topic"], data["idx"]
    q      = QUIZZES[topic][idx]

    chosen  = int(cb.data.split(":", 1)[1])
    correct = chosen == q["correct"]

    data["results"].append({"idx": idx, "correct": correct})
    score = data["score"] + int(correct)
    next_idx = idx + 1

    await cb.answer("✅ Верно!" if correct else "❌ Неверно")

    if next_idx < len(QUIZZES[topic]):
        await state.update_data(idx=next_idx, score=score, results=data["results"])
        await ask_question(cb.message, state)
        return

    # ─ финальное сообщение + выбор формата отчёта ─
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="📝 Показать отчёт", callback_data="report:text"),
            InlineKeyboardButton(text="📄 PDF с картинками", callback_data="report:pdf"),
        ]]
    )
    await cb.message.answer(
        f"🏁 Конец!\nПравильных ответов: *{score}* из *{len(data['results'])}*\n\n"
        "Выберите формат отчёта:",
        reply_markup=kb,
    )
    await state.update_data(score=score)
    await state.set_state(ReportChoice.waiting)

# ─ текстовый отчёт ─
@dp.callback_query(lambda c: c.data == "report:text", ReportChoice.waiting)
async def send_text_report(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    lines = []
    for i, item in enumerate(data["results"], 1):
        q_obj = QUIZZES[data["topic"]][item["idx"]]
        mark  = "✅" if item["correct"] else "❌"
        right = q_obj["options"][q_obj["correct"]]
        lines.append(f"{mark} *Вопрос {i}:* {q_obj['question']}\n *Верный ответ:* _{right}_")
    await cb.message.answer("\n\n".join(lines))
    await state.clear()

# ─ PDF-отчёт ─
@dp.callback_query(lambda c: c.data == "report:pdf", ReportChoice.waiting)
async def send_pdf_report(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    report_lines, img_paths = [], []

    for i, item in enumerate(data["results"], 1):
        q_obj = QUIZZES[data["topic"]][item["idx"]]
        # слова вместо эмодзи
        mark_pdf  = "ВЕРНО" if item["correct"] else "НЕВЕРНО"
        right = q_obj["options"][q_obj["correct"]]
        report_lines.append(f"{mark_pdf}: Вопрос {i}: {q_obj['question']} — ответ: {right}")
        img_paths.append(str(IMG_DIR / q_obj["image_file"]) if q_obj.get("image_file") else None)

    pdf_path = build_pdf(report_lines, img_paths)
    await cb.message.answer_document(open(pdf_path, "rb"), caption="PDF-отчёт")
    os.remove(pdf_path)
    await state.clear()

# ─ запуск ─
async def main() -> None:
    if not config.bot_token:
        raise RuntimeError("BOT_TOKEN не найден. Добавьте его в .env")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
