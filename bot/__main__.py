import asyncio
import json
import logging
from pathlib import Path

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
)

from .config import config

# ─── базовая настройка логирования ──────────────────────────────────────────
logging.basicConfig(level=logging.INFO)

# ─── загрузка вопросов ──────────────────────────────────────────────────────
QUIZ_PATH = Path(__file__).parent / "data" / "quizzes.json"
try:
    with QUIZ_PATH.open(encoding="utf-8") as f:
        QUIZZES: dict[str, list[dict]] = json.load(f)
except FileNotFoundError:
    raise RuntimeError(f"Файл с вопросами не найден: {QUIZ_PATH}")
except json.JSONDecodeError as e:
    raise RuntimeError(f"Ошибка синтаксиса в quizzes.json: {e}") from e

# ─── инициализация бота и диспетчера ────────────────────────────────────────
bot = Bot(
    token=config.bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
)
dp = Dispatcher()

# ─── fsm-состояния ──────────────────────────────────────────────────────────
class QuizState(StatesGroup):
    waiting_for_answer = State()

# ─── хэндлеры ───────────────────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(msg: Message) -> None:
    """Стартовое меню: выбор темы."""
    topics = list(QUIZZES.keys())
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=topic, callback_data=f"topic:{topic}")]
            for topic in topics
        ]
    )
    await msg.answer("*Привет!*\nВыбери тему викторины:", reply_markup=kb)


@dp.callback_query(lambda cb: cb.data.startswith("topic:"))
async def choose_topic(cb: CallbackQuery, state: FSMContext) -> None:
    """Пользователь щёлкнул по теме — начинаем викторину."""
    topic = cb.data.split(":", 1)[1]
    await state.update_data(
        topic=topic,
        idx=0,
        score=0,
        results=[]            # ➊ сюда будем складывать ответы
    )
    await ask_question(cb.message, state)

#@dp.message(lambda m: m.photo)
#async def echo_file_id(msg: Message):
    #file_id = msg.photo[-1].file_id
    #await msg.answer(file_id)      # отправит вам ID в чат
    # print(file_id)               # можно и в консоль


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

    caption = (
        f"❓_Вопрос {idx + 1} из {total}_\n\n"
        f"*{q['question']}*"
    )

    # ── НОВОЕ: если в JSON есть file_id, шлём фото ─────────────────────────
    if q.get("file_id"):
        await msg.answer_photo(
            q["file_id"],           # тот самый AgACAgIAAxk… из Telegram
            caption=caption,
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        # старый путь — вопрос без картинки
        await msg.answer(caption, reply_markup=kb)

    await state.set_state(QuizState.waiting_for_answer)



@dp.callback_query(QuizState.waiting_for_answer)
async def handle_answer(cb: CallbackQuery, state: FSMContext) -> None:
    data   = await state.get_data()
    topic  = data["topic"]
    idx    = data["idx"]
    q      = QUIZZES[topic][idx]
    chosen = int(cb.data.split(":", 1)[1])
    correct = chosen == q["correct"]

    # ➋ дописываем в results
    data["results"].append({"idx": idx, "correct": correct})
    # (можно сразу state.update_data(results=data["results"]) ― несложно)

    score = data["score"] + int(correct)
    next_idx = idx + 1

    await cb.answer("✅ Верно!" if correct else "❌ Неверно")

    if next_idx < len(QUIZZES[topic]):
        await state.update_data(idx=next_idx, score=score, results=data["results"])
        await ask_question(cb.message, state)

    else:
        # ── выводим итог + подробный отчёт ─────────────────────
        lines = []
        for i, item in enumerate(data["results"], start=1):
            q_obj = QUIZZES[topic][item["idx"]]
            mark  = "✅" if item["correct"] else "❌"
            right = q_obj["options"][q_obj["correct"]]
            lines.append(f"{mark} *Вопрос {i}:* {q_obj['question']}\n *Правильный ответ:* _{right}_")

        report = "\n\n".join(lines)

        await cb.message.answer(
            f"🏁 Конец!\nПравильных ответов: *{score}* из *{len(data['results'])}*\n\n{report}"
        )
        await state.clear()

        # ➕ показываем предложение сыграть снова
        topics = list(QUIZZES.keys())
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t, callback_data=f"topic:{t}")]
                for t in topics
            ]
        )
        await cb.message.answer(
            "🔄 Хочешь сыграть ещё раз? Выбери тему:",
            reply_markup=kb,
        )

# ─── запуск ────────────────────────────────────────────────────────────────
async def main() -> None:
    if not config.bot_token:
        raise RuntimeError("BOT_TOKEN не найден. Добавьте его в .env")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
