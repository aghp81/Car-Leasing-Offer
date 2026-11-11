"""Telegram car affordability bot. Prices and lease terms are loaded from JSON files."""
from __future__ import annotations

import json
import logging
import os
import asyncio
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from price_feed import refresh_prices

ROOT = Path(__file__).parent
SALARY, INSTALLMENT = range(2)
logging.basicConfig(level=logging.INFO)


def load_json(filename: str):
    with (ROOT / filename).open(encoding="utf-8") as stream:
        return json.load(stream)


def toman(value: int) -> str:
    return f"{value:,} تومان"


def monthly_payment(principal: int, annual_rate: float, months: int) -> int:
    """Fixed-payment loan formula; rate is nominal annual fraction (e.g. .24)."""
    if months <= 0:
        raise ValueError("months must be positive")
    rate = annual_rate / 12
    if rate == 0:
        return round(principal / months)
    return round(principal * rate / (1 - (1 + rate) ** -months))


def recommendations(max_installment: int, cars: list[dict]) -> list[dict]:
    plans = load_json("leasing.json")
    results = []
    for car in cars:
        price = int(car["price_toman"])
        for plan in plans:
            down_payment = round(price * float(plan["down_payment_percent"]))
            principal = price - down_payment
            payment = monthly_payment(principal, float(plan["annual_rate"]), int(plan["months"]))
            if payment <= max_installment:
                results.append({"car": car, "plan": plan, "down": down_payment,
                                "payment": payment, "principal": principal})
    return sorted(results, key=lambda item: (item["payment"], item["car"]["price_toman"]))[:5]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "سلام! برای پیدا کردن گزینه‌های متناسب، حقوق ماهانه‌تان را به تومان وارد کنید.\n"
        "مثال: ۴۵۰۰۰۰۰۰"
    )
    return SALARY


async def salary_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        salary = int(update.message.text.replace(",", "").replace("٬", "").replace(" ", ""))
        if salary <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("لطفاً مبلغ حقوق را به‌صورت عدد مثبت و به تومان وارد کنید.")
        return SALARY
    context.user_data["salary"] = salary
    await update.message.reply_text("حداکثر مبلغ قسط ماهانه‌ای که با خیال راحت می‌توانید بپردازید چقدر است؟ (تومان)")
    return INSTALLMENT


async def installment_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        installment = int(update.message.text.replace(",", "").replace("٬", "").replace(" ", ""))
        if installment <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("لطفاً مبلغ قسط را به‌صورت عدد مثبت و به تومان وارد کنید.")
        return INSTALLMENT

    salary = context.user_data["salary"]
    cars, prices_stale, fetched_at = await asyncio.to_thread(refresh_prices)
    options = recommendations(installment, cars)
    if not options:
        result = "با قیمت‌ها و شرایط فعلی ثبت‌شده، گزینه‌ای با این سقف قسط پیدا نشد."
    else:
        lines = ["گزینه‌های تقریبی شما:"]
        for item in options:
            car, plan = item["car"], item["plan"]
            lines.append(
                f"\n🚘 {car['name']} — قیمت ثبت‌شده: {toman(int(car['price_toman']))}"
                f"\nطرح: {plan['name']}، پیش‌پرداخت: {toman(item['down'])}"
                f"\nمبلغ تأمین مالی: {toman(item['principal'])}، مدت: {plan['months']} ماه"
                f"\nقسط تخمینی: {toman(item['payment'])}"
            )
            if car.get("source_age"):
                lines.append(f"زمان اعلام‌شده برای قیمت: {car['source_age']}")
        result = "\n".join(lines)
    if prices_stale:
        result += "\n\n⚠️ دریافت تازه از منبع انجام نشد؛ قیمت‌های ذخیره‌شده ممکن است قدیمی باشند." if cars else \
                  "\n\n⚠️ فعلاً دریافت قیمت از منبع انجام نشد؛ بعداً دوباره تلاش کنید."
    elif fetched_at:
        result += f"\n\nزمان دریافت از منبع: {fetched_at} UTC | منبع: car.ir/prices"
    result += (f"\n\nحقوق اعلام‌شده: {toman(salary)}. این محاسبه بر اساس اعداد ثبت‌شده است؛ "
               "قیمت بازار و شرایط نهایی شرکت لیزینگ ممکن است تغییر کند. هزینه‌های بیمه، کارمزد، "
               "اعتبارسنجی و وثیقه در این برآورد لحاظ نشده‌اند.")
    await update.message.reply_text(result)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("فرآیند لغو شد. هر زمان خواستید /start را بزنید.")
    return ConversationHandler.END


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable first.")
    app = Application.builder().token(token).build()
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, salary_received)],
            INSTALLMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, installment_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    app.run_polling()


if __name__ == "__main__":
    main()
