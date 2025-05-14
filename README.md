# Telegram File Download Bot

A Telegram bot that downloads files from URLs and uploads them to a specified channel.

## Features
- Download files from URLs
- Upload files to Telegram channel
- Progress visualization during download
- Support for various file types
- Automatic file cleanup

## Setup
1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure the bot:
   - Get a bot token from [@BotFather](https://t.me/BotFather)
   - Add your bot token in `bot.py`
   - Set your channel name in `bot.py`
   - Make the bot an admin in your channel

3. Run the bot:
```bash
python bot.py
```

## Usage
1. Start the bot with `/start`
2. Send any URL containing a file
3. The bot will download the file and upload it to your channel

## Requirements
- Python 3.7 or higher
- Dependencies listed in requirements.txt
