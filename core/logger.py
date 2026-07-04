import logging
import os
from logging.handlers import RotatingFileHandler

# --- ПРОФЕССИОНАЛЬНАЯ НАСТРОЙКА ЛОГГИРОВАНИЯ ---

# 1. Создаем директорию для логов, если она не существует
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# 2. Создаем основной логгер для проекта
logger = logging.getLogger('sniper_bot')
logger.setLevel(logging.INFO)

# 3. Создаем форматтер для сообщений
formatter = logging.Formatter(
    '[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s'
)

# 4. Настраиваем обработчик для вывода в консоль (StreamHandler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# 5. Настраиваем обработчик для записи в файл с ротацией (RotatingFileHandler)
file_handler = RotatingFileHandler(
    os.path.join(log_dir, 'sniper.log'), 
    maxBytes=5*1024*1024,  # 5 MB
    backupCount=3,
    encoding='utf-8'
)
file_handler.setFormatter(formatter)

# 6. Добавляем обработчики к логгеру (с проверкой, чтобы избежать дублирования)
if not logger.handlers:
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)