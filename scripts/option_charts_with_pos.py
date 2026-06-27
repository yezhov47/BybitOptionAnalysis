import pandas as pd
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import matplotlib
from datetime import datetime
import glob
import os
import re
from matplotlib.ticker import PercentFormatter
import matplotlib.dates as mdates
from collections import defaultdict
import functools
import warnings
warnings.filterwarnings('ignore')

matplotlib.use('TkAgg')
plt.style.use('seaborn-v0_8-darkgrid')


def cache_result(maxsize=128):
    """Декоратор для кэширования результатов функций"""
    def decorator(func):
        cache = {}
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            if key in cache:
                return cache[key]
            result = func(*args, **kwargs)
            if len(cache) < maxsize:
                cache[key] = result
            return result
        
        return wrapper
    
    return decorator


class PortfolioPosition:
    """Класс для хранения информации о позиции в портфеле"""
    
    def __init__(self, symbol, side, quantity, strike=None, option_type=None, expiry=None, entry_time=None, entry_price=None):
        self.symbol = symbol
        self.side = side  # 'long' или 'short'
        self.quantity = quantity  # количество контрактов
        self.strike = strike
        self.option_type = option_type  # 'C' или 'P'
        self.expiry = expiry
        self.entry_time = entry_time  # время входа (timestamp)
        self.entry_price = entry_price  # цена входа
    
    def get_multiplier(self):
        """Возвращает множитель для P&L (+1 для long, -1 для short)"""
        return 1 if self.side == 'long' else -1
    
    def get_color(self):
        """Возвращает цвет для отображения позиции"""
        if self.option_type == 'C':
            return '#2ecc71' if self.side == 'long' else '#27ae60'
        else:
            return '#e74c3c' if self.side == 'long' else '#c0392b'


class PortfolioManager:
    """Класс для управления портфелем опционов"""
    
    def __init__(self, app):
        self.app = app
        self.positions = []  # список позиций
        self.total_pnl_history = {}  # история P&L портфеля по времени
        
    def add_position(self, symbol, side, quantity, strike=None, option_type=None, expiry=None, entry_time=None, entry_price=None):
        """Добавить позицию в портфель"""
        position = PortfolioPosition(symbol, side, quantity, strike, option_type, expiry, entry_time, entry_price)
        self.positions.append(position)
        return position
    
    def remove_position(self, index):
        """Удалить позицию из портфеля"""
        if 0 <= index < len(self.positions):
            self.positions.pop(index)
    
    def clear_all_positions(self):
        """Очистить все позиции"""
        self.positions.clear()
        self.total_pnl_history.clear()
    
    def get_position_price_at_time(self, symbol, snapshot_time, price_type='markPrice'):
        """Получить цену опциона в указанное время"""
        if symbol not in self.app.all_options_data:
            return None
        
        data_info = self.app.all_options_data[symbol]
        symbol_data = data_info['data']
        
        # Находим цену в указанное время
        mask = symbol_data['fetch_time_utc'] == snapshot_time
        matching_rows = symbol_data[mask]
        
        if len(matching_rows) == 0:
            return None
        
        price = matching_rows[price_type].iloc[0]
        if pd.isna(price):
            return None
        
        return price
    
    def get_position_pnl(self, position, snapshot_time, price_type='markPrice'):
        """Рассчитать P&L для одной позиции на определенный момент времени"""
        current_price = self.get_position_price_at_time(position.symbol, snapshot_time, price_type)
        
        if current_price is None:
            return None
        
        # Если цена входа не установлена, пробуем получить её из данных
        if position.entry_price is None and position.entry_time is not None:
            position.entry_price = self.get_position_price_at_time(position.symbol, position.entry_time, price_type)
        
        if position.entry_price is None:
            return 0
        
        # Рассчитываем P&L
        multiplier = position.get_multiplier()
        pnl = multiplier * (current_price - position.entry_price) * position.quantity
        
        return pnl
    
    def calculate_portfolio_pnl_at_time(self, snapshot_time, price_type='markPrice'):
        """Рассчитать общий P&L портфеля в определенный момент времени"""
        total_pnl = 0
        for position in self.positions:
            pnl = self.get_position_pnl(position, snapshot_time, price_type)
            if pnl is not None:
                total_pnl += pnl
        return total_pnl
    
    def calculate_portfolio_greeks_at_time(self, snapshot_time):
        """Рассчитать греки портфеля в определенный момент времени"""
        greeks = {'delta': 0, 'gamma': 0, 'vega': 0, 'theta': 0}
        
        for position in self.positions:
            if position.symbol not in self.app.all_options_data:
                continue
            
            data_info = self.app.all_options_data[position.symbol]
            symbol_data = data_info['data']
            
            mask = symbol_data['fetch_time_utc'] == snapshot_time
            matching_rows = symbol_data[mask]
            
            if len(matching_rows) == 0:
                continue
            
            multiplier = position.get_multiplier()
            
            for greek in greeks.keys():
                if greek in matching_rows.columns:
                    greek_value = matching_rows[greek].iloc[0]
                    if pd.notna(greek_value):
                        greeks[greek] += multiplier * greek_value * position.quantity
        
        return greeks
    
    def update_pnl_history(self, times, price_type='markPrice'):
        """Обновить историю P&L портфеля"""
        self.total_pnl_history = {}
        for time in times:
            pnl = self.calculate_portfolio_pnl_at_time(time, price_type)
            if pnl is not None:
                self.total_pnl_history[time] = pnl
    
    def get_position_summary(self, position, current_time, price_type='markPrice'):
        """Получить сводку по позиции"""
        pnl = self.get_position_pnl(position, current_time, price_type)
        current_price = self.get_position_price_at_time(position.symbol, current_time, price_type)
        
        return {
            'symbol': position.symbol,
            'side': 'Long' if position.side == 'long' else 'Short',
            'quantity': position.quantity,
            'strike': position.strike,
            'type': position.option_type,
            'entry_time': position.entry_time,
            'entry_price': position.entry_price,
            'current_price': current_price,
            'pnl': pnl
        }


