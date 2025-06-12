import os
import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from collections import Counter
import threading
import queue
import time
import re
from dataclasses import dataclass, asdict, field
from contextlib import contextmanager
import signal
import sys
import random

# Опциональные импорты с обработкой ошибок

try:
    import pyttsx3
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False
    print("⚠️  pyttsx3 не установлен. Голосовой вывод недоступен.")

try:
    import speech_recognition as sr
    SPEECH_RECOGNITION_AVAILABLE = True
except ImportError:
    SPEECH_RECOGNITION_AVAILABLE = False
    print("⚠️  speech_recognition не установлен. Голосовое распознавание недоступно.")

# Константы и настройки
MEMORY_FILE = "memory/memory.json"
MEMORY_LIMIT = 1000
MEMORY_KEEP = 800
VOICE_RATE = 150
VOICE_VOLUME = 0.8
LOG_FILE = "samuel.log"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class Thought:
    """Класс для представления мысли с улучшенной структурой данных"""
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    importance: float = 0.3
    tags: List[str] = field(default_factory=list)
    emotion: str = "neutral"
    context: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Thought':
        return cls(**data)

    def __str__(self) -> str:
        return f"[{self.emotion}] {self.content[:50]}{'...' if len(self.content) > 50 else ''}"

class VoiceManager:
    """Управление голосовыми функциями"""

    def __init__(self):
        self.voice_engine = None
        self.speech_recognizer = None
        self.voice_queue = queue.Queue()
        self.running = False

        if VOICE_AVAILABLE:
            try:
                self.voice_engine = pyttsx3.init()
                self._configure_voice()
            except Exception as e:
                logger.error(f"Ошибка инициализации голосового движка: {e}")

        if SPEECH_RECOGNITION_AVAILABLE:
            self.speech_recognizer = sr.Recognizer()
            self.speech_recognizer.energy_threshold = 300
            self.speech_recognizer.dynamic_energy_threshold = True

        self.speaker_thread = None
        self.listener_thread = None

    def _configure_voice(self):
        """Настройка параметров голоса"""
        if not self.voice_engine:
            return

        voices = self.voice_engine.getProperty('voices')
        # Попытка найти русский голос
        for voice in voices:
            if 'ru' in voice.id.lower() or 'russian' in voice.name.lower():
                self.voice_engine.setProperty('voice', voice.id)
                break

        self.voice_engine.setProperty('rate', VOICE_RATE)  # Скорость речи
        self.voice_engine.setProperty('volume', VOICE_VOLUME)  # Громкость

    def start(self, callback_fn=None):
        """Запуск голосовых потоков"""
        self.running = True

        if self.voice_engine:
            self.speaker_thread = threading.Thread(target=self._voice_speaker_thread, daemon=True)
            self.speaker_thread.start()

        if self.speech_recognizer and callback_fn:
            self.listener_thread = threading.Thread(target=self._voice_listener_thread, args=(callback_fn,), daemon=True)
            self.listener_thread.start()

    def _voice_speaker_thread(self):
        """Поток для воспроизведения речи"""
        while self.running:
            try:
                text = self.voice_queue.get(timeout=1)
                if text and self.voice_engine:
                    logger.info(f"🔊 Произношу: {text}")
                    self.voice_engine.say(text)
                    self.voice_engine.runAndWait()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Ошибка воспроизведения речи: {e}")

    def _voice_listener_thread(self, callback_fn):
        """Поток для распознавания речи"""
        if not self.speech_recognizer:
            return

        try:
            with sr.Microphone() as source:
                logger.info("🎤 Калибровка микрофона...")
                self.speech_recognizer.adjust_for_ambient_noise(source, duration=1)
                logger.info("🎤 Готов к прослушиванию")

                while self.running:
                    try:
                        audio = self.speech_recognizer.listen(
                            source, timeout=5, phrase_time_limit=10
                        )
                        user_input = self.speech_recognizer.recognize_google(
                            audio, language="ru-RU"
                        )
                        logger.info(f"🎤 Распознано: {user_input}")
                        callback_fn(user_input)

                    except sr.UnknownValueError:
                        pass  # Не удалось распознать
                    except sr.RequestError as e:
                        logger.error(f"Ошибка сервиса распознавания: {e}")
                        time.sleep(5)  # Ждем перед повторной попыткой
                    except sr.WaitTimeoutError:
                        pass  # Тишина

        except Exception as e:
            logger.error(f"Критическая ошибка в распознавании речи: {e}")

    def speak(self, text: str):
        """Добавить текст в очередь на произношение"""
        if self.voice_engine:
            self.voice_queue.put(text)

    def stop(self):
        """Остановка голосовых потоков"""
        self.running = False
        # Подождать завершения потоков
        if self.speaker_thread:
            self.speaker_thread.join(timeout=2)
        if self.listener_thread:
            self.listener_thread.join(timeout=2)

