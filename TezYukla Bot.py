#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# TezYuklaBot.py - Instagram va YouTube video/rasm yuklab beruvchi Telegram bot

import asyncio
import logging
import sqlite3
import re
import os
import subprocess
import tempfile
import shutil
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
from contextlib import contextmanager
from collections import defaultdict
import aiohttp
import json

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, InputMediaPhoto, InputMediaVideo
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest

# =============== LOGGING SOZLAMALARI ===============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============== KONFIGURATSIYA ===============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8644529147:AAFB_TWdhxCNOMdZv64CUUU3RTt-yON76QQ")
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "7710687157").split(",")]
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "TezYuklaUzBot")

# =============== BOT SOZLAMALARI ===============
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# =============== HOLATLAR (FSM) ===============
class BroadcastState(StatesGroup):
    waiting_for_message = State()

class AddChannelState(StatesGroup):
    waiting_for_channel = State()

class AdState(StatesGroup):
    waiting_for_message = State()

class BanState(StatesGroup):
    waiting_for_user_id = State()

class UnbanState(StatesGroup):
    waiting_for_user_id = State()

class YouTubeFormatState(StatesGroup):
    waiting_for_choice = State()

# =============== MA'LUMOTLAR BAZASI (SQLite) ===============
DB_NAME = "tezyukla.db"

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"DB error: {e}")
        raise
    finally:
        conn.close()

def init_database():
    """Initialize database with all required tables and columns"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                fullname TEXT,
                joined_date TIMESTAMP,
                last_activity TIMESTAMP,
                is_banned INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0
            )
        ''')
        
        # Create insta_links table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS insta_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                link TEXT,
                link_type TEXT,
                timestamp TIMESTAMP,
                success INTEGER DEFAULT 0,
                error_message TEXT
            )
        ''')
        
        # Create youtube_links table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS youtube_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                link TEXT,
                format_type TEXT,
                timestamp TIMESTAMP,
                success INTEGER DEFAULT 0,
                error_message TEXT
            )
        ''')
        
        # Create channels table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_name TEXT UNIQUE
            )
        ''')
        
        # Create bot_status table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_status (
                id INTEGER PRIMARY KEY,
                is_active INTEGER DEFAULT 1
            )
        ''')
        cursor.execute('INSERT OR IGNORE INTO bot_status (id, is_active) VALUES (1, 1)')
        
        # Create user_requests table for anti-spam
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_requests (
                user_id INTEGER,
                request_time TIMESTAMP,
                PRIMARY KEY (user_id, request_time)
            )
        ''')
        
        # Create bot_logs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_type TEXT,
                message TEXT,
                timestamp TIMESTAMP
            )
        ''')
        
        conn.commit()
        logger.info("Database initialized successfully")

def add_log(log_type: str, message: str):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO bot_logs (log_type, message, timestamp) VALUES (?, ?, ?)',
                (log_type, message[:500], datetime.now())
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Add log error: {e}")

# =============== MEDIA CAPTION VA KEYBOARD ===============
def get_media_caption() -> str:
    """Return caption text for media messages"""
    return f"🚀 @{CHANNEL_USERNAME}\n\n🥇 Birinchi raqamli yuklovchi bot"

def get_media_keyboard() -> InlineKeyboardMarkup:
    """Return inline keyboard for media messages"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Guruhda ishlatish", url=f"https://t.me/{CHANNEL_USERNAME}?startgroup=true"),
            InlineKeyboardButton(text="📢 Kanal", url=f"https://t.me/{CHANNEL_USERNAME}")
        ],
        [
            InlineKeyboardButton(text="🗑 O'chirish", callback_data="delete_media")
        ]
    ])
    return keyboard

@dp.callback_query(F.data == "delete_media")
async def delete_media_callback(callback: CallbackQuery):
    """Delete the media message"""
    try:
        await callback.message.delete()
        await callback.answer("✅ Xabar o'chirildi!", show_alert=False)
    except Exception as e:
        logger.error(f"Delete media error: {e}")
        await callback.answer("❌ O'chirib bo'lmadi!", show_alert=True)

