import logging
import os
import requests
from telegram import Bot, Update
from telegram.ext import CommandHandler, MessageHandler, filters, Application
from urllib.parse import urlparse, unquote
import sys
from datetime import datetime
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import signal
import asyncio
import uuid
from typing import Dict, Optional
import atexit

# Set your bot token and channel name here
BOT_TOKEN = "7754780590:AAHw6KkrB1ge8gxFr2iuqIp7gtMUMkyzkS0"  # Replace with your bot token
CHANNEL_NAME = "@my_course_site"  # Replace with your channel username or ID

# Dictionary to store active downloads
active_downloads: Dict[str, dict] = {}

# Global variable for application
application = None

# Enable logging with INFO level
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.ERROR  # Set default to ERROR to suppress most logs
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

        # If filename is empty or doesn't have an extension, return None
        if not filename or '.' not in filename:
            return None

        # Clean the filename (remove any query parameters)
        filename = filename.split('?')[0]

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

def cleanup_files():
    """
    Cleanup function to remove any remaining files
    """
    for download_id, download_info in list(active_downloads.items()):
        try:
            if 'file_handle' in download_info and download_info['file_handle'] is not None:
                try:
                    download_info['file_handle'].close()
                    logger.info(f"Closed file handle for {download_info['filepath']}")
                except Exception as e:
                    logger.error(f"Error closing file handle: {str(e)}")

            if os.path.exists(download_info['filepath']):
                os.remove(download_info['filepath'])
                logger.info(f"Cleaned up file: {download_info['filepath']}")
        except Exception as e:
            logger.error(f"Error cleaning up file {download_info.get('filepath', 'unknown')}: {str(e)}")

# Register cleanup function
atexit.register(cleanup_files)

def validate_url(url: str) -> bool:
    """
    Validates if the given string is a proper URL
    """
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

