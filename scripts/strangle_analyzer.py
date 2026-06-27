import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.dates import DateFormatter
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import warnings
import os

warnings.filterwarnings("ignore", category=UserWarning)

COMMISSION = 0.07


class DeltaNeutralStrangleGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Delta-Neutral Strangle Analyzer — Multi-Asset Options")
        self.root.geometry("1800x1000")
        self.root.minsize(1280, 720)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.df = None
        self.unique_times = None
        self.time_index = 0
        self.call_options = None
        self.put_options = None

        self.selected_call_sym = None
        self.selected_put_sym = None
        self.entry_time = None
        self.purchase_time = None
        self.end_time = None

        self.qty_call = 0.0
        self.qty_put = 0.0
        self.capital_dirty = 0.0
        
        self.updating_selection = False
        
        self.show_all_combinations = False
        self.all_combinations_data = []
        
        self.call_filter_enabled = False
        self.put_filter_enabled = False
        
        # НОВЫЙ ПАРАМЕТР: режим cost-neutral вместо delta-neutral
        self.cost_neutral_mode = False  # По умолчанию delta-neutral

        # Тёмная тема TradingView
        plt.style.use('dark_background')
        plt.rcParams.update({
            'figure.facecolor': '#131722',
            'axes.facecolor': '#1e222d',
            'grid.color': '#2a2e39',
            'grid.linestyle': '-',
            'grid.linewidth': 0.6,
            'text.color': '#e1e1e1',
            'axes.labelcolor': '#e1e1e1',
            'xtick.color': '#e1e1e1',
            'ytick.color': '#e1e1e1',
            'axes.edgecolor': '#4a4e5a',
        })

        self.create_widgets()

    def format_strike(self, strike):
        try:
            strike_float = float(strike)
            # Проверяем, целое ли число
            if abs(strike_float - round(strike_float)) < 0.0001:
                return f"{round(strike_float):.0f}"
            else:
                return f"{strike_float:.4f}"
        except:
            return str(strike)

    def on_closing(self):
        if messagebox.askokcancel("Выход", "Вы уверены, что хотите выйти?"):
            plt.close(self.fig)
            self.root.quit()
            self.root.destroy()
            import sys
            sys.exit()

    def create_widgets(self):
        main_frame = tk.Frame(self.root, bg='#131722')
        main_frame.pack(fill=tk.BOTH, expand=True)

        left_frame = tk.Frame(main_frame, bg='#131722')
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.fig, self.ax = plt.subplots(figsize=(9.5, 7.2), dpi=100)
        canvas_frame = tk.Frame(left_frame, bg='#131722')
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.canvas = FigureCanvasTkAgg(self.fig, master=canvas_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        toolbar = NavigationToolbar2Tk(self.canvas, left_frame)
        toolbar.update()
        toolbar.pack(fill=tk.X, side=tk.BOTTOM)

        self.fig.canvas.mpl_connect('button_press_event', self.on_graph_click)

        right_frame = tk.Frame(main_frame, width=520, bg='#1e222d')
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=8, pady=8)
        right_frame.pack_propagate(False)

        top_right = tk.Frame(right_frame, bg='#1e222d', pady=10)
        top_right.pack(fill=tk.X)

        file_buttons_frame = tk.Frame(top_right, bg='#1e222d')
        file_buttons_frame.pack(fill=tk.X)

        tk.Button(file_buttons_frame, text="Выбрать файл", command=self.load_file,
                  bg='#0288d1', fg='white', width=16).pack(side=tk.LEFT, padx=6)

        tk.Button(file_buttons_frame, text="Сброс", command=self.reset_all,
                  bg="#d32f2f", fg="white", font=("Arial", 10, "bold"), width=10).pack(side=tk.LEFT, padx=6)

        self.file_label = tk.Label(top_right, text="Файл не выбран", fg="#b0bec5",
                                   bg='#1e222d', font=("Arial", 10), pady=8)
        self.file_label.pack(fill=tk.X)

        time_purchase_frame = tk.Frame(right_frame, bg='#1e222d')
        time_purchase_frame.pack(fill=tk.X, pady=(0, 4))

        tk.Label(time_purchase_frame, text="Время покупки:", fg="#e1e1e1", bg='#1e222d',
                 font=("Arial", 10, "bold"), width=14, anchor="w").pack(side=tk.LEFT)

        purchase_control_frame = tk.Frame(time_purchase_frame, bg='#1e222d')
        purchase_control_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.time_scale = tk.Scale(purchase_control_frame, from_=0, to=0, orient=tk.HORIZONTAL,
                                   length=200, showvalue=False, bg='#1e222d', fg='#e1e1e1',
                                   troughcolor='#37474f', highlightbackground='#1e222d',
                                   command=self.on_scale_changed)
        self.time_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))

        self.time_combo = ttk.Combobox(purchase_control_frame, width=20, state="readonly", font=("Arial", 10))
        self.time_combo.pack(side=tk.LEFT, padx=(0, 0))
        self.time_combo.bind("<<ComboboxSelected>>", self.on_combo_selected)

        time_end_frame = tk.Frame(right_frame, bg='#1e222d')
        time_end_frame.pack(fill=tk.X, pady=(4, 12))

        tk.Label(time_end_frame, text="Конечное время:", fg="#e1e1e1", bg='#1e222d',
                 font=("Arial", 10, "bold"), width=14, anchor="w").pack(side=tk.LEFT)

        end_control_frame = tk.Frame(time_end_frame, bg='#1e222d')
        end_control_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.time_end_scale = tk.Scale(end_control_frame, from_=0, to=0, orient=tk.HORIZONTAL,
                                       length=200, showvalue=False, bg='#1e222d', fg='#e1e1e1',
                                       troughcolor='#4a148c', highlightbackground='#1e222d',
                                       command=self.on_end_scale_changed)
        self.time_end_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))

        self.time_end_combo = ttk.Combobox(end_control_frame, width=20, state="readonly", font=("Arial", 10))
        self.time_end_combo.pack(side=tk.LEFT, padx=(0, 0))
        self.time_end_combo.bind("<<ComboboxSelected>>", self.on_end_combo_selected)

        options_frame = tk.Frame(right_frame, bg='#1e222d', pady=8)
        options_frame.pack(fill=tk.BOTH, expand=True)

        call_top_frame = tk.Frame(options_frame, bg='#1e222d')
        call_top_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        
        call_header_frame = tk.Frame(call_top_frame, bg='#1e222d')
        call_header_frame.pack(fill=tk.X, padx=6, pady=(0, 2))
        
        tk.Label(call_header_frame, text="CALL", fg='#00c853', bg='#1e222d',
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        
        call_filter_frame = tk.Frame(call_header_frame, bg='#1e222d')
        call_filter_frame.pack(side=tk.RIGHT)
        
        tk.Label(call_filter_frame, text="Δ:", fg="#80cbc4", bg='#1e222d',
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=(0, 2))
        
        self.call_min_delta_filter = tk.Entry(call_filter_frame, width=6, 
                                              font=("Arial", 8), justify="center")
        self.call_min_delta_filter.pack(side=tk.LEFT)
        self.call_min_delta_filter.insert(0, "0.05")
        
        tk.Label(call_filter_frame, text="-", fg="#80cbc4", bg='#1e222d',
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=(2, 2))
        
        self.call_max_delta_filter = tk.Entry(call_filter_frame, width=6, 
                                              font=("Arial", 8), justify="center")
        self.call_max_delta_filter.pack(side=tk.LEFT)
        self.call_max_delta_filter.insert(0, "0.6")
        
        self.call_filter_btn = tk.Button(call_filter_frame, text="✓", 
                                         command=self.apply_call_filter,
                                         bg='#37474f', fg='#e1e1e1',
                                         font=("Arial", 8), width=2)
        self.call_filter_btn.pack(side=tk.LEFT, padx=(2, 0))
        
        self.call_reset_filter_btn = tk.Button(call_filter_frame, text="✗", 
                                               command=self.reset_call_filter,
                                               bg='#37474f', fg='#e1e1e1',
                                               font=("Arial", 8), width=2)
        self.call_reset_filter_btn.pack(side=tk.LEFT, padx=(1, 0))

        call_f = tk.LabelFrame(call_top_frame, text="", bg='#1e222d', fg='#00c853',
                               font=("Arial", 10, "bold"), padx=6, pady=6)
        call_f.pack(fill=tk.BOTH, expand=True)

        self.call_list = tk.Listbox(call_f, height=10, font=("Consolas", 10), bg='#252b38', fg='#e1e1e1',
                                    selectbackground='#0288d1', selectforeground='white')
        self.call_list.pack(fill=tk.BOTH, expand=True)
        self.call_list.bind('<<ListboxSelect>>', self.on_option_selected)

        self.call_info = tk.Label(call_f, text="Выберите CALL", fg="#80cbc4",
                                  bg='#1e222d', justify="left", font=("Arial", 9))
        self.call_info.pack(fill=tk.X, pady=4)

        put_top_frame = tk.Frame(options_frame, bg='#1e222d')
        put_top_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(4, 0))
        
        put_header_frame = tk.Frame(put_top_frame, bg='#1e222d')
        put_header_frame.pack(fill=tk.X, padx=6, pady=(0, 2))
        
        tk.Label(put_header_frame, text="PUT", fg='#ff5252', bg='#1e222d',
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        
        put_filter_frame = tk.Frame(put_header_frame, bg='#1e222d')
        put_filter_frame.pack(side=tk.RIGHT)
        
        tk.Label(put_filter_frame, text="|Δ|:", fg="#ef9a9a", bg='#1e222d',
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=(0, 2))
        
        self.put_min_delta_filter = tk.Entry(put_filter_frame, width=6, 
                                             font=("Arial", 8), justify="center")
        self.put_min_delta_filter.pack(side=tk.LEFT)
        self.put_min_delta_filter.insert(0, "0.05")
        
        tk.Label(put_filter_frame, text="-", fg="#ef9a9a", bg='#1e222d',
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=(2, 2))
        
        self.put_max_delta_filter = tk.Entry(put_filter_frame, width=6, 
                                             font=("Arial", 8), justify="center")
        self.put_max_delta_filter.pack(side=tk.LEFT)
        self.put_max_delta_filter.insert(0, "0.6")
        
        self.put_filter_btn = tk.Button(put_filter_frame, text="✓", 
                                        command=self.apply_put_filter,
                                        bg='#37474f', fg='#e1e1e1',
                                        font=("Arial", 8), width=2)
        self.put_filter_btn.pack(side=tk.LEFT, padx=(2, 0))
        
        self.put_reset_filter_btn = tk.Button(put_filter_frame, text="✗", 
                                              command=self.reset_put_filter,
                                              bg='#37474f', fg='#e1e1e1',
                                              font=("Arial", 8), width=2)
        self.put_reset_filter_btn.pack(side=tk.LEFT, padx=(1, 0))

        put_f = tk.LabelFrame(put_top_frame, text="", bg='#1e222d', fg='#ff5252',
                              font=("Arial", 10, "bold"), padx=6, pady=6)
        put_f.pack(fill=tk.BOTH, expand=True)

        self.put_list = tk.Listbox(put_f, height=10, font=("Consolas", 10), bg='#252b38', fg='#e1e1e1',
                                   selectbackground='#d32f2f', selectforeground='white')
        self.put_list.pack(fill=tk.BOTH, expand=True)
        self.put_list.bind('<<ListboxSelect>>', self.on_option_selected)

        self.put_info = tk.Label(put_f, text="Выберите PUT", fg="#ef9a9a",
                                 bg='#1e222d', justify="left", font=("Arial", 9))
        self.put_info.pack(fill=tk.X, pady=4)

        settings_frame = tk.Frame(right_frame, bg='#1e222d', pady=12)
        settings_frame.pack(fill=tk.X)

        # НОВАЯ СТРОКА: галочка для cost-neutral режима
        row_cost_neutral = tk.Frame(settings_frame, bg='#1e222d')
        row_cost_neutral.pack(fill=tk.X, pady=(0, 8))
        
        self.cost_neutral_var = tk.BooleanVar(value=False)
        self.cost_neutral_check = tk.Checkbutton(
            row_cost_neutral,
            text="Cost-neutral (стоимость PUT = CALL)",
            variable=self.cost_neutral_var,
            command=self.toggle_cost_neutral_mode,
            bg='#1e222d',
            fg='#e1e1e1',
            selectcolor='#1e222d',
            activebackground='#1e222d',
            activeforeground='#e1e1e1',
            font=("Arial", 10, "bold")
        )
        self.cost_neutral_check.pack(side=tk.LEFT)
        
        # Добавляем информацию о режиме
        self.cost_neutral_label = tk.Label(row_cost_neutral, 
                                           text="Δ-neutral", 
                                           fg="#4fc3f7",
                                           bg='#1e222d', 
                                           font=("Arial", 9, "bold"))
        self.cost_neutral_label.pack(side=tk.RIGHT, padx=(0, 10))

        row3_frame = tk.Frame(settings_frame, bg='#1e222d')
        row3_frame.pack(fill=tk.X, pady=(0, 8))

        delta_frame = tk.Frame(row3_frame, bg='#1e222d')
        delta_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(delta_frame, text="Целевая Δ:", fg="#e1e1e1", bg='#1e222d', 
                 font=("Arial", 10), width=10, anchor="w").pack(side=tk.LEFT)

        self.delta_target_entry = tk.Entry(delta_frame, width=10, font=("Arial", 11))
        self.delta_target_entry.pack(side=tk.LEFT, padx=(4, 20))
        self.delta_target_entry.insert(0, "0.00")

        tk.Button(row3_frame, text="Построить позицию", command=self.build_position,
                  bg="#00c853", fg="black", font=("Arial", 11, "bold"), 
                  width=18).pack(side=tk.RIGHT)

        row4_frame = tk.Frame(settings_frame, bg='#1e222d')
        row4_frame.pack(fill=tk.X, pady=(0, 12))

        tk.Label(row4_frame, text="Сумма (грязная):", fg="#e1e1e1", bg='#1e222d', 
                 font=("Arial", 10), width=15, anchor="w").pack(side=tk.LEFT)

        self.capital_entry = tk.Entry(row4_frame, width=14, font=("Arial", 11))
        self.capital_entry.pack(side=tk.LEFT, padx=(4, 0))
        self.capital_entry.insert(0, "100")

        row5_frame = tk.Frame(settings_frame, bg='#1e222d')
        row5_frame.pack(fill=tk.X)

        mode_frame = tk.Frame(row5_frame, bg='#1e222d')
        mode_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.mode_var = tk.BooleanVar(value=False)
        self.mode_switch = tk.Checkbutton(
            mode_frame, 
            text="Все комбинации", 
            variable=self.mode_var,
            command=self.toggle_mode,
            bg='#1e222d',
            fg='#e1e1e1',
            selectcolor='#1e222d',
            activebackground='#1e222d',
            activeforeground='#e1e1e1',
            font=("Arial", 10)
        )
        self.mode_switch.pack(side=tk.LEFT)

        self.show_info_var = tk.BooleanVar(value=False)
        self.show_info_check = tk.Checkbutton(
            mode_frame,
            text="Доп окно",
            variable=self.show_info_var,
            bg='#1e222d',
            fg='#e1e1e1',
            selectcolor='#1e222d',
            activebackground='#1e222d',
            activeforeground='#e1e1e1',
            font=("Arial", 10)
        )
        self.show_info_check.pack(side=tk.LEFT, padx=(20, 0))

        self.calc_all_btn = tk.Button(
            row5_frame, 
            text="Рассчитать все", 
            command=self.calculate_all_combinations,
            bg="#6200ea", 
            fg="white", 
            font=("Arial", 10),
            width=15,
            state=tk.DISABLED
        )
        self.calc_all_btn.pack(side=tk.RIGHT)

        self.delta_min_filter_frame = tk.Frame(settings_frame, bg='#1e222d', pady=4)
        self.delta_min_filter_frame.pack_forget()

        tk.Label(self.delta_min_filter_frame, text="Мин. |Δ|:", fg="#e1e1e1", bg='#1e222d',
                 font=("Arial", 10), width=8, anchor="w").pack(side=tk.LEFT)

        self.delta_min_filter_entry = tk.Entry(self.delta_min_filter_frame, width=8, 
                                               font=("Arial", 11), justify="center")
        self.delta_min_filter_entry.pack(side=tk.LEFT, padx=(4, 12))
        self.delta_min_filter_entry.insert(0, "0.05")

        min_quick_frame = tk.Frame(self.delta_min_filter_frame, bg='#1e222d')
        min_quick_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        min_quick_values = [0.01, 0.05, 0.1, 0.2, 0.3, 0.4]
        
        for val in min_quick_values:
            btn = tk.Button(
                min_quick_frame, 
                text=f"{val:.2f}", 
                command=lambda v=val: self.delta_min_filter_entry.delete(0, tk.END) or 
                                      self.delta_min_filter_entry.insert(0, f"{v:.2f}"),
                bg='#37474f', 
                fg='#e1e1e1',
                font=("Arial", 8), 
                width=5,
                relief=tk.FLAT
            )
            btn.pack(side=tk.LEFT, padx=1, pady=2)

        self.delta_max_filter_frame = tk.Frame(settings_frame, bg='#1e222d', pady=4)
        self.delta_max_filter_frame.pack_forget()

        tk.Label(self.delta_max_filter_frame, text="Макс. |Δ|:", fg="#e1e1e1", bg='#1e222d',
                 font=("Arial", 10), width=8, anchor="w").pack(side=tk.LEFT)

        self.delta_max_filter_entry = tk.Entry(self.delta_max_filter_frame, width=8, 
                                               font=("Arial", 11), justify="center")
        self.delta_max_filter_entry.pack(side=tk.LEFT, padx=(4, 12))
        self.delta_max_filter_entry.insert(0, "0.6")

        max_quick_frame = tk.Frame(self.delta_max_filter_frame, bg='#1e222d')
        max_quick_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        max_quick_values = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        
        for val in max_quick_values:
            btn = tk.Button(
                max_quick_frame, 
                text=f"{val:.2f}", 
                command=lambda v=val: self.delta_max_filter_entry.delete(0, tk.END) or 
                                      self.delta_max_filter_entry.insert(0, f"{v:.2f}"),
                bg='#4a148c',
                fg='#e1e1e1',
                font=("Arial", 8), 
                width=5,
                relief=tk.FLAT
            )
            btn.pack(side=tk.LEFT, padx=1, pady=2)

        # НОВЫЙ ФИЛЬТР: максимальная разница между дельтами
        self.delta_diff_filter_frame = tk.Frame(settings_frame, bg='#1e222d', pady=4)
        self.delta_diff_filter_frame.pack_forget()

        tk.Label(self.delta_diff_filter_frame, text="Макс. разница Δ:", fg="#e1e1e1", bg='#1e222d',
                 font=("Arial", 10), width=12, anchor="w").pack(side=tk.LEFT)

        self.delta_diff_filter_entry = tk.Entry(self.delta_diff_filter_frame, width=8, 
                                                 font=("Arial", 11), justify="center")
        self.delta_diff_filter_entry.pack(side=tk.LEFT, padx=(4, 12))
        self.delta_diff_filter_entry.insert(0, "0.05")

        diff_quick_frame = tk.Frame(self.delta_diff_filter_frame, bg='#1e222d')
        diff_quick_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        diff_quick_values = [0.01, 0.02, 0.05, 0.1, 0.2, 0.3]
        
        for val in diff_quick_values:
            btn = tk.Button(
                diff_quick_frame, 
                text=f"{val:.2f}", 
                command=lambda v=val: self.delta_diff_filter_entry.delete(0, tk.END) or 
                                      self.delta_diff_filter_entry.insert(0, f"{v:.2f}"),
                bg='#5d4037', 
                fg='#e1e1e1',
                font=("Arial", 8), 
                width=5,
                relief=tk.FLAT
            )
            btn.pack(side=tk.LEFT, padx=1, pady=2)

        self.filter_info_frame = tk.Frame(settings_frame, bg='#1e222d')
        self.filter_info_frame.pack_forget()

        self.filter_info_label = tk.Label(
            self.filter_info_frame, 
            text="", 
            fg="#ff9800",
            bg='#1e222d', 
            font=("Arial", 9),
            justify="left",
            wraplength=480
        )
        self.filter_info_label.pack(fill=tk.X)

        self.purchase_label = tk.Label(settings_frame, text="Покупка: не выполнена", fg="#ff9800",
                                       bg='#1e222d', font=("Arial", 9, "italic"), pady=8)
        self.purchase_label.pack(fill=tk.X, pady=(12, 4))

        self.result_label = tk.Label(settings_frame, text="Ожидание выбора...", fg="#b0bec5",
                                     bg='#1e222d', font=("Arial", 10), justify="left",
                                     wraplength=480)
        self.result_label.pack(fill=tk.X, pady=(0, 6))

    def toggle_cost_neutral_mode(self):
        """Переключение между cost-neutral и delta-neutral режимами"""
        self.cost_neutral_mode = self.cost_neutral_var.get()
        
        if self.cost_neutral_mode:
            self.cost_neutral_label.config(text="Cost-neutral", fg="#ff9800")
            self.delta_target_entry.config(state=tk.DISABLED)
            self.delta_target_entry.delete(0, tk.END)
            self.delta_target_entry.insert(0, "0.00")
        else:
            self.cost_neutral_label.config(text="Δ-neutral", fg="#4fc3f7")
            self.delta_target_entry.config(state=tk.NORMAL)
        
        # Если есть построенная позиция, пересчитываем её
        if self.selected_call_sym and self.selected_put_sym:
            self.build_position()

    def apply_call_filter(self):
        try:
            min_delta = float(self.call_min_delta_filter.get())
            max_delta = float(self.call_max_delta_filter.get())
            if min_delta < 0 or min_delta > 1 or max_delta < 0 or max_delta > 1:
                raise ValueError()
            if min_delta > max_delta:
                messagebox.showerror("Ошибка", "Минимальная дельта не может быть больше максимальной")
                return
            self.call_filter_enabled = True
            self.update_strikes_for_current_time()
            self.call_filter_btn.config(bg='#00c853', fg='black')
            self.call_reset_filter_btn.config(bg='#37474f', fg='#e1e1e1')
        except:
            messagebox.showerror("Ошибка", "Введите корректное значение дельты (0-1)")
            self.call_min_delta_filter.delete(0, tk.END)
            self.call_min_delta_filter.insert(0, "0.05")
            self.call_max_delta_filter.delete(0, tk.END)
            self.call_max_delta_filter.insert(0, "0.6")

    def apply_put_filter(self):
        try:
            min_delta = float(self.put_min_delta_filter.get())
            max_delta = float(self.put_max_delta_filter.get())
            if min_delta < 0 or min_delta > 1 or max_delta < 0 or max_delta > 1:
                raise ValueError()
            if min_delta > max_delta:
                messagebox.showerror("Ошибка", "Минимальная дельта не может быть больше максимальной")
                return
            self.put_filter_enabled = True
            self.update_strikes_for_current_time()
            self.put_filter_btn.config(bg='#ff5252', fg='black')
            self.put_reset_filter_btn.config(bg='#37474f', fg='#e1e1e1')
        except:
            messagebox.showerror("Ошибка", "Введите корректное значение дельты (0-1)")
            self.put_min_delta_filter.delete(0, tk.END)
            self.put_min_delta_filter.insert(0, "0.05")
            self.put_max_delta_filter.delete(0, tk.END)
            self.put_max_delta_filter.insert(0, "0.6")

    def reset_call_filter(self):
        self.call_filter_enabled = False
        self.update_strikes_for_current_time()
        self.call_filter_btn.config(bg='#37474f', fg='#e1e1e1')
        self.call_reset_filter_btn.config(bg='#d32f2f', fg='white')
        min_value = self.call_min_delta_filter.get()
        max_value = self.call_max_delta_filter.get()
        self.call_info.config(
            text=f"Фильтр отключен. Значения в полях: {min_value} - {max_value}",
            fg="#ff9800"
        )

    def reset_put_filter(self):
        self.put_filter_enabled = False
        self.update_strikes_for_current_time()
        self.put_filter_btn.config(bg='#37474f', fg='#e1e1e1')
        self.put_reset_filter_btn.config(bg='#d32f2f', fg='white')
        min_value = self.put_min_delta_filter.get()
        max_value = self.put_max_delta_filter.get()
        self.put_info.config(
            text=f"Фильтр отключен. Значения в полях: {min_value} - {max_value}",
            fg="#ff9800"
        )

    def toggle_mode(self):
        self.show_all_combinations = self.mode_var.get()
        
        if self.show_all_combinations:
            self.delta_min_filter_frame.pack(fill=tk.X, pady=(8, 2))
            self.delta_max_filter_frame.pack(fill=tk.X, pady=(2, 2))
            self.delta_diff_filter_frame.pack(fill=tk.X, pady=(2, 4))
            self.filter_info_frame.pack(fill=tk.X, pady=(0, 8))
            self.calc_all_btn.config(state=tk.NORMAL)
            self.call_list.config(state=tk.DISABLED)
            self.put_list.config(state=tk.DISABLED)
            self.delta_target_entry.config(state=tk.NORMAL)
            self.capital_entry.config(state=tk.NORMAL)
            self.purchase_label.config(text="Режим: Все комбинации", fg="#6200ea")
            self.update_filter_info()
        else:
            self.delta_min_filter_frame.pack_forget()
            self.delta_max_filter_frame.pack_forget()
            self.delta_diff_filter_frame.pack_forget()
            self.filter_info_frame.pack_forget()
            self.calc_all_btn.config(state=tk.DISABLED)
            self.call_list.config(state=tk.NORMAL)
            self.put_list.config(state=tk.NORMAL)
            self.purchase_label.config(text="Покупка: не выполнена", fg="#ff9800")
            self.filter_info_label.config(text="")
        
        self.ax.clear()
        self.canvas.draw()

    def update_filter_info(self):
        try:
            min_delta = float(self.delta_min_filter_entry.get())
            max_delta = float(self.delta_max_filter_entry.get())
            diff_delta = float(self.delta_diff_filter_entry.get())
            
            info_text = (f"Фильтр: |Δ| от {min_delta:.2f} до {max_delta:.2f} | "
                         f"Макс. разница: {diff_delta:.2f}")
            self.filter_info_label.config(text=info_text, fg="#ff9800")
        except:
            self.filter_info_label.config(text="Ошибка в значениях фильтра дельты", fg="#ff5252")

    def calculate_position_quantities(self, ask_c, delta_c, ask_p, delta_p, capital, target_delta):
        """Рассчитывает количество контрактов для позиции"""
        # Добавляем комиссию к ценам
        price_c = ask_c * (1 + COMMISSION) if ask_c and ask_c > 0 else 0
        price_p = ask_p * (1 + COMMISSION) if ask_p and ask_p > 0 else 0
        
        # Проверяем, есть ли у нас оба актива для торговли
        if price_c <= 0 or price_p <= 0 or abs(delta_c) < 1e-10 or abs(delta_p) < 1e-10:
            return self.find_optimal_quantities(ask_c, delta_c, ask_p, delta_p, capital, target_delta)
        
        # В режиме cost-neutral вместо уравнения для дельты используем уравнение для стоимости
        if self.cost_neutral_mode:
            # Система уравнений для cost-neutral:
            # 1) price_c * qty_c + price_p * qty_p = capital  (общая стоимость = капиталу)
            # 2) price_c * qty_c = price_p * qty_p  (стоимость колов = стоимости путов)
            try:
                # Из уравнения 2: price_c * qty_c = price_p * qty_p
                # Подставляем в уравнение 1: price_c * qty_c + price_c * qty_c = capital
                # => 2 * price_c * qty_c = capital
                qty_call = capital / (2 * price_c)
                qty_put = (price_c * qty_call) / price_p
                
                # Проверяем на положительные количества и разумные значения
                if qty_call > 0 and qty_put > 0 and qty_call < 100000 and qty_put < 100000:
                    total_delta = qty_call * delta_c + qty_put * delta_p
                    # Проверяем, что стоимость действительно равна
                    call_cost = qty_call * price_c
                    put_cost = qty_put * price_p
                    if abs(call_cost - put_cost) / call_cost < 0.01:  # 1% допуск
                        return qty_call, qty_put, total_delta
            except Exception as e:
                pass
            
            # Если аналитическое решение не сработало, используем оптимизационный подход
            return self.find_optimal_quantities_cost_neutral(ask_c, delta_c, ask_p, delta_p, capital)
        
        # Оригинальный код для delta-neutral режима
        else:
            # Решаем систему уравнений:
            # 1) price_c * qty_c + price_p * qty_p = capital
            # 2) delta_c * qty_c + delta_p * qty_p = target_delta
            try:
                # Определитель матрицы
                det = price_c * delta_p - price_p * delta_c
                
                if abs(det) < 1e-10:  # Матрица вырождена
                    return self.find_optimal_quantities(ask_c, delta_c, ask_p, delta_p, capital, target_delta)
                
                qty_call = (capital * delta_p - price_p * target_delta) / det
                qty_put = (price_c * target_delta - capital * delta_c) / det
                
                # Проверяем на положительные количества и разумные значения
                if qty_call > 0 and qty_put > 0 and qty_call < 100000 and qty_put < 100000:
                    total_delta = qty_call * delta_c + qty_put * delta_p
                    return qty_call, qty_put, total_delta
                
            except Exception as e:
                pass
            
            # Если аналитическое решение не сработало, используем оптимизационный подход
            return self.find_optimal_quantities(ask_c, delta_c, ask_p, delta_p, capital, target_delta)

    def find_optimal_quantities_cost_neutral(self, ask_c, delta_c, ask_p, delta_p, capital):
        """Находит оптимальные количества для cost-neutral позиции"""
        price_c = ask_c * (1 + COMMISSION) if ask_c and ask_c > 0 else 0
        price_p = ask_p * (1 + COMMISSION) if ask_p and ask_p > 0 else 0
        
        best_cost_diff = float('inf')
        best_qty_call = 0
        best_qty_put = 0
        best_total_delta = 0
        best_total_cost = 0
        
        # Сначала попробуем аналитическое решение с округлением
        if price_c > 0 and price_p > 0:
            try:
                qty_call = capital / (2 * price_c)
                qty_put = (price_c * qty_call) / price_p
                
                # Если получились положительные количества
                if qty_call > 0 and qty_put > 0:
                    # Округляем до целых (для количества контрактов)
                    qty_call_int = int(round(qty_call))
                    qty_put_int = int(round(qty_put))
                    
                    # Пересчитываем с округленными значениями
                    call_cost = qty_call_int * price_c
                    put_cost = qty_put_int * price_p
                    total_cost = call_cost + put_cost
                    cost_diff = abs(call_cost - put_cost)
                    
                    if total_cost <= capital * 1.05:  # Допускаем небольшое превышение
                        if cost_diff < best_cost_diff:
                            best_cost_diff = cost_diff
                            best_qty_call = qty_call_int
                            best_qty_put = qty_put_int
                            best_total_delta = qty_call_int * delta_c + qty_put_int * delta_p
                            best_total_cost = total_cost
            except:
                pass
        
        # Если аналитическое решение не сработало или дало плохой результат,
        # используем перебор
        if best_cost_diff > capital * 0.01:  # Если разница стоимости больше 1% капитала
            max_calls = min(int(capital / price_c) + 1, 100) if price_c > 0 else 0
            max_puts = min(int(capital / price_p) + 1, 100) if price_p > 0 else 0
            
            for num_calls in range(0, max_calls + 1):
                for num_puts in range(0, max_puts + 1):
                    call_cost = num_calls * price_c
                    put_cost = num_puts * price_p
                    total_cost = call_cost + put_cost
                    
                    if total_cost <= capital:
                        cost_diff = abs(call_cost - put_cost)
                        
                        if cost_diff < best_cost_diff:
                            best_cost_diff = cost_diff
                            best_qty_call = num_calls
                            best_qty_put = num_puts
                            best_total_delta = num_calls * delta_c + num_puts * delta_p
                            best_total_cost = total_cost
        
        # Если всё еще не нашли, используем дробные количества
        if best_cost_diff > capital * 0.01 and price_c > 0 and price_p > 0:
            # Пробуем оптимизировать дробные количества
            max_calls_float = capital / price_c
            steps = 100
            
            for i in range(steps + 1):
                qty_call = i * max_calls_float / steps
                call_cost = qty_call * price_c
                
                if call_cost <= capital:
                    # Для cost-neutral: put_cost должен быть равен call_cost
                    qty_put = call_cost / price_p
                    put_cost = qty_put * price_p
                    total_cost = call_cost + put_cost
                    
                    if total_cost <= capital:
                        cost_diff = abs(call_cost - put_cost)
                        
                        if cost_diff < best_cost_diff and qty_put > 0:
                            best_cost_diff = cost_diff
                            best_qty_call = qty_call
                            best_qty_put = qty_put
                            best_total_delta = qty_call * delta_c + qty_put * delta_p
                            best_total_cost = total_cost
        
        return best_qty_call, best_qty_put, best_total_delta

    def find_optimal_quantities(self, ask_c, delta_c, ask_p, delta_p, capital, target_delta):
        price_c = ask_c * (1 + COMMISSION) if ask_c and ask_c > 0 else 0
        price_p = ask_p * (1 + COMMISSION) if ask_p and ask_p > 0 else 0
        
        best_delta_diff = float('inf')
        best_qty_call = 0
        best_qty_put = 0
        best_total_delta = 0
        best_cost = 0
        
        # Сначала попробуем аналитическое решение с округлением
        if price_c > 0 and price_p > 0 and abs(delta_c) > 1e-10 and abs(delta_p) > 1e-10:
            try:
                # Решение с учетом комиссии в цене
                det = price_c * delta_p - price_p * delta_c
                if abs(det) > 1e-10:
                    qty_call = (capital * delta_p - price_p * target_delta) / det
                    qty_put = (price_c * target_delta - capital * delta_c) / det
                    
                    # Если получились положительные количества
                    if qty_call > 0 and qty_put > 0:
                        # Округляем до целых (для количества контрактов)
                        qty_call_int = int(round(qty_call))
                        qty_put_int = int(round(qty_put))
                        
                        # Пересчитываем с округленными значениями
                        actual_cost = qty_call_int * price_c + qty_put_int * price_p
                        actual_delta = qty_call_int * delta_c + qty_put_int * delta_p
                        
                        if actual_cost <= capital * 1.05:  # Допускаем небольшое превышение
                            delta_diff = abs(actual_delta - target_delta)
                            if delta_diff < best_delta_diff:
                                best_delta_diff = delta_diff
                                best_qty_call = qty_call_int
                                best_qty_put = qty_put_int
                                best_total_delta = actual_delta
                                best_cost = actual_cost
            except:
                pass
        
        # Если аналитическое решение не сработало или дало плохой результат,
        # используем перебор
        if best_delta_diff > 0.01:  # Если точность недостаточна
            max_calls = min(int(capital / price_c) + 1, 100) if price_c > 0 else 0
            max_puts = min(int(capital / price_p) + 1, 100) if price_p > 0 else 0
            
            for num_calls in range(0, max_calls + 1):
                for num_puts in range(0, max_puts + 1):
                    cost = num_calls * price_c + num_puts * price_p
                    
                    if cost <= capital:
                        total_delta = num_calls * delta_c + num_puts * delta_p
                        delta_diff = abs(total_delta - target_delta)
                        
                        if delta_diff < best_delta_diff:
                            best_delta_diff = delta_diff
                            best_qty_call = num_calls
                            best_qty_put = num_puts
                            best_total_delta = total_delta
                            best_cost = cost
        
        # Если всё еще не нашли, используем дробные количества
        if best_delta_diff > 0.01 and price_c > 0 and price_p > 0:
            # Пробуем оптимизировать дробные количества
            max_calls_float = capital / price_c
            steps = 100
            
            for i in range(steps + 1):
                qty_call = i * max_calls_float / steps
                
                if qty_call * price_c <= capital:
                    remaining = capital - qty_call * price_c
                    qty_put = remaining / price_p
                    
                    total_delta = qty_call * delta_c + qty_put * delta_p
                    delta_diff = abs(total_delta - target_delta)
                    
                    if delta_diff < best_delta_diff and qty_put > 0:
                        best_delta_diff = delta_diff
                        best_qty_call = qty_call
                        best_qty_put = qty_put
                        best_total_delta = total_delta
                        best_cost = qty_call * price_c + qty_put * price_p
        
        return best_qty_call, best_qty_put, best_total_delta

    def calculate_all_combinations(self):
        if not self.entry_time or self.df is None:
            return
            
        try:
            target_delta = float(self.delta_target_entry.get())
            self.capital_dirty = float(self.capital_entry.get())
            min_delta = float(self.delta_min_filter_entry.get())
            max_delta = float(self.delta_max_filter_entry.get())
            max_delta_diff = float(self.delta_diff_filter_entry.get())
            
            if self.capital_dirty <= 0 or min_delta < 0 or min_delta > 1 or max_delta < 0 or max_delta > 1 or max_delta_diff < 0:
                raise ValueError()
            if min_delta > max_delta:
                messagebox.showerror("Ошибка", "Минимальная дельта не может быть больше максимальной")
                return
        except:
            messagebox.showerror("Ошибка", "Проверьте корректность введенных данных")
            return
        
        self.update_filter_info()
        
        self.result_label.config(
            text=f"Расчет всех комбинаций...\n"
                 f"Фильтр дельты: |Δ| от {min_delta:.2f} до {max_delta:.2f}\n"
                 f"Макс. разница Δ: {max_delta_diff:.2f}",
            fg="#ff9800"
        )
        self.root.update()
        
        current_time_df = self.df[self.df['fetch_time_utc'] == self.entry_time]
        
        # ДОПОЛНИТЕЛЬНАЯ ФИЛЬТРАЦИЯ: исключаем строки, где bid=0 и ask=0
        if 'bid1Price' in current_time_df.columns and 'ask1Price' in current_time_df.columns:
            current_time_df = current_time_df[~((current_time_df['bid1Price'] == 0) & 
                                                (current_time_df['ask1Price'] == 0))]
        
        calls = current_time_df[
            (current_time_df['symbol'].str.contains('-C-', na=False)) &
            (current_time_df['ask1Price'].notna()) &
            (current_time_df['ask1Price'] > 0) &
            (current_time_df['delta'].notna()) &
            (current_time_df['delta'] >= min_delta) &
            (current_time_df['delta'] <= max_delta)
        ].copy()

        puts = current_time_df[
            (current_time_df['symbol'].str.contains('-P-', na=False)) &
            (current_time_df['ask1Price'].notna()) &
            (current_time_df['ask1Price'] > 0) &
            (current_time_df['delta'].notna()) &
            (current_time_df['delta'] <= -min_delta) &
            (current_time_df['delta'] >= -max_delta)
        ].copy()
        
        calls_count = len(calls)
        puts_count = len(puts)
        total_before = len(current_time_df[current_time_df['symbol'].str.contains('-C-', na=False)])
        total_before_puts = len(current_time_df[current_time_df['symbol'].str.contains('-P-', na=False)])
        
        if calls_count == 0 or puts_count == 0:
            messagebox.showerror("Ошибка", 
                f"После фильтрации по дельте нет подходящих опционов.\n"
                f"CALL: было {total_before}, стало {calls_count}\n"
                f"PUT: было {total_before_puts}, стало {puts_count}")
            return
        
        self.filter_info_label.config(
            text=f"Фильтр: |Δ| {min_delta:.2f}-{max_delta:.2f} | Разница Δ: {max_delta_diff:.2f} | "
                 f"CALL: {calls_count}/{total_before} | PUT: {puts_count}/{total_before_puts}",
            fg="#00c853"
        )
        
        self.all_combinations_data = []
        
        max_total_combinations = 2000
        
        total_combos = calls_count * puts_count
        processed = 0
        
        for _, call_row in calls.iterrows():
            for _, put_row in puts.iterrows():
                processed += 1
                if processed % 50 == 0:
                    self.result_label.config(
                        text=f"Расчет... {processed}/{total_combos} ({len(self.all_combinations_data)} найдено)",
                        fg="#ff9800"
                    )
                    self.root.update()
                
                ask_c = call_row['ask1Price']
                delta_c = call_row['delta']
                ask_p = put_row['ask1Price']
                delta_p = put_row['delta']
                
                # НОВАЯ ПРОВЕРКА: разница между модулями дельт
                delta_c_abs = abs(delta_c)
                delta_p_abs = abs(delta_p)
                delta_diff = abs(delta_c_abs - delta_p_abs)
                
                if delta_diff > max_delta_diff:
                    continue  # Пропускаем комбинации, где разница больше допустимой
                
                try:
                    qty_call, qty_put, total_delta = self.calculate_position_quantities(
                        ask_c, delta_c, ask_p, delta_p, self.capital_dirty, target_delta
                    )
                    
                    if abs(qty_call) > 100000 or abs(qty_put) > 100000 or qty_call <= 0 or qty_put <= 0:
                        continue
                    
                    # Проверяем, что полученная суммарная дельта близка к целевой
                    if abs(total_delta - target_delta) > 0.1:  # Допуск 0.1
                        continue

                    future_data = self.df[
                        (self.df['fetch_time_utc'] > self.entry_time) & 
                        (self.df['fetch_time_utc'] <= self.end_time)
                    ]
                    
                    future_calls = future_data[future_data['symbol'] == call_row['symbol']]
                    future_puts = future_data[future_data['symbol'] == put_row['symbol']]
                    
                    common_future_times = set(future_calls['fetch_time_utc']).intersection(
                        set(future_puts['fetch_time_utc']))
                    
                    if len(common_future_times) < 2:
                        continue
                    
                    value_data = self.analyze_position_value_history_after_purchase(
                        call_row['symbol'], put_row['symbol'], qty_call, qty_put
                    )
                    
                    if value_data is None:
                        continue
                    
                    combo_data = {
                        'call_symbol': call_row['symbol'],
                        'put_symbol': put_row['symbol'],
                        'call_strike': float(call_row['strike']),
                        'put_strike': float(put_row['strike']),
                        'qty_call': qty_call,
                        'qty_put': qty_put,
                        'call_delta': delta_c,
                        'put_delta': delta_p,
                        'delta_diff': delta_diff,
                        'total_delta': total_delta,
                        'call_price': ask_c,
                        'put_price': ask_p,
                        'call_total': qty_call * ask_c,
                        'put_total': qty_put * ask_p,
                        'max_value_after_purchase': value_data['max_value_after_purchase'],
                        'min_value_after_purchase': value_data['min_value_after_purchase'],
                        'final_value': value_data['final_value'],
                        'value_history_after_purchase': value_data['value_history_after_purchase'],
                        'time_index_after_purchase': value_data['time_index_after_purchase'],
                        'max_value_time_after_purchase': value_data['max_value_time_after_purchase'],
                        'data_points_after_purchase': value_data['data_points_after_purchase']
                    }
                    self.all_combinations_data.append(combo_data)
                    
                    if len(self.all_combinations_data) >= max_total_combinations:
                        break
                        
                except Exception as e:
                    print(f"Ошибка комбинации {call_row.get('symbol','?')}/{put_row.get('symbol','?')}: {e}")
                    continue
            
            if len(self.all_combinations_data) >= max_total_combinations:
                break
        
        if len(self.all_combinations_data) == 0:
            self.result_label.config(
                text="Не удалось найти ни одной валидной комбинации",
                fg="#ff5252"
            )
            return
        
        self.all_combinations_data.sort(key=lambda x: x['max_value_after_purchase'], reverse=True)
        
        self.build_all_combinations_graph()
        
        if self.all_combinations_data:
            best = self.all_combinations_data[0]
            call_strike = float(best['call_strike'])
            put_strike = float(best['put_strike'])
            call_str = f"{call_strike:.0f}" if call_strike.is_integer() else f"{call_strike:.4f}"
            put_str = f"{put_strike:.0f}" if put_strike.is_integer() else f"{put_strike:.4f}"
            self.result_label.config(
                text=f"Найдено {len(self.all_combinations_data)} комбинаций\n"
                     f"Лучшая: {call_str}C / {put_str}P\n"
                     f"Разница Δ: {best['delta_diff']:.3f}\n"
                     f"Макс. стоимость после покупки: ${best['max_value_after_purchase']:,.0f}\n"
                     f"Δ CALL: {best['call_delta']:.3f}   Δ PUT: {best['put_delta']:.3f}\n"
                     f"Время максимума: {best['max_value_time_after_purchase'].strftime('%H:%M')}",
                fg="#00c853"
            )
            
            if self.show_info_var.get():
                self.show_results_window(min_delta, max_delta, max_delta_diff)
                
    def analyze_position_value_history_after_purchase(self, call_sym, put_sym, qty_call, qty_put):
        try:
            df_c = self.df[self.df['symbol'] == call_sym].set_index('fetch_time_utc')
            df_p = self.df[self.df['symbol'] == put_sym].set_index('fetch_time_utc')
            
            # ФИЛЬТРАЦИЯ: исключаем строки, где bid=0 и ask=0
            if 'bid1Price' in df_c.columns and 'ask1Price' in df_c.columns:
                df_c = df_c[~((df_c['bid1Price'] == 0) & (df_c['ask1Price'] == 0))]
            if 'bid1Price' in df_p.columns and 'ask1Price' in df_p.columns:
                df_p = df_p[~((df_p['bid1Price'] == 0) & (df_p['ask1Price'] == 0))]
            
            common = df_c.index.intersection(df_p.index)
            if len(common) < 2:
                return None
                
            common = sorted(common)
            
            times_after = [t for t in common if self.entry_time < t <= self.end_time]
            
            if len(times_after) < 2:
                return None
            
            value_history = []
            valid_times = []
            
            for t in times_after:
                bid_c = df_c.loc[t, 'bid1Price'] if t in df_c.index else np.nan
                bid_p = df_p.loc[t, 'bid1Price'] if t in df_p.index else np.nan
                
                if pd.isna(bid_c) or pd.isna(bid_p):
                    continue
                
                value = qty_call * bid_c + qty_put * bid_p
                value_history.append(value)
                valid_times.append(t)
            
            if len(value_history) < 2:
                return None
            
            max_val = np.max(value_history)
            min_val = np.min(value_history)
            final_val = value_history[-1]
            max_idx = np.argmax(value_history)
            max_time = valid_times[max_idx]
            
            return {
                'max_value_after_purchase': max_val,
                'min_value_after_purchase': min_val,
                'final_value': final_val,
                'value_history_after_purchase': value_history,
                'time_index_after_purchase': valid_times,
                'max_value_time_after_purchase': max_time,
                'data_points_after_purchase': len(value_history)
            }
        except Exception as e:
            print(f"Ошибка анализа {call_sym}/{put_sym}: {e}")
            return None

    def build_all_combinations_graph(self):
        if not self.all_combinations_data:
            return
        
        self.ax.clear()
        
        top_combinations = self.all_combinations_data[:10]
        colors = plt.cm.viridis(np.linspace(0.2, 0.8, len(top_combinations)))
        
        try:
            min_d = float(self.delta_min_filter_entry.get())
            max_d = float(self.delta_max_filter_entry.get())
            diff_d = float(self.delta_diff_filter_entry.get())
            filter_text = f"Фильтр |Δ| {min_d:.2f}–{max_d:.2f} | Разница Δ ≤ {diff_d:.2f}"
        except:
            filter_text = "Фильтр не установлен"
        
        plotted = 0
        for i, combo in enumerate(top_combinations):
            times = combo['time_index_after_purchase']
            values = combo['value_history_after_purchase']
            
            if len(times) < 2 or len(values) < 2:
                continue
                
            plotted += 1
            call_str = self.format_strike(combo['call_strike'])
            put_str = self.format_strike(combo['put_strike'])
            label = f"{call_str}C / {put_str}P"
            
            lw = 2.5 if plotted == 1 else 1.5
            alpha = 0.95 if plotted <= 3 else 0.65
            
            self.ax.plot(times, values, color=colors[i], lw=lw, alpha=alpha, label=label)
            
            max_idx = np.argmax(values)
            self.ax.plot(times[max_idx], values[max_idx],
                         marker='o', ms=8, color=colors[i], alpha=0.9,
                         label=f'Макс ${combo["max_value_after_purchase"]:,.0f}')
        
        self.ax.axhline(self.capital_dirty, color='#ff5252', ls='--', lw=1.5,
                        alpha=0.8, label=f'Вложено ${self.capital_dirty:,.0f}')
        
        capital_no_comm = self.capital_dirty / (1 + COMMISSION)
        self.ax.axhline(capital_no_comm, color='#ff9800', ls=':', lw=1.2,
                        alpha=0.6, label=f'Без комиссии ${capital_no_comm:,.0f}')
        
        if self.entry_time:
            self.ax.axvline(self.entry_time, color='#ff9800', ls='--', lw=2.0,
                            alpha=0.9, label='Покупка')
        
        if self.end_time:
            self.ax.axvline(self.end_time, color='#ba68c8', ls='--', lw=2.0,
                            alpha=0.9, label='Конец анализа')
        
        self.ax.grid(True, color='#2a2e39', linestyle='-', linewidth=0.6, alpha=0.7)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['bottom'].set_color('#4a4e5a')
        self.ax.spines['left'].set_color('#4a4e5a')
        
        if top_combinations:
            best = top_combinations[0]
            call_str = self.format_strike(best['call_strike'])
            put_str = self.format_strike(best['put_strike'])
            
            annotation_text = (
                f"Лучшая: {call_str}C / {put_str}P\n"
                f"Макс: ${best['max_value_after_purchase']:,.0f}\n"
                f"Разница Δ: {best['delta_diff']:.3f}\n"
                f"Время: {best['max_value_time_after_purchase'].strftime('%H:%M:%S')}\n"
                f"Финал: ${best['final_value']:,.0f}\n"
                f"{filter_text}"
            )
            
            self.ax.text(0.02, 0.98, annotation_text,
                         transform=self.ax.transAxes,
                         fontsize=9,
                         color='#00c853',
                         verticalalignment='top',
                         bbox=dict(boxstyle='round', facecolor='#1e222d', alpha=0.92,
                                   edgecolor='#00c853', linewidth=1))
        
        title = (f"Стрэнглы — топ по максимальной стоимости ПОСЛЕ покупки\n"
                 f"Покупка: {self.entry_time.strftime('%Y-%m-%d %H:%M')} | "
                 f"Капитал: ${self.capital_dirty:,.0f} | {filter_text}")
        self.ax.set_title(title, color='#e1e1e1', fontsize=12, pad=10)

        self.ax.set_xlabel("Время (UTC)", color='#b0bec5', fontsize=9)
        self.ax.set_ylabel("Стоимость позиции, USDT", color='#b0bec5', fontsize=9)

        self.ax.tick_params(axis='x', rotation=35, colors='#cfd8dc')
        self.ax.tick_params(axis='y', colors='#cfd8dc')

        handles, labels = self.ax.get_legend_handles_labels()
        unique = {}
        for h, l in zip(handles, labels):
            if l not in unique:
                unique[l] = h
        self.ax.legend(unique.values(), unique.keys(), loc='upper right', 
                       frameon=False, fontsize=8, ncol=2)
        
        self.ax.xaxis.set_major_formatter(DateFormatter('%d %b %H:%M'))
        
        self.fig.tight_layout()
        self.canvas.draw()

    def show_results_window(self, min_delta, max_delta, max_delta_diff=None):
        if not self.all_combinations_data:
            return
        
        win = tk.Toplevel(self.root)
        title = f"Результаты — {len(self.all_combinations_data)} комбинаций"
        if max_delta_diff:
            title += f" | Разница Δ ≤ {max_delta_diff:.2f}"
        win.title(title)
        win.geometry("1700x900")
        
        label_text = f"Найдено: {len(self.all_combinations_data)} комбинаций (фильтр |Δ| {min_delta:.2f}–{max_delta:.2f})"
        if max_delta_diff:
            label_text += f", макс. разница Δ: {max_delta_diff:.2f}"
        tk.Label(win, 
                 text=label_text,
                 font=("Arial", 12, "bold"), fg='#00c853').pack(pady=10)
        
        notebook = ttk.Notebook(win)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        top10 = tk.Frame(notebook)
        all_com = tk.Frame(notebook)
        notebook.add(top10, text="Топ-10")
        notebook.add(all_com, text=f"Все ({len(self.all_combinations_data)})")
        
        # Передаем исходные данные для сортировки
        self._create_table(top10, self.all_combinations_data[:10], "ТОП-10 ПО МАКСИМАЛЬНОЙ СТОИМОСТИ ПОСЛЕ ПОКУПКИ")
        
        # Передаем копию данных для сортировки
        sorted_data = self.all_combinations_data.copy()
        self._create_table(all_com, sorted_data, "ВСЕ КОМБИНАЦИИ — НАЖМИТЕ НА ЗАГОЛОВОК ДЛЯ СОРТИРОВКИ")
        
        tk.Button(win, text="Закрыть", command=win.destroy,
                  bg='#d32f2f', fg='white', font=("Arial", 10), width=20).pack(pady=10)

    def _create_table(self, parent, data, title):
        tk.Label(parent, text=title, font=("Arial", 11, "bold"), 
                 fg="#00c853", bg='#1e222d').pack(pady=(5, 10))
        
        tree_frame = tk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        columns = ("№", "CALL Strike", "PUT Strike", "QTY CALL", "QTY PUT", 
                   "CALL Price", "PUT Price", "CALL Total", "PUT Total",
                   "Δ CALL", "Δ PUT", "Δ Total", "Разница Δ", "МАКС. $", "ВРЕМЯ МАКС.")
        
        # Сохраняем ссылку на данные для этого дерева
        tree_data = {
            'original_data': data.copy(),
            'current_sort_column': None,
            'sort_reverse': False
        }
        
        tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=25)
        
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=hsb.set)
        
        tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        
        column_widths = {
            "№": 40,
            "CALL Strike": 100,
            "PUT Strike": 100,
            "QTY CALL": 80,
            "QTY PUT": 80,
            "CALL Price": 90,
            "PUT Price": 90,
            "CALL Total": 95,
            "PUT Total": 95,
            "Δ CALL": 70,
            "Δ PUT": 70,
            "Δ Total": 80,
            "Разница Δ": 75,
            "МАКС. $": 110,
            "ВРЕМЯ МАКС.": 110
        }
        
        # Словарь для преобразования русских названий столбцов в ключи данных
        column_keys = {
            "№": "index",
            "CALL Strike": "call_strike",
            "PUT Strike": "put_strike",
            "QTY CALL": "qty_call",
            "QTY PUT": "qty_put",
            "CALL Price": "call_price",
            "PUT Price": "put_price",
            "CALL Total": "call_total",
            "PUT Total": "put_total",
            "Δ CALL": "call_delta",
            "Δ PUT": "put_delta",
            "Δ Total": "total_delta",
            "Разница Δ": "delta_diff",
            "МАКС. $": "max_value_after_purchase",
            "ВРЕМЯ МАКС.": "max_value_time_after_purchase"
        }
        
        # Функция для сортировки при клике на заголовок
        def treeview_sort_column(tv, col, reverse):
            try:
                # Получаем ключ данных для этого столбца
                data_key = column_keys.get(col, None)
                
                if data_key:
                    # Для числовых столбцов
                    numeric_columns = ["call_strike", "put_strike", "qty_call", "qty_put", 
                                      "call_price", "put_price", "call_total", "put_total",
                                      "call_delta", "put_delta", "total_delta", "delta_diff",
                                      "max_value_after_purchase"]
                    
                    if data_key in numeric_columns:
                        # Сортируем как числа
                        tree_data['original_data'].sort(key=lambda x: float(x[data_key]) if x[data_key] is not None else 0, 
                                                        reverse=reverse)
                    elif data_key == "max_value_time_after_purchase":
                        # Сортируем как даты
                        tree_data['original_data'].sort(key=lambda x: x[data_key], 
                                                        reverse=reverse)
                    else:
                        # Сортируем как строки
                        tree_data['original_data'].sort(key=lambda x: str(x.get(data_key, '')), 
                                                        reverse=reverse)
                    
                    # Обновляем отображение
                    update_treeview()
                    
                    # Меняем направление сортировки для следующего клика
                    tree.heading(col, command=lambda: treeview_sort_column(tv, col, not reverse))
                    
                    # Обновляем состояние сортировки
                    tree_data['current_sort_column'] = col
                    tree_data['sort_reverse'] = reverse
                    
                    # Обновляем стрелочку в заголовке
                    for c in columns:
                        if c == col:
                            tree.heading(c, text=f"{c} {'↓' if reverse else '↑'}")
                        else:
                            tree.heading(c, text=c)
            except Exception as e:
                print(f"Ошибка сортировки: {e}")
        
        def update_treeview():
            # Очищаем дерево
            for item in tree.get_children():
                tree.delete(item)
            
            # Заполняем заново
            for i, combo in enumerate(tree_data['original_data'], 1):
                call_price = combo.get('call_price', 0)
                put_price = combo.get('put_price', 0)
                call_total = combo['qty_call'] * call_price
                put_total = combo['qty_put'] * put_price
                
                call_str = self.format_strike(combo['call_strike'])
                put_str = self.format_strike(combo['put_strike'])
                
                values = (
                    str(i),
                    call_str,
                    put_str,
                    f"{combo['qty_call']:.4f}",
                    f"{combo['qty_put']:.4f}",
                    f"{call_price:.2f}",
                    f"{put_price:.2f}",
                    f"{call_total:.2f}",
                    f"{put_total:.2f}",
                    f"{combo['call_delta']:.3f}",
                    f"{combo['put_delta']:.3f}",
                    f"{combo.get('total_delta', combo['call_delta'] + combo['put_delta']):.3f}",
                    f"{combo.get('delta_diff', abs(abs(combo['call_delta']) - abs(combo['put_delta']))):.3f}",
                    f"${combo['max_value_after_purchase']:,.0f}",
                    combo['max_value_time_after_purchase'].strftime('%H:%M:%S')
                )
                
                iid = tree.insert('', tk.END, values=values)
                
                if i % 2 == 0:
                    tree.tag_configure('even', background='#f8f9fa')
                    tree.item(iid, tags=('even',))
        
        for col in columns:
            # Назначаем команду сортировки для каждого заголовка
            tree.heading(col, text=col, 
                         command=lambda c=col: treeview_sort_column(tree, c, False))
            tree.column(col, width=column_widths[col], anchor='center')
        
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        # Инициализируем дерево данными
        update_treeview()
        
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Treeview", background="#ffffff", foreground="#000000",
                        rowheight=26, fieldbackground="#ffffff")
        style.configure("Treeview.Heading", background="#4a4e5a", foreground="#ffffff",
                        font=("Arial", 9, "bold"))
        style.map("Treeview.Heading", background=[('active', '#0288d1')])
        
        tree.update_idletasks()
        
        # Добавляем кнопки для быстрой сортировки по ключевым столбцам
        sort_buttons_frame = tk.Frame(parent, bg='#1e222d')
        sort_buttons_frame.pack(fill=tk.X, pady=(5, 5))
        
        tk.Label(sort_buttons_frame, text="Быстрая сортировка:", 
                 font=("Arial", 9), fg="#666666", bg='#1e222d').pack(side=tk.LEFT, padx=(0, 10))
        
        sort_options = [
            ("МАКС. $ ↓", "МАКС. $", True),
            ("МАКС. $ ↑", "МАКС. $", False),
            ("Δ CALL ↓", "Δ CALL", True),
            ("Δ CALL ↑", "Δ CALL", False),
            ("Δ PUT ↓", "Δ PUT", True),
            ("Δ PUT ↑", "Δ PUT", False),
            ("Разница Δ ↓", "Разница Δ", True),
            ("Разница Δ ↑", "Разница Δ", False),
            ("CALL Strike ↓", "CALL Strike", True),
            ("CALL Strike ↑", "CALL Strike", False),
        ]
        
        for btn_text, col, reverse in sort_options:
            btn = tk.Button(sort_buttons_frame, text=btn_text,
                          command=lambda c=col, r=reverse: treeview_sort_column(tree, c, r),
                          bg='#37474f', fg='#e1e1e1',
                          font=("Arial", 8), relief=tk.FLAT)
            btn.pack(side=tk.LEFT, padx=2)
        
        tk.Label(parent, text=f"Показано: {len(data)} строк | Кликните по заголовку для сортировки",
                 font=("Arial", 9), fg="#666666").pack(side=tk.BOTTOM, pady=5)

    def build_position(self):
        if self.show_all_combinations:
            return
            
        if not (self.selected_call_sym and self.selected_put_sym and self.entry_time):
            return

        try:
            target_delta = float(self.delta_target_entry.get())
            self.capital_dirty = float(self.capital_entry.get())
            if self.capital_dirty <= 0:
                raise ValueError()
        except:
            return

        row_c = self.df[(self.df['symbol'] == self.selected_call_sym) & 
                        (self.df['fetch_time_utc'] == self.entry_time)]
        row_p = self.df[(self.df['symbol'] == self.selected_put_sym) & 
                        (self.df['fetch_time_utc'] == self.entry_time)]

        # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: исключаем строки, где bid=0 и ask=0
        if not row_c.empty and 'bid1Price' in row_c.columns and 'ask1Price' in row_c.columns:
            row_c = row_c[~((row_c['bid1Price'] == 0) & (row_c['ask1Price'] == 0))]
        if not row_p.empty and 'bid1Price' in row_p.columns and 'ask1Price' in row_p.columns:
            row_p = row_p[~((row_p['bid1Price'] == 0) & (row_p['ask1Price'] == 0))]

        if row_c.empty or row_p.empty:
            return

        ask_c   = row_c['ask1Price'].iloc[0]
        delta_c = row_c['delta'].iloc[0]
        ask_p   = row_p['ask1Price'].iloc[0]
        delta_p = row_p['delta'].iloc[0]

        self.qty_call, self.qty_put, sum_delta = self.calculate_position_quantities(
            ask_c, delta_c, ask_p, delta_p, self.capital_dirty, target_delta
        )

        df_c = self.df[self.df['symbol'] == self.selected_call_sym].set_index('fetch_time_utc')
        df_p = self.df[self.df['symbol'] == self.selected_put_sym].set_index('fetch_time_utc')

        # ФИЛЬТРАЦИЯ для df_c и df_p
        if 'bid1Price' in df_c.columns and 'ask1Price' in df_c.columns:
            df_c = df_c[~((df_c['bid1Price'] == 0) & (df_c['ask1Price'] == 0))]
        if 'bid1Price' in df_p.columns and 'ask1Price' in df_p.columns:
            df_p = df_p[~((df_p['bid1Price'] == 0) & (df_p['ask1Price'] == 0))]

        common = df_c.index.intersection(df_p.index)
        pos = pd.DataFrame(index=common)
        pos['bid_c'] = df_c.loc[common, 'bid1Price']
        pos['bid_p'] = df_p.loc[common, 'bid1Price']
        pos['value'] = self.qty_call * pos['bid_c'] + self.qty_put * pos['bid_p']

        pos_after = pos[(pos.index >= self.entry_time) & (pos.index <= self.end_time)]
        
        self.ax.clear()

        self.purchase_time = self.entry_time
        
        # Обновляем информацию о режиме
        mode_text = "Cost-neutral" if self.cost_neutral_mode else "Δ-neutral"
        mode_color = "#ff9800" if self.cost_neutral_mode else "#4fc3f7"
        
        self.purchase_label.config(
            text=f"Покупка: {self.purchase_time.strftime('%Y-%m-%d %H:%M:%S')} | Режим: {mode_text}",
            fg=mode_color
        )

        if len(pos_after) > 0:
            max_value = pos_after['value'].max()
            max_value_time = pos_after['value'].idxmax()
            final_value = pos_after['value'].iloc[-1]
            min_value = pos_after['value'].min()
        else:
            max_value = max_value_time = final_value = min_value = 0

        if len(pos_after) > 0:
            self.ax.plot(pos_after.index, pos_after['value'], 
                         color='#00c853', lw=1.8, label='Стоимость позиции')
            
            if max_value_time:
                self.ax.plot(max_value_time, max_value, 
                             marker='o', ms=8, color='#00c853',
                             label=f'Макс ${max_value:,.0f}')
        else:
            self.ax.text(0.5, 0.5, 'Нет данных после покупки',
                         transform=self.ax.transAxes, fontsize=12,
                         ha='center', va='center', color='#ff5252')
        
        self.ax.axhline(self.capital_dirty, color='#ff5252', ls='--', lw=1.2,
                        label=f'Вложено ${self.capital_dirty:,.0f}')
        
        if self.purchase_time:
            self.ax.axvline(self.purchase_time, color='#ff9800', ls='--', lw=2.0,
                            label='Покупка')
        
        if self.end_time:
            self.ax.axvline(self.end_time, color='#ba68c8', ls='--', lw=2.0,
                            label=f'Конец ({self.end_time.strftime("%H:%M")})')

        self.ax.grid(True, color='#2a2e39', linestyle='-', linewidth=0.6, alpha=0.7)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['bottom'].set_color('#4a4e5a')
        self.ax.spines['left'].set_color('#4a4e5a')

        call_str = self.format_strike(row_c['strike'].iloc[0])
        put_str = self.format_strike(row_p['strike'].iloc[0])

        # Добавляем информацию о режиме в заголовок
        title = (f"Стрэнгл   {call_str}C / {put_str}P   [{mode_text}]\n"
                 f"qty C: {self.qty_call:+.4f}   qty P: {self.qty_put:+.4f}   ∑Δ ≈ {sum_delta:+.4f}\n"
                 f"Диапазон: {self.entry_time.strftime('%H:%M')} – {self.end_time.strftime('%H:%M')}")
        self.ax.set_title(title, color='#e1e1e1', fontsize=12, pad=10)

        self.ax.set_xlabel("Время (UTC)", color='#b0bec5', fontsize=9)
        self.ax.set_ylabel("Стоимость позиции, USDT", color='#b0bec5', fontsize=9)

        self.ax.tick_params(axis='x', rotation=35, colors='#cfd8dc')
        self.ax.tick_params(axis='y', colors='#cfd8dc')

        self.ax.legend(loc='upper left', frameon=False, fontsize=9)

        self.ax.xaxis.set_major_formatter(DateFormatter('%d %b %H:%M'))

        self.fig.tight_layout()
        self.canvas.draw()

        # Добавляем информацию о стоимости позиций в режиме cost-neutral
        if self.cost_neutral_mode:
            call_cost = self.qty_call * ask_c * (1 + COMMISSION)
            put_cost = self.qty_put * ask_p * (1 + COMMISSION)
            cost_diff = abs(call_cost - put_cost) / call_cost * 100 if call_cost > 0 else 100
            
            self.result_label.config(
                text=f"CALL: {self.qty_call:+.4f}   PUT: {self.qty_put:+.4f}\n"
                     f"∑Δ: {sum_delta:+.6f}\n"
                     f"Стоимость CALL: ${call_cost:.2f}\n"
                     f"Стоимость PUT: ${put_cost:.2f}\n"
                     f"Разница: {cost_diff:.2f}%\n"
                     f"Макс после покупки: ${max_value:,.0f}\n"
                     f"Мин после покупки: ${min_value:,.0f}\n"
                     f"Финальная: ${final_value:,.0f}",
                fg="#00c853"
            )
        else:
            self.result_label.config(
                text=f"CALL: {self.qty_call:+.4f}   PUT: {self.qty_put:+.4f}\n"
                     f"∑Δ: {sum_delta:+.6f}\n"
                     f"Макс после покупки: ${max_value:,.0f}\n"
                     f"Мин после покупки: ${min_value:,.0f}\n"
                     f"Финальная: ${final_value:,.0f}\n"
                     f"Анализ до: {self.end_time.strftime('%H:%M')}",
                fg="#00c853"
            )

    def load_file(self):
        file_types = [
            ("CSV файлы", "*.csv"),
            ("Excel файлы", "*.xlsx *.xls *.xlsm"),
            ("Все файлы", "*.*")
        ]
        
        path = filedialog.askopenfilename(filetypes=file_types)
        if not path:
            return

        try:
            self.reset_all()
            
            file_ext = os.path.splitext(path)[1].lower()
            
            if file_ext in ['.xlsx', '.xls', '.xlsm']:
                self.df = pd.read_excel(path, dtype=str)
            elif file_ext == '.csv':
                encodings = ['utf-8', 'cp1251', 'latin1']
                for enc in encodings:
                    try:
                        self.df = pd.read_csv(path, dtype=str, encoding=enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    self.df = pd.read_csv(path, dtype=str, encoding='utf-8', errors='replace')
            else:
                messagebox.showerror("Ошибка", "Неподдерживаемый формат")
                return

            numeric_cols = ['strike', 'markPrice', 'indexPrice', 'bid1Price', 'ask1Price',
                            'delta', 'gamma', 'vega', 'theta']

            for col in numeric_cols:
                if col in self.df.columns:
                    self.df[col] = self.df[col].astype(str).str.replace(r'\s+', '', regex=True)
                    self.df[col] = self.df[col].str.replace(',', '.', regex=False)
                    self.df[col] = pd.to_numeric(self.df[col], errors='coerce')

            if 'fetch_time_utc' in self.df.columns:
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%d.%m.%Y %H:%M:%S',
                            '%Y-%m-%d %H:%M:%S.%f']:
                    try:
                        self.df['fetch_time_utc'] = pd.to_datetime(self.df['fetch_time_utc'], 
                                                                format=fmt, utc=True, errors='raise')
                        break
                    except:
                        continue
                else:
                    self.df['fetch_time_utc'] = pd.to_datetime(self.df['fetch_time_utc'], 
                                                            utc=True, errors='coerce')
            
            # ФИЛЬТРАЦИЯ: УДАЛЯЕМ СТРОКИ, ГДЕ И bid1Price И ask1Price РАВНЫ 0
            if 'bid1Price' in self.df.columns and 'ask1Price' in self.df.columns:
                # Сохраняем исходное количество строк для отчета
                original_count = len(self.df)
                
                # Удаляем строки, где оба значения равны 0
                self.df = self.df[~((self.df['bid1Price'] == 0) & (self.df['ask1Price'] == 0))]
                
                # Подсчитываем сколько удалили
                removed_count = original_count - len(self.df)
                if removed_count > 0:
                    print(f"Удалено {removed_count} строк, где и bid и ask равны 0")
            
            self.df = self.df.dropna(subset=['fetch_time_utc']).sort_values('fetch_time_utc')

            if 'strike' in self.df.columns:
                self.df['strike'] = pd.to_numeric(self.df['strike'], errors='coerce')

            self.unique_times = sorted(self.df['fetch_time_utc'].unique())
            self.time_index = 0

            self.time_scale.config(from_=0, to=len(self.unique_times)-1 if self.unique_times else 0)
            self.time_scale.set(0)

            self.time_combo['values'] = [t.strftime("%Y-%m-%d %H:%M:%S") for t in self.unique_times]
            if self.unique_times:
                self.time_combo.current(0)
                self.entry_time = self.unique_times[0]
                
                self.end_time = self.unique_times[-1]
                self.time_end_scale.config(from_=0, to=len(self.unique_times)-1)
                self.time_end_scale.set(len(self.unique_times)-1)
                self.time_end_combo['values'] = [t.strftime("%Y-%m-%d %H:%M:%S") for t in self.unique_times]
                self.time_end_combo.current(len(self.unique_times)-1)

            self.update_strikes_for_current_time()

            file_name = os.path.basename(path)
            removed_info = f" (удалено {removed_count} строк с bid=ask=0)" if removed_count > 0 else ""
            self.file_label.config(
                text=f"{file_name} ({len(self.df)} строк{removed_info}, {len(self.unique_times)} точек)",
                fg="#00c853"
            )

        except Exception as e:
            messagebox.showerror("Ошибка загрузки", f"{str(e)}")
            print(f"Ошибка загрузки: {e}")

    def update_strikes_for_current_time(self):
        if not self.entry_time or self.df is None:
            return
        
        current = self.df[self.df['fetch_time_utc'] == self.entry_time]

        if 'bid1Price' in current.columns and 'ask1Price' in current.columns:
            current = current[~((current['bid1Price'] == 0) & (current['ask1Price'] == 0))]
        
        call_cond = [
            current['symbol'].str.contains('-C-', na=False),
            current['ask1Price'].notna(),
            current['delta'].notna()
        ]
        
        put_cond = [
            current['symbol'].str.contains('-P-', na=False),
            current['ask1Price'].notna(),
            current['delta'].notna()
        ]
        
        if self.call_filter_enabled:
            try:
                min_d = float(self.call_min_delta_filter.get())
                max_d = float(self.call_max_delta_filter.get())
                if min_d > 0:
                    call_cond.append(current['delta'].abs() >= min_d)
                if max_d < 1:
                    call_cond.append(current['delta'].abs() <= max_d)
                call_cond.append(current['delta'] > 0)
            except:
                pass
        
        if self.put_filter_enabled:
            try:
                min_d = float(self.put_min_delta_filter.get())
                max_d = float(self.put_max_delta_filter.get())
                if min_d > 0:
                    put_cond.append(current['delta'].abs() >= min_d)
                if max_d < 1:
                    put_cond.append(current['delta'].abs() <= max_d)
                put_cond.append(current['delta'] < 0)
            except:
                pass
        
        calls = current[np.logical_and.reduce(call_cond)].copy()
        puts  = current[np.logical_and.reduce(put_cond)].copy()
        
        self.call_options = calls[['strike', 'symbol']].drop_duplicates().sort_values('strike')
        self.put_options  = puts[['strike', 'symbol']].drop_duplicates().sort_values('strike')

        self.call_list.delete(0, tk.END)
        if not self.call_options.empty:
            for _, r in self.call_options.iterrows():
                strike_val = r['strike']
                strike_str = self.format_strike(strike_val) if pd.notna(strike_val) else "N/A"
                self.call_list.insert(tk.END, f"{strike_str:>10}  {r['symbol']}")
            
            count = len(self.call_options)
            if self.call_filter_enabled:
                try:
                    min_d = float(self.call_min_delta_filter.get())
                    max_d = float(self.call_max_delta_filter.get())
                    self.call_info.config(
                        text=f"CALL: {count}   Δ {min_d:.2f}–{max_d:.2f}",
                        fg="#00c853"
                    )
                except:
                    self.call_info.config(text=f"CALL: {count}   фильтр активен", fg="#00c853")
            else:
                self.call_info.config(text=f"CALL: {count}   фильтр отключён", fg="#80cbc4")
        else:
            self.call_list.insert(tk.END, "Нет подходящих CALL")
            self.call_info.config(text="Нет данных", fg="#ff9800")

        self.put_list.delete(0, tk.END)
        if not self.put_options.empty:
            for _, r in self.put_options.iterrows():
                strike_val = r['strike']
                strike_str = self.format_strike(strike_val) if pd.notna(strike_val) else "N/A"
                self.put_list.insert(tk.END, f"{strike_str:>10}  {r['symbol']}")
            
            count = len(self.put_options)
            if self.put_filter_enabled:
                try:
                    min_d = float(self.put_min_delta_filter.get())
                    max_d = float(self.put_max_delta_filter.get())
                    self.put_info.config(
                        text=f"PUT: {count}   |Δ| {min_d:.2f}–{max_d:.2f}",
                        fg="#ff5252"
                    )
                except:
                    self.put_info.config(text=f"PUT: {count}   фильтр активен", fg="#ff5252")
            else:
                self.put_info.config(text=f"PUT: {count}   фильтр отключён", fg="#ef9a9a")
        else:
            self.put_list.insert(tk.END, "Нет подходящих PUT")
            self.put_info.config(text="Нет данных", fg="#ff9800")
        
        # Восстанавливаем выделение, если символы всё ещё в списке
        if self.selected_call_sym and self.selected_call_sym in self.call_options['symbol'].values:
            mask = self.call_options['symbol'] == self.selected_call_sym
            if mask.any():
                self.updating_selection = True
                try:
                    self.call_list.selection_clear(0, tk.END)
                    idx = self.call_options.index[mask][0]
                    self.call_list.selection_set(idx)
                    self.call_list.see(idx)
                finally:
                    self.updating_selection = False
        
        if self.selected_put_sym and self.selected_put_sym in self.put_options['symbol'].values:
            mask = self.put_options['symbol'] == self.selected_put_sym
            if mask.any():
                self.updating_selection = True
                try:
                    self.put_list.selection_clear(0, tk.END)
                    idx = self.put_options.index[mask][0]
                    self.put_list.selection_set(idx)
                    self.put_list.see(idx)
                finally:
                    self.updating_selection = False
        
        self.update_option_info(update_highlight=False)

    def on_scale_changed(self, val):
        idx = int(float(val))
        if self.unique_times and 0 <= idx < len(self.unique_times):
            self.time_index = idx
            self.entry_time = self.unique_times[idx]
            self.time_combo.current(idx)
            self.update_strikes_for_current_time()
            
            if idx > self.time_end_scale.get():
                self.time_end_scale.set(idx)
                self.end_time = self.unique_times[idx]
                self.time_end_combo.current(idx)
            
            end_times = [t.strftime("%Y-%m-%d %H:%M:%S") for t in self.unique_times[idx:]]
            self.time_end_combo['values'] = end_times
            
            if not self.show_all_combinations and self.selected_call_sym and self.selected_put_sym:
                self.build_position()

    def on_combo_selected(self, event=None):
        idx = self.time_combo.current()
        if idx >= 0:
            self.time_index = idx
            self.time_scale.set(idx)
            self.entry_time = self.unique_times[idx]
            self.update_strikes_for_current_time()
            
            if idx > self.time_end_scale.get():
                self.time_end_scale.set(idx)
                self.end_time = self.unique_times[idx]
                self.time_end_combo.current(idx)
            
            end_times = [t.strftime("%Y-%m-%d %H:%M:%S") for t in self.unique_times[idx:]]
            self.time_end_combo['values'] = end_times
            
            if not self.show_all_combinations and self.selected_call_sym and self.selected_put_sym:
                self.build_position()

    def on_end_scale_changed(self, val):
        idx = int(float(val))
        if self.unique_times and 0 <= idx < len(self.unique_times):
            if idx < self.time_index:
                idx = self.time_index
                self.time_end_scale.set(idx)
            
            self.end_time = self.unique_times[idx]
            time_str = self.end_time.strftime("%Y-%m-%d %H:%M:%S")
            if time_str in self.time_end_combo['values']:
                values_list = list(self.time_end_combo['values'])
                combo_idx = values_list.index(time_str)
                self.time_end_combo.current(combo_idx)
            
            if not self.show_all_combinations and self.selected_call_sym and self.selected_put_sym:
                self.build_position()

    def on_end_combo_selected(self, event=None):
        idx = self.time_end_combo.current()
        if idx >= 0 and self.unique_times:
            time_str = self.time_end_combo.get()
            for i, t in enumerate(self.unique_times):
                if t.strftime("%Y-%m-%d %H:%M:%S") == time_str:
                    if i < self.time_index:
                        i = self.time_index
                        self.time_end_combo.current(i - self.time_index)
                    self.end_time = self.unique_times[i]
                    self.time_end_scale.set(i)
                    
                    if not self.show_all_combinations and self.selected_call_sym and self.selected_put_sym:
                        self.build_position()
                    break

    def on_option_selected(self, event=None):
        if self.updating_selection:
            return
            
        self.updating_selection = True
        
        try:
            if event and event.widget == self.call_list:
                c_sel = self.call_list.curselection()
                if c_sel:
                    if not self.call_options.empty and c_sel[0] < len(self.call_options):
                        self.selected_call_sym = self.call_options.iloc[c_sel[0]]['symbol']
                    else:
                        self.selected_call_sym = None
            
            elif event and event.widget == self.put_list:
                p_sel = self.put_list.curselection()
                if p_sel:
                    if not self.put_options.empty and p_sel[0] < len(self.put_options):
                        self.selected_put_sym = self.put_options.iloc[p_sel[0]]['symbol']
                    else:
                        self.selected_put_sym = None
            
            self.update_option_info(update_highlight=False)
            
            if not self.show_all_combinations and self.selected_call_sym and self.selected_put_sym:
                self.build_position()
            
        finally:
            self.updating_selection = False

    def update_option_info(self, update_highlight=True):
        if not self.entry_time:
            return

        if self.selected_call_sym:
            call_mask = self.call_options['symbol'] == self.selected_call_sym
            if call_mask.any() and update_highlight:
                self.updating_selection = True
                try:
                    self.call_list.selection_clear(0, tk.END)
                    listbox_index = self.call_options.index[call_mask][0]
                    self.call_list.selection_set(listbox_index)
                    self.call_list.see(listbox_index)
                finally:
                    self.updating_selection = False
            
            rec = self.df[(self.df['symbol'] == self.selected_call_sym) & 
                          (self.df['fetch_time_utc'] == self.entry_time)]
            if not rec.empty:
                r = rec.iloc[0]
                strike_val = r['strike']
                strike_str = self.format_strike(strike_val) if pd.notna(strike_val) else "N/A"
                txt = (f"CALL {strike_str}\n"
                       f"delta  {r.get('delta', np.nan):+.4f}   gamma {r.get('gamma', np.nan):.6f}\n"
                       f"vega   {r.get('vega', np.nan):.2f}    theta {r.get('theta', np.nan):.2f}\n"
                       f"bid    {r.get('bid1Price', np.nan):.2f}   ask  {r.get('ask1Price', np.nan):.2f}")
                self.call_info.config(text=txt, fg="#00c853")
            else:
                self.call_info.config(text="Данные недоступны", fg="#ff9800")
        else:
            self.call_info.config(text="Выберите CALL", fg="#80cbc4")

        if self.selected_put_sym:
            put_mask = self.put_options['symbol'] == self.selected_put_sym
            if put_mask.any() and update_highlight:
                self.updating_selection = True
                try:
                    self.put_list.selection_clear(0, tk.END)
                    listbox_index = self.put_options.index[put_mask][0]
                    self.put_list.selection_set(listbox_index)
                    self.put_list.see(listbox_index)
                finally:
                    self.updating_selection = False
            
            rec = self.df[(self.df['symbol'] == self.selected_put_sym) & 
                          (self.df['fetch_time_utc'] == self.entry_time)]
            if not rec.empty:
                r = rec.iloc[0]
                strike_val = r['strike']
                strike_str = self.format_strike(strike_val) if pd.notna(strike_val) else "N/A"
                txt = (f"PUT {strike_str}\n"
                       f"delta  {r.get('delta', np.nan):+.4f}   gamma {r.get('gamma', np.nan):.6f}\n"
                       f"vega   {r.get('vega', np.nan):.2f}    theta {r.get('theta', np.nan):.2f}\n"
                       f"bid    {r.get('bid1Price', np.nan):.2f}   ask  {r.get('ask1Price', np.nan):.2f}")
                self.put_info.config(text=txt, fg="#ff5252")
            else:
                self.put_info.config(text="Данные недоступны", fg="#ff9800")
        else:
            self.put_info.config(text="Выберите PUT", fg="#ef9a9a")

    def reset_all(self):
        self.df = None
        self.unique_times = None
        self.time_index = 0
        self.call_options = None
        self.put_options = None
        
        self.selected_call_sym = None
        self.selected_put_sym = None
        self.entry_time = None
        self.purchase_time = None
        self.end_time = None
        
        self.qty_call = 0.0
        self.qty_put = 0.0
        self.capital_dirty = 0.0
        
        self.call_filter_enabled = False
        self.put_filter_enabled = False
        
        self.show_all_combinations = False
        self.all_combinations_data = []
        
        self.delta_min_filter_frame.pack_forget()
        self.delta_max_filter_frame.pack_forget()
        self.delta_diff_filter_frame.pack_forget()
        self.filter_info_frame.pack_forget()
        
        self.delta_min_filter_entry.delete(0, tk.END)
        self.delta_min_filter_entry.insert(0, "0.05")
        self.delta_max_filter_entry.delete(0, tk.END)
        self.delta_max_filter_entry.insert(0, "0.6")
        self.delta_diff_filter_entry.delete(0, tk.END)
        self.delta_diff_filter_entry.insert(0, "0.05")

        self.ax.clear()
        self.canvas.draw()
        
        self.file_label.config(text="Файл не выбран", fg="#b0bec5")
        self.time_scale.config(from_=0, to=0)
        self.time_combo['values'] = []
        self.time_end_scale.config(from_=0, to=0)
        self.time_end_combo['values'] = []
        
        self.call_filter_btn.config(bg='#37474f', fg='#e1e1e1')
        self.call_reset_filter_btn.config(bg='#37474f', fg='#e1e1e1')
        self.call_min_delta_filter.delete(0, tk.END)
        self.call_min_delta_filter.insert(0, "0.05")
        self.call_max_delta_filter.delete(0, tk.END)
        self.call_max_delta_filter.insert(0, "0.6")
        self.put_filter_btn.config(bg='#37474f', fg='#e1e1e1')
        self.put_reset_filter_btn.config(bg='#37474f', fg='#e1e1e1')
        self.put_min_delta_filter.delete(0, tk.END)
        self.put_min_delta_filter.insert(0, "0.05")
        self.put_max_delta_filter.delete(0, tk.END)
        self.put_max_delta_filter.insert(0, "0.6")

        self.show_info_var.set(False)
        
        self.call_list.delete(0, tk.END)
        self.put_list.delete(0, tk.END)
        self.call_info.config(text="Выберите CALL", fg="#80cbc4")
        self.put_info.config(text="Выберите PUT", fg="#ef9a9a")
        
        self.delta_target_entry.delete(0, tk.END)
        self.delta_target_entry.insert(0, "0.00")
        self.capital_entry.delete(0, tk.END)
        self.capital_entry.insert(0, "100")
        
        self.mode_var.set(False)
        self.calc_all_btn.config(state=tk.DISABLED)
        self.call_list.config(state=tk.NORMAL)
        self.put_list.config(state=tk.NORMAL)
        self.filter_info_frame.pack_forget()
        self.filter_info_label.config(text="")
        
        self.purchase_label.config(text="Покупка: не выполнена", fg="#ff9800")
        self.result_label.config(text="Загрузите файл с данными", fg="#b0bec5")

    def on_graph_click(self, event):
        if not event.inaxes or not event.xdata:
            return

        if self.show_all_combinations:
            return

        if not self.ax.lines or len(self.ax.lines) == 0:
            return

        line = self.ax.lines[0]
        times = line.get_xdata()
        values = line.get_ydata()
        
        if len(times) == 0 or len(values) == 0:
            return

        idx = (np.abs(times - event.xdata)).argmin()
        close_t = times[idx]
        value_at_click = values[idx]

        if abs(self.qty_call) < 1e-6 or abs(self.qty_put) < 1e-6:
            return

        df_c = self.df[(self.df['symbol'] == self.selected_call_sym) & (self.df['fetch_time_utc'] == close_t)]
        df_p = self.df[(self.df['symbol'] == self.selected_put_sym) & (self.df['fetch_time_utc'] == close_t)]

        if df_c.empty or df_p.empty:
            return

        bid_c = df_c['bid1Price'].iloc[0]
        bid_p = df_p['bid1Price'].iloc[0]

        exit_value = self.qty_call * bid_c + self.qty_put * bid_p
        exit_after_comm = exit_value * (1 - COMMISSION)
        pnl = exit_after_comm - self.capital_dirty

        for artist in self.ax.lines:
            if artist.get_linestyle() == '--' and artist.get_color() == 'crimson':
                artist.remove()

        self.ax.axvline(close_t, color='crimson', ls='--', lw=1.4, alpha=0.8)
        
        legend_text = f"Закрытие {close_t.strftime('%d.%m %H:%M')}"
        if self.purchase_time:
            time_diff = close_t - self.purchase_time
            hours = time_diff.total_seconds() / 3600
            legend_text += f"\nДлительность: {hours:.1f} ч   PnL ${pnl:+,.0f}"
        
        self.ax.legend(loc='upper left', title=legend_text)
        self.canvas.draw()

        messagebox.showinfo("Закрытие",
                            f"Время: {close_t.strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"Стоимость на графике: ${value_at_click:,.2f}\n"
                            f"Получено после комиссии: ${exit_after_comm:,.2f}\n"
                            f"PnL: ${pnl:+,.0f}")


if __name__ == "__main__":
    root = tk.Tk()
    app = DeltaNeutralStrangleGUI(root)
    root.mainloop()