# =============== ADMIN PANEL INLINE TUGMALARI ===============
def get_admin_keyboard():
    buttons = [
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="👤 Userlar ro'yxati", callback_data="admin_users")],
        [InlineKeyboardButton(text="🚫 Ban / Unban", callback_data="admin_ban")],
        [InlineKeyboardButton(text="🔗 Loglar", callback_data="admin_logs")],
        [InlineKeyboardButton(text="⚙️ Bot ON/OFF", callback_data="admin_toggle")],
        [InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="admin_add_channel")],
        [InlineKeyboardButton(text="➖ Kanal o'chirish", callback_data="admin_remove_channel")],
        [InlineKeyboardButton(text="📣 Reklama yuborish", callback_data="admin_ad")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# =============== KANALGA OBUNA TEKSHIRISH ===============
async def check_user_subscription(user_id: int) -> bool:
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT channel_name FROM channels')
            channels = cursor.fetchall()
        
        if not channels:
            return True
        
        for channel in channels:
            channel_name = channel['channel_name']
            try:
                member = await bot.get_chat_member(chat_id=channel_name, user_id=user_id)
                if member.status in ['left', 'kicked']:
                    return False
            except Exception as e:
                logger.error(f"Subscription check error for {channel_name}: {e}")
                continue
        
        return True
    except Exception as e:
        logger.error(f"Check subscription error: {e}")
        return True

def get_subscription_keyboard():
    try:
        buttons = []
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT channel_name FROM channels')
            channels = cursor.fetchall()
        
        for channel in channels:
            channel_name = channel['channel_name']
            clean_name = channel_name[1:] if channel_name.startswith('@') else channel_name
            buttons.append([InlineKeyboardButton(
                text=f"📢 {channel_name} ga obuna bo'lish",
                url=f"https://t.me/{clean_name}"
            )])
        
        buttons.append([InlineKeyboardButton(text="🤖 Tekshirish", callback_data="check_subscription")])
        
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    except Exception as e:
        logger.error(f"Get subscription keyboard error: {e}")
        return InlineKeyboardMarkup(inline_keyboard=[])

# =============== ANTI-SPAM ===============
class AntiSpam:
    def __init__(self, max_requests: int = 3, time_window: int = 30):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = defaultdict(list)
    
    def is_spam(self, user_id: int) -> bool:
        now = datetime.now()
        user_requests = self.requests[user_id]
        
        user_requests = [t for t in user_requests if now - t < timedelta(seconds=self.time_window)]
        self.requests[user_id] = user_requests
        
        if len(user_requests) >= self.max_requests:
            return True
        
        self.requests[user_id].append(now)
        return False
    
    def clear_user(self, user_id: int):
        if user_id in self.requests:
            del self.requests[user_id]

anti_spam = AntiSpam()

# =============== URL DETEKTOR ===============
def detect_platform(url: str) -> str:
    """Detect if URL is from Instagram or YouTube"""
    url_lower = url.lower()
    if 'instagram.com' in url_lower or 'instagr.am' in url_lower:
        return 'instagram'
    elif 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    return 'unknown'

def clean_instagram_url(url: str) -> str:
    """Remove tracking parameters from Instagram URL"""
    try:
        if '?' in url:
            base_url = url.split('?')[0]
            return base_url
        return url
    except Exception:
        return url

def extract_shortcode(url: str) -> Optional[str]:
    """Extract shortcode from Instagram URL"""
    patterns = [
        r'instagram\.com/p/([^/?]+)',
        r'instagram\.com/reel/([^/?]+)',
        r'instagram\.com/stories/[^/]+/([^/?]+)',
        r'instagr\.am/p/([^/?]+)',
        r'instagr\.am/reel/([^/?]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def extract_youtube_id(url: str) -> Optional[str]:
    """Extract video ID from YouTube URL"""
    patterns = [
        r'youtube\.com/watch\?v=([^&]+)',
        r'youtu\.be/([^?]+)',
        r'youtube\.com/embed/([^?]+)',
        r'youtube\.com/v/([^?]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

# =============== INSTAGRAM YUKLAB BERISH ===============
async def download_instagram_content(url: str) -> Tuple[bool, Union[List[str], None], str]:
    """Instagram content download function"""
    temp_dir = None
    try:
        clean_url = clean_instagram_url(url)
        logger.info(f"Downloading Instagram: {clean_url}")
        
        temp_dir = tempfile.mkdtemp()
        output_template = os.path.join(temp_dir, '%(title)s_%(id)s.%(ext)s')
        
        cmd = [
            'yt-dlp',
            '--no-playlist',
            '--no-warnings',
            '--no-check-certificate',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            '--format', 'best[ext=mp4]/best',
            '--output', output_template,
            '--restrict-filenames',
            '--retries', '5',
            '--fragment-retries', '5',
            '--no-cache-dir',
            clean_url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60.0)
        except asyncio.TimeoutError:
            process.kill()
            return False, None, "❌ Yuklab olish vaqti tugadi"
        
        stderr_text = stderr.decode('utf-8', errors='ignore') if stderr else ""
        
        if process.returncode != 0:
            error_lower = stderr_text.lower()
            
            if "private" in error_lower:
                return False, None, "❌ Bu post yopiq akkauntga tegishli"
            elif "not found" in error_lower or "deleted" in error_lower:
                return False, None, "❌ Post topilmadi yoki o'chirilgan"
            elif "rate limit" in error_lower or "too many" in error_lower:
                return False, None, "❌ Instagram cheklov qo'ydi. Biroz kuting"
            else:
                add_log("DOWNLOAD_ERROR", f"Instagram failed: {stderr_text[:200]}")
                return False, None, "❌ Yuklab olishda xatolik"
        
        files = []
        for root, dirs, filenames in os.walk(temp_dir):
            for filename in filenames:
                if filename.endswith(('.mp4', '.mov', '.avi', '.mkv', '.jpg', '.jpeg', '.png', '.webp')):
                    files.append(os.path.join(root, filename))
        
        if not files:
            return False, None, "❌ Fayl topilmadi"
        
        files.sort()
        add_log("DOWNLOAD_SUCCESS", f"Instagram: {len(files)} file(s)")
        return True, files, ""
            
    except Exception as e:
        logger.error(f"Instagram download error: {e}")
        return False, None, f"❌ Xatolik: {str(e)[:100]}"

# =============== YOUTUBE YUKLAB BERISH ===============
async def download_youtube_video(url: str) -> Tuple[bool, Optional[str], str]:
    """Download YouTube video as MP4"""
    temp_dir = None
    try:
        logger.info(f"Downloading YouTube video: {url}")
        
        temp_dir = tempfile.mkdtemp()
        output_template = os.path.join(temp_dir, '%(title)s_%(id)s.%(ext)s')
        
        cmd = [
            'yt-dlp',
            '-f', 'best[ext=mp4]/best',
            '--no-playlist',
            '--no-warnings',
            '--no-check-certificate',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            '--output', output_template,
            '--restrict-filenames',
            '--retries', '5',
            '--no-cache-dir',
            url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120.0)
        except asyncio.TimeoutError:
            process.kill()
            return False, None, "❌ Yuklab olish vaqti tugadi"
        
        stderr_text = stderr.decode('utf-8', errors='ignore') if stderr else ""
        
        if process.returncode != 0:
            error_lower = stderr_text.lower()
            if "private" in error_lower:
                return False, None, "❌ Video yopiq"
            elif "not found" in error_lower or "deleted" in error_lower:
                return False, None, "❌ Video topilmadi yoki o'chirilgan"
            else:
                add_log("DOWNLOAD_ERROR", f"YouTube video failed: {stderr_text[:200]}")
                return False, None, "❌ Video yuklab olishda xatolik"
        
        files = []
        for root, dirs, filenames in os.walk(temp_dir):
            for filename in filenames:
                if filename.endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    files.append(os.path.join(root, filename))
        
        if not files:
            return False, None, "❌ Video fayl topilmadi"
        
        add_log("DOWNLOAD_SUCCESS", f"YouTube video: {files[0]}")
        return True, files[0], ""
            
    except Exception as e:
        logger.error(f"YouTube video download error: {e}")
        return False, None, f"❌ Xatolik: {str(e)[:100]}"

async def download_youtube_audio(url: str) -> Tuple[bool, Optional[str], str]:
    """Download YouTube audio as MP3"""
    temp_dir = None
    try:
        logger.info(f"Downloading YouTube audio: {url}")
        
        temp_dir = tempfile.mkdtemp()
        output_template = os.path.join(temp_dir, '%(title)s_%(id)s.%(ext)s')
        
        cmd = [
            'yt-dlp',
            '-f', 'bestaudio',
            '--extract-audio',
            '--audio-format', 'mp3',
            '--audio-quality', '0',
            '--no-playlist',
            '--no-warnings',
            '--no-check-certificate',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            '--output', output_template,
            '--restrict-filenames',
            '--retries', '5',
            '--no-cache-dir',
            url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120.0)
        except asyncio.TimeoutError:
            process.kill()
            return False, None, "❌ Yuklab olish vaqti tugadi"
        
        stderr_text = stderr.decode('utf-8', errors='ignore') if stderr else ""
        
        if process.returncode != 0:
            error_lower = stderr_text.lower()
            if "private" in error_lower:
                return False, None, "❌ Video yopiq"
            elif "not found" in error_lower or "deleted" in error_lower:
                return False, None, "❌ Video topilmadi yoki o'chirilgan"
            else:
                add_log("DOWNLOAD_ERROR", f"YouTube audio failed: {stderr_text[:200]}")
                return False, None, "❌ Audio yuklab olishda xatolik"
        
        files = []
        for root, dirs, filenames in os.walk(temp_dir):
            for filename in filenames:
                if filename.endswith('.mp3'):
                    files.append(os.path.join(root, filename))
        
        if not files:
            return False, None, "❌ Audio fayl topilmadi"
        
        add_log("DOWNLOAD_SUCCESS", f"YouTube audio: {files[0]}")
        return True, files[0], ""
            
    except Exception as e:
        logger.error(f"YouTube audio download error: {e}")
        return False, None, f"❌ Xatolik: {str(e)[:100]}"

async def detect_instagram_type(url: str) -> str:
    url_lower = url.lower()
    if '/reel/' in url_lower:
        return "reel"
    elif '/stories/' in url_lower:
        return "story"
    elif '/p/' in url_lower:
        return "post"
    return "unknown"

async def cleanup_temp_files(file_paths: List[str]):
    try:
        for file_path in file_paths:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        
        if file_paths:
            dir_path = os.path.dirname(file_paths[0])
            if dir_path and os.path.exists(dir_path):
                shutil.rmtree(dir_path, ignore_errors=True)
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

# =============== BOT FUNKSIYALARI ===============
async def save_user_info(message: Message):
    try:
        user = message.from_user
        fullname = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username or str(user.id)
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user.id,))
            exists = cursor.fetchone()
            
            if not exists:
                cursor.execute('''
                    INSERT INTO users (user_id, username, first_name, last_name, fullname, joined_date, last_activity, is_banned, is_admin)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user.id,
                    user.username or "",
                    user.first_name or "",
                    user.last_name or "",
                    fullname,
                    datetime.now(),
                    datetime.now(),
                    0,
                    1 if user.id in ADMIN_IDS else 0
                ))
            else:
                cursor.execute('''
                    UPDATE users 
                    SET username = ?, first_name = ?, last_name = ?, fullname = ?, last_activity = ?
                    WHERE user_id = ?
                ''', (user.username or "", user.first_name or "", user.last_name or "", fullname, datetime.now(), user.id))
            
            conn.commit()
    except Exception as e:
        logger.error(f"Save user info error: {e}")

async def is_user_banned(user_id: int) -> bool:
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT is_banned FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return bool(result['is_banned']) if result else False
    except Exception as e:
        logger.error(f"Check user banned error: {e}")
        return False

async def is_bot_active() -> bool:
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT is_active FROM bot_status WHERE id = 1')
            result = cursor.fetchone()
            return bool(result['is_active']) if result else True
    except Exception as e:
        logger.error(f"Check bot active error: {e}")
        return True

# =============== SEND MEDIA WITH CAPTION AND KEYBOARD ===============
async def send_media_with_caption(message: Message, file_path: str, is_video: bool = True):
    """Send video or photo with caption and keyboard"""
    caption = get_media_caption()
    reply_markup = get_media_keyboard()
    
    if is_video:
        video_file = FSInputFile(file_path)
        await message.answer_video(video_file, caption=caption, reply_markup=reply_markup)
    else:
        photo_file = FSInputFile(file_path)
        await message.answer_photo(photo_file, caption=caption, reply_markup=reply_markup)

async def send_media_group_with_caption(message: Message, file_paths: List[str]):
    """Send multiple media files with caption"""
    media_group = []
    for fp in file_paths[:10]:
        ext = os.path.splitext(fp)[1].lower()
        if ext in ['.mp4', '.mov', '.avi', '.mkv']:
            media_group.append(InputMediaVideo(media=FSInputFile(fp)))
        else:
            media_group.append(InputMediaPhoto(media=FSInputFile(fp)))
    
    if media_group:
        await message.answer_media_group(media_group)
        caption = get_media_caption()
        reply_markup = get_media_keyboard()
        await message.answer(caption, reply_markup=reply_markup)

# =============== HANDLERS ===============
@dp.message(CommandStart())
async def start_command(message: Message):
    try:
        await save_user_info(message)
        
        if await is_user_banned(message.from_user.id):
            await message.answer("❌ Siz bloklangansiz!")
            return
        
        if not await check_user_subscription(message.from_user.id):
            await message.answer(
                "📢 Kanalga obuna bo'ling!",
                reply_markup=get_subscription_keyboard()
            )
            return
        
        welcome_text = """
🤖 **TezYuklaBot**

📥 Instagram va YouTube video yuklab beruvchi bot.

📤 **Ishlatish:** Instagram yoki YouTube linkini yuboring

✅ **Qo'llab-quvvatlanadi:**
• 📸 Instagram Post/Reel/Story
• 🎥 YouTube Video
• 🎵 YouTube Audio (MP3)
        """
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ℹ️ Yordam", callback_data="help")]
        ])
        
        await message.answer(welcome_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Start error: {e}")

@dp.message(Command("admin"))
async def admin_panel_command(message: Message):
    try:
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("❌ Bu buyruq faqat adminlar uchun!")
            return
        
        admin_text = "👨‍💻 **Admin Panel**"
        await message.answer(admin_text, reply_markup=get_admin_keyboard(), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Admin error: {e}")

@dp.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery):
    try:
        help_text = """
📖 **Yordam:**

1. Instagram/YouTube'dan linkni nusxalang
2. Botga linkni yuboring
3. Bot avtomatik yuklab beradi

📌 **Misol linklar:**
• https://www.instagram.com/p/...
• https://www.instagram.com/reel/...
• https://youtube.com/watch?v=...
• https://youtu.be/...

⚠️ **Cheklovlar:**
• 30 sekundda 3 tadan ko'p so'rov yubormang
• Yopiq akkauntlar ishlamaydi
        """
        await callback.message.edit_text(help_text, parse_mode="Markdown")
        await callback.answer()
    except Exception as e:
        logger.error(f"Help error: {e}")

@dp.callback_query(F.data == "check_subscription")
async def check_subscription(callback: CallbackQuery):
    try:
        if await check_user_subscription(callback.from_user.id):
            await callback.message.delete()
            await start_command(callback.message)
        else:
            await callback.answer("❌ Obuna bo'lmagansiz!", show_alert=True)
    except Exception as e:
        logger.error(f"Check subscription error: {e}")

# =============== YOUTUBE FORMAT SELECTION ===============
@dp.callback_query(F.data == "youtube_video")
async def youtube_video_callback(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        url = data.get('youtube_url')
        
        if not url:
            await callback.answer("❌ Xatolik yuz berdi!", show_alert=True)
            await state.clear()
            return
        
        await callback.message.edit_text("⏳ Video yuklanmoqda...")
        
        success, file_path, error = await download_youtube_video(url)
        
        if success and file_path:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO youtube_links (user_id, link, format_type, timestamp, success)
                    VALUES (?, ?, ?, ?, ?)
                ''', (callback.from_user.id, url, "video", datetime.now(), 1))
                conn.commit()
            
            await send_media_with_caption(callback.message, file_path, is_video=True)
            await cleanup_temp_files([file_path])
        else:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO youtube_links (user_id, link, format_type, timestamp, success, error_message)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (callback.from_user.id, url, "video", datetime.now(), 0, error[:200]))
                conn.commit()
            await callback.message.answer(error)
        
        await state.clear()
        await callback.answer()
    except Exception as e:
        logger.error(f"YouTube video error: {e}")
        await callback.message.answer("❌ Xatolik yuz berdi!")
        await state.clear()