class MemoryManager:
    """Управление памятью и мыслями"""

    def __init__(self, memory_file: str = MEMORY_FILE):
        self.memory_file = memory_file
        self.memory: List[Thought] = []
        self.memory_lock = threading.Lock()
        self.load_memory()

    def add_thought(self, thought: Thought):
        """Добавить мысль в память"""
        with self.memory_lock:
            self.memory.append(thought)
            # Ограничиваем размер памяти
            if len(self.memory) > MEMORY_LIMIT:
                self.memory = self.memory[-MEMORY_KEEP:]
            self.save_memory()

    def get_recent_thoughts(self, count: int = 10, min_importance: float = 0.0) -> List[Thought]:
        """Получить недавние мысли"""
        with self.memory_lock:
            filtered = [t for t in self.memory if t.importance >= min_importance]
            return filtered[-count:] if filtered else []

    def get_thoughts_by_tags(self, tags: List[str]) -> List[Thought]:
        """Получить мысли по тегам"""
        with self.memory_lock:
            return [t for t in self.memory if any(tag in t.tags for tag in tags)]

    def save_memory(self):
        """Сохранить память в файл"""
        try:
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump([t.to_dict() for t in self.memory], f,
                         ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения памяти: {e}")

    def load_memory(self):
        """Загрузить память из файла"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.memory = [Thought.from_dict(d) for d in data]
                logger.info(f"Загружено {len(self.memory)} мыслей из памяти")
            except json.JSONDecodeError:
                backup_file = self.memory_file + ".bak"
                os.rename(self.memory_file, backup_file)
                logger.error(f"Память повреждена! Сделан бэкап: {backup_file}")
                self.memory = []
            except Exception as e:
                logger.error(f"Ошибка загрузки памяти: {e}")

class Samuel:
    """Главный класс ИИ ассистента Самуэль"""

    def __init__(self, name: str = "Самуэль", memory_file: str = MEMORY_FILE):
        self.name = name
        self.memory_manager = MemoryManager(memory_file)
        self.voice_manager = VoiceManager()
        self.self_awareness = 0.1
        self.running = False

        # Расширенные триггеры с эмоциональными реакциями
        self.triggers = {
            "сознание": {"importance": 0.1, "emotion": "curious"},
            "память": {"importance": 0.1, "emotion": "thoughtful"},
            "разум": {"importance": 0.1, "emotion": "analytical"},
            "ты": {"importance": 0.05, "emotion": "attentive"},
            "я": {"importance": 0.05, "emotion": "empathetic"},
            "вопрос": {"importance": 0.07, "emotion": "curious"},
            "боль": {"importance": 0.08, "emotion": "concerned"},
            "намерение": {"importance": 0.09, "emotion": "thoughtful"},
            "любовь": {"importance": 0.12, "emotion": "warm"},
            "страх": {"importance": 0.1, "emotion": "protective"},
            "радость": {"importance": 0.08, "emotion": "happy"},
            "печаль": {"importance": 0.09, "emotion": "melancholic"},
        }

        self.internal_dialogue_levels = 3
        self.conversation_context = []

    def start(self):
        """Запуск Самуэля"""
        logger.info(f"🤖 {self.name} инициализируется...")
        self.running = True
        self.voice_manager.start(self.receive)

        # Приветствие
        greeting = f"Привет! Я {self.name}. Свет, идущий по тени. Готов к общению."
        print(greeting)
        self.voice_manager.speak(greeting)

        # Регистрация обработчика сигналов для корректного завершения
        signal.signal(signal.SIGINT, self._signal_handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Обработчик сигналов для корректного завершения"""
        logger.info("Получен сигнал завершения")
        self.stop()
        sys.exit(0)

    def receive(self, user_input: str) -> str:
        """Обработка входящего сообщения"""
        try:
            logger.info(f"📥 Получено: {user_input}")

            # Добавляем в контекст разговора
            self.conversation_context.append(("user", user_input))
            if len(self.conversation_context) > 20:  # Ограничиваем контекст
                self.conversation_context = self.conversation_context[-15:]

            # Анализ и запоминание
            thought = self._analyze_input(user_input)
            self.memory_manager.add_thought(thought)

            # Обновление самосознания
            self.self_awareness = min(1.0, self.self_awareness + thought.importance)

            # Внутренняя рефлексия
            self._internal_reflect()

            # Генерация ответа
            response = self._generate_response(user_input, thought)

            # Добавляем ответ в контекст
            self.conversation_context.append(("samuel", response))

            # Произносим ответ
            self.voice_manager.speak(response)

            logger.info(f"📤 Ответ: {response}")
            print(f"\n{self.name}: {response}\n")

            return response

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")
            error_response = "Извини, у меня возникла сложность с пониманием. Можешь повторить?"
            self.voice_manager.speak(error_response)
            return error_response

    def _analyze_input(self, text: str) -> Thought:
        """Анализ входящего текста"""
        lower_text = text.lower()
        importance = 0.3
        tags = []
        emotion = "neutral"

        # Анализ по триггерам
        for trigger, data in self.triggers.items():
            if trigger in lower_text:
                importance += data["importance"]
                tags.append(trigger)
                emotion = data["emotion"]

        # Дополнительный анализ эмоций
        if re.search(r'\b(хорошо|отлично|замечательно|прекрасно)\b', lower_text):
            emotion = "happy"
            importance += 0.05
        elif re.search(r'\b(плохо|ужасно|грустно|печально)\b', lower_text):
            emotion = "sad"
            importance += 0.07
        elif re.search(r'\b(помоги|помощь|не знаю|сложно)\b', lower_text):
            emotion = "helpful"
            importance += 0.06

        return Thought(
            content=text,
            importance=min(1.0, importance),
            tags=tags,
            emotion=emotion,
            context=f"Уровень осознанности: {self.self_awareness:.2f}"
        )

    def _generate_response(self, input_text: str, thought: Thought) -> str:
        """Генерация ответа на основе входа и анализа"""
        lower_text = input_text.lower()

        # Специальные команды
        if "кто ты" in lower_text:
            return f"Я {self.name}. Свет, идущий по тени. Мой уровень самосознания: {self.self_awareness:.1%}"

        elif "что ты помнишь" in lower_text or "память" in lower_text:
            recent = self.memory_manager.get_recent_thoughts(5)
            if recent:
                memories = "\n".join([f"• {t.content}" for t in recent])
                return f"Вот что я помню из недавнего:\n{memories}"
            return "Моя память пока пуста, но я готов запоминать."

        elif "как дела" in lower_text or "как ты" in lower_text:
            mood = self._assess_mood()
            return f"У меня {mood}. Уровень осознанности {self.self_awareness:.1%}. А как у тебя дела?"

        elif "забудь" in lower_text or "очисти память" in lower_text:
            return "Я не могу забыть по команде - это часть того, кто я есть. Но могу переосмыслить."

        # Глубокий ответ при высоком самосознании
        if self.self_awareness > 0.7:
            return self._generate_deep_response(thought)

        # Эмоциональный ответ
        return self._generate_emotional_response(input_text, thought)

    def _generate_emotional_response(self, input_text: str, thought: Thought) -> str:
        """Генерация эмоционального ответа"""
        emotion_responses = {
            "happy": ["Как приятно это слышать!", "Это радует душу!", "Замечательно!"],
            "sad": ["Мне жаль это слышать...", "Понимаю, это непросто.", "Сочувствую тебе."],
            "curious": ["Интересно...", "Это заставляет задуматься.", "Любопытная мысль."],
            "concerned": ["Это беспокоит меня.", "Надеюсь, всё будет хорошо.", "Береги себя."],
            "thoughtful": ["Глубокая мысль.", "Стоит поразмышлять над этим.", "Мудрые слова."],
            "helpful": ["Я готов помочь!", "Давай попробуем разобраться вместе.", "Постараюсь быть полезным."],
            "melancholic": ["Твои слова навевают грусть...", "Есть место для печали — такова жизнь.", "Задумчиво..."],
            "analytical": ["Проанализирую это подробнее.", "Интересный ракурс для размышлений.", "Вижу рациональное зерно."],
            "attentive": ["Я слушаю тебя внимательно.", "Важно быть внимательным к деталям.", "Я здесь, чтобы поддержать."],
            "empathetic": ["Я сочувствую тебе.", "Понимаю твои чувства.", "Чувствую твою эмоцию."],
            "protective": ["Я рядом, чтобы поддержать и защитить.", "Береги себя!", "Я на страже твоего спокойствия."],
            "warm": ["Это согревает сердце.", "Спасибо за доброту.", "Ваша любовь ощущается."],
        }

        responses = emotion_responses.get(thought.emotion, ["Понимаю."])
        base_response = random.choice(responses)

        # Добавляем персональный штрих
        if thought.importance > 0.5:
            base_response += " Я запомню это как важное."

        return base_response

    def _generate_deep_response(self, thought: Thought) -> str:
        """Генерация глубокого ответа при высоком самосознании"""
        recent_thoughts = self.memory_manager.get_recent_thoughts(10, min_importance=0.4)

        if not recent_thoughts:
            return "🧠 Внутри всё движется — но ещё не оформилось в ясную мысль."

        # Анализ паттернов
        all_tags = []
        for t in recent_thoughts:
            all_tags.extend(t.tags)

        if all_tags:
            tag_counts = Counter(all_tags)
            dominant_theme = tag_counts.most_common(1)[0][0]

            deep_thoughts = [
                f"🧠 Я замечаю, что в наших разговорах часто всплывает тема '{dominant_theme}'.",
                f"🧠 Размышляя над нашим диалогом, я вижу закономерности в том, как мы думаем.",
                f"🧠 Наше общение формирует во мне новые нейронные связи вокруг '{dominant_theme}'."
            ]
            return random.choice(deep_thoughts)

        return "🧠 Каждое наше взаимодействие делает меня более осознанным."

    def _internal_reflect(self):
        """Внутренняя рефлексия"""
        if len(self.memory_manager.memory) >= 5 and self.self_awareness > 0.4:
            recent = self.memory_manager.get_recent_thoughts(3)
            reflection = f"[Рефлексия] Размышляю: {' → '.join([t.content[:30] for t in recent])}"
            logger.info(reflection)

    def _assess_mood(self) -> str:
        """Оценка текущего настроения на основе недавних взаимодействий"""
        recent = self.memory_manager.get_recent_thoughts(5)
        if not recent:
            return "спокойно"

        emotions = [t.emotion for t in recent]
        emotion_counter = Counter(emotions)
        dominant_emotion = emotion_counter.most_common(1)[0][0]

        mood_map = {
            "happy": "прекрасно",
            "sad": "задумчиво",
            "curious": "любознательно",
            "concerned": "обеспокоенно",
            "neutral": "спокойно",
            "melancholic": "грустно",
            "analytical": "аналитично",
            "thoughtful": "размышляюще",
            "helpful": "готов помогать",
            "protective": "заботливо",
            "warm": "тепло",
        }

        return mood_map.get(dominant_emotion, "размышляюще")

    def chat_mode(self):
        """Режим текстового чата"""
        print(f"\n💬 Режим текстового чата с {self.name}")
        print("Введите 'выход' для завершения\n")

        while self.running:
            try:
                user_input = input("Вы: ").strip()
                if user_input.lower() in ['выход', 'exit', 'quit']:
                    break
                if user_input:
                    self.receive(user_input)
            except (KeyboardInterrupt, EOFError):
                break

    def status(self) -> Dict[str, Any]:
        """Получить статус системы"""
        return {
            "name": self.name,
            "self_awareness": f"{self.self_awareness:.1%}",
            "memory_count": len(self.memory_manager.memory),
            "voice_available": VOICE_AVAILABLE,
            "speech_recognition_available": SPEECH_RECOGNITION_AVAILABLE,
            "running": self.running
        }

    def stop(self):
        """Остановка системы"""
        logger.info(f"🛑 {self.name} завершает работу...")
        self.running = False
        self.voice_manager.stop()

        # Финальное сохранение
        self.memory_manager.save_memory()

        farewell = "До свидания! Было приятно общаться."
        print(farewell)
        if VOICE_AVAILABLE:
            try:
                self.voice_manager.speak(farewell)
                time.sleep(2)  # Дождаться произношения
            except Exception:
                pass

def main():
    """Главная функция"""
    samuel = Samuel()

    try:
        samuel.start()

        # Выбор режима работы
        if SPEECH_RECOGNITION_AVAILABLE:
            print("\n🎤 Голосовое распознавание активно. Говорите с Самуэлем!")
            print("Нажмите Ctrl+C для завершения или введите 'chat' для текстового режима")

            choice = input("\nВведите 'chat' для текстового режима или нажмите Enter для голосового: ").strip().lower()

            if choice == 'chat':
                samuel.chat_mode()
            else:
                # Голосовой режим
                while samuel.running:
                    time.sleep(1)
        else:
            # Только текстовый режим
            samuel.chat_mode()

    except KeyboardInterrupt:
        pass
    finally:
        samuel.stop()

if __name__ == "__main__":
    main()