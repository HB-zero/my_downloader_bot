import logging
import os
import requests
from telegram import Bot
from telegram.ext import CommandHandler, MessageHandler, filters, Application
from urllib.parse import urlparse, unquote
import tqdm  # Add this import for progress bar
import sys
from datetime import datetime
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import signal
import asyncio

# Set your bot token and channel name here
BOT_TOKEN = "7754780590:AAHw6KkrB1ge8gxFr2iuqIp7gtMUMkyzkS0"  # Replace with your bot token
CHANNEL_NAME = "@my_course_site"  # Replace with your channel username or ID

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.ERROR  # Changed from DEBUG to ERROR
)
logger = logging.getLogger(__name__)

# Function to extract filename from URL
def get_filename_from_url(url):
    """
    Extracts the filename from a given URL.
    """
    try:
        parsed_url = urlparse(url)
        # Get the path from the URL
        path = parsed_url.path
        # Unquote the path to handle special characters
        path = unquote(path)
        # Split the path by '/'
        parts = path.split('/')
        # The filename is the last part of the path
        filename = parts[-1]
        if not filename:
            return None
        return filename
    except Exception as e:
        logger.error(f"Error extracting filename from URL: {e}")
        return None

def format_size(size_bytes):
    """
    Format size in bytes to human readable format
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def create_session_with_retry():
    """
    Creates a requests session with retry logic for handling connection issues.
    """
    session = requests.Session()
    retry_strategy = Retry(
        total=3,  # number of retries
        backoff_factor=1,  # wait 1, 2, 4 seconds between retries
        status_forcelist=[500, 502, 503, 504]  # HTTP status codes to retry on
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

# Function to download file
async def download_file(url, update, context):
    """
    Downloads a file from a given URL with progress bar and streaming support.
    """
    try:
        # Remove @ if present at the start of URL
        if url.startswith('@'):
            url = url[1:]

        logger.info(f"Starting download from URL: {url}")

        # Get filename from URL first
        filename = get_filename_from_url(url)
        if not filename:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Invalid URL. Please make sure you're using a valid file URL."
            )
            return None, None

        # Add headers to mimic a browser request
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        # Create session with retry logic
        session = create_session_with_retry()

        # Get file size first
        response = session.get(url, stream=True, headers=headers)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))

        filepath = filename
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                # Send initial progress message
                progress_message = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"⏳ Downloading {filename}...\n"
                         f"📦 Size: {format_size(total_size)}\n"
                         f"📊 Progress: 0%"
                )

                # Download with progress bar
                with open(filepath, 'wb') as file:
                    downloaded = 0
                    last_progress = -1
                    chunk_size = 1024 * 1024  # 1MB chunks for better performance

                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            file.write(chunk)
                            downloaded += len(chunk)
                            # Update progress every 1%
                            progress = int((downloaded / total_size) * 100)
                            if progress != last_progress:
                                try:
                                    await context.bot.edit_message_text(
                                        chat_id=update.effective_chat.id,
                                        message_id=progress_message.message_id,
                                        text=f"⏳ Downloading {filename}...\n"
                                             f"📦 Size: {format_size(total_size)}\n"
                                             f"⬇️ Downloaded: {format_size(downloaded)}\n"
                                             f"📊 Progress: {progress}%"
                                    )
                                    last_progress = progress
                                except Exception as e:
                                    if "Message is not modified" not in str(e):
                                        logger.error(f"Progress update failed: {str(e)}")

                # If we get here, download was successful
                break

            except Exception as e:
                retry_count += 1
                logger.error(f"Download attempt {retry_count}/{max_retries} failed: {str(e)}")
                if retry_count < max_retries:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="⚠️ Connection interrupted. Retrying download..."
                    )
                    time.sleep(2)  # Wait before retrying
                    response = session.get(url, stream=True, headers=headers)
                    response.raise_for_status()
                else:
                    raise e

        # Send completion message
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=progress_message.message_id,
            text=f"✅ Download completed!\n"
                 f"📤 Uploading {filename} to channel...\n"
                 f"📦 Total size: {format_size(total_size)}"
        )

        file_size = os.path.getsize(filepath)
        logger.info(f"File downloaded successfully: {filename} ({format_size(file_size)})")
        return filepath, filename

    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Download failed. Please check the URL and try again."
        )
        return None, None

# Function to upload file to Telegram channel
async def upload_file_to_channel(bot, filepath, filename):
    """
    Uploads a file to a Telegram channel.
    """
    try:
        file_size = os.path.getsize(filepath)
        logger.info(f"Attempting to upload file: {filename} (size: {file_size} bytes)")

        with open(filepath, 'rb') as file:
            # Try to send as photo if it's an image
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                try:
                    logger.info("Attempting to send as photo")
                    await bot.send_photo(
                        chat_id=CHANNEL_NAME,
                        photo=file,
                        caption=f"Uploaded file: {filename}"
                    )
                    logger.info("File uploaded successfully as photo")
                    return True
                except Exception as photo_error:
                    logger.error(f"Failed to upload as photo: {photo_error}")
                    # If photo upload fails, try as document
                    file.seek(0)  # Reset file pointer

            # Send as document
            logger.info("Attempting to send as document")
            await bot.send_document(
                chat_id=CHANNEL_NAME,
                document=file,
                filename=filename
            )
            logger.info("File uploaded successfully as document")
            return True

    except Exception as e:
        logger.error(f"Error uploading file to channel: {str(e)}")
        return False

# Command handler for /start
async def start(update, context):
    """
    Handles the /start command with a welcoming message.
    """
    user = update.effective_user
    welcome_message = (
        f"👋 Hello {user.first_name}! Welcome to the File Download Bot!\n\n"
        "🤖 I can help you download files from URLs and upload them to a Telegram channel.\n\n"
        "📝 Here's how to use me:\n"
        "1. Use the command /download followed by the URL\n"
        "2. I'll download the file\n"
        "3. Upload it to the specified channel\n"
        "4. Clean up after myself\n\n"
        "Example: /download https://example.com/file.pdf\n\n"
        "❓ Need help? Just use /download with a URL and I'll handle the rest!\n"
        "⚠️ Note: Make sure the URL is accessible and contains a downloadable file."
    )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=welcome_message,
        parse_mode='HTML'
    )

# Command handler for /test
async def test_channel_access(update, context):
    """
    Test if the bot can access the channel
    """
    try:
        logger.info(f"Attempting to send test message to channel: {CHANNEL_NAME}")

        # First try to get channel info
        try:
            chat = await context.bot.get_chat(CHANNEL_NAME)
            logger.info(f"Channel info: {chat}")
        except Exception as e:
            logger.error(f"Error getting channel info: {e}")
            await update.message.reply_text(f"Error getting channel info: {str(e)}")
            return

        # Try to send message
        message = await context.bot.send_message(
            chat_id=CHANNEL_NAME,
            text="Test message from bot"
        )
        logger.info(f"Test message sent successfully: {message}")
        await update.message.reply_text("Test message sent successfully to channel!")

    except Exception as e:
        error_msg = f"Error sending test message: {str(e)}"
        logger.error(error_msg)
        await update.message.reply_text(error_msg)

# Command handler for /download
async def download_command(update, context):
    """
    Command handler for /download command.
    """
    if not context.args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Please provide a URL to download.\n\n"
                 "Example:\n"
                 "`/download https://example.com/file.pdf`"
        )
        return

    url = context.args[0]
    try:
        # Download the file
        filepath, filename = await download_file(url, update, context)
        if not filepath:
            return

        try:
            # Upload to channel
            with open(filepath, 'rb') as file:
                await context.bot.send_document(
                    chat_id=CHANNEL_NAME,
                    document=file,
                    filename=filename
                )
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="✅ File has been successfully uploaded to the channel!"
            )
        except Exception as e:
            logger.error(f"Channel upload failed: {str(e)}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Failed to upload to channel. Please try again later."
            )
        finally:
            # Clean up the downloaded file
            try:
                os.remove(filepath)
                logger.info(f"Temporary file removed: {filepath}")
            except Exception as e:
                logger.error(f"Failed to remove temporary file {filepath}: {str(e)}")

    except Exception as e:
        logger.error(f"Command execution failed: {str(e)}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Something went wrong. Please try again later."
        )

# Error handler
async def error(update, context):
    """
    Logs errors caused by updates.
    """
    logger.error(f"Update {update} caused error {context.error}")

def print_bot_info():
    """
    Prints bot information to console when starting
    """
    print("\n" + "="*50)
    print("🤖 Telegram File Download Bot")
    print("="*50)
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📢 Channel: {CHANNEL_NAME}")
    print("📝 Available commands:")
    print("  /start - Start the bot and see instructions")
    print("  /download <url> - Download and upload a file")
    print("="*50)
    print("Bot is running... Press Ctrl+C to stop")
    print("="*50 + "\n")

async def handle_message(update, context):
    """
    Message handler for all text messages
    """
    user = update.effective_user
    message_text = update.message.text

    # Check if it's a URL
    if message_text.startswith(('http://', 'https://')):
        log_user_action(user, "Sent URL without command", f"URL: {message_text}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Please use the /download command to download files.\n\n"
                 "Example:\n"
                 "`/download https://example.com/file.pdf`\n\n"
                 "Type /start to see all available commands."
        )
    else:
        # Handle any other text message
        log_user_action(user, "Sent invalid message", f"Message: {message_text}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ I can only process file URLs.\n\n"
                 "Please use the /download command followed by a valid URL.\n"
                 "Example:\n"
                 "`/download https://example.com/file.pdf`\n\n"
                 "Type /start to see all available commands."
        )

async def shutdown(application: Application):
    """
    Gracefully shutdown the bot
    """
    logger.info("Shutting down bot...")
    # Stop the application
    await application.stop()
    # Stop the event loop
    await application.shutdown()
    logger.info("Bot shutdown complete")

def signal_handler(signum, frame):
    """
    Handle shutdown signals
    """
    logger.info(f"Received signal {signum}")
    # Get the running event loop
    loop = asyncio.get_event_loop()
    # Create shutdown task
    shutdown_task = loop.create_task(shutdown(application))
    # Run the shutdown task
    loop.run_until_complete(shutdown_task)
    # Stop the event loop
    loop.stop()
    logger.info("Event loop stopped")
    sys.exit(0)

async def shutdown_command(update, context):
    """
    Command handler for /shutdown command.
    Only allows shutdown from authorized users.
    """
    # Check if user is authorized (you can modify this to check against a list of admin user IDs)
    if update.effective_user.id not in [YOUR_ADMIN_USER_ID]:  # Replace with your admin user ID
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ You are not authorized to use this command."
        )
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🔄 Shutting down bot..."
    )

    # Log the shutdown
    logger.info(f"Shutdown initiated by user {update.effective_user.id}")

    # Create shutdown task
    shutdown_task = asyncio.create_task(shutdown(context.application))
    await shutdown_task

def main():
    """
    Main function to start the bot
    """
    global application
    try:
        # Create the Application
        application = Application.builder().token(BOT_TOKEN).build()

        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("download", download_command))
        application.add_handler(CommandHandler("shutdown", shutdown_command))
        # Add handler for all text messages
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_error_handler(error)

        # Print bot info
        print_bot_info()

        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start the bot
        logger.info("Starting bot...")
        application.run_polling()

    except Exception as e:
        logger.error(f"Error starting bot: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main()