@dp.callback_query(F.data == "youtube_audio")
async def youtube_audio_callback(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        url = data.get('youtube_url')
        
        if not url:
            await callback.answer("❌ Xatolik yuz berdi!", show_alert=True)
            await state.clear()
            return
        
        await callback.message.edit_text("⏳ Audio yuklanmoqda...")
        
        success, file_path, error = await download_youtube_audio(url)
        
        if success and file_path:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO youtube_links (user_id, link, format_type, timestamp, success)
                    VALUES (?, ?, ?, ?, ?)
                ''', (callback.from_user.id, url, "audio", datetime.now(), 1))
                conn.commit()
            
            audio_file = FSInputFile(file_path)
            caption = get_media_caption()
            reply_markup = get_media_keyboard()
            await callback.message.answer_audio(audio_file, caption=caption, reply_markup=reply_markup)
            await cleanup_temp_files([file_path])
        else:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO youtube_links (user_id, link, format_type, timestamp, success, error_message)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (callback.from_user.id, url, "audio", datetime.now(), 0, error[:200]))
                conn.commit()
            await callback.message.answer(error)
        
        await state.clear()
        await callback.answer()
    except Exception as e:
        logger.error(f"YouTube audio error: {e}")
        await callback.message.answer("❌ Xatolik yuz berdi!")
        await state.clear()

# =============== MAIN MESSAGE HANDLER ===============
@dp.message(F.text)
async def handle_message(message: Message, state: FSMContext):
    file_paths = []
    try:
        if not await is_bot_active():
            await message.answer("⚠️ Bot vaqtincha ishlamayapti!")
            return
        
        if await is_user_banned(message.from_user.id):
            await message.answer("❌ Siz bloklangansiz!")
            return
        
        if not await check_user_subscription(message.from_user.id):
            await message.answer(
                "📢 Kanalga obuna bo'ling!",
                reply_markup=get_subscription_keyboard()
            )
            return
        
        if anti_spam.is_spam(message.from_user.id):
            await message.answer("⚠️ Juda ko'p so'rov! 30 soniya kuting.")
            return
        
        url = message.text.strip()
        
        # Check if URL contains Instagram or YouTube
        if not ('instagram.com' in url or 'instagr.am' in url or 'youtube.com' in url or 'youtu.be' in url):
            await message.answer("❌ Faqat Instagram yoki YouTube linki yuboring!\nYoki /start")
            return
        
        platform = detect_platform(url)
        
        if platform == 'instagram':
            link_type = await detect_instagram_type(url)
            
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO insta_links (user_id, link, link_type, timestamp, success)
                    VALUES (?, ?, ?, ?, ?)
                ''', (message.from_user.id, url, link_type, datetime.now(), 0))
                link_id = cursor.lastrowid
                conn.commit()
            
            status_msg = await message.answer("⏳ Instagram yuklanmoqda...")
            
            success, result, error_msg = await download_instagram_content(url)
            
            if success and result:
                file_paths = result
                
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute('UPDATE insta_links SET success = 1 WHERE id = ?', (link_id,))
                    conn.commit()
                
                if len(file_paths) == 1:
                    file_path = file_paths[0]
                    ext = os.path.splitext(file_path)[1].lower()
                    is_video = ext in ['.mp4', '.mov', '.avi', '.mkv']
                    await send_media_with_caption(message, file_path, is_video)
                else:
                    await send_media_group_with_caption(message, file_paths)
            else:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute('UPDATE insta_links SET error_message = ? WHERE id = ?', (error_msg[:200], link_id))
                    conn.commit()
                await message.answer(error_msg)
            
            await status_msg.delete()
            
        elif platform == 'youtube':
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🎥 Video", callback_data="youtube_video"),
                    InlineKeyboardButton(text="🎵 MP3 Audio", callback_data="youtube_audio")
                ]
            ])
            
            await state.update_data(youtube_url=url)
            await message.answer("📥 Qaysi formatni tanlang:", reply_markup=keyboard)
            return
            
        else:
            await message.answer("❌ Faqat Instagram yoki YouTube linki yuboring!")
            return
        
        if file_paths:
            await cleanup_temp_files(file_paths)
            
    except Exception as e:
        logger.error(f"Handle error: {e}")
        await message.answer("❌ Xatolik yuz berdi!")
        if file_paths:
            await cleanup_temp_files(file_paths)

