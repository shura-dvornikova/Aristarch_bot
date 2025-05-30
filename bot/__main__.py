import asyncio, json, logging, os, tempfile, unicodedata
from pathlib import Path
from typing import List

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    Message, FSInputFile,
)

from fpdf import FPDF
from fontTools.ttLib import TTLibError
from .config import config

# ── инфраструктура ────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
BASE_DIR = Path(__file__).parent
IMG_DIR  = BASE_DIR.parent / "images"
QUIZ_PATH = BASE_DIR / "data" / "quizzes.json"

with QUIZ_PATH.open(encoding="utf-8") as f:
    QUIZZES: dict[str, List[dict]] = json.load(f)

# ── поиск шрифта ───────────────────────────────────────────────────────────
CANDIDATE_FONTS = [
    BASE_DIR / "fonts" / "DejaVuSans.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path.home() / ".fonts" / "DejaVuSans.ttf",
]
FONT_PATH: Path | None = next((p for p in CANDIDATE_FONTS if p.exists()), None)
if FONT_PATH:
    logging.info("Unicode-шрифт найден: %s", FONT_PATH)
else:
    logging.warning("Unicode-шрифт не найден — PDF будет ASCII-only")

# ── bot ────────────────────────────────────────────────────────────────────
bot = Bot(token=config.bot_token,
          default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

# ── FSM ────────────────────────────────────────────────────────────────────
class QuizState(StatesGroup):
    waiting_for_answer = State()
class ReportChoice(StatesGroup):
    waiting = State()

# ── helpers ────────────────────────────────────────────────────────────────
def ascii_fallback(txt: str) -> str:
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")

def build_pdf(lines: List[str], image_paths: List[str]) -> str:
    pdf = FPDF(format="A4")
    use_unicode = False
    font_name = "Helvetica"

    if FONT_PATH:
        try:
            pdf.add_font("DejaVu", "", str(FONT_PATH))  # uni=True не нужен
            font_name = "DejaVu"
            use_unicode = True
        except TTLibError as e:
            logging.warning("Файл шрифта повреждён (%s) — ASCII fallback.", e)

    pdf.set_auto_page_break(True, margin=15)

    for text, img in zip(lines, image_paths):
        pdf.add_page()
        pdf.set_font(font_name, size=12)
        pdf.multi_cell(0, 10, text if use_unicode else ascii_fallback(text))
        if img and Path(img).exists():
            pdf.ln(5); pdf.image(img, w=170)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp.name)
    return tmp.name

# ── /start ────────────────────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(msg: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=f"topic:{t}")]
                         for t in QUIZZES.keys()])
    await msg.answer("*Привет!* Выбери тему викторины:", reply_markup=kb)

# ── выбор темы и вопросы (без изменений) ──────────────────────────────────
@dp.callback_query(lambda cb: cb.data.startswith("topic:"))
async def choose_topic(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(topic=cb.data.split(":", 1)[1], idx=0, score=0, results=[])
    await ask_question(cb.message, state)

async def ask_question(msg: Message, state: FSMContext) -> None:
    d = await state.get_data(); t, i = d["topic"], d["idx"]; q = QUIZZES[t][i]
    total = len(QUIZZES[t])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=o, callback_data=f"ans:{n}")]
        for n, o in enumerate(q["options"])])
    caption = f"❓_Вопрос {i+1} из {total}_\n\n*{q['question']}*"
    img = IMG_DIR / q.get("image_file", "")
    if img.exists():
        await msg.answer_photo(FSInputFile(img), caption=caption,
                               reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await msg.answer(caption, reply_markup=kb)
    await state.set_state(QuizState.waiting_for_answer)

@dp.callback_query(QuizState.waiting_for_answer)
async def handle_answer(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data(); t, i = d["topic"], d["idx"]; q = QUIZZES[t][i]
    correct = int(cb.data.split(":", 1)[1]) == q["correct"]
    d["results"].append({"idx": i, "correct": correct})
    score = d["score"] + int(correct); next_i = i + 1
    await cb.answer("✅ Верно!" if correct else "❌ Неверно")
    if next_i < len(QUIZZES[t]):
        await state.update_data(idx=next_i, score=score, results=d["results"])
        await ask_question(cb.message, state); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📝 Показать отчёт", callback_data="report:text"),
        InlineKeyboardButton(text="📄 PDF с картинками", callback_data="report:pdf")]])
    await cb.message.answer(
        f"🏁 Конец! Правильных ответов: *{score}* из *{len(d['results'])}*\n\n"
        "Выберите формат отчёта:", reply_markup=kb)
    await state.update_data(score=score); await state.set_state(ReportChoice.waiting)

# ── текстовый отчёт (без изменений) ───────────────────────────────────────
@dp.callback_query(lambda c: c.data == "report:text", ReportChoice.waiting)
async def send_text_report(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data(); lines=[]
    for n, r in enumerate(d["results"],1):
        q=QUIZZES[d["topic"]][r["idx"]]; mark="✅" if r["correct"] else "❌"
        right=q["options"][q["correct"]]
        lines.append(f"{mark} *Вопрос {n}:* {q['question']}\n *Верный ответ:* _{right}_")
    await cb.message.answer("\n\n".join(lines)); await state.clear()

# ── PDF-отчёт (FSInputFile) ───────────────────────────────────────────────
@dp.callback_query(lambda c: c.data == "report:pdf", ReportChoice.waiting)
async def send_pdf_report(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data(); lines, imgs = [], []
    for n,r in enumerate(d["results"],1):
        q=QUIZZES[d["topic"]][r["idx"]]
        mark="CORRECT" if r["correct"] else "WRONG"
        lines.append(f"{mark}: Question {n}: {q['question']} — answer: {q['options'][q['correct']]}")
        imgs.append(str(IMG_DIR / q["image_file"]) if q.get("image_file") else None)
    pdf_path = build_pdf(lines, imgs)
    await cb.message.answer_document(FSInputFile(pdf_path), caption="PDF-report")
    os.remove(pdf_path); await state.clear()

# ── запуск ────────────────────────────────────────────────────────────────
async def main() -> None:
    if not config.bot_token:
        raise RuntimeError("BOT_TOKEN не найден в .env")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