class PortfolioTab:
    """Класс для вкладки управления портфелем"""
    
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.portfolio_manager = PortfolioManager(app)
        self.current_expiry = None
        self.selected_symbol = None
        self.selected_strike = None
        self.selected_type = None
        self._updating_slider = False
        
        # Фильтр для отображения опционов
        self.filter_var = tk.StringVar(value='all')  # 'all', 'calls', 'puts'
        
        # Переменные для новой позиции
        self.side_var = tk.StringVar(value='long')
        self.quantity_var = tk.IntVar(value=1)
        
        self.setup_ui()
        
        # Привязываем обновление времени при переключении вкладки
        self.parent.bind("<Visibility>", self.on_tab_visible)
    
    def on_tab_visible(self, event):
        """Обновить ползунок при переключении на вкладку"""
        self.update_time_slider()
        self.update_options_table()
    
    def setup_ui(self):
        """Настройка интерфейса вкладки портфеля"""
        main_frame = ttk.Frame(self.parent)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Создаем PanedWindow для разделения
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        # Левая панель - добавление позиций
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=1)
        
        # Правая панель - список позиций и графики
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=2)
        
        # ========== ЛЕВАЯ ПАНЕЛЬ ==========
        # Выбор даты экспирации
        expiry_frame = ttk.LabelFrame(left_frame, text="Выбор даты экспирации", padding="5")
        expiry_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.expiry_combo = ttk.Combobox(expiry_frame, state="readonly", width=25)
        self.expiry_combo.pack(fill=tk.X, pady=5)
        self.expiry_combo.bind('<<ComboboxSelected>>', self.on_expiry_selected)
        
        # Выбор времени (синхронизирован с глобальным)
        time_frame = ttk.LabelFrame(left_frame, text="Выбор времени для входа в позицию", padding="5")
        time_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.time_slider = ttk.Scale(time_frame, from_=0, to=100,
                                    orient=tk.HORIZONTAL, command=self.on_time_slider_move)
        self.time_slider.pack(fill=tk.X, pady=(5, 5))
        
        time_control_frame = ttk.Frame(time_frame)
        time_control_frame.pack(fill=tk.X)
        
        ttk.Button(time_control_frame, text="◀", 
                command=lambda: self.move_time(-1), width=5).pack(side=tk.LEFT, padx=2)
        ttk.Button(time_control_frame, text="▶", 
                command=lambda: self.move_time(1), width=5).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(time_control_frame, text="Синхр.", 
                command=self.sync_with_current_time, width=8).pack(side=tk.LEFT, padx=2)
        
        self.time_label = ttk.Label(time_control_frame, text="----.--.-- --:--:--")
        self.time_label.pack(side=tk.RIGHT, padx=5)
        
        # Фильтр опционов
        filter_frame = ttk.LabelFrame(left_frame, text="Фильтр опционов", padding="5")
        filter_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Radiobutton(filter_frame, text="Все опционы", 
                    variable=self.filter_var, value='all',
                    command=self.update_options_table).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(filter_frame, text="Только CALL",  # ИСПРАВЛЕНО: было filter_var, теперь self.filter_var
                    variable=self.filter_var, value='calls',
                    command=self.update_options_table).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(filter_frame, text="Только PUT", 
                    variable=self.filter_var, value='puts',
                    command=self.update_options_table).pack(side=tk.LEFT, padx=5)
        
        # Выбор опциона
        option_frame = ttk.LabelFrame(left_frame, text="Выбор опциона (кликните для выбора)", padding="5")
        option_frame.pack(fill=tk.BOTH, expand=True)
        
        # Таблица опционов для выбора
        columns = ('Выбор', 'Страйк', 'Тип', 'Дельта', 'Mark', 'Bid', 'Ask')
        self.options_tree = ttk.Treeview(option_frame, columns=columns, show='headings', height=15)
        
        self.options_tree.heading('Выбор', text='●')
        self.options_tree.heading('Страйк', text='Страйк')
        self.options_tree.heading('Тип', text='Тип')
        self.options_tree.heading('Дельта', text='Дельта')
        self.options_tree.heading('Mark', text='Mark Price')
        self.options_tree.heading('Bid', text='Bid')
        self.options_tree.heading('Ask', text='Ask')
        
        self.options_tree.column('Выбор', width=50, anchor='center')
        self.options_tree.column('Страйк', width=100, anchor='center')
        self.options_tree.column('Тип', width=80, anchor='center')
        self.options_tree.column('Дельта', width=100, anchor='center')
        self.options_tree.column('Mark', width=100, anchor='center')
        self.options_tree.column('Bid', width=100, anchor='center')
        self.options_tree.column('Ask', width=100, anchor='center')
        
        scrollbar = ttk.Scrollbar(option_frame, orient=tk.VERTICAL, command=self.options_tree.yview)
        self.options_tree.configure(yscrollcommand=scrollbar.set)
        
        self.options_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        option_frame.columnconfigure(0, weight=1)
        option_frame.rowconfigure(0, weight=1)
        
        self.options_tree.bind('<Button-1>', self.on_option_click)
        self.options_tree.bind('<ButtonRelease-1>', lambda e: self.options_tree.selection_remove(self.options_tree.selection()))
        
        # Параметры позиции
        position_frame = ttk.LabelFrame(left_frame, text="Параметры позиции", padding="5")
        position_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Label(position_frame, text="Направление:").grid(row=0, column=0, sticky=tk.W, pady=2)
        ttk.Radiobutton(position_frame, text="Long (покупка)", 
                    variable=self.side_var, value='long').grid(row=0, column=1, sticky=tk.W, padx=10)
        ttk.Radiobutton(position_frame, text="Short (продажа)", 
                    variable=self.side_var, value='short').grid(row=1, column=1, sticky=tk.W, padx=10)
        
        ttk.Label(position_frame, text="Количество:").grid(row=2, column=0, sticky=tk.W, pady=5)
        quantity_spinbox = ttk.Spinbox(position_frame, from_=1, to=1000, 
                                    textvariable=self.quantity_var, width=10)
        quantity_spinbox.grid(row=2, column=1, sticky=tk.W, padx=10)
        
        # Информация о выбранном опционе
        self.selected_info_label = ttk.Label(position_frame, text="Выбран: ---", 
                                            foreground='blue', font=('Arial', 9, 'bold'))
        self.selected_info_label.grid(row=3, column=0, columnspan=2, pady=5, sticky=tk.W)
        
        ttk.Button(position_frame, text="Добавить позицию", 
                command=self.add_position, width=20).grid(row=4, column=0, columnspan=2, pady=10)
        
        # ========== ПРАВАЯ ПАНЕЛЬ ==========
        # Список позиций
        positions_frame = ttk.LabelFrame(right_frame, text="Позиции в портфеле", padding="5")
        positions_frame.pack(fill=tk.BOTH, expand=True)
        
        columns_pos = ('Символ', 'Тип', 'Страйк', 'Направление', 'Кол-во', 
                    'Время входа', 'Цена входа', 'Текущая цена', 'P&L')
        self.positions_tree = ttk.Treeview(positions_frame, columns=columns_pos, show='headings', height=8)
        
        for col in columns_pos:
            self.positions_tree.heading(col, text=col)
            width = 100 if col != 'Символ' else 150
            self.positions_tree.column(col, width=width, anchor='center')
        
        scrollbar_pos = ttk.Scrollbar(positions_frame, orient=tk.VERTICAL, command=self.positions_tree.yview)
        self.positions_tree.configure(yscrollcommand=scrollbar_pos.set)
        
        self.positions_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar_pos.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        positions_frame.columnconfigure(0, weight=1)
        positions_frame.rowconfigure(0, weight=1)
        
        # Кнопки управления позициями
        btn_frame = ttk.Frame(positions_frame)
        btn_frame.grid(row=1, column=0, columnspan=2, pady=5)
        
        ttk.Button(btn_frame, text="Удалить выбранную", 
                command=self.remove_selected_position).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Очистить все", 
                command=self.clear_all_positions).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Построить графики", 
                command=self.plot_portfolio).pack(side=tk.LEFT, padx=5)
        
        # Опции для графиков
        graph_options_frame = ttk.LabelFrame(right_frame, text="Опции графиков", padding="5")
        graph_options_frame.pack(fill=tk.X, pady=5)
        
        self.plot_from_entry_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(graph_options_frame, text="Строить графики с момента покупки",
                    variable=self.plot_from_entry_var).pack(anchor=tk.W, pady=2)
        
        self.show_individual_pnl_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(graph_options_frame, text="Показывать P&L по каждой позиции",
                    variable=self.show_individual_pnl_var).pack(anchor=tk.W, pady=2)
        
        # Статус портфеля
        self.portfolio_status = ttk.Label(right_frame, text="Позиций: 0 | Общий P&L: 0.00")
        self.portfolio_status.pack(pady=5)
        
        # Графики портфеля
        portfolio_graphs_frame = ttk.LabelFrame(right_frame, text="Графики портфеля", padding="5")
        portfolio_graphs_frame.pack(fill=tk.BOTH, expand=True)
        
        # Создаем Notebook для графиков
        self.portfolio_notebook = ttk.Notebook(portfolio_graphs_frame)
        self.portfolio_notebook.pack(fill=tk.BOTH, expand=True)
        
        # Вкладка P&L
        pnl_tab = ttk.Frame(self.portfolio_notebook)
        self.portfolio_notebook.add(pnl_tab, text="P&L портфеля")
        
        self.pnl_canvas_frame = ttk.Frame(pnl_tab)
        self.pnl_canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        self.figure_pnl = Figure(figsize=(8, 3), dpi=100)
        self.canvas_pnl = FigureCanvasTkAgg(self.figure_pnl, self.pnl_canvas_frame)
        self.canvas_pnl.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Вкладка Греки
        greeks_tab = ttk.Frame(self.portfolio_notebook)
        self.portfolio_notebook.add(greeks_tab, text="Греки")
        
        self.greeks_canvas_frame = ttk.Frame(greeks_tab)
        self.greeks_canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        self.figure_greeks = Figure(figsize=(8, 3), dpi=100)
        self.canvas_greeks = FigureCanvasTkAgg(self.figure_greeks, self.greeks_canvas_frame)
        self.canvas_greeks.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Инициализируем время
        self.update_time_slider()

    def update_time_slider(self):
        """Обновить ползунок времени в соответствии с глобальным индексом"""
        if self.app.all_times and not self._updating_slider:
            self._updating_slider = True
            try:
                self.time_slider.configure(from_=0, to=len(self.app.all_times) - 1)
                self.time_slider.set(self.app.current_time_idx)
                self.update_time_label()
            finally:
                self._updating_slider = False
    
    def on_time_slider_move(self, value):
        """Обработка движения ползунка времени"""
        if self._updating_slider:
            return
        
        try:
            idx = int(float(value))
            if 0 <= idx < len(self.app.all_times):
                self._updating_slider = True
                # Синхронизируем глобальное время приложения
                self.app.current_time_idx = idx
                
                # Обновляем метку времени
                self.update_time_label()
                
                # Обновляем таблицу опционов для нового времени
                self.update_options_table()
                
                # Обновляем другие вкладки для синхронизации
                for expiry_date, selection_tab in self.app.selection_tabs.items():
                    if hasattr(selection_tab, '_updating_slider'):
                        selection_tab._updating_slider = True
                        try:
                            if hasattr(selection_tab, 'time_slider'):
                                selection_tab.time_slider.set(idx)
                            selection_tab.update_table()
                            selection_tab.update_time_label()
                        finally:
                            selection_tab._updating_slider = False
                
                # Обновляем таблицу позиций
                self.update_positions_table()
                self.update_portfolio_status()
                
        except Exception as e:
            print(f"Ошибка в on_time_slider_move: {e}")
        finally:
            self._updating_slider = False
    
    def move_time(self, step):
        """Переместить время на указанный шаг"""
        if self.app.all_times:
            new_idx = max(0, min(len(self.app.all_times) - 1, self.app.current_time_idx + step))
            self._updating_slider = True
            self.time_slider.set(new_idx)
            self._updating_slider = False
            self.on_time_slider_move(new_idx)
    
    def sync_with_current_time(self):
        """Синхронизировать с текущим глобальным временем"""
        if self.app.all_times and 0 <= self.app.current_time_idx < len(self.app.all_times):
            self._updating_slider = True
            self.time_slider.set(self.app.current_time_idx)
            self._updating_slider = False
            self.on_time_slider_move(self.app.current_time_idx)
            messagebox.showinfo("Синхронизация", f"Время синхронизировано с текущим: {self.time_label.cget('text')}")
    
    def update_time_label(self):
        """Обновить метку времени"""
        if self.app.all_times and 0 <= self.app.current_time_idx < len(self.app.all_times):
            current_time = self.app.all_times[self.app.current_time_idx]
            if hasattr(current_time, 'tz') and current_time.tz is not None:
                current_time = current_time.tz_localize(None)
            self.time_label.config(text=current_time.strftime('%Y-%m-%d %H:%M:%S'))
    
    def update_expiry_list(self):
        """Обновить список дат экспирации"""
        if self.app.dataframes:
            expiries = sorted(self.app.dataframes.keys())
            display_expiries = [e[:10] for e in expiries]
            current_selection = self.expiry_combo.get()
            
            self.expiry_combo['values'] = display_expiries
            
            if display_expiries:
                # Если было выбрано значение, пытаемся сохранить выбор
                if current_selection and current_selection in display_expiries:
                    self.expiry_combo.set(current_selection)
                    for full_expiry in self.app.dataframes.keys():
                        if full_expiry.startswith(current_selection):
                            self.current_expiry = full_expiry
                            break
                else:
                    self.expiry_combo.current(0)
                    self.current_expiry = expiries[0]
                
                self.update_options_table()
    
    def on_expiry_selected(self, event):
        """Обработка выбора даты экспирации"""
        selection = self.expiry_combo.get()
        if selection and self.app.dataframes:
            for full_expiry in self.app.dataframes.keys():
                if full_expiry.startswith(selection):
                    self.current_expiry = full_expiry
                    self.update_options_table()
                    # Сбрасываем выбор опциона
                    self.selected_symbol = None
                    self.selected_strike = None
                    self.selected_type = None
                    self.selected_info_label.config(text="Выбран: ---", foreground='blue')
                    break
    
    def update_options_table(self):
        """Обновить таблицу опционов для выбора с учетом фильтра и текущего времени"""
        # Очищаем таблицу
        for item in self.options_tree.get_children():
            self.options_tree.delete(item)
        
        if not self.current_expiry or self.current_expiry not in self.app.dataframes:
            return
        
        # Получаем текущее время
        current_time = None
        if self.app.all_times and 0 <= self.app.current_time_idx < len(self.app.all_times):
            current_time = self.app.all_times[self.app.current_time_idx]
        
        if current_time is None:
            return
        
        df = self.app.dataframes[self.current_expiry]
        
        # Фильтруем данные по времени
        df_time = df[df['fetch_time_utc'] == current_time]
        
        if df_time.empty:
            # Если нет данных для выбранного времени, показываем сообщение
            self.options_tree.insert('', tk.END, values=('', 'Нет данных', '', '', '', '', ''))
            return
        
        # Группируем по страйку и типу
        options_data = {}
        
        for _, row in df_time.iterrows():
            if pd.notna(row['strike']):
                strike = str(int(row['strike']))
                symbol = row['symbol']
                
                parts = symbol.split('-')
                if len(parts) >= 4:
                    opt_type = parts[3][0]
                    
                    # Применяем фильтр
                    if self.filter_var.get() == 'calls' and opt_type != 'C':
                        continue
                    if self.filter_var.get() == 'puts' and opt_type != 'P':
                        continue
                    
                    key = f"{strike}_{opt_type}"
                    
                    options_data[key] = {
                        'strike': strike,
                        'type': opt_type,
                        'delta': row.get('delta', None),
                        'mark': row.get('markPrice', None),
                        'bid': row.get('bid1Price', None),
                        'ask': row.get('ask1Price', None),
                        'symbol': symbol
                    }
        
        # Сортируем по страйку
        sorted_options = sorted(options_data.values(), key=lambda x: (float(x['strike']), x['type']))
        
        for opt in sorted_options:
            delta_str = f"{opt['delta']:.4f}" if opt['delta'] is not None and pd.notna(opt['delta']) else "—"
            mark_str = f"{opt['mark']:.2f}" if opt['mark'] is not None and pd.notna(opt['mark']) else "—"
            bid_str = f"{opt['bid']:.2f}" if opt['bid'] is not None and pd.notna(opt['bid']) else "—"
            ask_str = f"{opt['ask']:.2f}" if opt['ask'] is not None and pd.notna(opt['ask']) else "—"
            
            # Проверяем, выбран ли этот опцион
            selected_mark = '●' if self.selected_symbol == opt['symbol'] else '○'
            
            # Добавляем теги для цвета
            tags = ('call',) if opt['type'] == 'C' else ('put',)
            
            self.options_tree.insert('', tk.END, values=(
                selected_mark,
                f"{int(float(opt['strike'])):,}",
                "CALL" if opt['type'] == 'C' else "PUT",
                delta_str,
                mark_str,
                bid_str,
                ask_str
            ), tags=tags, iid=opt['symbol'])
        
        # Настройка цветов строк
        self.options_tree.tag_configure('call', background='#e8f5e9')
        self.options_tree.tag_configure('put', background='#ffebee')
    
    def on_option_click(self, event):
        """Обработка клика по опциону для выбора"""
        region = self.options_tree.identify_region(event.x, event.y)
        if region != 'cell':
            return
        
        item = self.options_tree.identify_row(event.y)
        if not item:
            return
        
        # Получаем символ выбранного опциона
        self.selected_symbol = item
        
        # Получаем информацию об опционе
        values = self.options_tree.item(item, 'values')
        if len(values) < 3:
            return
            
        strike = values[1].replace(',', '')
        opt_type = "CALL" if values[2] == "CALL" else "PUT"
        
        # Сохраняем информацию
        self.selected_strike = strike
        self.selected_type = 'C' if opt_type == "CALL" else 'P'
        
        # Обновляем информацию в интерфейсе
        current_time = self.app.all_times[self.app.current_time_idx] if self.app.all_times else None
        time_str = ""
        if current_time:
            if hasattr(current_time, 'tz') and current_time.tz is not None:
                current_time = current_time.tz_localize(None)
            time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
        
        mark_price = values[4] if len(values) > 4 else "?"
        
        self.selected_info_label.config(
            text=f"Выбран: {opt_type} {strike} (Mark={mark_price}) на {time_str}",
            foreground='green'
        )
        
        # Обновляем отображение выбора в таблице
        self.update_options_table()
    
    def add_position(self):
        """Добавить выбранный опцион в портфель"""
        if not self.selected_symbol:
            messagebox.showwarning("Внимание", "Выберите опцион для добавления (кликните по строке)")
            return
        
        if not self.current_expiry:
            messagebox.showwarning("Внимание", "Выберите дату экспирации")
            return
        
        # Получаем текущее время (время входа)
        if not self.app.all_times or self.app.current_time_idx >= len(self.app.all_times):
            messagebox.showwarning("Внимание", "Нет данных о времени")
            return
        
        entry_time = self.app.all_times[self.app.current_time_idx]
        
        # Получаем цену входа
        entry_price = self.portfolio_manager.get_position_price_at_time(
            self.selected_symbol, entry_time, 'markPrice'
        )
        
        if entry_price is None:
            messagebox.showwarning("Внимание", f"Не удалось получить цену для {self.selected_symbol} в выбранное время")
            return
        
        # Добавляем позицию
        position = self.portfolio_manager.add_position(
            symbol=self.selected_symbol,
            side=self.side_var.get(),
            quantity=self.quantity_var.get(),
            strike=self.selected_strike,
            option_type=self.selected_type,
            expiry=self.current_expiry,
            entry_time=entry_time,
            entry_price=entry_price
        )
        
        # Обновляем отображение
        self.update_positions_table()
        self.update_portfolio_status()
        
        # Форматируем время для отображения
        time_str = ""
        if hasattr(entry_time, 'tz') and entry_time.tz is not None:
            entry_time_display = entry_time.tz_localize(None)
            time_str = entry_time_display.strftime('%Y-%m-%d %H:%M:%S')
        else:
            time_str = entry_time.strftime('%Y-%m-%d %H:%M:%S')
        
        messagebox.showinfo("Успех", 
                           f"Позиция добавлена:\n"
                           f"Символ: {self.selected_symbol}\n"
                           f"Направление: {'Long' if self.side_var.get() == 'long' else 'Short'}\n"
                           f"Количество: {self.quantity_var.get()}\n"
                           f"Время входа: {time_str}\n"
                           f"Цена входа: {entry_price:.2f}")
    
    def update_positions_table(self):
        """Обновить таблицу позиций"""
        for item in self.positions_tree.get_children():
            self.positions_tree.delete(item)
        
        current_time = None
        if self.app.all_times and 0 <= self.app.current_time_idx < len(self.app.all_times):
            current_time = self.app.all_times[self.app.current_time_idx]
        
        total_pnl = 0
        
        for i, position in enumerate(self.portfolio_manager.positions):
            summary = self.portfolio_manager.get_position_summary(position, current_time)
            
            # Форматируем время входа
            entry_time_str = ""
            if summary['entry_time']:
                entry_time = summary['entry_time']
                if hasattr(entry_time, 'tz') and entry_time.tz is not None:
                    entry_time = entry_time.tz_localize(None)
                entry_time_str = entry_time.strftime('%Y-%m-%d %H:%M:%S')
            
            entry_price_str = f"{summary['entry_price']:.2f}" if summary['entry_price'] else "—"
            current_price_str = f"{summary['current_price']:.2f}" if summary['current_price'] else "—"
            pnl_str = f"{summary['pnl']:.2f}" if summary['pnl'] is not None else "—"
            
            if summary['pnl'] is not None:
                total_pnl += summary['pnl']
            
            # Определяем цвет строки
            tags = ('call',) if summary['type'] == 'C' else ('put',)
            
            self.positions_tree.insert('', tk.END, values=(
                summary['symbol'],
                summary['type'],
                summary['strike'],
                summary['side'],
                summary['quantity'],
                entry_time_str,
                entry_price_str,
                current_price_str,
                pnl_str
            ), tags=tags, iid=str(i))
        
        # Настройка цветов
        self.positions_tree.tag_configure('call', background='#e8f5e9')
        self.positions_tree.tag_configure('put', background='#ffebee')
        
        self.portfolio_status.config(text=f"Позиций: {len(self.portfolio_manager.positions)} | Общий P&L: {total_pnl:.2f}")
    
    def remove_selected_position(self):
        """Удалить выбранную позицию"""
        selection = self.positions_tree.selection()
        if selection:
            index = int(selection[0])
            self.portfolio_manager.remove_position(index)
            self.update_positions_table()
            self.update_portfolio_status()
    
    def clear_all_positions(self):
        """Очистить все позиции"""
        self.portfolio_manager.clear_all_positions()
        self.update_positions_table()
        self.update_portfolio_status()
    
    def update_portfolio_status(self):
        """Обновить статус портфеля"""
        current_time = None
        if self.app.all_times and 0 <= self.app.current_time_idx < len(self.app.all_times):
            current_time = self.app.all_times[self.app.current_time_idx]
        
        total_pnl = self.portfolio_manager.calculate_portfolio_pnl_at_time(current_time)
        total_pnl_str = f"{total_pnl:.2f}" if total_pnl is not None else "0.00"
        self.portfolio_status.config(text=f"Позиций: {len(self.portfolio_manager.positions)} | Общий P&L: {total_pnl_str}")
    
    def plot_portfolio(self):
        """Построить графики портфеля с возможностью обрезки по времени входа"""
        if not self.portfolio_manager.positions:
            messagebox.showwarning("Внимание", "Нет позиций в портфеле")
            return
        
        if not self.app.all_times:
            messagebox.showwarning("Внимание", "Нет данных по времени")
            return
        
        try:
            # Определяем диапазон времени для отображения
            if self.plot_from_entry_var.get():
                # Находим самое позднее время входа среди всех позиций
                latest_entry_time = None
                for position in self.portfolio_manager.positions:
                    if position.entry_time:
                        if latest_entry_time is None or position.entry_time > latest_entry_time:
                            latest_entry_time = position.entry_time
                
                if latest_entry_time:
                    # Находим индекс самого позднего времени входа
                    entry_idx = None
                    for i, time in enumerate(self.app.all_times):
                        if time >= latest_entry_time:
                            entry_idx = i
                            break
                    
                    if entry_idx is not None:
                        times_to_plot = self.app.all_times[entry_idx:]
                    else:
                        times_to_plot = self.app.all_times
                else:
                    times_to_plot = self.app.all_times
            else:
                times_to_plot = self.app.all_times
            
            if not times_to_plot:
                messagebox.showwarning("Внимание", "Нет данных для отображения после времени входа")
                return
            
            # Обновляем историю P&L только для выбранного диапазона
            self.portfolio_manager.update_pnl_history(times_to_plot)
            
            # Очищаем графики
            self.figure_pnl.clear()
            self.figure_greeks.clear()
            
            ax_pnl = self.figure_pnl.add_subplot(111)
            ax_greeks = self.figure_greeks.add_subplot(111)
            
            # Подготавливаем данные
            times = []
            pnl_values = []
            greeks_history = {'delta': [], 'gamma': [], 'vega': [], 'theta': []}
            
            # Словарь для хранения P&L по каждой позиции
            individual_pnl = {}
            
            for time in times_to_plot:
                # Преобразуем время для отображения
                display_time = time
                if hasattr(time, 'tz') and time.tz is not None:
                    display_time = time.tz_localize(None)
                
                times.append(display_time)
                
                # P&L
                pnl = self.portfolio_manager.total_pnl_history.get(time, 0)
                pnl_values.append(pnl)
                
                # Если нужно показать P&L по каждой позиции
                if self.show_individual_pnl_var.get():
                    for idx, position in enumerate(self.portfolio_manager.positions):
                        pnl_pos = self.portfolio_manager.get_position_pnl(position, time)
                        if pnl_pos is not None:
                            if idx not in individual_pnl:
                                individual_pnl[idx] = {'times': [], 'values': [], 'label': f"{position.symbol} ({position.side})"}
                            individual_pnl[idx]['times'].append(display_time)
                            individual_pnl[idx]['values'].append(pnl_pos)
                
                # Греки
                greeks = self.portfolio_manager.calculate_portfolio_greeks_at_time(time)
                for greek in greeks_history.keys():
                    greeks_history[greek].append(greeks[greek])
            
            # График общего P&L
            ax_pnl.plot(times, pnl_values, color='blue', linewidth=2, 
                       label='Portfolio P&L', marker='o', markersize=3)
            ax_pnl.set_ylabel('P&L', fontsize=10, color='blue')
            ax_pnl.tick_params(axis='y', labelcolor='blue')
            ax_pnl.grid(True, alpha=0.3, linestyle='--')
            ax_pnl.set_xlabel('Время', fontsize=10)
            
            # Добавляем линию нуля
            ax_pnl.axhline(y=0, color='red', linestyle='--', alpha=0.5, linewidth=1)
            
            # Заливаем область
            ax_pnl.fill_between(times, 0, pnl_values, 
                               where=np.array(pnl_values) >= 0, 
                               color='green', alpha=0.3, label='Profit')
            ax_pnl.fill_between(times, 0, pnl_values, 
                               where=np.array(pnl_values) < 0, 
                               color='red', alpha=0.3, label='Loss')
            
            # Добавляем вертикальную линию в момент входа
            if self.plot_from_entry_var.get():
                for position in self.portfolio_manager.positions:
                    if position.entry_time:
                        entry_time_display = position.entry_time
                        if hasattr(entry_time_display, 'tz') and entry_time_display.tz is not None:
                            entry_time_display = entry_time_display.tz_localize(None)
                        ax_pnl.axvline(x=entry_time_display, color='purple', 
                                      linestyle=':', alpha=0.5, linewidth=1)
            
            # Добавляем графики P&L по отдельным позициям
            if self.show_individual_pnl_var.get():
                colors = ['orange', 'brown', 'pink', 'cyan', 'magenta', 'olive', 'navy']
                for idx, data in individual_pnl.items():
                    color = colors[idx % len(colors)]
                    ax_pnl.plot(data['times'], data['values'], 
                              color=color, linewidth=1.5, alpha=0.6,
                              linestyle='--', label=data['label'])
            
            ax_pnl.legend(loc='upper left', fontsize=8, ncol=2 if self.show_individual_pnl_var.get() else 1)
            
            # График греков
            colors_greeks = {'delta': 'blue', 'gamma': 'green', 'vega': 'orange', 'theta': 'red'}
            labels = {'delta': 'Delta', 'gamma': 'Gamma', 'vega': 'Vega', 'theta': 'Theta'}
            
            for greek, values in greeks_history.items():
                if any(v != 0 for v in values):
                    ax_greeks.plot(times, values, color=colors_greeks[greek], linewidth=1.5,
                                  label=labels[greek], alpha=0.7)
            
            ax_greeks.set_ylabel('Греки', fontsize=10)
            ax_greeks.grid(True, alpha=0.3, linestyle='--')
            ax_greeks.set_xlabel('Время', fontsize=10)
            ax_greeks.legend(loc='upper left', fontsize=9)
            
            # Добавляем вертикальные линии на график греков
            if self.plot_from_entry_var.get():
                for position in self.portfolio_manager.positions:
                    if position.entry_time:
                        entry_time_display = position.entry_time
                        if hasattr(entry_time_display, 'tz') and entry_time_display.tz is not None:
                            entry_time_display = entry_time_display.tz_localize(None)
                        ax_greeks.axvline(x=entry_time_display, color='purple', 
                                         linestyle=':', alpha=0.5, linewidth=1)
            
            # Настройка оси X
            for ax in [ax_pnl, ax_greeks]:
                ax.tick_params(axis='x', rotation=45)
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%a %H:%M'))
                ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            
            self.figure_pnl.tight_layout()
            self.figure_greeks.tight_layout()
            
            self.canvas_pnl.draw()
            self.canvas_greeks.draw()
            
            status_msg = f"Построены графики портфеля ({len(self.portfolio_manager.positions)} позиций)"
            if self.plot_from_entry_var.get():
                status_msg += " (с момента покупки)"
            self.app.status_label.config(text=status_msg)
            
        except Exception as e:
            messagebox.showerror("Ошибка построения", str(e))
            import traceback
            traceback.print_exc()