# =============== ADMIN CALLBACKS ===============
@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as total FROM users')
            total = cursor.fetchone()['total']
            
            cursor.execute('SELECT COUNT(*) as banned FROM users WHERE is_banned = 1')
            banned = cursor.fetchone()['banned']
            
            cursor.execute('SELECT COUNT(*) as insta_links FROM insta_links')
            insta_links = cursor.fetchone()['insta_links']
            
            cursor.execute('SELECT COUNT(*) as insta_success FROM insta_links WHERE success = 1')
            insta_success = cursor.fetchone()['insta_success']
            
            cursor.execute('SELECT COUNT(*) as youtube_links FROM youtube_links')
            youtube_links = cursor.fetchone()['youtube_links']
            
            cursor.execute('SELECT COUNT(*) as youtube_success FROM youtube_links WHERE success = 1')
            youtube_success = cursor.fetchone()['youtube_success']
        
        text = f"""
📊 **Statistika**

👥 **Foydalanuvchilar:**
• Jami: {total}
• Bloklangan: {banned}
• Aktiv: {total - banned}

📸 **Instagram:**
• Jami: {insta_links}
• Muvaffaqiyatli: {insta_success}
• Daraja: {round((insta_success/insta_links*100) if insta_links > 0 else 0, 1)}%

🎥 **YouTube:**
• Jami: {youtube_links}
• Muvaffaqiyatli: {youtube_success}
• Daraja: {round((youtube_success/youtube_links*100) if youtube_links > 0 else 0, 1)}%
        """
        await callback.message.edit_text(text, parse_mode="Markdown")
        await callback.answer()
    except Exception as e:
        logger.error(f"Stats error: {e}")

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
            return
        
        await callback.message.edit_text("📢 Broadcast xabarini yuboring:")
        await state.set_state(BroadcastState.waiting_for_message)
        await callback.answer()
    except Exception as e:
        logger.error(f"Broadcast error: {e}")

