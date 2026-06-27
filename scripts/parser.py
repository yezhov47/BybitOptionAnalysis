import requests
import pandas as pd
import time
from datetime import datetime, timezone
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import json
import sys
import re
import pystray
from PIL import Image, ImageDraw
import calendar
import traceback

def global_exception_handler(exctype, value, tb):
    """Глобальный обработчик исключений"""
    error_msg = ''.join(traceback.format_exception(exctype, value, tb))
    print(f"Критическая ошибка: {error_msg}")
    
    # Пытаемся показать ошибку в GUI, если он еще жив
    try:
        root = tk._default_root
        if root:
            messagebox.showerror("Критическая ошибка", 
                               f"Программа будет закрыта.\n\n{str(value)[:200]}")
    except:
        pass
    
    # Завершаем программу корректно
    sys.__excepthook__(exctype, value, tb)

# Устанавливаем глобальный обработчик
sys.excepthook = global_exception_handler

# Настройки
INTERVAL_OPTIONS = {
    "10 минут": 600,
    "30 минут": 1800,
    "1 час": 3600,
    "5 секунд": 5
}

SUPPORTED_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "MNT"]
DEFAULT_DATA_FOLDER = "bybit_options_data"
MIN_DELTA_DEFAULT = 0.01
MAX_DELTA_DEFAULT = 1.0

MIN_DELTA_PRESETS = [0.00, 0.01, 0.05, 0.10, 0.20, 0.30, 0.40]
MAX_DELTA_PRESETS = [0.50, 0.60, 0.70, 0.80, 0.90, 1.00]

# Дни недели
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAY_NUMBERS = list(range(1, 8))  # 1 = Пн, 7 = Вс

class OptionParserApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Парсер доски опционов (BYBIT)")
        self.root.geometry("850x850")
        self.root.resizable(False, False)

        self.is_running = False
        self.interval_seconds = INTERVAL_OPTIONS["10 минут"]
        self.base_folder = DEFAULT_DATA_FOLDER

        self.selected_coins = {coin: tk.BooleanVar(value=(coin == "BTC")) for coin in SUPPORTED_COINS}
        
        # Дни недели для фильтрации
        self.selected_weekdays = {i: tk.BooleanVar(value=True) for i in range(1, 8)}  # по умолчанию все дни

        self.icon = None
        self.tray_running = False

        self.tooltip_template = (
            "Интервал: {interval}\n"
            "Дней до экспирации: {days}\n"
            "Дельты: {min_delta} – {max_delta}\n"
            "Активы: {assets}\n"
            "Дни недели: {weekdays}"
        )

        self.setup_ui()
        self.load_settings()
        self.create_base_folder()

        # Автоматическое обновление tooltip
        self.interval_var.trace("w", lambda *args: self.update_tray_tooltip())
        self.days_enabled.trace("w", lambda *args: self.update_tray_tooltip())
        self.days_var.trace("w", lambda *args: self.update_tray_tooltip())
        self.min_delta_var.trace("w", lambda *args: self.update_tray_tooltip())
        self.max_delta_var.trace("w", lambda *args: self.update_tray_tooltip())
        for var in self.selected_coins.values():
            var.trace("w", lambda *args: self.update_tray_tooltip())
        for var in self.selected_weekdays.values():
            var.trace("w", lambda *args: self.update_tray_tooltip())

        # Перехват кнопки "свернуть" (−)
        self.root.bind("<Unmap>", self.on_unmap)

        # Перехват крестика (×)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.bind("<Destroy>", self.on_destroy)

    

    def run_tray_icon(self):
        """Запуск иконки в трее с обработкой ошибок"""
        try:
            self.icon.run()
        except Exception as e:
            print(f"Ошибка в трее: {e}")
            self.tray_running = False
            self.icon = None
            # Пытаемся восстановить окно
            self.root.after(0, self.restore_from_tray)

    def schedule_tray_updates(self):
        """Периодическое обновление информации в трее"""
        if self.tray_running:
            self.update_tray_tooltip()
            # Обновляем каждые 5 секунд
            self.root.after(5000, self.schedule_tray_updates)

    def on_destroy(self, event):
        """Обработка уничтожения окна"""
        if event.widget == self.root:
            self.is_running = False
            if self.tray_running and self.icon:
                try:
                    self.icon.stop()
                except:
                    pass
                self.tray_running = False
                self.icon = None

    def create_image(self):
        image = Image.new('RGB', (64, 64), color=(44, 62, 80))
        dc = ImageDraw.Draw(image)
        dc.text((18, 20), "BYB", fill=(255, 255, 255))
        return image

    def on_unmap(self, event):
        """Реакция на сворачивание окна (кнопка −)"""
        if self.root.state() == 'iconic' and not self.tray_running:
            self.minimize_to_tray()

    def minimize_to_tray(self):
        if self.tray_running:
            return

        self.root.withdraw()
        self.update_tray_tooltip()

        image = self.create_image()

        menu = pystray.Menu(
            pystray.MenuItem("Показать", self.restore_from_tray, default=True),
            pystray.MenuItem("Выйти", self.quit_application)
        )

        self.icon = pystray.Icon(
            "bybit-options-parser",
            image,
            "Bybit Options Parser",
            menu
        )

        # Запускаем в отдельном потоке с обработкой ошибок
        threading.Thread(target=self.run_tray_icon, daemon=True).start()
        self.tray_running = True

        # Периодически обновляем tooltip
        self.update_tray_tooltip()
        self.schedule_tray_updates()

    def restore_from_tray(self, icon=None, item=None):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        
        if self.icon:
            # Останавливаем иконку в отдельном потоке
            def stop_icon():
                try:
                    self.icon.stop()
                except:
                    pass
                self.icon = None
                self.tray_running = False
            
            threading.Thread(target=stop_icon, daemon=True).start()
        else:
            self.tray_running = False

    def quit_application(self, icon=None, item=None):
        self.is_running = False
        
        if self.icon:
            try:
                self.icon.stop()
            except:
                pass
            self.icon = None
            self.tray_running = False
        
        # Закрываем окно, если оно еще существует
        try:
            self.root.quit()
            self.root.destroy()
        except:
            pass
        
        # Принудительный выход
        os._exit(0)

    def update_tray_tooltip(self):
        if not self.tray_running or not self.icon:
            return

        interval = self.interval_var.get() or "—"
        days = self.days_var.get() if self.days_enabled.get() else "все"
        min_d = self.min_delta_var.get() or "0.00"
        max_d = self.max_delta_var.get() or "1.00"
        
        # Сокращаем список активов
        assets_list = [c for c, v in self.selected_coins.items() if v.get()]
        if len(assets_list) > 2:
            assets = f"{assets_list[0]}+{len(assets_list)-1}"
        else:
            assets = ", ".join(assets_list) if assets_list else "—"
        
        # Сокращаем список дней недели
        weekdays = []
        for i, var in self.selected_weekdays.items():
            if var.get():
                weekdays.append(WEEKDAYS[i-1])
        if len(weekdays) > 3:
            weekdays_str = f"{weekdays[0]}-{weekdays[-1]}"
        else:
            weekdays_str = "".join(weekdays) if weekdays else "—"
        
        # Максимально короткий текст
        text = (f"{interval} | {days}д | "
                f"δ{min_d}-{max_d} | "
                f"{assets} | {weekdays_str}")
        
        # Обрезаем если все еще длинно
        if len(text) > 120:
            text = text[:117] + "..."

        try:
            self.icon.title = text
        except:
            pass

    def on_closing(self):
        """Обработка нажатия на крестик (×) - упрощенная версия"""
        if self.is_running:
            if not messagebox.askyesno("Выход", "Парсинг работает. Выйти?"):
                return  # остаёмся в окне
        
        # Сохраняем настройки
        self.save_settings()
        
        # Всегда спрашиваем подтверждение закрытия
        if messagebox.askyesno("Выход", "Вы уверены, что хотите выйти?"):
            if self.tray_running:
                self.quit_application()
            else:
                self.is_running = False
                self.root.quit()

    def set_controls_state(self, enabled):
        """Включение/отключение элементов управления в зависимости от состояния парсинга"""
        state = "normal" if enabled else "disabled"
        
        # Элементы настроек
        self.folder_btn.config(state=state)
        self.interval_combo.config(state="readonly" if enabled else "disabled")
        self.days_checkbtn.config(state=state)
        self.days_spinbox.config(state=state if self.days_enabled.get() else "disabled")
        self.min_delta_entry.config(state=state)
        self.max_delta_entry.config(state=state)
        
        # Кнопки пресетов дельты
        for btn in self.min_delta_btns:
            btn.config(state=state)
        for btn in self.max_delta_btns:
            btn.config(state=state)
        
        # Чекбоксы монет
        for coin, var in self.selected_coins.items():
            self.coin_checkboxes[coin].config(state=state)
        
        # Чекбоксы дней недели
        for cb in self.weekday_checkboxes:
            cb.config(state=state)
        
        # Кнопка выбора всех дней недели
        self.select_all_weekdays_btn.config(state=state)
        
        # Кнопки управления
        if enabled:
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
        else:
            self.start_button.config(state="disabled")
            self.stop_button.config(state="normal")
        # Кнопка "Один запрос" всегда доступна
        self.one_time_button.config(state="normal")
        self.open_folder.config(state="normal")

    def toggle_days_spinbox(self):
        """Включение/отключение spinbox для дней в зависимости от чекбокса"""
        state = "normal" if self.days_enabled.get() else "disabled"
        self.days_spinbox.config(state=state)

    def select_all_weekdays(self):
        """Выбрать все дни недели"""
        select_all = not all(var.get() for var in self.selected_weekdays.values())
        for var in self.selected_weekdays.values():
            var.set(select_all)

    def setup_ui(self):
        settings_frame = ttk.LabelFrame(self.root, text="Настройки", padding=10)
        settings_frame.pack(fill="x", padx=10, pady=10)

        # Папка
        folder_frame = ttk.Frame(settings_frame)
        folder_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

        ttk.Label(folder_frame, text="Базовая папка:").pack(side="left", padx=(0, 5))
        self.folder_var = tk.StringVar(value=self.base_folder)
        ttk.Label(folder_frame, textvariable=self.folder_var, relief="sunken", padding=3, width=50).pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.folder_btn = ttk.Button(folder_frame, text="Выбрать...", command=self.browse_folder, width=10)
        self.folder_btn.pack(side="left")

        # Интервал
        ttk.Label(settings_frame, text="Интервал парсинга:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.interval_var = tk.StringVar(value="10 минут")
        self.interval_combo = ttk.Combobox(settings_frame, textvariable=self.interval_var, values=list(INTERVAL_OPTIONS.keys()), state="readonly")
        self.interval_combo.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

        # Дни до экспирации с чекбоксом
        days_frame = ttk.Frame(settings_frame)
        days_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        
        self.days_enabled = tk.BooleanVar(value=True)
        self.days_checkbtn = ttk.Checkbutton(days_frame, text="Макс. дней до экспирации:", 
                                            variable=self.days_enabled, command=self.toggle_days_spinbox)
        self.days_checkbtn.pack(side="left")
        
        self.days_var = tk.StringVar(value="3")
        self.days_spinbox = ttk.Spinbox(days_frame, from_=1, to=60, textvariable=self.days_var, width=10)
        self.days_spinbox.pack(side="left", padx=(5, 0))
        
        ttk.Label(days_frame, text="(снимите галочку для всех дней)").pack(side="left", padx=(10, 0))

        # Фильтр по дельте
        delta_frame = ttk.LabelFrame(settings_frame, text="Фильтр по |дельте|", padding=10)
        delta_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=5, pady=8)

        delta_frame.columnconfigure(0, weight=0)
        delta_frame.columnconfigure(1, weight=0)
        delta_frame.columnconfigure(2, weight=0)
        delta_frame.columnconfigure(3, weight=1)
        delta_frame.columnconfigure(4, weight=0)

        ttk.Label(delta_frame, text="Мин. |δ|:").grid(row=0, column=0, sticky="e", padx=(0, 4), pady=2)
        self.min_delta_var = tk.StringVar(value=f"{MIN_DELTA_DEFAULT:.2f}")
        self.min_delta_entry = ttk.Entry(delta_frame, textvariable=self.min_delta_var, width=7)
        self.min_delta_entry.grid(row=0, column=1, sticky="w", padx=(4, 8), pady=2)
        ttk.Label(delta_frame, text="(0 = отключить нижний предел)").grid(row=0, column=2, sticky="w", padx=(0, 12), pady=2)

        col = 4
        self.min_delta_btns = []
        for val in MIN_DELTA_PRESETS:
            btn_text = "0" if val == 0 else f"{val:.2f}"
            btn = ttk.Button(delta_frame, text=btn_text, width=6,
                           command=lambda v=val: self.min_delta_var.set(f"{v:.2f}"))
            btn.grid(row=0, column=col, sticky="w", padx=(0, 3), pady=2)
            self.min_delta_btns.append(btn)
            col += 1

        ttk.Label(delta_frame, text="Макс. |δ|:").grid(row=1, column=0, sticky="e", padx=(0, 4), pady=2)
        self.max_delta_var = tk.StringVar(value=f"{MAX_DELTA_DEFAULT:.2f}")
        self.max_delta_entry = ttk.Entry(delta_frame, textvariable=self.max_delta_var, width=7)
        self.max_delta_entry.grid(row=1, column=1, sticky="w", padx=(4, 8), pady=2)
        ttk.Label(delta_frame, text="(1.0 = отключить верхний предел)").grid(row=1, column=2, sticky="w", padx=(0, 12), pady=2)

        col = 4
        self.max_delta_btns = []
        for val in MAX_DELTA_PRESETS:
            btn = ttk.Button(delta_frame, text=f"{val:.2f}", width=6,
                           command=lambda v=val: self.max_delta_var.set(f"{v:.2f}"))
            btn.grid(row=1, column=col, sticky="w", padx=(0, 3), pady=2)
            self.max_delta_btns.append(btn)
            col += 1

        # Дни недели
        weekday_frame = ttk.LabelFrame(self.root, text="Фильтр по дню недели экспирации", padding=10)
        weekday_frame.pack(fill="x", padx=10, pady=(0, 10))

        weekday_header = ttk.Frame(weekday_frame)
        weekday_header.pack(fill="x", pady=(0, 5))
        
        ttk.Label(weekday_header, text="Выберите дни недели:").pack(side="left")
        
        weekday_check_frame = ttk.Frame(weekday_frame)
        weekday_check_frame.pack(fill="x")
        
        self.weekday_checkboxes = []
        for i, (num, name) in enumerate(zip(WEEKDAY_NUMBERS, WEEKDAYS)):
            cb = ttk.Checkbutton(weekday_check_frame, text=name, variable=self.selected_weekdays[num])
            cb.grid(row=0, column=i, padx=10, pady=5, sticky="w")
            self.weekday_checkboxes.append(cb)

        self.select_all_weekdays_btn = ttk.Button(weekday_check_frame, text="Выбрать все", 
                                                command=self.select_all_weekdays, width=15)
        self.select_all_weekdays_btn.grid(row=0, column=len(WEEKDAYS), padx=(20, 0), pady=5, sticky="w")

        # Базовые активы
        coin_frame = ttk.LabelFrame(self.root, text="Выберите базовые активы", padding=10)
        coin_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.coin_checkboxes = {}
        for i, coin in enumerate(SUPPORTED_COINS):
            cb = ttk.Checkbutton(coin_frame, text=coin, variable=self.selected_coins[coin])
            cb.grid(row=i//3, column=i%3, padx=20, pady=5, sticky="w")
            self.coin_checkboxes[coin] = cb

        # Информация
        info_frame = ttk.LabelFrame(self.root, text="Информация", padding=10)
        info_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.info_label = ttk.Label(info_frame, text=f"Данные → {os.path.abspath(self.base_folder)} / [BTC, ETH, ...] /")
        self.info_label.pack(anchor="w")

        self.filter_stats_label = ttk.Label(info_frame, text="Фильтр: готов", foreground="gray")
        self.filter_stats_label.pack(anchor="w")

        # Кнопки управления
        button_frame = ttk.Frame(self.root)
        button_frame.pack(fill="x", padx=10, pady=10)

        self.start_button    = ttk.Button(button_frame, text="Запустить",   command=self.start_monitoring)
        self.stop_button     = ttk.Button(button_frame, text="Остановить", command=self.stop_monitoring, state="disabled")
        self.one_time_button = ttk.Button(button_frame, text="Один запрос", command=self.fetch_one_time)
        self.open_folder     = ttk.Button(button_frame, text="Открыть папку", command=self.open_data_folder)

        self.start_button.pack(side="left", padx=5)
        self.stop_button.pack(side="left", padx=5)
        self.one_time_button.pack(side="left", padx=5)
        self.open_folder.pack(side="left", padx=5)

        # Лог
        log_frame = ttk.LabelFrame(self.root, text="Лог", padding=10)
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.log_text = tk.Text(log_frame, height=15, state="disabled")
        self.log_text.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(self.log_text)
        scrollbar.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.log_text.yview)

        self.status_var = tk.StringVar(value="Готов")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken").pack(fill="x", padx=10, pady=(0, 10))

    def browse_folder(self):
        if self.is_running:
            messagebox.showwarning("Внимание", "Сначала остановите парсинг")
            return
        folder = filedialog.askdirectory(initialdir=self.base_folder)
        if folder:
            self.base_folder = folder
            self.folder_var.set(folder)
            self.info_label.config(text=f"Данные → {os.path.abspath(folder)} / [BTC, ETH, ...] /")
            self.create_base_folder()
            self.log_message(f"Выбрана базовая папка: {folder}")

    def create_base_folder(self):
        try:
            os.makedirs(self.base_folder, exist_ok=True)
        except Exception as e:
            self.log_message(f"Ошибка создания базовой папки: {e}")

    def get_coin_folder(self, coin):
        path = os.path.join(self.base_folder, coin.upper())
        os.makedirs(path, exist_ok=True)
        return path

    def log_message(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def update_status(self, msg):
        self.status_var.set(msg)
        self.root.update_idletasks()

    def parse_expiry_date(self, expiry_str):
        if not expiry_str or pd.isna(expiry_str):
            return None
        expiry_str = str(expiry_str).strip().upper()
        match = re.match(r'^(\d{1,2})([A-Z]{3})(\d{2})$', expiry_str)
        if not match:
            return None
        day, month_str, year_short = match.groups()
        month_map = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
                     'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
        month = month_map.get(month_str)
        if not month:
            return None
        try:
            day = int(day)
            year = 2000 + int(year_short)
            return datetime(year, month, day)
        except:
            return None

    def is_within_days_limit(self, expiry_date):
        if expiry_date is None or not self.days_enabled.get():
            return True
        try:
            days_limit = int(self.days_var.get())
            today = datetime.now(timezone.utc).replace(tzinfo=None)
            days_left = (expiry_date - today).days
            return days_left <= days_limit
        except:
            return False

    def is_weekday_selected(self, expiry_date):
        """Проверяет, выбран ли день недели даты экспирации"""
        if expiry_date is None:
            return True
        try:
            # isoweekday(): 1 = Пн, 7 = Вс
            weekday = expiry_date.isoweekday()
            return self.selected_weekdays[weekday].get()
        except:
            return True

    def filter_by_delta(self, df):
        try:
            min_d = float(self.min_delta_var.get())
            max_d = float(self.max_delta_var.get())
            use_min = min_d > 0
            use_max = max_d > 0 and max_d >= min_d
            if not use_min and not use_max:
                return df, len(df), 0
            if 'delta' not in df.columns:
                return df, len(df), 0
            original = len(df)
            filtered = df.copy()
            removed_low = removed_high = 0
            abs_delta = filtered['delta'].abs()
            if use_min:
                mask_low = abs_delta < min_d
                removed_low = mask_low.sum()
                filtered = filtered[~mask_low]
            if use_max and len(filtered) > 0:
                abs_delta = filtered['delta'].abs()
                mask_high = abs_delta > max_d
                removed_high = mask_high.sum()
                filtered = filtered[~mask_high]
            total_removed = removed_low + removed_high
            kept = len(filtered)
            filter_text = []
            if use_min: filter_text.append(f"≥ {min_d:.2f}")
            if use_max: filter_text.append(f"≤ {max_d:.2f}")
            desc = " | ".join(filter_text) if filter_text else "отключён"
            self.filter_stats_label.config(text=f"Фильтр |δ|: {desc} → сохранено {kept}/{original} строк")
            if total_removed > 0:
                msg = f"Удалено {total_removed} строк"
                if removed_low: msg += f" (ниже {min_d:.2f}: {removed_low})"
                if removed_high: msg += f" (выше {max_d:.2f}: {removed_high})"
                self.log_message(msg)
            return filtered, kept, total_removed
        except ValueError:
            self.log_message("Некорректное значение min/max delta → фильтр отключён")
            return df, len(df), 0

    def fetch_options_data(self, base_coin):
        url = "https://api.bybit.com/v5/market/tickers"
        params = {"category": "option", "baseCoin": base_coin.upper()}
        try:
            r = requests.get(url, params=params, timeout=12)
            r.raise_for_status()
            data = r.json()
            if data.get("retCode") != 0:
                self.log_message(f"[{base_coin}] API ошибка: {data.get('retMsg')}")
                return None
            df = pd.DataFrame(data["result"]["list"])
            if df.empty:
                return None
            num_cols = ['markPrice','indexPrice','bid1Price','ask1Price',
                        'bid1Iv','ask1Iv','markIv','delta','gamma','vega','theta']
            for col in num_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            df['fetch_time_utc'] = datetime.now(timezone.utc).isoformat()
            df['expiry'] = df['symbol'].str.extract(r'-(\d{1,2}[A-Z]{3}\d{2})-')
            df['strike']  = pd.to_numeric(df['symbol'].str.extract(r'-(\d+\.?\d*)-')[0], errors='coerce')
            df['expiry'] = df['expiry'].fillna("None")
            mask_zero = (df['bid1Price'] == 0) & (df['ask1Price'] == 0)
            if mask_zero.any():
                removed = mask_zero.sum()
                df = df[~mask_zero]
                if removed:
                    self.log_message(f"[{base_coin}] Удалено {removed} строк с bid=ask=0")
            
            # Фильтр по дням до экспирации
            df['expiry_date'] = df['expiry'].apply(self.parse_expiry_date)
            df = df[df['expiry_date'].apply(self.is_within_days_limit)]
            
            # Фильтр по дням недели
            df = df[df['expiry_date'].apply(self.is_weekday_selected)]
            
            if df.empty:
                days_text = self.days_var.get() if self.days_enabled.get() else "все"
                self.log_message(f"[{base_coin}] Нет опционов под фильтры (дни: {days_text}, дни недели: активны)")
                return None
            
            df, kept, removed_delta = self.filter_by_delta(df)
            if df.empty:
                return None
            
            # Удаляем временную колонку
            df = df.drop(columns=['expiry_date'])
            
            cols = ['fetch_time_utc', 'symbol', 'expiry', 'strike', 'markPrice', 'indexPrice',
                    'bid1Price', 'ask1Price', 'delta', 'gamma', 'vega', 'theta',
                    'bid1Iv', 'ask1Iv', 'markIv']
            df = df[[c for c in cols if c in df.columns]]
            self.log_message(f"[{base_coin}] Получено {len(df)} строк после фильтров")
            return df.groupby('expiry')
        except Exception as e:
            self.log_message(f"[{base_coin}] Ошибка запроса: {e}")
            return None

    def get_filename(self, coin, expiry_date):
        if expiry_date is None:
            return os.path.join(self.get_coin_folder(coin), f"{coin}_UNKNOWN.csv")
        date_str = expiry_date.strftime("%Y-%m-%d")
        weekday = expiry_date.isoweekday()  # 1-7
        filename = f"{coin.upper()}_{date_str}_{weekday}.csv"
        return os.path.join(self.get_coin_folder(coin), filename)

    def save_grouped_data(self, grouped, coin):
        if grouped is None:
            return 0, []
        total_saved = 0
        files = []
        for expiry_str, df_group in grouped:
            if df_group.empty:
                continue
            expiry_dt = self.parse_expiry_date(expiry_str)
            filepath = self.get_filename(coin, expiry_dt)
            try:
                if os.path.exists(filepath):
                    df_group.to_csv(filepath, mode='a', header=False, index=False, encoding='utf-8')
                else:
                    df_group.to_csv(filepath, mode='w', header=True, index=False, encoding='utf-8')
                count = len(df_group)
                total_saved += count
                files.append(os.path.basename(filepath))
                self.log_message(f"[{coin}] +{count} строк → {os.path.basename(filepath)}")
            except Exception as e:
                self.log_message(f"[{coin}] Ошибка сохранения {filepath}: {e}")
        return total_saved, files

    def process_all_coins(self):
        selected = [coin for coin, var in self.selected_coins.items() if var.get()]
        if not selected:
            self.log_message("Не выбран ни один актив!")
            self.update_status("Ошибка: выберите хотя бы один актив")
            return
        total_all = 0
        for coin in selected:
            self.update_status(f"Запрос {coin} ...")
            grouped = self.fetch_options_data(coin)
            if grouped is not None:
                saved, _ = self.save_grouped_data(grouped, coin)
                total_all += saved
        if total_all > 0:
            self.log_message(f"Итого сохранено {total_all} строк")
            self.update_status(f"Сохранено {total_all} строк")
        else:
            self.update_status("Нет новых данных")

    def monitoring_loop(self):
        while self.is_running:
            try:
                self.process_all_coins()
            except Exception as e:
                self.log_message(f"Критическая ошибка в цикле: {e}")
            interval = self.interval_seconds
            self.update_status(f"Ожидание следующего цикла ({self.interval_var.get()})...")
            for _ in range(interval // 5):
                if not self.is_running:
                    break
                time.sleep(5)

    def start_monitoring(self):
        if self.is_running:
            return
        try:
            min_d = float(self.min_delta_var.get())
            if min_d < 0:
                raise ValueError("min delta < 0")
        except:
            messagebox.showerror("Ошибка", "Некорректное значение мин. дельты")
            return
        
        # Проверка выбора дней недели
        if not any(var.get() for var in self.selected_weekdays.values()):
            messagebox.showwarning("Внимание", "Выберите хотя бы один день недели")
            return
        
        selected = [c for c,v in self.selected_coins.items() if v.get()]
        if not selected:
            messagebox.showwarning("Внимание", "Выберите хотя бы один актив")
            return
        
        self.interval_seconds = INTERVAL_OPTIONS.get(self.interval_var.get(), 600)
        self.create_base_folder()
        self.is_running = True
        
        # Отключаем элементы управления
        self.set_controls_state(False)
        
        self.monitor_thread = threading.Thread(target=self.monitoring_loop, daemon=True)
        self.monitor_thread.start()
        
        days_text = f"дни до экспирации: {self.days_var.get()}" if self.days_enabled.get() else "все дни до экспирации"
        weekdays = [WEEKDAYS[i-1] for i, var in self.selected_weekdays.items() if var.get()]
        self.log_message(f"Парсинг запущен: {', '.join(selected)} | интервал {self.interval_var.get()} | {days_text} | дни недели: {', '.join(weekdays)}")
        self.update_status("Парсинг активен")

    def stop_monitoring(self):
        if self.is_running:
            self.is_running = False
            # Включаем элементы управления
            self.set_controls_state(True)
            self.log_message("Парсинг остановлен")
            self.update_status("Остановлен")

    def fetch_one_time(self):
        # Разрешаем выполнять даже во время парсинга
        self.process_all_coins()

    def open_data_folder(self):
        path = os.path.abspath(self.base_folder)
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        try:
            if os.name == 'nt':
                os.startfile(path)
            elif sys.platform == 'darwin':
                import subprocess
                subprocess.Popen(['open', path])
            else:
                import subprocess
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть папку\n{e}")

    def save_settings(self):
        settings = {
            'interval': self.interval_var.get(),
            'days_enabled': self.days_enabled.get(),
            'max_days': self.days_var.get(),
            'min_delta': self.min_delta_var.get(),
            'max_delta': self.max_delta_var.get(),
            'base_folder': self.base_folder,
            'selected_coins': [c for c,v in self.selected_coins.items() if v.get()],
            'selected_weekdays': [i for i,v in self.selected_weekdays.items() if v.get()]
        }
        try:
            with open('bybit_options_monitor.json', 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except:
            pass

    def load_settings(self):
        try:
            with open('bybit_options_monitor.json', 'r', encoding='utf-8') as f:
                s = json.load(f)
            if 'interval' in s:    self.interval_var.set(s['interval'])
            if 'days_enabled' in s: self.days_enabled.set(s['days_enabled'])
            if 'max_days' in s:    self.days_var.set(s['max_days'])
            if 'min_delta' in s:   self.min_delta_var.set(s['min_delta'])
            if 'max_delta' in s:   self.max_delta_var.set(s['max_delta'])
            if 'base_folder' in s:
                self.base_folder = s['base_folder']
                self.folder_var.set(self.base_folder)
                self.info_label.config(text=f"Данные → {os.path.abspath(self.base_folder)} / [BTC, ETH, ...] /")
            if 'selected_coins' in s:
                for coin in SUPPORTED_COINS:
                    self.selected_coins[coin].set(coin in s['selected_coins'])
            if 'selected_weekdays' in s:
                # Сначала сбрасываем все
                for i in range(1, 8):
                    self.selected_weekdays[i].set(False)
                # Устанавливаем сохраненные
                for day in s['selected_weekdays']:
                    if day in self.selected_weekdays:
                        self.selected_weekdays[day].set(True)
            else:
                # Если нет сохраненных, выбираем все по умолчанию
                for i in range(1, 8):
                    self.selected_weekdays[i].set(True)
        except:
            # При ошибке загрузки выбираем все дни
            for i in range(1, 8):
                self.selected_weekdays[i].set(True)
            
        self.toggle_days_spinbox()


def main():
    root = tk.Tk()
    app = OptionParserApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()