class FullScreenGraphWindow:
    """Класс для отображения графика в полноэкранном режиме"""
    
    def __init__(self, parent, figure, title, graph_type=None, expiry_date=None):
        self.parent = parent
        self.original_figure = figure
        self.title = title
        self.graph_type = graph_type
        self.expiry_date = expiry_date
        self.window = None
        self.graph = None
        self.canvas = None
        self.toolbar = None
        self.screenshot_button = None
        self.create_window()
    
    def create_window(self):
        """Создать новое окно с графиком на весь экран"""
        self.window = tk.Toplevel(self.parent)
        
        # Добавляем дату экспирации в заголовок если есть
        if self.expiry_date:
            display_date = self.expiry_date[:10] if len(self.expiry_date) >= 10 else self.expiry_date
            self.window.title(f"{self.title} - Экспирация: {display_date}")
        else:
            self.window.title(self.title)
        
        # На весь экран
        self.window.state('zoomed')
        
        # Основной фрейм
        main_frame = ttk.Frame(self.window)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Фрейм для графика
        graph_frame = ttk.Frame(main_frame)
        graph_frame.pack(fill=tk.BOTH, expand=True)
        
        # Создаем новую фигуру
        new_figure = Figure(figsize=(16, 9), dpi=120)
        new_ax = new_figure.add_subplot(111)
        
        # Копируем данные с оригинального графика
        if self.original_figure.get_axes():
            original_ax = self.original_figure.get_axes()[0]
            
            # Копируем линии
            for line in original_ax.get_lines():
                x_data = line.get_xdata()
                y_data = line.get_ydata()
                
                if len(x_data) > 0 and len(y_data) > 0:
                    new_ax.plot(x_data, y_data,
                               color=line.get_color(),
                               linewidth=line.get_linewidth(),
                               label=line.get_label(),
                               linestyle=line.get_linestyle(),
                               marker=line.get_marker(),
                               markersize=line.get_markersize(),
                               alpha=line.get_alpha())
            
            # Копируем настройки осей
            new_ax.set_xlabel(original_ax.get_xlabel())
            new_ax.set_ylabel(original_ax.get_ylabel())
            new_ax.set_title(original_ax.get_title())
            new_ax.grid(True, alpha=0.3, linestyle='--')
            
            # Копируем легенду
            if original_ax.get_legend():
                lines = new_ax.get_lines()
                labels = [line.get_label() for line in original_ax.get_lines()]
                if lines and labels:
                    new_ax.legend(lines, labels, loc='upper left', bbox_to_anchor=(1.02, 1))
        
        # Настройка формата оси X
        new_ax.xaxis.set_major_formatter(mdates.DateFormatter('%a %H:%M'))
        new_ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        new_ax.tick_params(axis='x', rotation=45)
        
        new_figure.tight_layout()
        
        # Создаем канвас
        self.canvas = FigureCanvasTkAgg(new_figure, graph_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Создаем стандартную тулбар matplotlib
        toolbar_frame = ttk.Frame(main_frame)
        toolbar_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        self.toolbar.update()
        
        # СОЗДАЕМ КНОПКУ СКРИНШОТА НА ХОЛСТЕ
        self.create_screenshot_button_on_canvas()
        
        # Создаем интерактивный график
        axes = [new_ax]
        self.graph = InteractiveGraph(new_figure, axes, parent=self.window, graph_type=self.graph_type)
        self.graph.connect_events(self.canvas)
        
        # Нижняя панель с дополнительными кнопками
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(btn_frame, text="Закрыть окно",
                  command=self.window.destroy, width=20).pack(side=tk.LEFT, padx=5)
        
        # Обновляем канвас
        self.canvas.draw()
        
        # Обработка закрытия окна
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
    
    def create_screenshot_button_on_canvas(self):
        """Создать кнопку скриншота непосредственно на холсте графика"""
        button_frame = tk.Frame(self.canvas.get_tk_widget(), bg='white', relief=tk.RAISED, bd=1)
        button_frame.place(x=10, y=10, anchor='nw')
        
        ttk.Label(button_frame, text="📸 Скриншот:", 
                 font=('Arial', 9, 'bold')).pack(padx=2, pady=(2, 0))
        
        buttons_inner = ttk.Frame(button_frame)
        buttons_inner.pack(padx=2, pady=2)
        
        self.copy_btn = ttk.Button(
            buttons_inner, 
            text="📋 В буфер", 
            command=self.copy_to_clipboard,
            width=10
        )
        self.copy_btn.pack(side=tk.LEFT, padx=1)
        
        self.save_btn = ttk.Button(
            buttons_inner, 
            text="💾 В файл", 
            command=self.save_screenshot,
            width=10
        )
        self.save_btn.pack(side=tk.LEFT, padx=1)
        
        self.create_tooltip(self.copy_btn, "Копировать график в буфер обмена (Ctrl+V)")
        self.create_tooltip(self.save_btn, "Сохранить график в PNG файл")
        
        button_frame.lift()
    
    def create_tooltip(self, widget, text):
        """Создать всплывающую подсказку для виджета"""
        def show_tooltip(event):
            tooltip = tk.Toplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")
            
            label = ttk.Label(tooltip, text=text, background="#ffffe0", 
                             relief="solid", borderwidth=1, padding=2)
            label.pack()
            
            def hide_tooltip():
                tooltip.destroy()
            
            widget.tooltip = tooltip
            widget.after(3000, hide_tooltip)
        
        def hide_tooltip(event):
            if hasattr(widget, 'tooltip'):
                widget.tooltip.destroy()
        
        widget.bind('<Enter>', show_tooltip)
        widget.bind('<Leave>', hide_tooltip)
    
    def copy_to_clipboard(self):
        """Скопировать скриншот в буфер обмена"""
        try:
            import io
            from PIL import ImageGrab, Image
            import platform
            
            self.hide_buttons()
            self.canvas.draw()
            
            canvas_widget = self.canvas.get_tk_widget()
            x = canvas_widget.winfo_rootx()
            y = canvas_widget.winfo_rooty()
            width = canvas_widget.winfo_width()
            height = canvas_widget.winfo_height()
            
            image = ImageGrab.grab(bbox=(x, y, x + width, y + height))
            
            if platform.system() == 'Windows':
                import win32clipboard
                from io import BytesIO
                
                output = BytesIO()
                image.convert('RGB').save(output, format='BMP')
                data = output.getvalue()[14:]
                output.close()
                
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                win32clipboard.CloseClipboard()
                
                messagebox.showinfo("Успех", "График скопирован в буфер обмена")
            
            elif platform.system() == 'Darwin':
                from subprocess import Popen, PIPE
                
                with io.BytesIO() as output:
                    image.save(output, format='PNG')
                    data = output.getvalue()
                    
                    p = Popen(['pngpaste', '-'], stdin=PIPE, stdout=PIPE, stderr=PIPE)
                    p.communicate(input=data)
                    
                    messagebox.showinfo("Успех", "График скопирован в буфер обмена")
            
            else:
                from subprocess import Popen, PIPE
                
                with io.BytesIO() as output:
                    image.save(output, format='PNG')
                    data = output.getvalue()
                    
                    p = Popen(['xclip', '-selection', 'clipboard', '-t', 'image/png', '-i'], 
                             stdin=PIPE, stdout=PIPE, stderr=PIPE)
                    p.communicate(input=data)
                    
                    messagebox.showinfo("Успех", "График скопирован в буфер обмена")
            
        except ImportError as e:
            messagebox.showerror("Ошибка", 
                               f"Не удалось скопировать в буфер обмена. Установите необходимые библиотеки:\n"
                               f"pip install pillow pywin32 (для Windows)\n"
                               f"Или: pip install pillow xclip (для Linux)\n"
                               f"Ошибка: {str(e)}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось скопировать в буфер обмена:\n{str(e)}")
        finally:
            self.show_buttons()
            self.canvas.draw()
    
    def hide_buttons(self):
        """Скрыть кнопки перед скриншотом"""
        if hasattr(self, 'screenshot_button') and self.screenshot_button:
            self.screenshot_button.master.place_forget()
    
    def show_buttons(self):
        """Показать кнопки после скриншота"""
        if hasattr(self, 'screenshot_button') and self.screenshot_button:
            self.screenshot_button.master.place(x=10, y=10, anchor='nw')
    
    def save_screenshot(self):
        """Сохранить скриншот в файл"""
        from datetime import datetime
        from tkinter import filedialog, messagebox
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if self.expiry_date:
            display_date = self.expiry_date[:10] if len(self.expiry_date) >= 10 else self.expiry_date
            filename = f"screenshot_{self.graph_type}_{display_date}_{timestamp}.png"
        else:
            filename = f"screenshot_{self.graph_type}_{timestamp}.png"
        
        file_path = filedialog.asksaveasfilename(
            title="Сохранить скриншот",
            defaultextension=".png",
            filetypes=[("PNG files", "*.png"), ("All files", "*.*")],
            initialfile=filename
        )
        
        if file_path:
            try:
                self.hide_buttons()
                self.canvas.draw()
                
                figure = self.canvas.figure
                figure.savefig(file_path, dpi=300, bbox_inches='tight', 
                              facecolor='white', edgecolor='none')
                
                messagebox.showinfo("Успех", f"Скриншот сохранен:\n{file_path}")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить скриншот:\n{str(e)}")
            finally:
                self.show_buttons()
                self.canvas.draw()


class CustomNavigationToolbar(NavigationToolbar2Tk):
    """Кастомная тулбар с дополнительной кнопкой для полноэкранного режима"""
    
    def __init__(self, canvas, window, graph_type, parent_app):
        self.graph_type = graph_type
        self.parent_app = parent_app
        super().__init__(canvas, window)
        self.add_fullscreen_button()
    
    def add_fullscreen_button(self):
        """Добавить кнопку полноэкранного режима"""
        self.fullscreen_btn = ttk.Button(self, text="Full Screen",
                                        command=self.open_fullscreen)
        self.fullscreen_btn.pack(side=tk.LEFT, padx=2)
    
    def open_fullscreen(self):
        """Открыть график в полноэкранном режиме"""
        figure = self.canvas.figure
        
        titles = {
            'price': "График цен опционов - полноэкранный режим",
            'iv': "График волатильности опционов - полноэкранный режим",
            'index': "Цена индекса - полноэкранный режим",
            'atm_iv': "Индексная волатильность - полноэкранный режим"
        }
        title = titles.get(self.graph_type, "График - полноэкранный режим")
        
        expiry_date = None
        if hasattr(self.parent_app, 'atm_tab') and self.parent_app.atm_tab:
            if hasattr(self.parent_app.atm_tab, 'selected_expiry'):
                expiry_date = self.parent_app.atm_tab.selected_expiry
        elif hasattr(self.parent_app, 'current_expiry_date'):
            expiry_date = self.parent_app.current_expiry_date
        
        parent_window = self.parent_app.root if hasattr(self.parent_app, 'root') else self.parent_app
        
        FullScreenGraphWindow(parent_window, figure, title, self.graph_type, expiry_date)


class InteractiveGraph:
    """Класс для управления интерактивным графиком"""
    
    def __init__(self, figure, axes, sync_graph=None, parent=None, graph_type=None):
        self.figure = figure
        self.axes = axes
        self.drag_start = None
        self.zoom_factor = 1.15
        self.is_dragging = False
        self.sync_graph = sync_graph
        self.parent = parent
        self.graph_type = graph_type
        self.annotation = None
        self.canvas = None
        self.last_click_time = 0
        self._last_mouse_pos = None
        self._last_check_time = 0
        
    def connect_events(self, canvas):
        """Привязать события мыши к холсту"""
        self.canvas = canvas
        canvas_widget = canvas.get_tk_widget()
        
        canvas_widget.bind("<ButtonPress-1>", self.on_press)
        canvas_widget.bind("<B1-Motion>", self.on_drag)
        canvas_widget.bind("<ButtonRelease-1>", self.on_release)
        canvas_widget.bind("<Double-Button-1>", self.on_double_click)
        canvas_widget.bind("<MouseWheel>", self.on_scroll)
        canvas_widget.bind("<Button-4>", self.on_scroll)
        canvas_widget.bind("<Button-5>", self.on_scroll)
        canvas_widget.bind("<Motion>", self.on_mouse_move)
        canvas_widget.bind("<Leave>", self.on_mouse_leave)
    
    @staticmethod
    def _normalize_time(value):
        """Нормализовать временное значение для преобразования"""
        if hasattr(value, 'timestamp'):
            return mdates.date2num(value.to_pydatetime())
        elif hasattr(value, 'toordinal'):
            return mdates.date2num(value)
        return value
    
    def on_mouse_move(self, event):
        """Обработка движения мыши для отображения информации при наведении"""
        if self.is_dragging or not self.canvas:
            return
        
        self._last_mouse_pos = (event.x, event.y)
        
        x_pixel, y_pixel = event.x, event.y
        
        canvas_height = self.canvas.get_tk_widget().winfo_height()
        y_pixel_inverted = canvas_height - y_pixel
        
        found = False
        
        for ax in self.axes:
            if ax is None:
                continue
            
            if ax.bbox.contains(x_pixel, y_pixel_inverted):
                min_dist = float('inf')
                best_point = None
                best_line = None
                best_ax = None
                
                for line in ax.get_lines():
                    x_line = line.get_xdata()
                    y_line = line.get_ydata()
                    
                    if len(x_line) == 0:
                        continue
                    
                    try:
                        x_numeric = np.array([self._normalize_time(x) for x in x_line])
                        
                        x_display, y_display = ax.transData.transform(np.column_stack([x_numeric, y_line])).T
                        
                        dx = x_display - x_pixel
                        dy = y_display - y_pixel_inverted
                        distances = np.sqrt(dx*dx + dy*dy)
                        
                        min_idx = np.argmin(distances)
                        min_dist_point = distances[min_idx]
                        
                        if min_dist_point < min_dist and min_dist_point < 20:
                            min_dist = min_dist_point
                            best_point = (x_line[min_idx], y_line[min_idx])
                            best_line = line
                            best_ax = ax
                            
                    except Exception as e:
                        for i in range(len(x_line)):
                            try:
                                x_val = x_line[i]
                                x_numeric = self._normalize_time(x_val)
                                
                                x_display, y_display = ax.transData.transform((x_numeric, y_line[i]))
                                
                                dx = x_display - x_pixel
                                dy = y_display - y_pixel_inverted
                                dist = np.sqrt(dx*dx + dy*dy)
                                
                                if dist < min_dist and dist < 20:
                                    min_dist = dist
                                    best_point = (x_val, y_line[i])
                                    best_line = line
                                    best_ax = ax
                            except:
                                continue
                
                if best_point is not None:
                    self.show_annotation(best_line, best_point, best_ax)
                    found = True
                    break
        
        if not found:
            self.hide_annotation()
    
    def on_mouse_leave(self, event):
        """Скрыть аннотацию при выходе мыши за пределы графика"""
        self.hide_annotation()
        self._last_mouse_pos = None
    
    def show_annotation(self, line, point, ax):
        """Показать аннотацию с информацией о линии"""
        if self.annotation is not None:
            try:
                self.annotation.remove()
            except:
                pass
            self.annotation = None
        
        label = line.get_label()
        x, y = point
        
        clean_label = label.replace('_', ' ').replace('-', ' ').strip()
        if len(clean_label) > 30:
            clean_label = clean_label[:27] + '...'
        
        try:
            if hasattr(x, 'strftime'):
                if hasattr(x, 'to_pydatetime'):
                    x = x.to_pydatetime()
                if hasattr(x, 'tz') and x.tz is not None:
                    x = x.replace(tzinfo=None)
                time_str = x.strftime('%d.%m.%Y %H:%M:%S')
            else:
                time_str = f"{float(x):.2f}"
        except:
            time_str = ""
        
        value_str = f"{y:.2f}"
        
        text = f"{clean_label}\n{time_str}\n{value_str}"
        
        self.annotation = ax.annotate(
            text,
            xy=(x, y),
            xytext=(12, 12),
            textcoords='offset points',
            bbox=dict(
                boxstyle='round,pad=0.4',
                fc='#f0f0f0',
                ec='#808080',
                lw=1,
                alpha=0.95
            ),
            arrowprops=dict(
                arrowstyle='-',
                connectionstyle='arc3,rad=0.2',
                color='#808080',
                lw=1,
                alpha=0.7
            ),
            fontsize=9,
            family='Segoe UI',
            ha='left',
            va='bottom',
            zorder=1000
        )
        
        self.canvas.draw_idle()
    
    def hide_annotation(self):
        """Скрыть аннотацию"""
        if self.annotation is not None:
            try:
                self.annotation.remove()
                self.annotation = None
                if self.canvas:
                    self.canvas.draw_idle()
            except Exception as e:
                pass
    
    @staticmethod
    def get_modifiers(event):
        """Определяем зажатые модификаторы"""
        return {
            'shift': bool(event.state & 0x0001),
            'alt': bool(event.state & 0x0008),
        }
    
    def on_press(self, event):
        """Нажатие мыши"""
        self.drag_start = (event.x, event.y)
        self.is_dragging = False
        self.hide_annotation()
    
    def on_drag(self, event):
        """Перетаскивание мыши"""
        if self.drag_start is None:
            return
        
        self.is_dragging = True
        self.hide_annotation()
        
        dx = event.x - self.drag_start[0]
        dy = event.y - self.drag_start[1]
        
        if dx == 0 and dy == 0:
            return
        
        for ax in self.axes:
            if ax is not None and hasattr(ax, 'get_xlim'):
                xlim = ax.get_xlim()
                ylim = ax.get_ylim()
                
                try:
                    inv = ax.transData.inverted()
                    start_x_data, start_y_data = inv.transform(self.drag_start)
                    end_x_data, end_y_data = inv.transform((event.x, event.y))
                    
                    dx_data = end_x_data - start_x_data
                    dy_data = end_y_data - start_y_data
                    
                    ax.set_xlim(xlim[0] - dx_data, xlim[1] - dx_data)
                    ax.set_ylim(ylim[0] + dy_data, ylim[1] + dy_data)
                    
                except Exception:
                    x_range = xlim[1] - xlim[0]
                    y_range = ylim[1] - ylim[0]
                    
                    bbox = ax.get_window_extent().transformed(
                        self.figure.dpi_scale_trans.inverted()
                    )
                    width_pixels = bbox.width * self.figure.dpi
                    height_pixels = bbox.height * self.figure.dpi
                    
                    dx_data = dx * x_range / width_pixels if width_pixels != 0 else 0
                    dy_data = dy * y_range / height_pixels if height_pixels != 0 else 0
                    
                    ax.set_xlim(xlim[0] - dx_data, xlim[1] - dx_data)
                    ax.set_ylim(ylim[0] + dy_data, ylim[1] + dy_data)
        
        self.drag_start = (event.x, event.y)
        self.canvas.draw()
        
        if self.sync_graph:
            self.sync_x_only()
    
    def sync_x_only(self):
        """Синхронизировать только ось X между графиками"""
        if not self.sync_graph or not self.axes or not self.sync_graph.axes:
            return
        
        current_xlim = None
        for ax in self.axes:
            if ax is not None and hasattr(ax, 'get_xlim'):
                current_xlim = ax.get_xlim()
                break
        
        if current_xlim is None:
            return
        
        for ax in self.sync_graph.axes:
            if ax is not None and hasattr(ax, 'set_xlim'):
                ax.set_xlim(current_xlim)
        
        if self.sync_graph.canvas:
            self.sync_graph.canvas.draw_idle()
    
    def on_release(self, event):
        """Отпускание мыши"""
        self.drag_start = None
        self.is_dragging = False
    
    def on_scroll(self, event):
        """Прокрутка колеса мыши"""
        self.hide_annotation()
        
        mods = self.get_modifiers(event)
        
        if hasattr(event, 'delta'):
            zoom_factor = 1 / self.zoom_factor if event.delta > 0 else self.zoom_factor
        else:
            zoom_factor = 1 / self.zoom_factor if event.num == 4 else self.zoom_factor
        
        for ax in self.axes:
            if ax is None or not hasattr(ax, 'get_xlim'):
                continue
            
            try:
                inv = ax.transData.inverted()
                x_data, y_data = inv.transform((event.x, event.y))
            except Exception:
                xlim = ax.get_xlim()
                ylim = ax.get_ylim()
                x_data = (xlim[0] + xlim[1]) / 2
                y_data = (ylim[0] + ylim[1]) / 2
            
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            x_range = xlim[1] - xlim[0]
            y_range = ylim[1] - ylim[0]
            
            if mods['shift']:
                new_x_range = x_range * zoom_factor
                x_offset = (x_data - xlim[0]) / x_range if x_range != 0 else 0.5
                ax.set_xlim(x_data - new_x_range * x_offset,
                           x_data + new_x_range * (1 - x_offset))
            
            elif mods['alt']:
                new_y_range = y_range * zoom_factor
                y_offset = (y_data - ylim[0]) / y_range if y_range != 0 else 0.5
                ax.set_ylim(y_data - new_y_range * y_offset,
                           y_data + new_y_range * (1 - y_offset))
            
            else:
                new_x_range = x_range * zoom_factor
                new_y_range = y_range * zoom_factor
                
                x_offset = (x_data - xlim[0]) / x_range if x_range != 0 else 0.5
                y_offset = (y_data - ylim[0]) / y_range if y_range != 0 else 0.5
                
                ax.set_xlim(x_data - new_x_range * x_offset,
                           x_data + new_x_range * (1 - x_offset))
                ax.set_ylim(y_data - new_y_range * y_offset,
                           y_data + new_y_range * (1 - y_offset))
        
        self.canvas.draw()
        if self.sync_graph:
            self.sync_x_only()
    
    def on_double_click(self, event):
        """Двойной клик - сброс масштаба"""
        for ax in self.axes:
            if ax is not None:
                ax.relim()
                ax.autoscale_view()
        
        self.canvas.draw()
        
        if self.sync_graph:
            for ax in self.sync_graph.axes:
                if ax is not None:
                    ax.relim()
                    ax.autoscale_view()
            if self.sync_graph.canvas:
                self.sync_graph.canvas.draw()
        
        self.hide_annotation()


class OptionSelectionTab:
    """Класс для вкладки выбора опционов с таблицей"""
    
    def __init__(self, parent, app, expiry_date):
        self.parent = parent
        self.app = app
        self.expiry_date = expiry_date
        self.selected_calls = set()
        self.selected_puts = set()
        self._updating_slider = False
        
        self._symbol_cache = {}
        
        self.setup_ui()
        self.update_table()
        self.parent.bind("<Visibility>", self.on_tab_visible)
    
    def on_tab_visible(self, event):
        """Обновить ползунок при переключении на вкладку"""
        self.update_time_slider()
    
    def setup_ui(self):
        main_frame = ttk.Frame(self.parent)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Панель с ползунком времени
        time_frame = ttk.LabelFrame(main_frame, text="Выбор времени снепшота", padding="5")
        time_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.time_slider = ttk.Scale(time_frame, from_=0, to=100,
                                    orient=tk.HORIZONTAL, command=self.on_time_slider_move)
        self.time_slider.pack(fill=tk.X, pady=(5, 5))
        
        time_control_frame = ttk.Frame(time_frame)
        time_control_frame.pack(fill=tk.X)
        
        ttk.Button(time_control_frame, text="◀",
                  command=lambda: self.move_time(-1), width=5).pack(side=tk.LEFT, padx=2)
        ttk.Button(time_control_frame, text="▶",
                  command=lambda: self.move_time(1), width=5).pack(side=tk.LEFT, padx=2)
        
        self.time_label = ttk.Label(time_control_frame, text="----.--.-- --:--:--")
        self.time_label.pack(side=tk.RIGHT, padx=5)
        
        # Кнопки выбора опционов
        select_frame = ttk.Frame(main_frame)
        select_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Button(select_frame, text="Выбрать все CALL",
                  command=self.select_all_calls, width=15).pack(side=tk.LEFT, padx=2)
        ttk.Button(select_frame, text="Снять все CALL",
                  command=self.deselect_all_calls, width=15).pack(side=tk.LEFT, padx=2)
        ttk.Button(select_frame, text="Выбрать все PUT",
                  command=self.select_all_puts, width=15).pack(side=tk.LEFT, padx=2)
        ttk.Button(select_frame, text="Снять все PUT",
                  command=self.deselect_all_puts, width=15).pack(side=tk.LEFT, padx=2)
        
        # Таблица с опционами
        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill=tk.BOTH, expand=True)
        
        columns = ('Выбор CALL', 'Δ CALL', 'Страйк', 'Δ PUT', 'Выбор PUT')
        self.tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=20)
        
        self.tree.heading('Выбор CALL', text='● CALL')
        self.tree.heading('Δ CALL', text='Δ CALL')
        self.tree.heading('Страйк', text='Страйк')
        self.tree.heading('Δ PUT', text='Δ PUT')
        self.tree.heading('Выбор PUT', text='● PUT')
        
        self.tree.column('Выбор CALL', width=70, anchor='center')
        self.tree.column('Δ CALL', width=80, anchor='center')
        self.tree.column('Страйк', width=100, anchor='center')
        self.tree.column('Δ PUT', width=80, anchor='center')
        self.tree.column('Выбор PUT', width=70, anchor='center')
        
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        
        style = ttk.Style()
        style.configure("Treeview",
                       background="white", foreground="black",
                       rowheight=25, fieldbackground="white",
                       font=('Arial', 9),
                       selectbackground="white", selectforeground="black",
                       highlightthickness=0, borderwidth=0)
        
        style.configure("Treeview.Heading",
                       font=('Arial', 9, 'bold'),
                       background='#f0f0f0', relief='flat')
        
        style.map('Treeview',
                  background=[('selected', 'white')],
                  foreground=[('selected', 'black')])
        
        self.tree.tag_configure('oddrow', background='#f9f9f9')
        self.tree.tag_configure('evenrow', background='white')
        self.tree.tag_configure('selected_call', background='#e6f3ff')
        self.tree.tag_configure('selected_put', background='#ffe6e6')
        
        self.tree.bind('<Button-1>', self.on_tree_click)
        self.tree.bind('<ButtonRelease-1>', lambda e: self.tree.selection_remove(self.tree.selection()))
        
        self.update_time_slider()
    
    def update_time_slider(self):
        """Обновить ползунок времени без вызова команды"""
        if self.app.all_times and not self._updating_slider:
            self._updating_slider = True
            try:
                self.time_slider.configure(from_=0, to=len(self.app.all_times) - 1)
                self.time_slider.set(self.app.current_time_idx)
                self.update_time_label()
            finally:
                self._updating_slider = False
    
    def on_time_slider_move(self, value):
        """Обработка движения ползунка времени"""
        if self._updating_slider:
            return
        
        try:
            idx = int(float(value))
            if 0 <= idx < len(self.app.all_times):
                self._updating_slider = True
                self.app.current_time_idx = idx
                
                for selection_tab in self.app.selection_tabs.values():
                    if selection_tab != self:
                        selection_tab.update_table()
                        selection_tab.update_time_label()
                        selection_tab._updating_slider = True
                        try:
                            selection_tab.time_slider.set(idx)
                        finally:
                            selection_tab._updating_slider = False
                
                self.update_table()
                self.update_time_label()
                self.app.update_tab_indicators()
                
                # Обновляем таблицу портфеля
                if hasattr(self.app, 'portfolio_tab'):
                    self.app.portfolio_tab.update_positions_table()
                
        except Exception as e:
            print(f"Ошибка в on_time_slider_move: {e}")
        finally:
            self._updating_slider = False
    
    def move_time(self, step):
        """Переместить время на указанный шаг"""
        if self.app.all_times:
            new_idx = max(0, min(len(self.app.all_times) - 1, self.app.current_time_idx + step))
            self._updating_slider = True
            self.time_slider.set(new_idx)
            self._updating_slider = False
            self.on_time_slider_move(new_idx)
    
    def update_time_label(self):
        """Обновить метку времени с полной датой"""
        if self.app.all_times and 0 <= self.app.current_time_idx < len(self.app.all_times):
            current_time = self.app.all_times[self.app.current_time_idx]
            if hasattr(current_time, 'tz') and current_time.tz is not None:
                current_time = current_time.tz_localize(None)
            self.time_label.config(text=current_time.strftime('%Y-%m-%d %H:%M:%S'))
    
    def update_table(self):
        """Обновить таблицу с опционами"""
        if self.expiry_date not in self.app.dataframes:
            return
        
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        current_time = None
        if self.app.all_times and 0 <= self.app.current_time_idx < len(self.app.all_times):
            current_time = self.app.all_times[self.app.current_time_idx]
        
        df = self.app.dataframes[self.expiry_date]
        
        if current_time:
            df_time = df[df['fetch_time_utc'] == current_time]
        else:
            df_time = df
        
        strikes_data = {}
        
        for _, row in df_time.iterrows():
            strike = str(int(row['strike'])) if pd.notna(row['strike']) else '0'
            symbol = row['symbol']
            
            parts = symbol.split('-')
            if len(parts) >= 4:
                opt_type = parts[3][0]
                
                if strike not in strikes_data:
                    strikes_data[strike] = {'C': None, 'P': None, 'strike_val': float(strike)}
                
                delta = row.get('delta')
                if pd.notna(delta):
                    strikes_data[strike][opt_type] = delta
        
        sorted_strikes = sorted(strikes_data.items(), key=lambda x: x[1]['strike_val'])
        
        for i, (strike, data) in enumerate(sorted_strikes):
            call_delta = data['C']
            put_delta = data['P']
            
            if call_delta is not None or put_delta is not None:
                call_delta_str = f"{call_delta:.4f}" if call_delta is not None else "—"
                put_delta_str = f"{put_delta:.4f}" if put_delta is not None else "—"
                
                call_selected = '●' if strike in self.selected_calls else '○'
                put_selected = '●' if strike in self.selected_puts else '○'
                
                tags = ['evenrow' if i % 2 == 0 else 'oddrow']
                if strike in self.selected_calls:
                    tags.append('selected_call')
                if strike in self.selected_puts:
                    tags.append('selected_put')
                
                self.tree.insert('', tk.END, values=(
                    call_selected, call_delta_str,
                    f"{int(float(strike)):,}",
                    put_delta_str, put_selected
                ), tags=tuple(tags), iid=strike)
    
    def find_symbol(self, expiry, strike, option_type):
        """Найти символ опциона по экспирации, страйку и типу"""
        cache_key = (expiry, strike, option_type)
        if cache_key in self._symbol_cache:
            return self._symbol_cache[cache_key]
        
        if expiry not in self.app.option_symbols:
            return None
        
        strike_str = str(int(strike))
        for symbol in self.app.option_symbols[expiry]:
            parts = symbol.split('-')
            if len(parts) >= 4:
                symbol_strike = parts[2]
                symbol_type = parts[3][0]
                if symbol_strike == strike_str and symbol_type == option_type:
                    self._symbol_cache[cache_key] = symbol
                    return symbol
        
        self._symbol_cache[cache_key] = None
        return None
    
    def on_tree_click(self, event):
        """Обработка клика по таблице"""
        region = self.tree.identify_region(event.x, event.y)
        if region != 'cell':
            return
        
        column = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item:
            return
        
        col_num = int(column.replace('#', ''))
        
        if col_num == 1:
            current = self.tree.item(item, 'values')[0]
            if current == '●':
                self.tree.set(item, 'Выбор CALL', '○')
                self.selected_calls.discard(item)
                current_tags = list(self.tree.item(item, 'tags'))
                if 'selected_call' in current_tags:
                    current_tags.remove('selected_call')
                self.tree.item(item, tags=tuple(current_tags))
            else:
                self.tree.set(item, 'Выбор CALL', '●')
                self.selected_calls.add(item)
                current_tags = list(self.tree.item(item, 'tags'))
                current_tags.append('selected_call')
                self.tree.item(item, tags=tuple(current_tags))
        
        elif col_num == 5:
            current = self.tree.item(item, 'values')[4]
            if current == '●':
                self.tree.set(item, 'Выбор PUT', '○')
                self.selected_puts.discard(item)
                current_tags = list(self.tree.item(item, 'tags'))
                if 'selected_put' in current_tags:
                    current_tags.remove('selected_put')
                self.tree.item(item, tags=tuple(current_tags))
            else:
                self.tree.set(item, 'Выбор PUT', '●')
                self.selected_puts.add(item)
                current_tags = list(self.tree.item(item, 'tags'))
                current_tags.append('selected_put')
                self.tree.item(item, tags=tuple(current_tags))
        
        self.app.update_selected_count()
        self.app.update_tab_indicators()
    
    def select_all_calls(self):
        """Выбрать все CALL опционы"""
        for item in self.tree.get_children():
            self.tree.set(item, 'Выбор CALL', '●')
            self.selected_calls.add(item)
            current_tags = list(self.tree.item(item, 'tags'))
            if 'selected_call' not in current_tags:
                current_tags.append('selected_call')
            self.tree.item(item, tags=tuple(current_tags))
        self.app.update_selected_count()
        self.app.update_tab_indicators()
    
    def deselect_all_calls(self):
        """Снять все CALL опционы"""
        for item in self.tree.get_children():
            self.tree.set(item, 'Выбор CALL', '○')
            self.selected_calls.discard(item)
            current_tags = list(self.tree.item(item, 'tags'))
            if 'selected_call' in current_tags:
                current_tags.remove('selected_call')
            self.tree.item(item, tags=tuple(current_tags))
        self.app.update_selected_count()
        self.app.update_tab_indicators()
    
    def select_all_puts(self):
        """Выбрать все PUT опционы"""
        for item in self.tree.get_children():
            self.tree.set(item, 'Выбор PUT', '●')
            self.selected_puts.add(item)
            current_tags = list(self.tree.item(item, 'tags'))
            if 'selected_put' not in current_tags:
                current_tags.append('selected_put')
            self.tree.item(item, tags=tuple(current_tags))
        self.app.update_selected_count()
        self.app.update_tab_indicators()
    
    def deselect_all_puts(self):
        """Снять все PUT опционы"""
        for item in self.tree.get_children():
            self.tree.set(item, 'Выбор PUT', '○')
            self.selected_puts.discard(item)
            current_tags = list(self.tree.item(item, 'tags'))
            if 'selected_put' in current_tags:
                current_tags.remove('selected_put')
            self.tree.item(item, tags=tuple(current_tags))
        self.app.update_selected_count()
        self.app.update_tab_indicators()
    
    def get_selected_symbols(self):
        """Получить символы выбранных опционов"""
        symbols = []
        
        for strike in self.selected_calls:
            symbol = self.find_symbol(self.expiry_date, strike, 'C')
            if symbol:
                symbols.append(symbol)
        
        for strike in self.selected_puts:
            symbol = self.find_symbol(self.expiry_date, strike, 'P')
            if symbol:
                symbols.append(symbol)
        
        return symbols