@dp.message(BroadcastState.waiting_for_message)
async def send_broadcast(message: Message, state: FSMContext):
    try:
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("❌ Ruxsat yo'q!")
            await state.clear()
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM users WHERE is_banned = 0')
            users = cursor.fetchall()
        
        success = 0
        status = await message.answer(f"📤 Yuborilmoqda... 0/{len(users)}")
        
        for i, user in enumerate(users):
            try:
                if message.text:
                    await bot.send_message(user['user_id'], message.text)
                elif message.photo:
                    await bot.send_photo(user['user_id'], message.photo[-1].file_id, caption=message.caption)
                success += 1
            except:
                pass
            
            if (i + 1) % 10 == 0:
                await status.edit_text(f"📤 Yuborilmoqda... {i+1}/{len(users)} | ✅{success}")
        
        await status.edit_text(f"✅ Broadcast yakunlandi!\n✅ Yuborildi: {success}")
        add_log("BROADCAST", f"Sent to {success} users")
        await state.clear()
    except Exception as e:
        logger.error(f"Send broadcast error: {e}")
        await state.clear()

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, username, first_name, joined_date, is_banned FROM users ORDER BY joined_date DESC LIMIT 30')
            users = cursor.fetchall()
        
        text = "👥 **Oxirgi 30 foydalanuvchi:**\n\n"
        for u in users:
            status = "🚫" if u['is_banned'] else "✅"
            name = u['first_name'][:15] if u['first_name'] else "N/A"
            text += f"{status} `{u['user_id']}` | {name}\n"
        
        await callback.message.edit_text(text, parse_mode="Markdown")
        await callback.answer()
    except Exception as e:
        logger.error(f"Users error: {e}")

