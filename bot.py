import logging
import os
import requests
from telegram import Bot
from telegram.ext import CommandHandler, MessageHandler, filters, Application
from urllib.parse import urlparse, unquote

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

    Args:
        url (str): The URL of the file.

    Returns:
        str: The filename, or None if it cannot be extracted.
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

# Function to download file
async def download_file(url):
    """
    Downloads a file from a given URL.
    """
    try:
        # Remove @ if present at the start of URL
        if url.startswith('@'):
            url = url[1:]

        logger.info(f"Starting download from URL: {url}")

        # Add headers to mimic a browser request
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, stream=True, headers=headers)
        response.raise_for_status()

        # Get content type
        content_type = response.headers.get('content-type', '')
        logger.info(f"Content type: {content_type}")

        # Determine file extension from content type or URL
        if 'image/png' in content_type or url.endswith('.png'):
            extension = '.png'
        elif 'image/jpeg' in content_type or url.endswith('.jpg'):
            extension = '.jpg'
        elif 'image/gif' in content_type or url.endswith('.gif'):
            extension = '.gif'
        else:
            extension = '.png'  # default to png for your case

        filename = f"downloaded_image{extension}"
        filepath = filename

        logger.info(f"Saving file as: {filepath}")
        with open(filepath, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)

        file_size = os.path.getsize(filepath)
        logger.info(f"File downloaded successfully. Size: {file_size} bytes")
        return filepath, filename
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
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
            # For PNG files, we'll try sending as photo first
            if filename.lower().endswith('.png'):
                try:
                    logger.info("Attempting to send as photo")
                    await bot.send_photo(
                        chat_id=CHANNEL_NAME,
                        photo=file,
                        caption=f"Uploaded image from {filename}"
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
        "1. Simply send me any URL containing a file\n"
        "2. I'll download the file\n"
        "3. Upload it to the specified channel\n"
        "4. Clean up after myself\n\n"
        "❓ Need help? Just send me a URL and I'll handle the rest!\n"
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

# Message handler for URLs
async def handle_url(update, context):
    """
    Handles messages containing URLs.
    """
    url = update.message.text.strip()
    logger.info(f"Processing URL: {url}")

    await context.bot.send_message(chat_id=update.effective_chat.id, text="Downloading file...")

    try:
        filepath, filename = await download_file(url)
        if filepath:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="File downloaded. Uploading to channel...")

            # Log channel information
            logger.info(f"Attempting to upload to channel: {CHANNEL_NAME}")

            success = await upload_file_to_channel(context.bot, filepath, filename)
            if success:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="File uploaded successfully!")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="Failed to upload file to channel. Please check bot permissions.")

            # Clean up
            try:
                os.remove(filepath)
                logger.info(f"Cleaned up file: {filepath}")
            except Exception as e:
                logger.error(f"Error cleaning up file: {e}")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Error downloading file.")
    except Exception as e:
        logger.error(f"Error in handle_url: {str(e)}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"An error occurred: {str(e)}")

# Error handler
async def error(update, context):
    """
    Logs errors caused by updates.
    """
    logger.error(f"Update {update} caused error {context.error}")

def main():
    """
    Starts the Telegram bot.
    """
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_error_handler(error)

    # Start the Bot
    application.run_polling()

if __name__ == '__main__':
    main()