class ATMTab:
    """Класс для вкладки ATM/ITM опционов с расчетом волатильности по дельте"""
    
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.selected_expiry = None
        self.target_delta = tk.DoubleVar(value=0.5)
        
        self.show_index_iv = tk.BooleanVar(value=True)
        self.show_put_mark = tk.BooleanVar(value=False)
        self.show_put_bid = tk.BooleanVar(value=False)
        self.show_put_ask = tk.BooleanVar(value=False)
        self.show_call_mark = tk.BooleanVar(value=False)
        self.show_call_bid = tk.BooleanVar(value=False)
        self.show_call_ask = tk.BooleanVar(value=False)
        
        self._iv_cache = {}
        self._index_price_cache = {}
        
        self.figure_index = None
        self.figure_iv = None
        self.canvas_index = None
        self.canvas_iv = None
        self.index_toolbar_frame = None
        self.iv_toolbar_frame = None
        self.graph_index = None
        self.graph_iv_atm = None
        
        self.setup_ui()
    
    def setup_ui(self):
        """Настройка пользовательского интерфейса"""
        main_frame = ttk.Frame(self.parent)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(top_frame, text="Выберите дату экспирации:",
                 font=('Arial', 10, 'bold')).pack(side=tk.LEFT, padx=(0, 10))
        
        self.expiry_combo = ttk.Combobox(top_frame, state="readonly", width=20)
        self.expiry_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.expiry_combo.bind('<<ComboboxSelected>>', self.on_expiry_selected)
        
        ttk.Label(top_frame, text="Целевая дельта (по модулю):",
                 font=('Arial', 10)).pack(side=tk.LEFT, padx=(10, 5))
        
        delta_entry = ttk.Entry(top_frame, textvariable=self.target_delta, width=8)
        delta_entry.pack(side=tk.LEFT, padx=5)
        ttk.Label(top_frame, text="(0-1)").pack(side=tk.LEFT)
        
        paned = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        index_price_frame = ttk.LabelFrame(paned, text="Цена индекса", padding="5")
        paned.add(index_price_frame, weight=1)
        
        index_container = ttk.Frame(index_price_frame)
        index_container.pack(fill=tk.BOTH, expand=True)
        
        self.index_toolbar_frame = ttk.Frame(index_container)
        self.index_toolbar_frame.pack(fill=tk.X)
        
        self.index_price_canvas_frame = ttk.Frame(index_container)
        self.index_price_canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        self.figure_index = Figure(figsize=(10, 3), dpi=100)
        self.canvas_index = FigureCanvasTkAgg(self.figure_index, self.index_price_canvas_frame)
        self.canvas_index.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        iv_frame = ttk.LabelFrame(paned, text="Волатильность по дельте", padding="5")
        paned.add(iv_frame, weight=1)
        
        iv_container = ttk.Frame(iv_frame)
        iv_container.pack(fill=tk.BOTH, expand=True)
        
        self.iv_toolbar_frame = ttk.Frame(iv_container)
        self.iv_toolbar_frame.pack(fill=tk.X)
        
        self.iv_canvas_frame = ttk.Frame(iv_container)
        self.iv_canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        self.figure_iv = Figure(figsize=(10, 3), dpi=100)
        self.canvas_iv = FigureCanvasTkAgg(self.figure_iv, self.iv_canvas_frame)
        self.canvas_iv.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=(10, 0))
        
        row1_frame = ttk.Frame(control_frame)
        row1_frame.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(row1_frame, text="Index IV (по центр. страйку)",
                       variable=self.show_index_iv, width=25).pack(side=tk.LEFT, padx=2)
        
        row2_frame = ttk.Frame(control_frame)
        row2_frame.pack(fill=tk.X, pady=2)
        ttk.Label(row2_frame, text="PUT по дельте:", font=('Arial', 9, 'bold')).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(row2_frame, text="Mark IV", variable=self.show_put_mark, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(row2_frame, text="Bid IV", variable=self.show_put_bid, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(row2_frame, text="Ask IV", variable=self.show_put_ask, width=10).pack(side=tk.LEFT, padx=2)
        
        row3_frame = ttk.Frame(control_frame)
        row3_frame.pack(fill=tk.X, pady=2)
        ttk.Label(row3_frame, text="CALL по дельте:", font=('Arial', 9, 'bold')).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(row3_frame, text="Mark IV", variable=self.show_call_mark, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(row3_frame, text="Bid IV", variable=self.show_call_bid, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(row3_frame, text="Ask IV", variable=self.show_call_ask, width=10).pack(side=tk.LEFT, padx=2)
        
        row4_frame = ttk.Frame(control_frame)
        row4_frame.pack(fill=tk.X, pady=5)
        ttk.Label(row4_frame, text="Быстрый выбор:", font=('Arial', 9, 'bold')).pack(side=tk.LEFT, padx=5)
        ttk.Button(row4_frame, text="Все", command=self.select_all, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(row4_frame, text="Снять все", command=self.deselect_all, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(row4_frame, text="Index", command=self.select_only_index, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(row4_frame, text="PUT все", command=self.select_all_put, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(row4_frame, text="CALL все", command=self.select_all_call, width=8).pack(side=tk.LEFT, padx=2)
        
        row5_frame = ttk.Frame(control_frame)
        row5_frame.pack(fill=tk.X, pady=5)
        ttk.Button(row5_frame, text="Построить графики",
                  command=self.plot_atm_itm, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(row5_frame, text="Сбросить всё",
                  command=self.reset_params, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Button(row5_frame, text="Обновить",
                  command=self.refresh_plots, width=15).pack(side=tk.LEFT, padx=5)
    
    def select_all(self):
        self.show_index_iv.set(True)
        self.show_put_mark.set(True)
        self.show_put_bid.set(True)
        self.show_put_ask.set(True)
        self.show_call_mark.set(True)
        self.show_call_bid.set(True)
        self.show_call_ask.set(True)
    
    def deselect_all(self):
        self.show_index_iv.set(False)
        self.show_put_mark.set(False)
        self.show_put_bid.set(False)
        self.show_put_ask.set(False)
        self.show_call_mark.set(False)
        self.show_call_bid.set(False)
        self.show_call_ask.set(False)
    
    def select_only_index(self):
        self.show_index_iv.set(True)
        self.show_put_mark.set(False)
        self.show_put_bid.set(False)
        self.show_put_ask.set(False)
        self.show_call_mark.set(False)
        self.show_call_bid.set(False)
        self.show_call_ask.set(False)
    
    def select_all_put(self):
        self.show_index_iv.set(False)
        self.show_put_mark.set(True)
        self.show_put_bid.set(True)
        self.show_put_ask.set(True)
        self.show_call_mark.set(False)
        self.show_call_bid.set(False)
        self.show_call_ask.set(False)
    
    def select_all_call(self):
        self.show_index_iv.set(False)
        self.show_put_mark.set(False)
        self.show_put_bid.set(False)
        self.show_put_ask.set(False)
        self.show_call_mark.set(True)
        self.show_call_bid.set(True)
        self.show_call_ask.set(True)
    
    def reset_params(self):
        self.select_only_index()
        self.target_delta.set(0.5)
        self.plot_atm_itm()
    
    def refresh_plots(self):
        self.plot_atm_itm()
    
    def update_expiry_list(self):
        if self.app.dataframes:
            expiries = sorted(self.app.dataframes.keys())
            display_expiries = [e[:10] for e in expiries]
            self.expiry_combo['values'] = display_expiries
            if display_expiries:
                self.expiry_combo.current(0)
                self.selected_expiry = expiries[0]
    
    def on_expiry_selected(self, event):
        selection = self.expiry_combo.get()
        if selection and self.app.dataframes:
            for full_expiry in self.app.dataframes.keys():
                if full_expiry.startswith(selection):
                    self.selected_expiry = full_expiry
                    self._iv_cache.clear()
                    self._index_price_cache.clear()
                    break
    
    @cache_result(maxsize=256)
    def _get_index_price(self, current_time):
        for exp, df in self.app.dataframes.items():
            if 'indexPrice' in df.columns:
                mask = df['fetch_time_utc'] == current_time
                matching_rows = df[mask]
                if len(matching_rows) > 0:
                    return matching_rows['indexPrice'].iloc[0]
        return None
    
    @cache_result(maxsize=1024)
    def _get_option_data(self, expiry_date, current_time, option_type):
        if expiry_date not in self.app.option_board_data:
            return None
        
        data = self.app.option_board_data[expiry_date]
        options_data = data['puts'] if option_type == 'put' else data['calls']
        
        result = []
        for strike_str, time_data in options_data.items():
            if current_time in time_data:
                data_point = time_data[current_time].copy()
                if data_point.get('delta') is not None and not pd.isna(data_point.get('delta')):
                    data_point['strike'] = float(strike_str)
                    result.append(data_point)
        
        return result
    
    def calculate_iv_by_delta(self, expiry_date, current_time, option_type='put', iv_type='mark'):
        cache_key = (expiry_date, current_time, option_type, iv_type, self.target_delta.get())
        if cache_key in self._iv_cache:
            return self._iv_cache[cache_key]
        
        if expiry_date not in self.app.option_board_data or current_time is None:
            return None
        
        target_delta = self.target_delta.get()
        if target_delta <= 0 or target_delta >= 1:
            return None
        
        iv_key = {'mark': 'iv', 'bid': 'bid_iv', 'ask': 'ask_iv'}.get(iv_type, 'iv')
        
        data_points = self._get_option_data(expiry_date, current_time, option_type)
        if not data_points or len(data_points) < 2:
            return None
        
        delta_items = []
        for point in data_points:
            delta = point.get('delta')
            iv = point.get(iv_key)
            if delta is not None and iv is not None and not pd.isna(delta) and not pd.isna(iv):
                delta_abs = abs(delta)
                delta_items.append((point['strike'], delta_abs, iv))
        
        if len(delta_items) < 2:
            return None
        
        delta_items.sort(key=lambda x: x[1])
        
        lower_item = None
        upper_item = None
        
        for item in delta_items:
            strike, delta_abs, iv = item
            if delta_abs <= target_delta:
                lower_item = item
            elif delta_abs > target_delta and lower_item is not None:
                upper_item = item
                break
        
        if upper_item is None and lower_item is not None:
            idx = delta_items.index(lower_item)
            if idx + 1 < len(delta_items):
                upper_item = delta_items[idx + 1]
        
        if lower_item is None and upper_item is not None:
            idx = delta_items.index(upper_item)
            if idx > 0:
                lower_item = delta_items[idx - 1]
        
        if lower_item is None or upper_item is None:
            return None
        
        lower_strike, lower_delta, lower_iv = lower_item
        upper_strike, upper_delta, upper_iv = upper_item
        
        if upper_delta == lower_delta:
            iv = (lower_iv + upper_iv) / 2
        else:
            weight = (target_delta - lower_delta) / (upper_delta - lower_delta)
            iv = lower_iv + weight * (upper_iv - lower_iv)
        
        self._iv_cache[cache_key] = iv
        return iv
    
    def calculate_index_iv(self, expiry_date, current_time):
        cache_key = (expiry_date, current_time, 'index_iv')
        if cache_key in self._iv_cache:
            return self._iv_cache[cache_key]
        
        if expiry_date not in self.app.option_board_data or current_time is None:
            return None
        
        index_price = self._get_index_price(current_time)
        if index_price is None or pd.isna(index_price):
            return None
        
        data = self.app.option_board_data[expiry_date]
        options_data = data['puts']
        
        strikes = []
        for strike_str, time_data in options_data.items():
            if current_time in time_data:
                strikes.append(float(strike_str))
        
        if len(strikes) < 2:
            return None
        
        strikes.sort()
        
        lower_strike = None
        upper_strike = None
        
        for strike in strikes:
            if strike <= index_price:
                lower_strike = strike
            elif strike > index_price and lower_strike is not None:
                upper_strike = strike
                break
        
        if upper_strike is None and lower_strike is not None:
            idx = strikes.index(lower_strike)
            if idx + 1 < len(strikes):
                upper_strike = strikes[idx + 1]
            else:
                upper_strike = lower_strike
        
        if lower_strike is None and upper_strike is not None:
            idx = strikes.index(upper_strike)
            if idx > 0:
                lower_strike = strikes[idx - 1]
            else:
                lower_strike = upper_strike
        
        if lower_strike is None or upper_strike is None:
            return None
        
        lower_strike_str = str(int(lower_strike))
        upper_strike_str = str(int(upper_strike))
        
        lower_iv = options_data[lower_strike_str][current_time].get('iv')
        upper_iv = options_data[upper_strike_str][current_time].get('iv')
        
        if lower_iv is None or upper_iv is None:
            return None
        
        if upper_strike == lower_strike:
            index_iv = lower_iv
        else:
            index_iv = ((index_price - lower_strike) * upper_iv +
                       (upper_strike - index_price) * lower_iv) / (upper_strike - lower_strike)
        
        self._iv_cache[cache_key] = index_iv
        return index_iv
    
    def _is_param_selected(self, param):
        param_map = {
            'index': self.show_index_iv.get(),
            'put_mark': self.show_put_mark.get(),
            'put_bid': self.show_put_bid.get(),
            'put_ask': self.show_put_ask.get(),
            'call_mark': self.show_call_mark.get(),
            'call_bid': self.show_call_bid.get(),
            'call_ask': self.show_call_ask.get()
        }
        return param_map.get(param, False)
    
    def plot_atm_itm(self):
        if self.selected_expiry is None or not self.app.all_times:
            messagebox.showwarning("Внимание", "Выберите дату экспирации")
            return
        
        target_delta = self.target_delta.get()
        if target_delta <= 0 or target_delta >= 1:
            messagebox.showwarning("Внимание", "Целевая дельта должна быть между 0 и 1")
            return
        
        if not any([self.show_index_iv.get(), self.show_put_mark.get(), self.show_put_bid.get(),
                   self.show_put_ask.get(), self.show_call_mark.get(), self.show_call_bid.get(),
                   self.show_call_ask.get()]):
            messagebox.showwarning("Внимание", "Выберите хотя бы один параметр для отображения")
            return
        
        try:
            self.figure_index.clear()
            self.figure_iv.clear()
            
            ax_index = self.figure_index.add_subplot(111)
            ax_iv = self.figure_iv.add_subplot(111)
            
            display_date = self.selected_expiry[:10] if len(self.selected_expiry) >= 10 else self.selected_expiry
            ax_index.set_title(f"Цена индекса (Экспирация: {display_date})", fontsize=12)
            ax_iv.set_title(f"Волатильность по дельте (Экспирация: {display_date}, δ={target_delta:.2f})", fontsize=12)
            
            times = []
            index_prices = []
            
            iv_data = {key: [] for key in ['index', 'put_mark', 'put_bid', 'put_ask',
                                           'call_mark', 'call_bid', 'call_ask']}
            
            params_to_calc = []
            if self.show_index_iv.get():
                params_to_calc.append('index')
            if self.show_put_mark.get():
                params_to_calc.append('put_mark')
            if self.show_put_bid.get():
                params_to_calc.append('put_bid')
            if self.show_put_ask.get():
                params_to_calc.append('put_ask')
            if self.show_call_mark.get():
                params_to_calc.append('call_mark')
            if self.show_call_bid.get():
                params_to_calc.append('call_bid')
            if self.show_call_ask.get():
                params_to_calc.append('call_ask')
            
            self._iv_cache.clear()
            
            for current_time in self.app.all_times:
                index_price = self._get_index_price(current_time)
                
                if index_price is not None and not pd.isna(index_price):
                    current_data = {}
                    
                    for param in params_to_calc:
                        if param == 'index':
                            current_data['index'] = self.calculate_index_iv(
                                self.selected_expiry, current_time)
                        elif param == 'put_mark':
                            current_data['put_mark'] = self.calculate_iv_by_delta(
                                self.selected_expiry, current_time, 'put', 'mark')
                        elif param == 'put_bid':
                            current_data['put_bid'] = self.calculate_iv_by_delta(
                                self.selected_expiry, current_time, 'put', 'bid')
                        elif param == 'put_ask':
                            current_data['put_ask'] = self.calculate_iv_by_delta(
                                self.selected_expiry, current_time, 'put', 'ask')
                        elif param == 'call_mark':
                            current_data['call_mark'] = self.calculate_iv_by_delta(
                                self.selected_expiry, current_time, 'call', 'mark')
                        elif param == 'call_bid':
                            current_data['call_bid'] = self.calculate_iv_by_delta(
                                self.selected_expiry, current_time, 'call', 'bid')
                        elif param == 'call_ask':
                            current_data['call_ask'] = self.calculate_iv_by_delta(
                                self.selected_expiry, current_time, 'call', 'ask')
                    
                    if any(v is not None for v in current_data.values()):
                        times.append(current_time)
                        index_prices.append(index_price)
                        
                        for key in iv_data.keys():
                            iv_data[key].append(current_data.get(key, np.nan))
            
            if not times:
                messagebox.showwarning("Внимание", "Нет данных для построения графиков")
                return
            
            display_times = []
            for t in times:
                if hasattr(t, 'tz') and t.tz is not None:
                    t = t.tz_localize(None)
                display_times.append(t)
            
            ax_index.plot(display_times, index_prices, color='black', linewidth=2,
                         label='Index Price', marker='o', markersize=3)
            ax_index.set_ylabel('Цена индекса', color='black', fontsize=10)
            ax_index.grid(True, alpha=0.3, linestyle='--')
            ax_index.set_xlabel('Время', fontsize=10)
            ax_index.legend(loc='upper left', fontsize=9)
            
            colors = {
                'index': ('black', f'Index IV (центр. страйк, {display_date})', '-'),
                'put_mark': ('darkred', f'PUT Mark IV (δ={target_delta:.2f}, {display_date})', '-'),
                'put_bid': ('darkred', f'PUT Bid IV (δ={target_delta:.2f}, {display_date})', '--'),
                'put_ask': ('darkred', f'PUT Ask IV (δ={target_delta:.2f}, {display_date})', '--'),
                'call_mark': ('darkgreen', f'CALL Mark IV (δ={target_delta:.2f}, {display_date})', '-'),
                'call_bid': ('darkgreen', f'CALL Bid IV (δ={target_delta:.2f}, {display_date})', '--'),
                'call_ask': ('darkgreen', f'CALL Ask IV (δ={target_delta:.2f}, {display_date})', '--')
            }
            
            markers = {
                'index': 'o', 'put_mark': 'o', 'put_bid': 's', 'put_ask': '^',
                'call_mark': 'o', 'call_bid': 's', 'call_ask': '^'
            }
            
            for key, (color, label, style) in colors.items():
                if key in iv_data and iv_data[key]:
                    valid_indices = [i for i, v in enumerate(iv_data[key])
                                    if v is not None and not pd.isna(v) and not np.isnan(v)]
                    
                    if valid_indices and self._is_param_selected(key):
                        valid_times = [display_times[i] for i in valid_indices]
                        valid_values = [iv_data[key][i] * 100 for i in valid_indices]
                        
                        linewidth = 0.5 if key == 'index' else (1.5 if 'mark' in key else 1)
                        alpha = 1 if 'ask' in key else (0.5 if 'bid' in key else 0.8)
                        marker_size = 1 if 'mark' in key or key == 'index' else 1
                        
                        ax_iv.plot(valid_times, valid_values, color=color,
                                  linewidth=linewidth, label=label,
                                  marker=markers[key], markersize=marker_size,
                                  alpha=alpha, linestyle=style)
            
            ax_iv.set_ylabel('Волатильность (%)', color='blue', fontsize=10)
            ax_iv.tick_params(axis='y', labelcolor='blue')
            ax_iv.grid(True, alpha=0.3, linestyle='--')
            ax_iv.set_xlabel('Время', fontsize=10)
            ax_iv.legend(loc='upper left', bbox_to_anchor=(1.02, 1), fontsize=8)
            
            for ax in [ax_index, ax_iv]:
                ax.tick_params(axis='x', rotation=45)
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%a %H:%M'))
                ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            
            ax_index.relim()
            ax_index.autoscale_view()
            ax_iv.relim()
            ax_iv.autoscale_view()
            
            self.figure_index.tight_layout()
            self.figure_iv.tight_layout()
            
            for widget in self.index_toolbar_frame.winfo_children():
                widget.destroy()
            for widget in self.iv_toolbar_frame.winfo_children():
                widget.destroy()
            
            self.toolbar_index = CustomNavigationToolbar(self.canvas_index, self.index_toolbar_frame, 'index', self.app)
            self.toolbar_index.update()
            
            self.toolbar_iv = CustomNavigationToolbar(self.canvas_iv, self.iv_toolbar_frame, 'atm_iv', self.app)
            self.toolbar_iv.update()
            
            self.canvas_index.draw()
            self.canvas_iv.draw()
            
            axes_index = [ax_index]
            axes_iv = [ax_iv]
            
            self.graph_index = InteractiveGraph(self.figure_index, axes_index, parent=self.app.root, graph_type='index')
            self.graph_iv_atm = InteractiveGraph(self.figure_iv, axes_iv, parent=self.app.root, graph_type='atm_iv')
            
            self.graph_index.sync_graph = self.graph_iv_atm
            self.graph_iv_atm.sync_graph = self.graph_index
            
            self.graph_index.connect_events(self.canvas_index)
            self.graph_iv_atm.connect_events(self.canvas_iv)
            
            self.app.status_label.config(
                text=f"Построены графики для даты {display_date} (δ={target_delta:.2f})"
            )
            
            self.app.current_expiry_date = self.selected_expiry
            
        except Exception as e:
            messagebox.showerror("Ошибка построения", str(e))
            import traceback
            traceback.print_exc()


class OptionAnalyzerApp:
    """Главный класс приложения"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Анализатор опционов - Управление портфелем")
        self.root.state('zoomed')
        
        self.dataframes = {}
        self.option_symbols = {}
        self.selected_files = []
        self.all_options_data = {}
        self.option_board_data = {}
        self.all_times = []
        self.current_time_idx = 0
        self.current_expiry_date = None
        
        self._delta_cache = {}
        self._symbol_cache = {}
        
        self.selection_tabs = {}
        self.atm_tab = None
        self.portfolio_tab = None
        
        self.setup_gui()
        self.load_example_data()
    
    def setup_gui(self):
        """Настройка графического интерфейса"""
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=3)
        
        right_frame = ttk.Frame(main_paned, width=500)
        main_paned.add(right_frame, weight=1)
        
        # ========== ЛЕВАЯ ЧАСТЬ ==========
        top_paned = ttk.PanedWindow(left_frame, orient=tk.VERTICAL)
        top_paned.pack(fill=tk.BOTH, expand=True)
        
        graph_control_frame = ttk.Frame(top_paned)
        top_paned.add(graph_control_frame, weight=0)
        
        ttk.Button(graph_control_frame, text="Построить графики выбранных опционов",
                  command=self.plot_selected_options, width=30).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(graph_control_frame, text="Очистить графики",
                  command=self.clear_plots, width=20).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(graph_control_frame, text="Загрузить CSV файлы",
                  command=self.load_csv_files, width=20).pack(side=tk.LEFT)
        
        graph_paned = ttk.PanedWindow(top_paned, orient=tk.VERTICAL)
        top_paned.add(graph_paned, weight=1)
        
        price_frame = ttk.LabelFrame(graph_paned, text="График цен опционов", padding="5")
        graph_paned.add(price_frame, weight=1)
        
        price_container = ttk.Frame(price_frame)
        price_container.pack(fill=tk.BOTH, expand=True)
        
        self.price_toolbar_frame = ttk.Frame(price_container)
        self.price_toolbar_frame.pack(fill=tk.X)
        
        self.price_canvas_frame = ttk.Frame(price_container)
        self.price_canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        self.figure_price = Figure(figsize=(10, 4), dpi=100)
        self.canvas_price = FigureCanvasTkAgg(self.figure_price, self.price_canvas_frame)
        self.canvas_price.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        iv_frame = ttk.LabelFrame(graph_paned, text="График волатильности опционов", padding="5")
        graph_paned.add(iv_frame, weight=1)
        
        iv_container = ttk.Frame(iv_frame)
        iv_container.pack(fill=tk.BOTH, expand=True)
        
        self.iv_toolbar_frame = ttk.Frame(iv_container)
        self.iv_toolbar_frame.pack(fill=tk.X)
        
        self.iv_canvas_frame = ttk.Frame(iv_container)
        self.iv_canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        self.figure_iv = Figure(figsize=(10, 4), dpi=100)
        self.canvas_iv = FigureCanvasTkAgg(self.figure_iv, self.iv_canvas_frame)
        self.canvas_iv.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        self.graph_price = None
        self.graph_iv = None
        
        # ========== ПРАВАЯ ЧАСТЬ ==========
        self.right_notebook = ttk.Notebook(right_frame)
        self.right_notebook.pack(fill=tk.BOTH, expand=True)
        
        # Вкладка 1: Параметры
        params_tab = ttk.Frame(self.right_notebook)
        self.right_notebook.add(params_tab, text='Параметры')
        self.setup_params_tab(params_tab)
        
        # Вкладка 2: Опционы
        self.options_notebook = ttk.Notebook(self.right_notebook)
        self.right_notebook.add(self.options_notebook, text='Опционы')
        
        # Вкладка 3: ATM/ITM
        atm_tab = ttk.Frame(self.right_notebook)
        self.right_notebook.add(atm_tab, text='ATM/ITM')
        self.atm_tab = ATMTab(atm_tab, self)
        
        # Вкладка 4: Портфель
        portfolio_tab = ttk.Frame(self.right_notebook)
        self.right_notebook.add(portfolio_tab, text='📊 Портфель')
        self.portfolio_tab = PortfolioTab(portfolio_tab, self)
        
        control_frame = ttk.Frame(right_frame)
        control_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(control_frame, text="Сбросить все",
                  command=self.reset_all, width=20).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(control_frame, text="Обновить",
                  command=self.refresh_display, width=20).pack(side=tk.LEFT)
        
        info_frame = ttk.Frame(right_frame)
        info_frame.pack(fill=tk.X, pady=(10, 0))
        
        self.status_label = ttk.Label(info_frame, text="Готов к работе")
        self.status_label.pack()
        
        self.file_count_label = ttk.Label(info_frame, text="Загружено файлов: 0")
        self.file_count_label.pack()
        
        self.selected_count_label = ttk.Label(info_frame, text="Выбрано опционов: 0")
        self.selected_count_label.pack()
    
    def setup_params_tab(self, tab):
        """Настроить вкладку с параметрами"""
        canvas = tk.Canvas(tab)
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind("<Configure>",
                             lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        price_frame = ttk.LabelFrame(scrollable_frame, text="Верхний график (Цены)", padding="10")
        price_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        self.price_vars = {}
        price_params = ['bid1Price', 'markPrice', 'ask1Price']
        for param in price_params:
            var = tk.BooleanVar(value=False)
            self.price_vars[param] = var
            cb = ttk.Checkbutton(price_frame, text=param, variable=var)
            cb.pack(anchor=tk.W, pady=2)
        
        iv_frame = ttk.LabelFrame(scrollable_frame, text="Нижний график (Волатильность)", padding="10")
        iv_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.iv_vars = {}
        iv_params = ['bid1Iv', 'markIv', 'ask1Iv']
        for param in iv_params:
            var = tk.BooleanVar(value=False)
            self.iv_vars[param] = var
            cb = ttk.Checkbutton(iv_frame, text=param, variable=var)
            cb.pack(anchor=tk.W, pady=2)
    
    def create_option_tabs(self):
        """Создать подвкладки с опционами для каждой даты экспирации"""
        for tab_id in self.options_notebook.tabs():
            self.options_notebook.forget(tab_id)
        
        self.selection_tabs.clear()
        self._symbol_cache.clear()
        
        if not self.dataframes:
            return
        
        for expiry_date in sorted(self.dataframes.keys()):
            tab = ttk.Frame(self.options_notebook)
            display_date = expiry_date[:10] if len(expiry_date) >= 10 else expiry_date
            
            self.options_notebook.add(tab, text=display_date)
            
            selection_tab = OptionSelectionTab(tab, self, expiry_date)
            self.selection_tabs[expiry_date] = selection_tab
        
        self.update_tab_indicators()
        
        if self.atm_tab:
            self.atm_tab.update_expiry_list()
        
        if self.portfolio_tab:
            self.portfolio_tab.update_expiry_list()
    
    def update_tab_indicators(self):
        """Обновить индикаторы на вкладках опционов"""
        try:
            for expiry_date, selection_tab in self.selection_tabs.items():
                tab_index = None
                for i, tab_id in enumerate(self.options_notebook.tabs()):
                    tab_text = self.options_notebook.tab(tab_id, "text")
                    clean_text = tab_text
                    for prefix in ["[C] ", "[P] ", "[C+P] "]:
                        if tab_text.startswith(prefix):
                            clean_text = tab_text[len(prefix):]
                            break
                    
                    if clean_text == expiry_date[:10]:
                        tab_index = i
                        break
                
                if tab_index is None:
                    continue
                
                has_calls = len(selection_tab.selected_calls) > 0
                has_puts = len(selection_tab.selected_puts) > 0
                
                if has_calls and has_puts:
                    prefix = "[C+P] "
                elif has_calls:
                    prefix = "[C] "
                elif has_puts:
                    prefix = "[P] "
                else:
                    prefix = ""
                
                current_text = self.options_notebook.tab(tab_index, "text")
                for p in ["[C] ", "[P] ", "[C+P] "]:
                    if current_text.startswith(p):
                        current_text = current_text[len(p):]
                        break
                
                self.options_notebook.tab(tab_index, text=f"{prefix}{current_text}")
        except Exception as e:
            print(f"Ошибка в update_tab_indicators: {e}")
    
    @cache_result(maxsize=512)
    def get_delta_for_snapshot(self, symbol, snapshot_time):
        if snapshot_time is None or symbol not in self.all_options_data:
            return None
        
        data = self.all_options_data[symbol]['data']
        if 'delta' not in data.columns:
            return None
        
        mask = data['fetch_time_utc'] == snapshot_time
        matching_rows = data[mask]
        
        if len(matching_rows) > 0:
            delta = matching_rows['delta'].iloc[0]
            return delta if pd.notna(delta) else None
        
        return None
    
    def load_example_data(self):
        csv_files = glob.glob("*.csv")
        if csv_files:
            self.load_specific_files(csv_files[:3])
    
    def load_csv_files(self):
        file_paths = filedialog.askopenfilenames(
            title="Выберите CSV файлы",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if file_paths:
            self.load_specific_files(file_paths)
    
    def parse_expiry_date(self, expiry_str):
        try:
            if '-' in expiry_str and len(expiry_str) == 10:
                return expiry_str
            
            match = re.search(r'([A-Za-z]{3})', expiry_str)
            if not match:
                return expiry_str
            
            month_str = match.group(1).upper()
            parts = expiry_str.split(month_str)
            
            day = parts[0]
            year = parts[1] if len(parts) > 1 else ''
            
            if len(day) == 1:
                day = '0' + day
            
            year = "20" + year[-2:] if year else "2024"
            
            month_dict = {
                'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
                'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
                'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
            }
            month = month_dict.get(month_str, '01')
            
            return f"{year}-{month}-{day}"
        except Exception as e:
            print(f"Ошибка парсинга даты {expiry_str}: {e}")
            return expiry_str
    
    def load_specific_files(self, file_paths):
        self.selected_files = list(file_paths)
        
        self.dataframes.clear()
        self.option_symbols.clear()
        self.all_options_data.clear()
        self.option_board_data.clear()
        self.all_times = []
        self._delta_cache.clear()
        self._symbol_cache.clear()
        
        all_timestamps = []
        
        print(f"Загрузка {len(file_paths)} файлов...")
        
        for file_path in file_paths:
            try:
                df = pd.read_csv(file_path)
                print(f"Загружен файл {file_path}, строк: {len(df)}, колонки: {df.columns.tolist()}")
                
                numeric_cols = ['strike', 'markPrice', 'indexPrice', 'bid1Price', 'ask1Price',
                               'delta', 'gamma', 'vega', 'theta', 'bid1Iv', 'ask1Iv', 'markIv']
                for col in numeric_cols:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                
                if 'fetch_time_utc' in df.columns:
                    df['fetch_time_utc'] = pd.to_datetime(df['fetch_time_utc'])
                    all_timestamps.extend(df['fetch_time_utc'].tolist())
                
                if 'expiry' in df.columns and not df.empty:
                    df['expiry_parsed'] = df['expiry'].apply(self.parse_expiry_date)
                    expiry_date = df['expiry_parsed'].iloc[0]
                    
                    if expiry_date not in self.dataframes:
                        self.dataframes[expiry_date] = df
                        symbols = df['symbol'].unique().tolist()
                        self.option_symbols[expiry_date] = symbols
                        print(f"Новая дата экспирации: {expiry_date}, символов: {len(symbols)}")
                    else:
                        existing_df = self.dataframes[expiry_date]
                        self.dataframes[expiry_date] = pd.concat(
                            [existing_df, df], ignore_index=True
                        )
                        symbols = self.dataframes[expiry_date]['symbol'].unique().tolist()
                        self.option_symbols[expiry_date] = symbols
                        print(f"Обновлена дата экспирации: {expiry_date}, всего символов: {len(symbols)}")
                
            except Exception as e:
                print(f"Ошибка загрузки файла {file_path}: {str(e)}")
                messagebox.showerror("Ошибка", f"Ошибка загрузки файла {file_path}:\n{str(e)}")
        
        if all_timestamps:
            self.all_times = sorted(set(all_timestamps))
            print(f"Загружено временных меток: {len(self.all_times)}")
            self.current_time_idx = 0
        else:
            print("Нет временных меток в данных!")
        
        self.create_data_cache()
        self.prepare_option_board_data()
        self.update_file_count()
        
        if self.dataframes:
            self.create_option_tabs()
            self.status_label.config(text=f"Загружено {len(self.dataframes)} дат экспирации")
            print("Данные обновлены")
    
    def create_data_cache(self):
        for expiry_date, df in self.dataframes.items():
            for symbol in df['symbol'].unique():
                symbol_data = df[df['symbol'] == symbol].copy()
                symbol_data = symbol_data.sort_values('fetch_time_utc')
                self.all_options_data[symbol] = {
                    'data': symbol_data,
                    'expiry': expiry_date
                }
        print(f"Создан кэш данных для {len(self.all_options_data)} символов")
    
    def prepare_option_board_data(self):
        for expiry_date, df in self.dataframes.items():
            self.option_board_data[expiry_date] = {
                'calls': defaultdict(dict),
                'puts': defaultdict(dict)
            }
            
            for _, row in df.iterrows():
                symbol = row['symbol']
                strike = str(int(row['strike'])) if pd.notna(row['strike']) else '0'
                time = row['fetch_time_utc']
                
                parts = symbol.split('-')
                if len(parts) >= 4:
                    option_type = parts[3][0]
                    
                    option_data = {
                        'price': row.get('markPrice', None),
                        'delta': row.get('delta', None),
                        'bid': row.get('bid1Price', None),
                        'ask': row.get('ask1Price', None),
                        'iv': row.get('markIv', None),
                        'bid_iv': row.get('bid1Iv', None),
                        'ask_iv': row.get('ask1Iv', None)
                    }
                    
                    if option_type == 'C':
                        self.option_board_data[expiry_date]['calls'][strike][time] = option_data
                    elif option_type == 'P':
                        self.option_board_data[expiry_date]['puts'][strike][time] = option_data
        
        print(f"Подготовлены данные для расчетов: {len(self.option_board_data)} дат экспирации")
    
    def update_file_count(self):
        count = len(self.selected_files)
        self.file_count_label.config(text=f"Загружено файлов: {count}")
    
    def update_selected_count(self):
        total = 0
        for selection_tab in self.selection_tabs.values():
            total += len(selection_tab.get_selected_symbols())
        self.selected_count_label.config(text=f"Выбрано опционов: {total}")
    
    def get_selected_options(self):
        selected = []
        for selection_tab in self.selection_tabs.values():
            selected.extend(selection_tab.get_selected_symbols())
        return selected
    
    def plot_selected_options(self):
        selected_options = self.get_selected_options()
        
        if not selected_options:
            messagebox.showwarning("Внимание", "Не выбраны опционы для построения графиков")
            return
        
        try:
            self.figure_price.clear()
            ax_price = self.figure_price.add_subplot(111)
            
            self.figure_iv.clear()
            ax_iv = self.figure_iv.add_subplot(111)
            
            call_color = '#2ecc71'
            put_color = '#e74c3c'
            
            price_params_selected = [p for p, var in self.price_vars.items() if var.get()]
            iv_params_selected = [p for p, var in self.iv_vars.items() if var.get()]
            
            for symbol in selected_options:
                if symbol not in self.all_options_data:
                    continue
                
                data_info = self.all_options_data[symbol]
                symbol_data = data_info['data']
                
                parts = symbol.split('-')
                opt_type = parts[3][0] if len(parts) > 3 else '?'
                color = call_color if opt_type == 'C' else put_color
                
                display_times = []
                for t in symbol_data['fetch_time_utc']:
                    if hasattr(t, 'tz') and t.tz is not None:
                        t = t.tz_localize(None)
                    display_times.append(t)
                
                for param in price_params_selected:
                    if param not in symbol_data.columns:
                        continue
                    
                    valid_mask = symbol_data[param].notna()
                    if not valid_mask.any():
                        continue
                    
                    valid_times = [display_times[i] for i, v in enumerate(valid_mask) if v]
                    valid_values = symbol_data[param][valid_mask].values
                    
                    linestyle = '--' if param == 'markPrice' else '-'
                    
                    ax_price.plot(valid_times, valid_values,
                                 color=color, linewidth=1.5, alpha=0.75,
                                 linestyle=linestyle,
                                 label=f"{symbol} {param}")
                
                for param in iv_params_selected:
                    if param not in symbol_data.columns:
                        continue
                    
                    valid_mask = symbol_data[param].notna()
                    if not valid_mask.any():
                        continue
                    
                    valid_times = [display_times[i] for i, v in enumerate(valid_mask) if v]
                    valid_values = symbol_data[param][valid_mask].values
                    if param.endswith('Iv'):
                        valid_values = valid_values * 100
                    
                    linestyle = '--' if param == 'markIv' else '-'
                    
                    ax_iv.plot(valid_times, valid_values,
                              color=color, linewidth=1.5, alpha=0.75,
                              linestyle=linestyle,
                              label=f"{symbol} {param}")
            
            ax_price.set_ylabel('Цена', color='blue', fontsize=10)
            ax_price.tick_params(axis='y', labelcolor='blue')
            ax_price.grid(True, alpha=0.3, linestyle='--')
            ax_price.set_xlabel('Время', fontsize=10)
            
            if price_params_selected:
                handles, labels = ax_price.get_legend_handles_labels()
                unique = dict(zip(labels, handles))
                if unique:
                    ax_price.legend(unique.values(), unique.keys(),
                                   loc='upper left', bbox_to_anchor=(1.02, 1), fontsize=8)
            
            ax_iv.set_ylabel('Волатильность (%)', color='green', fontsize=10)
            ax_iv.tick_params(axis='y', labelcolor='green')
            ax_iv.grid(True, alpha=0.3, linestyle='--')
            ax_iv.set_xlabel('Время', fontsize=10)
            ax_iv.yaxis.set_major_formatter(PercentFormatter())
            
            if iv_params_selected:
                handles, labels = ax_iv.get_legend_handles_labels()
                unique = dict(zip(labels, handles))
                if unique:
                    ax_iv.legend(unique.values(), unique.keys(),
                                loc='upper left', bbox_to_anchor=(1.02, 1), fontsize=8)
            
            for ax in [ax_price, ax_iv]:
                ax.tick_params(axis='x', rotation=45)
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%a %H:%M'))
                ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            
            ax_price.autoscale(enable=True, axis='both', tight=True)
            ax_iv.autoscale(enable=True, axis='both', tight=True)
            
            self.figure_price.tight_layout()
            self.figure_iv.tight_layout()
            
            for widget in self.price_toolbar_frame.winfo_children():
                widget.destroy()
            for widget in self.iv_toolbar_frame.winfo_children():
                widget.destroy()
            
            self.toolbar_price = CustomNavigationToolbar(self.canvas_price, self.price_toolbar_frame, 'price', self)
            self.toolbar_price.update()
            
            self.toolbar_iv = CustomNavigationToolbar(self.canvas_iv, self.iv_toolbar_frame, 'iv', self)
            self.toolbar_iv.update()
            
            axes_price = [ax_price]
            axes_iv = [ax_iv]
            
            self.graph_price = InteractiveGraph(self.figure_price, axes_price, parent=self.root, graph_type='price')
            self.graph_iv = InteractiveGraph(self.figure_iv, axes_iv, parent=self.root, graph_type='iv')
            
            self.graph_price.sync_graph = self.graph_iv
            self.graph_iv.sync_graph = self.graph_price
            
            self.graph_price.connect_events(self.canvas_price)
            self.graph_iv.connect_events(self.canvas_iv)
            
            self.canvas_price.draw()
            self.canvas_iv.draw()
            
            self.status_label.config(text=f"Построено {len(selected_options)} опционов")
            
        except Exception as e:
            messagebox.showerror("Ошибка построения", str(e))
            import traceback
            traceback.print_exc()
    
    def clear_plots(self):
        self.figure_price.clear()
        self.figure_iv.clear()
        self.canvas_price.draw()
        self.canvas_iv.draw()
        
        if self.atm_tab:
            self.atm_tab.figure_index.clear()
            self.atm_tab.figure_iv.clear()
            self.atm_tab.canvas_index.draw()
            self.atm_tab.canvas_iv.draw()
        
        if self.portfolio_tab:
            self.portfolio_tab.figure_pnl.clear()
            self.portfolio_tab.figure_greeks.clear()
            self.portfolio_tab.canvas_pnl.draw()
            self.portfolio_tab.canvas_greeks.draw()
        
        self.status_label.config(text="Графики очищены")
        
        self.graph_price = None
        self.graph_iv = None
    
    def reset_all(self):
        for var in self.price_vars.values():
            var.set(False)
        for var in self.iv_vars.values():
            var.set(False)
        
        for selection_tab in self.selection_tabs.values():
            selection_tab.deselect_all_calls()
            selection_tab.deselect_all_puts()
        
        if self.portfolio_tab:
            self.portfolio_tab.portfolio_manager.clear_all_positions()
            self.portfolio_tab.update_positions_table()
            self.portfolio_tab.update_portfolio_status()
        
        self.clear_plots()
        self.update_selected_count()
        self.update_tab_indicators()
        
        self.status_label.config(text="Все выборы сброшены")
    
    def refresh_display(self):
        for selection_tab in self.selection_tabs.values():
            selection_tab.update_table()
        self.update_selected_count()
        self.update_tab_indicators()
        
        if self.portfolio_tab:
            self.portfolio_tab.update_positions_table()
            self.portfolio_tab.update_portfolio_status()
        
        self.status_label.config(text="Отображение обновлено")
    
    def run(self):
        self.root.mainloop()
    

def main():
    root = tk.Tk()
    app = OptionAnalyzerApp(root)
    app.run()


if __name__ == "__main__":
    main()