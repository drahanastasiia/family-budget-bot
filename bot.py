import datetime as dt
import logging
import os
from datetime import datetime
from typing import Optional

import pytz
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
)

import database

load_dotenv()

logging.basicConfig(format='%(asctime)s  %(levelname)s  %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ── constants ────────────────────────────────────────────────────────────────

CATEGORIES: dict[str, str] = {
    'food':          '🍔 Їжа',
    'transport':     '🚗 Транспорт',
    'utilities':     '🏠 Комунальні',
    'entertainment': '🎮 Розваги',
    'health':        '💊 Здоров\'я',
    'clothing':      '👕 Одяг',
    'other':         '📦 Інше',
}

MONTH_UA = [
    '', 'Січень', 'Лютий', 'Березень', 'Квітень', 'Травень', 'Червень',
    'Липень', 'Серпень', 'Вересень', 'Жовтень', 'Листопад', 'Грудень',
]

# user_data['state'] values
S_IDLE        = None
S_WAIT_AMOUNT = 'wait_amount'
S_WAIT_DESC   = 'wait_desc'

KYIV_TZ = pytz.timezone('Europe/Kyiv')

# ── keyboards ────────────────────────────────────────────────────────────────

def _main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Додати витрату',  callback_data='menu_add')],
        [InlineKeyboardButton('📊 Звіт за місяць', callback_data='menu_report')],
    ])


def _category_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(label, callback_data=f'cat_{key}')
        for key, label in CATEGORIES.items()
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def _skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('⏭ Пропустити', callback_data='skip_desc'),
        InlineKeyboardButton('❌ Скасувати',  callback_data='cancel'),
    ]])

# ── report builder ───────────────────────────────────────────────────────────

def _build_report(month: int, year: int) -> Optional[str]:
    expenses = database.get_monthly_expenses(month, year)
    if not expenses:
        return None

    total = sum(e['amount'] for e in expenses)

    by_cat: dict[str, float] = {}
    by_user: dict[str, float] = {}
    for e in expenses:
        by_cat[e['category']]  = by_cat.get(e['category'], 0)  + e['amount']
        by_user[e['username']] = by_user.get(e['username'], 0) + e['amount']

    lines = [
        f'📊 *Звіт за {MONTH_UA[month]} {year}*',
        '',
        f'💰 *Загальна сума:* {total:,.2f} грн',
        '',
        '📂 *По категоріях:*',
    ]
    for key, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
        pct = amt / total * 100
        lines.append(f'  {CATEGORIES.get(key, key)}: {amt:,.2f} грн  ({pct:.0f}%)')

    lines += ['', '👥 *По учасниках:*']
    for uname, amt in sorted(by_user.items(), key=lambda x: -x[1]):
        lines.append(f'  {uname}: {amt:,.2f} грн')

    return '\n'.join(lines)

# ── helpers ──────────────────────────────────────────────────────────────────

def _display_name(user) -> str:
    return f'@{user.username}' if user.username else user.first_name


