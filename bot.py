import asyncio
import os
import zipfile
import aiohttp
from playwright.async_api import async_playwright
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# وضعیت هر کاربر
user_states = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_states[update.effective_user.id] = {}
    await update.message.reply_text(
        "سلام! 👋\nلینک سایت کامیک رو بفرست\n\nمثال:\nhttps://example.com/chapter-{}"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id not in user_states:
        user_states[user_id] = {}

    state = user_states[user_id]

    # مرحله ۱ - گرفتن لینک
    if "url" not in state:
        if "{}" not in text:
            await update.message.reply_text(
                "❌ لینک باید شامل {} باشه\n\nمثال:\nhttps://example.com/chapter-{}"
            )
            return
        state["url"] = text
        await update.message.reply_text("✅ لینک ثبت شد!\n\nحالا چپتر شروع رو بفرست:")
        return

    # مرحله ۲ - گرفتن چپتر شروع
    if "start" not in state:
        if not text.isdigit():
            await update.message.reply_text("❌ فقط عدد بفرست:")
            return
        state["start"] = int(text)
        await update.message.reply_text("✅ ثبت شد!\n\nحالا چپتر پایان رو بفرست:")
        return

    # مرحله ۳ - گرفتن چپتر پایان و شروع دانلود
    if "end" not in state:
        if not text.isdigit():
            await update.message.reply_text("❌ فقط عدد بفرست:")
            return
        state["end"] = int(text)

        url = state["url"]
        start = state["start"]
        end = state["end"]

        # ریست وضعیت
        user_states[user_id] = {}

        await update.message.reply_text(
            f"🚀 شروع کردم!\nلینک: {url}\nچپتر {start} تا {end}\n\nصبر کن..."
        )

        # شروع دانلود
        await download_chapters(update, url, start, end)

async def advanced_scroll_and_wait(page, page_number):
    viewport_height = await page.evaluate("window.innerHeight")
    total_height = await page.evaluate("document.body.scrollHeight")
    current_scroll = 0
    step = int(viewport_height * 0.4)

    while current_scroll < total_height:
        await page.evaluate(f"window.scrollTo(0, {current_scroll})")
        current_scroll += step
        await page.wait_for_timeout(400)
        total_height = await page.evaluate("document.body.scrollHeight")

    await page.evaluate("window.scrollTo(0, 0)")
    await page.evaluate("""
        () => {
            document.querySelectorAll('img').forEach(img => {
                let realSrc = img.getAttribute('data-src') || img.src;
                if (realSrc) {
                    img.src = realSrc;
                    img.removeAttribute('loading');
                }
            });
        }
    """)

async def get_valid_image_urls(page):
    return await page.evaluate("""
    () => {
        const images = Array.from(document.querySelectorAll('img'));
        return images
            .map(img => img.src)
            .filter(src => src && !src.includes('logo') && !src.includes('avatar') && src.startsWith('http'));
    }
    """)

async def download_image_safely(session, url, index, attempts=20):
    for attempt in range(attempts):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    return await response.read()
        except Exception as e:
            print(f"Error image {index+1} attempt {attempt+1}: {e}")
        await asyncio.sleep(3)
    return None

async def download_chapters(update: Update, base_url: str, start: int, end: int):
    os.makedirs("/tmp/comics", exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for chapter_num in range(start, end + 1):
            url = base_url.format(chapter_num)
            cbz_path = f"/tmp/comics/Chapter_{chapter_num}.cbz"
            temp_path = cbz_path + ".tmp"

            await update.message.reply_text(f"📖 دانلود چپتر {chapter_num}...")

            context = await browser.new_context(
                viewport={"width": 393, "height": 852},
                is_mobile=True,
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=0)
                await advanced_scroll_and_wait(page, chapter_num)
                src_list = await get_valid_image_urls(page)

                if not src_list or len(src_list) < 5:
                    await update.message.reply_text(f"❌ چپتر {chapter_num}: عکس پیدا نشد، رد شد.")
                    await context.close()
                    continue

                downloaded_images = {}
                async with aiohttp.ClientSession() as session:
                    for index, img_url in enumerate(src_list):
                        img_data = await download_image_safely(session, img_url, index)
                        if img_data:
                            ext = img_url.split('.')[-1].split('?')[0]
                            if ext not in ['jpg', 'jpeg', 'png', 'webp']:
                                ext = 'jpg'
                            downloaded_images[f"{str(index+1).zfill(3)}.{ext}"] = img_data
                        else:
                            await update.message.reply_text(f"❌ چپتر {chapter_num}: عکس {index+1} دانلود نشد.")
                            break

                if len(downloaded_images) == len(src_list):
                    with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as cbz:
                        for name, data in downloaded_images.items():
                            cbz.writestr(name, data)
                    os.rename(temp_path, cbz_path)

                    # ارسال فایل به تلگرام
                    with open(cbz_path, 'rb') as f:
                        await update.message.reply_document(
                            document=f,
                            filename=f"Chapter_{chapter_num}.cbz",
                            caption=f"✅ چپتر {chapter_num} آماده!"
                        )
                    os.remove(cbz_path)
                else:
                    await update.message.reply_text(f"❌ چپتر {chapter_num} ناقص بود، رد شد.")

            except Exception as e:
                await update.message.reply_text(f"❌ خطا در چپتر {chapter_num}: {e}")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            finally:
                await context.close()

            await asyncio.sleep(5)

        await browser.close()
        await update.message.reply_text("🎉 همه چپترها تموم شد!")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()