# Function to download file
async def download_file(url, update, context):
    """
    Downloads a file from a given URL with progress bar and streaming support.
    """
    try:
        # Validate URL
        if not validate_url(url):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Invalid URL format. Please provide a complete URL starting with http:// or https://"
            )
            return None, None

        logger.info(f"Starting download from URL: {url}")

        # Get filename from URL first
        filename = get_filename_from_url(url)
        if not filename:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Invalid URL. Please make sure the URL points to a file with a proper filename and extension."
            )
            return None, None

        # Generate UUID for this download
        download_id = str(uuid.uuid4())[:8]  # Using first 8 characters for shorter ID

        # Add headers to mimic a browser request
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        # Create session with retry logic
        session = create_session_with_retry()

        try:
            # Get file size first
            response = session.get(url, stream=True, headers=headers)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to connect to URL: {str(e)}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Failed to connect to the URL. Please check if the URL is accessible."
            )
            return None, None

        filepath = filename
        max_retries = 3
        retry_count = 0
        file_handle = None

        while retry_count < max_retries:
            try:
                # Store download info (initialize with file_handle as None)
                active_downloads[download_id] = {
                    'user_id': update.effective_user.id,
                    'filename': filename,
                    'filepath': filepath,
                    'total_size': total_size,
                    'downloaded': 0,
                    'cancelled': False,
                    'completed': False,
                    'start_time': datetime.now(),
                    'file_handle': None,
                    'progress_message_id': None
                }

                # Log new download
                logger.info(f"New download started - ID: {download_id}, User: {update.effective_user.first_name} ({update.effective_user.id}), File: {filename}, Size: {format_size(total_size)}")

                # Send initial progress message
                progress_message = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"⏳ Downloading {filename}...\n"
                         f"📦 Size: {format_size(total_size)}\n"
                         f"📊 Progress: 0%\n"
                         f"🆔 Download ID: {download_id}\n"
                         f"Use /cancel {download_id} to cancel this download"
                )

                # Store progress message ID
                active_downloads[download_id]['progress_message_id'] = progress_message.message_id

                # Download with progress bar
                file_handle = open(filepath, 'wb')
                active_downloads[download_id]['file_handle'] = file_handle

                downloaded = 0
                last_progress = -1
                chunk_size = 1024 * 1024  # 1MB chunks for better performance

                for chunk in response.iter_content(chunk_size=chunk_size):
                    if active_downloads[download_id]['cancelled']:
                        raise Exception("Download cancelled by user")

                    if chunk:
                        file_handle.write(chunk)
                        downloaded += len(chunk)
                        active_downloads[download_id]['downloaded'] = downloaded

                        # Update progress every 1%
                        progress = int((downloaded / total_size) * 100)
                        if progress != last_progress:
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=update.effective_chat.id,
                                    message_id=active_downloads[download_id]['progress_message_id'],
                                    text=f"⏳ Downloading {filename}...\n"
                                         f"📦 Size: {format_size(total_size)}\n"
                                         f"⬇️ Downloaded: {format_size(downloaded)}\n"
                                         f"📊 Progress: {progress}%\n"
                                         f"🆔 Download ID: {download_id}\n"
                                         f"Use /cancel {download_id} to cancel this download"
                                )
                                last_progress = progress
                            except Exception as e:
                                if "Message is not modified" not in str(e):
                                    logger.error(f"Progress update failed: {str(e)}")

                # Close the file handle
                file_handle.close()
                active_downloads[download_id]['file_handle'] = None
                active_downloads[download_id]['completed'] = True

                # If we get here, download was successful
                break

            except Exception as e:
                if "Download cancelled by user" in str(e):
                    logger.info(f"Download cancelled - ID: {download_id}, User: {update.effective_user.first_name} ({update.effective_user.id}), File: {filename}")
                    try:
                        await context.bot.edit_message_text(
                            chat_id=update.effective_chat.id,
                            message_id=active_downloads[download_id]['progress_message_id'],
                            text=f"❌ Download cancelled!\n"
                                 f"🆔 Download ID: {download_id}"
                        )
                    except Exception as msg_error:
                        logger.error(f"Error updating cancellation message: {str(msg_error)}")

                    # Close file handle if it's open
                    if file_handle and not file_handle.closed:
                        file_handle.close()
                    active_downloads[download_id]['file_handle'] = None
                    active_downloads[download_id]['completed'] = True

                    # Clean up the file
                    try:
                        if os.path.exists(filepath):
                            os.remove(filepath)
                    except Exception as file_error:
                        logger.error(f"Error removing file: {str(file_error)}")
                    return None, None

                # Close file handle if it's open
                if file_handle and not file_handle.closed:
                    file_handle.close()
                active_downloads[download_id]['file_handle'] = None

                retry_count += 1
                logger.error(f"Download attempt {retry_count}/{max_retries} failed: {str(e)}")
                if retry_count < max_retries:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="⚠️ Connection interrupted. Retrying download..."
                    )
                    time.sleep(2)  # Wait before retrying
                    try:
                        response = session.get(url, stream=True, headers=headers)
                        response.raise_for_status()
                    except requests.exceptions.RequestException as e:
                        logger.error(f"Retry failed: {str(e)}")
                        continue
                else:
                    raise e

        # Remove from active downloads
        if download_id in active_downloads:
            del active_downloads[download_id]

        # Send completion message
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=progress_message.message_id,
                text=f"✅ Download completed!\n"
                     f"📤 Uploading {filename} to channel...\n"
                     f"📦 Total size: {format_size(total_size)}"
            )
        except Exception as msg_error:
            logger.error(f"Error updating completion message: {str(msg_error)}")

        file_size = os.path.getsize(filepath)
        logger.info(f"Download completed - ID: {download_id}, User: {update.effective_user.first_name} ({update.effective_user.id}), File: {filename}, Size: {format_size(file_size)}")
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