def _state(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get('state', S_IDLE)


def _reset(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

# ── command handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _reset(context)
    user = update.effective_user
    database.upsert_user(user.id, _display_name(user))
    await update.message.reply_text(
        f'👋 Привіт, *{user.first_name}*!\n\n'
        '💰 Я допомагаю сім\'ї відстежувати витрати.\n'
        'Кожен член родини може додавати свої витрати — все зберігається спільно.\n\n'
        'Що робимо?',
        parse_mode='Markdown',
        reply_markup=_main_menu(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '📖 *Команди:*\n'
        '/start — головне меню\n'
        '/add — додати витрату\n'
        '/report — звіт за поточний місяць\n'
        '/cancel — скасувати поточну дію\n\n'
        '💡 Просто поділись ботом з рідними — кожен може додавати витрати.',
        parse_mode='Markdown',
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _reset(context)
    await update.message.reply_text('❌ Скасовано.', reply_markup=_main_menu())


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now  = datetime.now(KYIV_TZ)
    text = _build_report(now.month, now.year)
    await update.message.reply_text(
        text if text else '📊 Витрат цього місяця ще немає.',
        parse_mode='Markdown' if text else None,
        reply_markup=_main_menu() if text else None,
    )

# ── add flow helpers ──────────────────────────────────────────────────────────

async def _ask_amount(send_fn, context: ContextTypes.DEFAULT_TYPE):
    _reset(context)
    context.user_data['state'] = S_WAIT_AMOUNT
    await send_fn('💵 Введи суму витрати (наприклад: 250 або 1500.50):')


async def _save_and_confirm(chat_id: int, user, context: ContextTypes.DEFAULT_TYPE, description: str):
    amount   = context.user_data['amount']
    category = context.user_data['category']
    _reset(context)
    database.add_expense(user.id, _display_name(user), amount, category, description)

    text = (
        '✅ *Витрату збережено!*\n\n'
        f'💵 Сума: *{amount:,.2f} грн*\n'
        f'📂 Категорія: {CATEGORIES[category]}\n'
        + (f'📝 Опис: {description}' if description else '')
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode='Markdown',
        reply_markup=_main_menu(),
    )

# ── text message handler ──────────────────────────────────────────────────────

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.strip()
    state = _state(context)

    if state == S_WAIT_AMOUNT:
        raw = text.replace(',', '.')
        try:
            amount = float(raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                '❌ Не розумію. Введи число, наприклад: *250* або *1500.50*',
                parse_mode='Markdown',
            )
            return

        context.user_data['amount'] = amount
        context.user_data['state']  = None  # category comes via button
        await update.message.reply_text('📂 Вибери категорію:', reply_markup=_category_keyboard())

    elif state == S_WAIT_DESC:
        await _save_and_confirm(update.effective_chat.id, update.effective_user, context, text)

    else:
        await update.message.reply_text(
            'Скористайся меню або введи /add щоб додати витрату.',
            reply_markup=_main_menu(),
        )

# ── callback query handler ────────────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data
    state = _state(context)

    await query.answer()

    # ── main menu ──
    if data == 'menu_add':
        await _ask_amount(query.message.reply_text, context)

    elif data == 'menu_report':
        now  = datetime.now(KYIV_TZ)
        text = _build_report(now.month, now.year)
        await query.message.reply_text(
            text if text else '📊 Витрат цього місяця ще немає.',
            parse_mode='Markdown' if text else None,
            reply_markup=_main_menu() if text else None,
        )

    elif data == 'cancel':
        _reset(context)
        await query.message.reply_text('❌ Скасовано.', reply_markup=_main_menu())

    # ── category selection (no state check — button is only shown at right time) ──
    elif data.startswith('cat_'):
        if context.user_data.get('amount') is None:
            await query.message.reply_text(
                '⚠️ Спочатку введи суму. Натисни /add.',
                reply_markup=_main_menu(),
            )
            return
        key = data[4:]
        context.user_data['category'] = key
        context.user_data['state']    = S_WAIT_DESC
        await query.message.reply_text(
            f'Категорія: {CATEGORIES[key]}\n\n📝 Додай опис (або пропусти):',
            reply_markup=_skip_keyboard(),
        )

    # ── skip description ──
    elif data == 'skip_desc':
        if context.user_data.get('category') is None:
            await query.message.reply_text('⚠️ Щось пішло не так. Спробуй /add ще раз.')
            _reset(context)
            return
        await _save_and_confirm(update.effective_chat.id, update.effective_user, context, '')

# ── auto-report ───────────────────────────────────────────────────────────────

async def auto_monthly_report(context: ContextTypes.DEFAULT_TYPE):
    now   = datetime.now(KYIV_TZ)
    month = now.month - 1 or 12
    year  = now.year if now.month > 1 else now.year - 1
    text  = _build_report(month, year)
    if not text:
        return
    full = '🔔 *Автоматичний місячний звіт*\n\n' + text
    for user_id in database.get_all_user_ids():
        try:
            await context.bot.send_message(chat_id=user_id, text=full, parse_mode='Markdown')
        except Exception as exc:
            logger.warning('Cannot send auto-report to %s: %s', user_id, exc)

# ── error handler ─────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error('Unhandled exception:', exc_info=context.error)

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    database.init_db()

    token = os.getenv('BOT_TOKEN')
    if not token:
        raise RuntimeError('BOT_TOKEN is not set.')

    data_dir    = os.getenv('DATA_DIR', '.')
    persistence = PicklePersistence(filepath=os.path.join(data_dir, 'conversations.pickle'))
    app = Application.builder().token(token).persistence(persistence).build()

    app.add_handler(CommandHandler('start',  cmd_start))
    app.add_handler(CommandHandler('help',   cmd_help))
    app.add_handler(CommandHandler('cancel', cmd_cancel))
    app.add_handler(CommandHandler('add',    lambda u, c: _ask_amount(u.message.reply_text, c)))
    app.add_handler(CommandHandler('report', cmd_report))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(error_handler)

    app.job_queue.run_monthly(
        auto_monthly_report,
        when=dt.time(9, 0, tzinfo=KYIV_TZ),
        day=1,
    )

    async def post_init(application: Application):
        await application.bot.set_my_commands([
            ('start',  'Головне меню'),
            ('add',    'Додати витрату'),
            ('report', 'Звіт за поточний місяць'),
            ('cancel', 'Скасувати поточну дію'),
            ('help',   'Допомога'),
        ])

    app.post_init = post_init

    logger.info('Bot started.')
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == '__main__':
    main()