@dp.callback_query(F.data == "admin_ban")
async def admin_ban_menu(callback: CallbackQuery):
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Ban", callback_data="ban_user")],
            [InlineKeyboardButton(text="✅ Unban", callback_data="unban_user")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_back")]
        ])
        
        await callback.message.edit_text("🚫 **Ban/Unban**", reply_markup=keyboard, parse_mode="Markdown")
        await callback.answer()
    except Exception as e:
        logger.error(f"Ban menu error: {e}")

@dp.callback_query(F.data == "ban_user")
async def ban_user_prompt(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text("🚫 ID yuboring:\nMisol: 123456789")
        await state.set_state(BanState.waiting_for_user_id)
        await callback.answer()
    except Exception as e:
        logger.error(f"Ban prompt error: {e}")

@dp.callback_query(F.data == "unban_user")
async def unban_user_prompt(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text("✅ ID yuboring:")
        await state.set_state(UnbanState.waiting_for_user_id)
        await callback.answer()
    except Exception as e:
        logger.error(f"Unban prompt error: {e}")

@dp.message(BanState.waiting_for_user_id)
async def process_ban(message: Message, state: FSMContext):
    try:
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("❌ Ruxsat yo'q!")
            await state.clear()
            return
        
        if not message.text.isdigit():
            await message.answer("❌ Faqat raqam!")
            return
        
        user_id = int(message.text)
        
        if user_id in ADMIN_IDS:
            await message.answer("❌ Adminni ban qilish mumkin emas!")
            await state.clear()
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
            conn.commit()
        
        await message.answer(f"✅ {user_id} ban qilindi!")
        add_log("BAN", f"User {user_id} banned")
        anti_spam.clear_user(user_id)
        await state.clear()
    except Exception as e:
        logger.error(f"Process ban error: {e}")
        await state.clear()

@dp.message(UnbanState.waiting_for_user_id)
async def process_unban(message: Message, state: FSMContext):
    try:
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("❌ Ruxsat yo'q!")
            await state.clear()
            return
        
        if not message.text.isdigit():
            await message.answer("❌ Faqat raqam!")
            return
        
        user_id = int(message.text)
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
            conn.commit()
        
        await message.answer(f"✅ {user_id} unban qilindi!")
        add_log("UNBAN", f"User {user_id} unbanned")
        await state.clear()
    except Exception as e:
        logger.error(f"Process unban error: {e}")
        await state.clear()

@dp.callback_query(F.data == "admin_logs")
async def admin_logs(callback: CallbackQuery):
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT log_type, message, timestamp FROM bot_logs ORDER BY timestamp DESC LIMIT 20')
            logs = cursor.fetchall()
        
        text = "📋 **Oxirgi loglar:**\n\n"
        for log in logs:
            text += f"[{log['timestamp'][11:16]}] {log['log_type']}: {log['message'][:30]}\n"
        
        await callback.message.edit_text(text, parse_mode="Markdown")
        await callback.answer()
    except Exception as e:
        logger.error(f"Logs error: {e}")

@dp.callback_query(F.data == "admin_toggle")
async def admin_toggle(callback: CallbackQuery):
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
            return
        
        current = await is_bot_active()
        new_status = not current
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE bot_status SET is_active = ? WHERE id = 1', (1 if new_status else 0,))
            conn.commit()
        
        status = "ON" if new_status else "OFF"
        await callback.message.edit_text(f"⚙️ Bot {status}")
        add_log("BOT_TOGGLE", f"Toggled to {status}")
        await asyncio.sleep(2)
        await admin_panel_command(callback.message)
        await callback.answer()
    except Exception as e:
        logger.error(f"Toggle error: {e}")

@dp.callback_query(F.data == "admin_add_channel")
async def admin_add_channel(callback: CallbackQuery, state: FSMContext):
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
            return
        
        await callback.message.edit_text("➕ Kanal username:\nMisol: @my_channel")
        await state.set_state(AddChannelState.waiting_for_channel)
        await callback.answer()
    except Exception as e:
        logger.error(f"Add channel error: {e}")

@dp.message(AddChannelState.waiting_for_channel)
async def add_channel(message: Message, state: FSMContext):
    try:
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("❌ Ruxsat yo'q!")
            await state.clear()
            return
        
        channel = message.text.strip()
        if not channel.startswith('@'):
            channel = '@' + channel
        
        try:
            await bot.get_chat(channel)
        except:
            await message.answer(f"❌ Kanal topilmadi: {channel}")
            await state.clear()
            return
        
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT INTO channels (channel_name) VALUES (?)', (channel,))
                conn.commit()
            
            await message.answer(f"✅ Kanal qo'shildi: {channel}")
            add_log("CHANNEL_ADD", f"Added: {channel}")
        except sqlite3.IntegrityError:
            await message.answer(f"❌ Kanal mavjud: {channel}")
        
        await state.clear()
    except Exception as e:
        logger.error(f"Add channel error: {e}")
        await state.clear()

@dp.callback_query(F.data == "admin_remove_channel")
async def admin_remove_channel(callback: CallbackQuery):
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, channel_name FROM channels')
            channels = cursor.fetchall()
        
        if not channels:
            await callback.message.edit_text("❌ Kanal yo'q!")
            await asyncio.sleep(2)
            await admin_panel_command(callback.message)
            return
        
        buttons = []
        for ch in channels:
            buttons.append([InlineKeyboardButton(
                text=f"❌ {ch['channel_name']}",
                callback_data=f"remove_{ch['id']}"
            )])
        buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_back")])
        
        await callback.message.edit_text("➖ Kanal tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await callback.answer()
    except Exception as e:
        logger.error(f"Remove channel error: {e}")

@dp.callback_query(F.data.startswith("remove_"))
async def remove_channel(callback: CallbackQuery):
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
            return
        
        channel_id = int(callback.data.replace("remove_", ""))
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT channel_name FROM channels WHERE id = ?', (channel_id,))
            channel = cursor.fetchone()
            
            if channel:
                cursor.execute('DELETE FROM channels WHERE id = ?', (channel_id,))
                conn.commit()
                await callback.message.edit_text(f"✅ O'chirildi: {channel['channel_name']}")
                add_log("CHANNEL_REMOVE", f"Removed: {channel['channel_name']}")
        
        await asyncio.sleep(2)
        await admin_panel_command(callback.message)
        await callback.answer()
    except Exception as e:
        logger.error(f"Remove error: {e}")

@dp.callback_query(F.data == "admin_ad")
async def admin_ad(callback: CallbackQuery, state: FSMContext):
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
            return
        
        await callback.message.edit_text("📣 Reklama xabarini yuboring:")
        await state.set_state(AdState.waiting_for_message)
        await callback.answer()
    except Exception as e:
        logger.error(f"Admin ad error: {e}")

@dp.message(AdState.waiting_for_message)
async def send_ad(message: Message, state: FSMContext):
    try:
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("❌ Ruxsat yo'q!")
            await state.clear()
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM users WHERE is_banned = 0')
            users = cursor.fetchall()
        
        ad_text = "📣 **REKLAMA**\n\n"
        success = 0
        
        status = await message.answer(f"📤 Yuborilmoqda... 0/{len(users)}")
        
        for i, user in enumerate(users):
            try:
                if message.text:
                    await bot.send_message(user['user_id'], ad_text + message.text, parse_mode="Markdown")
                elif message.photo:
                    await bot.send_photo(user['user_id'], message.photo[-1].file_id, caption=ad_text + (message.caption or ""))
                success += 1
            except:
                pass
            
            if (i + 1) % 20 == 0:
                await status.edit_text(f"📤 Yuborilmoqda... {i+1}/{len(users)} | ✅{success}")
        
        await status.edit_text(f"✅ Reklama yuborildi!\n✅ {success} foydalanuvchiga")
        add_log("ADVERTISEMENT", f"Ad to {success} users")
        await state.clear()
    except Exception as e:
        logger.error(f"Send ad error: {e}")
        await state.clear()

@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    try:
        await admin_panel_command(callback.message)
        await callback.answer()
    except Exception as e:
        logger.error(f"Admin back error: {e}")

# =============== ERROR HANDLER ===============
@dp.errors()
async def error_handler(event, error):
    logger.error(f"Error: {error}")
    add_log("BOT_ERROR", str(error)[:200])
    return True

# =============== MAIN ===============
async def main():
    try:
        init_database()
        logger.info("Bot started!")
        print("=" * 50)
        print("✅ TezYuklaBot ishga tushdi!")
        print(f"👨‍💻 Admin: {ADMIN_IDS}")
        print(f"📢 Kanal: @{CHANNEL_USERNAME}")
        print("=" * 50)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Main error: {e}")

def run():
    """Main entry point"""
    print("🚀 TezYuklaBot...")
    print("=" * 50)
    
    try:
        result = subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ yt-dlp {result.stdout.strip()}")
        else:
            print("❌ yt-dlp error!")
            return
    except FileNotFoundError:
        print("❌ yt-dlp not installed!")
        print("Install: pip install yt-dlp")
        return
    
    try:
        import aiogram
        print("✅ aiogram installed")
    except ImportError:
        print("❌ aiogram not installed!")
        return
    
    print("=" * 50)
    asyncio.run(main())

if __name__ == "__main__":
    run()