def log_user_action(user, action, details=""):
    """
    Log user actions with timestamp and details
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_info = f"User: {user.first_name} (ID: {user.id})"
    log_message = f"[{timestamp}] {user_info} - {action}"
    if details:
        log_message += f" - {details}"
    print(log_message)  # Console output
    logger.info(log_message)  # File logging

async def start(update, context):
    """
    Handles the /start command with a welcoming message.
    """
    user = update.effective_user
    log_user_action(user, "Started bot")
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
    user = update.effective_user
    if not context.args:
        log_user_action(user, "Attempted download without URL")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Please provide a URL to download.\n\n"
                 "Example:\n"
                 "`/download https://example.com/file.pdf`"
        )
        return

    url = context.args[0]
    log_user_action(user, "Started download", f"URL: {url}")

    # Start download in background
    asyncio.create_task(process_download(url, update, context))

async def process_download(url, update, context):
    """
    Process the download in the background
    """
    try:
        # Download the file
        filepath, filename = await download_file(url, update, context)
        if not filepath:
            return

        try:
            # Upload to channel using the dedicated function
            success = await upload_file_to_channel(context.bot, filepath, filename)

            if success:
                log_user_action(update.effective_user, "Upload successful", f"File: {filename}")
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="✅ File has been successfully uploaded to the channel!"
                )
            else:
                log_user_action(update.effective_user, "Upload failed", f"File: {filename}")
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Failed to upload to channel. Please check if:\n"
                         "1. The bot is an admin in the channel\n"
                         "2. The channel name is correct\n"
                         "3. The file size is within Telegram's limits"
                )
        except Exception as e:
            logger.error(f"Channel upload failed: {str(e)}")
            log_user_action(update.effective_user, "Upload error", f"Error: {str(e)}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"❌ Upload failed with error: {str(e)}\n"
                     "Please try again later or contact support if the issue persists."
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
        log_user_action(update.effective_user, "Download error", f"Error: {str(e)}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Something went wrong. Please try again later."
        )

# Command handler for /help
async def help_command(update, context):
    """
    Handles the /help command with a list of available commands.
    """
    user = update.effective_user
    log_user_action(user, "Requested help")
    help_message = (
        "🤖 Available Commands:\n\n"
        "📝 /start - Start the bot and see welcome message\n"
        "📥 /download <url> - Download and upload a file\n"
        "📋 /list - Show your active downloads\n"
        "❌ /cancel <id> - Cancel an active download\n"
        "❓ /help - Show this help message\n\n"
        "Example usage:\n"
        "<code>/download https://example.com/file.pdf</code>\n"
        "<code>/cancel abc12345</code>"
    )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=help_message,
        parse_mode='HTML'
    )

# Command handler for /cancel
async def cancel_command(update, context):
    """
    Command handler for /cancel command.
    """
    user = update.effective_user
    if not context.args:
        log_user_action(user, "Attempted to cancel without ID")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Please provide a download ID to cancel.\n\n"
                 "Example:\n"
                 "`/cancel abc12345`"
        )
        return

    download_id = context.args[0]
    log_user_action(user, f"Attempted to cancel download", f"ID: {download_id}")

    if download_id not in active_downloads:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Invalid download ID or download not found."
        )
        return

    download_info = active_downloads[download_id]

    # Check if the user is the one who started the download
    if download_info['user_id'] != user.id:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ You can only cancel your own downloads."
        )
        return

    # Mark download as cancelled
    download_info['cancelled'] = True
    log_user_action(user, f"Cancelled download", f"ID: {download_id}, File: {download_info['filename']}")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🔄 Cancelling download {download_id}...\n"
             f"File: {download_info['filename']}"
    )

# Command handler for /list
async def list_downloads_command(update, context):
    """
    Command handler for /list command to show active downloads.
    """
    user = update.effective_user
    log_user_action(user, "Listed downloads")
    user_id = user.id

    # Filter downloads for this user
    user_downloads = {
        download_id: info for download_id, info in active_downloads.items()
        if info['user_id'] == user_id and not info.get('completed', False)
    }

    if not user_downloads:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📭 No active downloads found."
        )
        return

    # Create message with download information
    message = "📥 Your Active Downloads:\n\n"
    for download_id, info in user_downloads.items():
        try:
            progress = int((info['downloaded'] / info['total_size']) * 100) if info['total_size'] > 0 else 0
            elapsed_time = datetime.now() - info['start_time']
            elapsed_minutes = int(elapsed_time.total_seconds() / 60)

            # Calculate download speed
            if elapsed_minutes > 0:
                speed = info['downloaded'] / elapsed_minutes / 60  # bytes per second
                speed_str = f"⚡ {format_size(speed)}/s"
            else:
                speed_str = "⚡ Calculating..."

            message += (
                f"🆔 ID: {download_id}\n"
                f"📁 File: {info['filename']}\n"
                f"📊 Progress: {progress}%\n"
                f"⬇️ Downloaded: {format_size(info['downloaded'])} / {format_size(info['total_size'])}\n"
                f"⏱️ Time: {elapsed_minutes} minutes\n"
                f"{speed_str}\n"
                f"❌ Cancel: /cancel {download_id}\n\n"
            )
        except Exception as e:
            logger.error(f"Error formatting download info for {download_id}: {str(e)}")
            continue

    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message
        )
    except Exception as e:
        logger.error(f"Error sending list message: {str(e)}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Error showing downloads. Please try again."
        )

# Error handler
async def error(update, context):
    """
    Logs errors caused by updates.
    """
    try:
        if update:
            logger.error(f"Update {update} caused error {context.error}")
        else:
            logger.error(f"Error: {context.error}")
    except Exception as e:
        logger.error(f"Error in error handler: {str(e)}")

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
                 "Type /help to see all available commands."
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
                 "Type /help to see all available commands."
        )

def signal_handler(signum, frame):
    """
    Handle shutdown signals
    """
    print("\n" + "="*50)
    print("Bot is shutting down...")
    print("="*50 + "\n")

    try:
        # First close any open file handles
        for download_id, download_info in list(active_downloads.items()):
            if 'file_handle' in download_info and download_info['file_handle'] is not None:
                try:
                    download_info['file_handle'].close()
                    logger.info(f"Closed file handle for {download_info['filepath']}")
                except Exception as e:
                    logger.error(f"Error closing file handle: {str(e)}")

        # Clean up any active downloads
        for download_id, download_info in list(active_downloads.items()):
            # Mark downloads as cancelled to stop download loops
            download_info['cancelled'] = True

            try:
                if os.path.exists(download_info['filepath']):
                    # Try with a small delay to ensure file handles are released
                    time.sleep(0.5)
                    os.remove(download_info['filepath'])
                    logger.info(f"Cleaned up file during shutdown: {download_info['filepath']}")
            except Exception as e:
                logger.error(f"Error cleaning up file during shutdown: {str(e)}")
                # Don't raise the exception, just log it
    except Exception as e:
        logger.error(f"Error during shutdown cleanup: {str(e)}")

    # Exit without using sys.exit which can cause issues
    os._exit(0)

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
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("download", download_command))
        application.add_handler(CommandHandler("cancel", cancel_command))
        application.add_handler(CommandHandler("list", list_downloads_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_error_handler(error)

        # Print startup message
        print("\n" + "="*50)
        print("🤖 Telegram File Download Bot")
        print("="*50)
        print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📢 Channel: {CHANNEL_NAME}")
        print("="*50)
        print("Bot is running... Press Ctrl+C to stop")
        print("="*50 + "\n")

        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start the bot with drop_pending_updates=True to avoid conflicts
        logger.info("Starting bot...")
        application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

    except Exception as e:
        logger.error(f"Error starting bot: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    try:
        # Set up asyncio policy for Windows
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        main()
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        sys.exit(1)
