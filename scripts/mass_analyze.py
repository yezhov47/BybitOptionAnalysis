"""
Анализатор дельта-нейтральных стренглов для опционов на BTC
Версия: старый GUI + новое ядро с оптимизированным кэшем
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from datetime import datetime, timedelta, date
from datetime import time as datetime_time
import os
from typing import Tuple, List, Optional, Dict, Any, Set
import warnings
import win32clipboard
from PIL import Image
import io
import pickle
import shutil
import tempfile
import zipfile
import threading
import queue

warnings.filterwarnings('ignore')

# ============================================================================
# КОНФИГУРАЦИЯ И КОНСТАНТЫ
# ============================================================================

EXPIRY_TIME = "08:00"  # время экспирации (всегда 08:00 UTC)
MINUTE_SLOTS = 6  # 0,10,20,30,40,50
HOURS_IN_DAY = 24
TOTAL_SLOTS_PER_DAY = HOURS_IN_DAY * MINUTE_SLOTS

# Точность округления для разных типов данных
PRECISION_MAP = {
    'bid1Price': 2,
    'ask1Price': 2,
    'markPrice': 2,
    'delta': 8,
    'gamma': 10,
    'vega': 6,
    'theta': 6,
    'bid1Iv': 4,
    'ask1Iv': 4,
    'markIv': 4,
    'indexPrice': 2
}

# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С ВРЕМЕНЕМ
# ============================================================================

def datetime_to_slot(dt: datetime) -> Tuple[date, int, int]:
    """Преобразует datetime в кортеж (дата, час, номер 10-минутного слота)"""
    norm_dt = normalize_time_floor(dt)
    return (norm_dt.date(), norm_dt.hour, norm_dt.minute // 10)

def slot_to_datetime(slot_date: date, hour: int, minute_slot: int) -> datetime:
    """Преобразует (дата, час, слот) в datetime"""
    return datetime.combine(slot_date, datetime_time(hour, minute_slot * 10))

def add_minutes_to_slot(slot_date: date, hour: int, minute_slot: int, minutes: int) -> Tuple[date, int, int]:
    """Добавляет минуты к слоту с учётом смены даты, возвращает новый слот."""
    dt = slot_to_datetime(slot_date, hour, minute_slot) + timedelta(minutes=minutes)
    return datetime_to_slot(dt)

def subtract_minutes_from_slot(slot_date: date, hour: int, minute_slot: int, minutes: int) -> Tuple[date, int, int]:
    """Вычитает минуты из слота с учётом смены даты, возвращает новый слот."""
    dt = slot_to_datetime(slot_date, hour, minute_slot) - timedelta(minutes=minutes)
    return datetime_to_slot(dt)

def normalize_time_floor(ts: datetime) -> datetime:
    """Округляет время вниз до ближайших 10 минут"""
    minutes = (ts.minute // 10) * 10
    return ts.replace(minute=minutes, second=0, microsecond=0)

def slot_to_minutes(hour: int, minute_slot: int) -> int:
    """Преобразует (час, слот) в минуты от начала дня"""
    return hour * 60 + minute_slot * 10

def minutes_to_slot(total_minutes: int) -> Tuple[int, int]:
    """Преобразует минуты от начала дня в (час, слот)"""
    total_minutes = max(0, min(total_minutes, 24*60 - 10))
    hour = total_minutes // 60
    minute_slot = (total_minutes % 60) // 10
    return hour, minute_slot


def parse_symbol(symbol: str) -> Dict[str, Any]:
    """
    Разбирает символ опциона на составляющие
    Формат обычного опциона: BTC-26FEB26-71000-C-USDT
    Формат индексного опциона: BTC-26FEB26-INDEX-C-USDT (тоже 5 частей)
    """
    try:
        parts = symbol.split('-')
        
        if len(parts) < 5:
            return {
                'base_asset': parts[0] if len(parts) > 0 else 'UNKNOWN',
                'expiry_str': parts[1] if len(parts) > 1 else 'UNKNOWN',
                'strike': None,
                'option_type': 'unknown',
                'quote_asset': 'USDT',
                'is_index': False,
                'full_symbol': symbol
            }
        
        is_index = (parts[2] == 'INDEX')
        option_part = parts[3]
        option_type = 'call' if 'C' in option_part else 'put' if 'P' in option_part else 'unknown'
        
        return {
            'base_asset': parts[0],
            'expiry_str': parts[1],
            'strike': None if is_index else float(parts[2]),
            'option_type': option_type,
            'quote_asset': parts[4],
            'is_index': is_index,
            'full_symbol': symbol
        }
        
    except Exception as e:
        return {
            'base_asset': 'UNKNOWN',
            'expiry_str': 'UNKNOWN',
            'strike': None,
            'option_type': 'unknown',
            'quote_asset': 'USDT',
            'is_index': False,
            'full_symbol': symbol
        }

def round_down_to_5(x: float) -> float:
    """Округление вниз до ближайшего числа, кратного 5"""
    if pd.isna(x) or x <= 0:
        return 0.0
    return np.floor(x / 5) * 5

def round_up_to_5(x: float) -> float:
    """Округление вверх до ближайшего числа, кратного 5"""
    if pd.isna(x) or x <= 0:
        return 0.0
    return np.ceil(x / 5) * 5

# ============================================================================
# КЛАССЫ ДЛЯ РАБОТЫ С КЭШЕМ (НОВОЕ ЯДРО)
# ============================================================================

class OptionData:
    """Класс для хранения данных одного опциона"""
    
    def __init__(self, symbol: str, data_rows: pd.DataFrame):
        self.symbol = symbol
        parsed = parse_symbol(symbol)
        self.base_asset = parsed['base_asset']
        self.expiry_str = parsed['expiry_str']
        self.strike = parsed['strike']
        self.option_type = parsed['option_type']
        self.quote_asset = parsed['quote_asset']
        self.is_index = parsed['is_index']
        
        self.expiry_date = datetime.strptime(self.expiry_str, '%d%b%y').date()
        self.expiry_weekday = self.expiry_date.weekday()
        
        self.time_series = {}
        self.slot_index = set()
        
        self._build_time_series(data_rows)
    
    def _build_time_series(self, data_rows: pd.DataFrame):
        for _, row in data_rows.iterrows():
            ts = row['fetch_time_utc']
            norm_ts = normalize_time_floor(ts)
            key = (norm_ts.date(), norm_ts.hour, norm_ts.minute // 10)
            
            expiry_dt = datetime.combine(self.expiry_date, datetime_time(8, 0))
            time_to_expiry = expiry_dt - norm_ts
            days_to_expiry = time_to_expiry.days
            hours_to_expiry = time_to_expiry.seconds // 3600
            
            self.time_series[key] = {
                'timestamp': norm_ts,
                'original_timestamp': ts,
                'snapshot_date': norm_ts.date(),
                'snapshot_hour': norm_ts.hour,
                'snapshot_minute_slot': norm_ts.minute // 10,
                'snapshot_weekday': norm_ts.weekday(),
                'days_to_expiry': days_to_expiry,
                'hours_to_expiry': hours_to_expiry,
                'dte': max(0, days_to_expiry),
                'indexPrice': float(row['indexPrice']),
                'bid1Price': float(row['bid1Price']),
                'ask1Price': float(row['ask1Price']),
                'markPrice': float(row['markPrice']),
                'delta': float(row['delta']),
                'gamma': float(row.get('gamma', 0.0)),
                'vega': float(row.get('vega', 0.0)),
                'theta': float(row.get('theta', 0.0)),
                'bid1Iv': float(row.get('bid1Iv', 0.0)),
                'ask1Iv': float(row.get('ask1Iv', 0.0)),
                'markIv': float(row.get('markIv', 0.0)),
                'is_original': True
            }
            
            self.slot_index.add(key)
    
    def get_at_slot(self, snapshot_date: date, hour: int, minute_slot: int) -> Optional[Dict]:
        key = (snapshot_date, hour, minute_slot)
        if key not in self.time_series:
            return None
        
        data = self.time_series[key].copy()
        data.update({
            'symbol': self.symbol,
            'base_asset': self.base_asset,
            'quote_asset': self.quote_asset,
            'option_type': self.option_type,
            'strike': self.strike,
            'expiry_date': self.expiry_date,
            'expiry_weekday': self.expiry_weekday
        })
        return data
    
    def has_slot(self, snapshot_date: date, hour: int, minute_slot: int) -> bool:
        return (snapshot_date, hour, minute_slot) in self.slot_index
    
    def get_all_slots(self) -> List[Tuple[date, int, int]]:
        return list(self.slot_index)


class IndexOption:
    """Синтетический индексный опцион (страйк = индексная цена)"""
    
    def __init__(self, base_asset: str, expiry_date, quote_asset: str, option_type: str):
        self.base_asset = base_asset
        
        if isinstance(expiry_date, str):
            self.expiry_date = pd.to_datetime(expiry_date).date()
        else:
            self.expiry_date = expiry_date
            
        self.expiry_weekday = self.expiry_date.weekday()
        self.quote_asset = quote_asset
        self.option_type = option_type.lower()
        self.expiry_str = self.expiry_date.strftime('%d%b%y').upper()
        
        option_code = 'C' if self.option_type == 'call' else 'P'
        self.symbol = f"{base_asset}-{self.expiry_str}-INDEX-{option_code}-{quote_asset}"
        
        self.time_series = {}
        self.slot_index = set()
    
    def add_snapshot(self, key: Tuple[date, int, int], data: Dict):
        self.time_series[key] = data
        self.slot_index.add(key)
    
    def get_at_slot(self, snapshot_date: date, hour: int, minute_slot: int) -> Optional[Dict]:
        return self.time_series.get((snapshot_date, hour, minute_slot))
    
    def has_slot(self, snapshot_date: date, hour: int, minute_slot: int) -> bool:
        return (snapshot_date, hour, minute_slot) in self.slot_index


class OptimizedCache:
    """Оптимизированный кэш с предрасчитанными данными"""
    
    def __init__(self):
        self.options: Dict[str, OptionData] = {}
        self.index_options = {}
        self.expiry_map: Dict[date, List[str]] = {}
        self.base_asset_map: Dict[str, List[str]] = {}
        self.slot_index: Set[Tuple[date, int, int]] = set()
        self.slot_to_options: Dict[Tuple[date, int, int], List[str]] = {}  # <-- явное указание типа
        self.metadata = {}
    
    def add_option(self, option: OptionData):
        """Добавляет опцион в кэш"""
        self.options[option.symbol] = option
        
        # Обновляем expiry_map
        if option.expiry_date not in self.expiry_map:
            self.expiry_map[option.expiry_date] = []
        self.expiry_map[option.expiry_date].append(option.symbol)
        
        # Обновляем base_asset_map
        if option.base_asset not in self.base_asset_map:
            self.base_asset_map[option.base_asset] = []
        self.base_asset_map[option.base_asset].append(option.symbol)
        
        # Обновляем слоты
        for slot in option.get_all_slots():
            self.slot_index.add(slot)
            
            # Добавляем в slot_to_options
            if slot not in self.slot_to_options:
                self.slot_to_options[slot] = []
            self.slot_to_options[slot].append(option.symbol)
            
            # ДИАГНОСТИКА: покажем первые несколько слотов
            if len(self.slot_to_options) <= 10:
                print(f"    Добавлен слот {slot} для {option.symbol}")
    
    def add_index_option(self, index_option: IndexOption):
        key = (index_option.base_asset, index_option.expiry_date, index_option.option_type)
        self.index_options[key] = index_option
    
    def get_index_option(self, base_asset: str, expiry_date: date, option_type: str) -> Optional[IndexOption]:
        key = (base_asset, expiry_date, option_type)
        return self.index_options.get(key)
    
    def has_slot(self, snapshot_date: date, hour: int, minute_slot: int) -> bool:
        return (snapshot_date, hour, minute_slot) in self.slot_index
    
    def get_options_at_slot(self, snapshot_date: date, hour: int, minute_slot: int, 
                        dte: Optional[int] = None) -> List[Dict]:
        """
        Получает все опционы для конкретного слота
        Если указан dte, фильтрует по дням до экспирации
        """
        key = (snapshot_date, hour, minute_slot)
        
        # Проверяем наличие в slot_to_options
        if key not in self.slot_to_options:
            print(f"        get_options_at_slot: ключ {key} не найден в slot_to_options")
            print(f"        Всего ключей в slot_to_options: {len(self.slot_to_options)}")
            # Покажем первые 5 ключей для отладки
            sample_keys = list(self.slot_to_options.keys())[:5]
            print(f"        Примеры ключей: {sample_keys}")
            return []
        
        result = []
        for symbol in self.slot_to_options[key]:
            option = self.options[symbol]
            data = option.get_at_slot(snapshot_date, hour, minute_slot)
            if data:
                if dte is None or data['dte'] == dte:
                    result.append(data)
        
        return result
    
    def get_all_snapshot_dates(self) -> List[date]:
        return sorted(set(s[0] for s in self.slot_index))
    
    def save(self, filepath: str):
        """Сохраняет кэш в файл"""
        # Преобразуем ключи в строки для сериализации
        slot_to_options_str = {}
        for key, value in self.slot_to_options.items():
            # Преобразуем tuple в строку
            str_key = f"{key[0].isoformat()},{key[1]},{key[2]}"
            slot_to_options_str[str_key] = value
        
        cache_data = {
            'metadata': self.metadata,
            'options': self.options,
            'index_options': self.index_options,
            'expiry_map': self.expiry_map,
            'base_asset_map': self.base_asset_map,
            'slot_index': [(d.isoformat(), h, m) for (d, h, m) in self.slot_index],
            'slot_to_options': slot_to_options_str
        }
        
        with open(filepath, 'wb') as f:
            pickle.dump(cache_data, f)
    
    @classmethod
    def load(cls, filepath: str) -> 'OptimizedCache':
        with open(filepath, 'rb') as f:
            cache_data = pickle.load(f)
        
        cache = cls()
        cache.options = cache_data['options']
        cache.index_options = cache_data['index_options']
        cache.expiry_map = cache_data['expiry_map']
        cache.base_asset_map = cache_data['base_asset_map']
        
        # Восстанавливаем slot_index
        cache.slot_index = set()
        for item in cache_data.get('slot_index', []):
            if len(item) == 3:
                if isinstance(item[0], str):
                    d = date.fromisoformat(item[0])
                    cache.slot_index.add((d, item[1], item[2]))
                else:
                    cache.slot_index.add((item[0], item[1], item[2]))
        
        # Восстанавливаем slot_to_options - ЭТО КЛЮЧЕВОЕ МЕСТО!
        cache.slot_to_options = {}
        for str_key, symbols in cache_data.get('slot_to_options', {}).items():
            # Парсим строку обратно в tuple
            parts = str_key.split(',')
            if len(parts) == 3:
                d = date.fromisoformat(parts[0])
                key = (d, int(parts[1]), int(parts[2]))
                cache.slot_to_options[key] = symbols
        
        cache.metadata = cache_data.get('metadata', {})
        
        print(f"Загружен кэш: {len(cache.options)} опционов, {len(cache.slot_index)} слотов, {len(cache.slot_to_options)} записей в slot_to_options")
        return cache


class CacheBuilder:
    """Строит оптимизированный кэш из CSV файлов"""
    
    def __init__(self):
        self.cache = OptimizedCache()
        self.raw_data = None
        self.progress_callback = None
        self.update_queue = None
        self.interpolation_stats = {
            'total_options': 0,
            'total_slots': 0,
            'original_slots': 0,
            'interpolated_slots': 0
        }
        self.detected_assets = set()  # Добавляем множество для обнаруженных активов
    
    def _detect_assets(self):
        """Определяет уникальные базовые активы из данных"""
        for symbol in self.raw_data['symbol'].unique():
            if '-INDEX-' not in symbol:
                parsed = parse_symbol(symbol)
                if parsed['base_asset'] != 'UNKNOWN':
                    self.detected_assets.add(parsed['base_asset'])
        
        self._send_progress('assets_detected', assets=list(self.detected_assets))
    
    def build_from_files(self, data_files: List[str], expiry_time: str = "08:00") -> List[OptimizedCache]:
        """
        Строит кэши из файлов - отдельный кэш для каждого токена
        Возвращает список кэшей
        """
        # 1. Загружаем все файлы
        self.raw_data = self._load_files(data_files)
        self._send_progress('stage1_complete')
        
        # 2. Определяем все активы
        self._detect_assets()
        
        # 3. Нормализуем время
        self._normalize_time()
        
        # 4. Для каждого актива создаем отдельный кэш
        caches = []
        total_assets = len(self.detected_assets)
        
        for idx, asset in enumerate(self.detected_assets):
            self._send_progress('asset_start', current=idx+1, total=total_assets, asset=asset)
            
            # Создаем новый кэш для этого актива
            asset_cache = OptimizedCache()
            
            # Фильтруем данные только для этого актива
            asset_data = self.raw_data[self.raw_data['symbol'].str.contains(f'^{asset}-', na=False)]
            
            if asset_data.empty:
                self._send_progress('debug', msg=f"Нет данных для актива {asset}")
                continue
            
            # Создаем временный builder для этого актива
            temp_builder = CacheBuilder()
            temp_builder.raw_data = asset_data
            temp_builder.update_queue = self.update_queue
            temp_builder.detected_assets = {asset}
            
            # Нормализуем время для этого актива
            temp_builder._normalize_time()
            
            # Создаем объекты опционов
            temp_builder._build_options()
            
            # Интерполяция
            temp_builder._interpolate_regular_options()
            
            # Строим индексные опционы
            temp_builder._build_index_options()
            
            # Собираем метаданные с именем актива
            temp_builder._build_metadata(data_files, expiry_time, asset)
            
            caches.append(temp_builder.cache)
            
            self._send_progress('asset_complete', asset=asset)
        
        return caches

    def _build_metadata(self, data_files: List[str], expiry_time: str, asset_name: str = None):
        """Собирает метаданные кэша"""
        snapshot_dates = self.cache.get_all_snapshot_dates()
        expiry_dates = list(self.cache.expiry_map.keys())
        total_slots = len(self.cache.slot_index)
        
        # Определяем основной актив
        if asset_name:
            main_asset = asset_name
        elif self.detected_assets:
            main_asset = list(self.detected_assets)[0]
        else:
            main_asset = 'UNKNOWN'
        
        self.cache.metadata = {
            'created': datetime.now().isoformat(),
            'data_files': data_files,
            'file_count': len(data_files),
            'expiry_time': expiry_time,
            'num_options': len(self.cache.options),
            'num_index_options': len(self.cache.index_options),
            'num_snapshots': total_slots,
            'num_dates': len(snapshot_dates),
            'num_expiries': len(expiry_dates),
            'snapshot_dates': [d.isoformat() for d in snapshot_dates],
            'expiry_dates': [d.isoformat() for d in expiry_dates],
            'base_assets': list(self.detected_assets) if self.detected_assets else [main_asset],
            'main_asset': main_asset,
            'interpolation_stats': self.interpolation_stats
        }

    def set_queue(self, queue):
        """Устанавливает очередь для отправки прогресса в GUI"""
        self.update_queue = queue
    
    def _send_progress(self, stage, **kwargs):
        """Отправляет сообщение о прогрессе в GUI"""
        if self.update_queue:
            data = {'stage': stage}
            data.update(kwargs)
            self.update_queue.put(data)
    
    def _load_files(self, data_files: List[str]) -> pd.DataFrame:
        """Загружает и объединяет CSV файлы"""
        data_frames = []
        
        total_files = len(data_files)
        for i, file_path in enumerate(data_files):
            try:
                df = pd.read_csv(file_path)
                df['source_file'] = os.path.basename(file_path)
                data_frames.append(df)
                # Отправляем прогресс загрузки каждого файла
                self._send_progress('file_loaded', current=i+1, total=total_files, filename=os.path.basename(file_path))
            except Exception as e:
                self._send_progress('file_error', filename=os.path.basename(file_path), error=str(e))
                print(f"  ! Ошибка загрузки {os.path.basename(file_path)}: {e}")
        
        if not data_frames:
            raise ValueError("Не удалось загрузить ни одного файла")
        
        combined = pd.concat(data_frames, ignore_index=True)
        combined['fetch_time_utc'] = pd.to_datetime(combined['fetch_time_utc'])
        if combined['fetch_time_utc'].dt.tz is not None:
            combined['fetch_time_utc'] = combined['fetch_time_utc'].dt.tz_localize(None)
        
        return combined
    
    def _normalize_time(self):
        """Нормализует время к 10-минутным интервалам"""
        self.raw_data['time_norm'] = self.raw_data['fetch_time_utc'].apply(normalize_time_floor)
        self.raw_data['snapshot_date'] = self.raw_data['time_norm'].dt.date
        self.raw_data['snapshot_hour'] = self.raw_data['time_norm'].dt.hour
        self.raw_data['snapshot_minute_slot'] = self.raw_data['time_norm'].dt.minute // 10
        self.raw_data['snapshot_weekday'] = self.raw_data['time_norm'].dt.dayofweek
        
        # Удаляем дубликаты в одном слоте (оставляем последний)
        before = len(self.raw_data)
        self.raw_data = self.raw_data.sort_values('fetch_time_utc').drop_duplicates(
            subset=['symbol', 'snapshot_date', 'snapshot_hour', 'snapshot_minute_slot'],
            keep='last'
        )
        after = len(self.raw_data)
        
        self._send_progress('normalize_complete', before=before, after=after, duplicates=before-after)
    
    def _build_options(self):
        """Создает объекты OptionData для каждого символа"""
        symbols = self.raw_data['symbol'].unique()
        total_symbols = len([s for s in symbols if '-INDEX-' not in s])
        
        self._send_progress('stage2_start', total=total_symbols)
        
        processed = 0
        for i, symbol in enumerate(symbols):
            if '-INDEX-' in symbol:
                continue
            
            group = self.raw_data[self.raw_data['symbol'] == symbol]
            
            try:
                option = OptionData(symbol, group)
                self.cache.add_option(option)
                self.interpolation_stats['total_options'] += 1
                processed += 1
                
                # Отправляем прогресс каждые 10 опционов или при завершении
                if processed % 10 == 0 or processed == total_symbols:
                    self._send_progress('stage2_progress', current=processed, total=total_symbols)
                    
            except Exception as e:
                self._send_progress('option_error', symbol=symbol, error=str(e))
                import traceback
                traceback.print_exc()
        
        self._send_progress('stage2_complete', total=processed)
    
    def _interpolate_regular_options(self):
        """Интерполирует только обычные опционы"""
        total_options = len(self.cache.options)
        self._send_progress('stage4_start', total=total_options)
        
        total_interpolated_slots = 0
        total_original_slots = 0
        
        for idx, (symbol, option) in enumerate(self.cache.options.items(), 1):
            original_slots = len(option.time_series)
            total_original_slots += original_slots
            
            self._interpolate_option(option)
            
            interpolated = len(option.time_series) - original_slots
            total_interpolated_slots += interpolated
            
            # Отправляем прогресс каждые 10 опционов
            if idx % 10 == 0 or idx == total_options:
                self._send_progress('stage4_progress', 
                                   current=idx, 
                                   total=total_options,
                                   interpolated=total_interpolated_slots)
        
        self.interpolation_stats['original_slots'] = total_original_slots
        self.interpolation_stats['interpolated_slots'] = total_interpolated_slots
        self.interpolation_stats['total_slots'] = total_original_slots + total_interpolated_slots
        
        self._send_progress('stage4_complete', 
                           total=total_options,
                           interpolated=total_interpolated_slots)
    
    def _build_index_options(self):
        """Строит индексные опционы для каждой экспирации"""
        self._send_progress('stage3_start')
        
        quote_asset = 'USDT'
        if self.cache.options:
            first_option = next(iter(self.cache.options.values()))
            quote_asset = first_option.quote_asset
        
        all_snapshot_dates = self.cache.get_all_snapshot_dates()
        total_index_options = 0
        
        for (base_asset, expiry_date), options in self._group_by_expiry():
            valid_dates = [d for d in all_snapshot_dates if d <= expiry_date]
            
            index_call = IndexOption(base_asset, expiry_date, quote_asset, 'call')
            index_put = IndexOption(base_asset, expiry_date, quote_asset, 'put')
            
            slots_call = 0
            slots_put = 0
            
            for snapshot_date in valid_dates:
                for hour in range(24):
                    for minute_slot in range(6):
                        key = (snapshot_date, hour, minute_slot)
                        
                        slot_data = []
                        for opt in options:
                            data = opt.get_at_slot(snapshot_date, hour, minute_slot)
                            if data:
                                slot_data.append(data)
                        
                        if len(slot_data) < 2:
                            continue
                        
                        calls = [d for d in slot_data if d['option_type'] == 'call']
                        puts = [d for d in slot_data if d['option_type'] == 'put']
                        
                        if not calls or not puts:
                            continue
                        
                        index_price = slot_data[0]['indexPrice']
                        if index_price == 0:
                            continue
                        
                        # Находим ATM Call (ближайший OTM call)
                        otm_calls = [c for c in calls if c['strike'] >= index_price]
                        if otm_calls:
                            atm_call = min(otm_calls, key=lambda x: x['strike'] - index_price)
                        else:
                            atm_call = min(calls, key=lambda x: abs(x['strike'] - index_price))
                        
                        # Находим ATM Put (ближайший OTM put)
                        otm_puts = [p for p in puts if p['strike'] <= index_price]
                        if otm_puts:
                            atm_put = max(otm_puts, key=lambda x: x['strike'])
                        else:
                            atm_put = min(puts, key=lambda x: abs(x['strike'] - index_price))
                        
                        # Вычисляем ATM IV
                        atm_call_iv = float(atm_call.get('markIv', 0))
                        atm_put_iv = float(atm_put.get('markIv', 0))
                        
                        if atm_call_iv > 0 and atm_put_iv > 0:
                            atm_iv = (atm_call_iv + atm_put_iv) / 2
                        elif atm_call_iv > 0:
                            atm_iv = atm_call_iv
                        elif atm_put_iv > 0:
                            atm_iv = atm_put_iv
                        else:
                            atm_iv = 0.0
                        
                        # Данные для CALL
                        call_data = {
                            'timestamp': datetime.combine(snapshot_date, 
                                                        datetime_time(hour, minute_slot * 10)),
                            'snapshot_date': snapshot_date,
                            'snapshot_hour': hour,
                            'snapshot_minute_slot': minute_slot,
                            'snapshot_weekday': snapshot_date.weekday(),
                            'index_price': float(index_price),
                            'strike': atm_call['strike'],
                            'price': float(atm_call.get('markPrice', 0)),
                            'bid': float(atm_call.get('bid1Price', 0)),
                            'ask': float(atm_call.get('ask1Price', 0)),
                            'delta': float(atm_call.get('delta', 0.5)),
                            'gamma': float(atm_call.get('gamma', 0)),
                            'vega': float(atm_call.get('vega', 0)),
                            'theta': float(atm_call.get('theta', 0)),
                            'iv': atm_call_iv,
                            'atm_iv': atm_iv,
                            'days_to_expiry': (expiry_date - snapshot_date).days,
                            'dte': (expiry_date - snapshot_date).days,
                            'is_original': True
                        }
                        index_call.add_snapshot(key, call_data)
                        slots_call += 1
                        
                        # Данные для PUT
                        put_data = {
                            'timestamp': datetime.combine(snapshot_date, 
                                                        datetime_time(hour, minute_slot * 10)),
                            'snapshot_date': snapshot_date,
                            'snapshot_hour': hour,
                            'snapshot_minute_slot': minute_slot,
                            'snapshot_weekday': snapshot_date.weekday(),
                            'index_price': float(index_price),
                            'strike': atm_put['strike'],
                            'price': float(atm_put.get('markPrice', 0)),
                            'bid': float(atm_put.get('bid1Price', 0)),
                            'ask': float(atm_put.get('ask1Price', 0)),
                            'delta': float(atm_put.get('delta', -0.5)),
                            'gamma': float(atm_put.get('gamma', 0)),
                            'vega': float(atm_put.get('vega', 0)),
                            'theta': float(atm_put.get('theta', 0)),
                            'iv': atm_put_iv,
                            'atm_iv': atm_iv,
                            'days_to_expiry': (expiry_date - snapshot_date).days,
                            'dte': (expiry_date - snapshot_date).days,
                            'is_original': True
                        }
                        index_put.add_snapshot(key, put_data)
                        slots_put += 1
            
            if slots_call > 0:
                self.cache.add_index_option(index_call)
                total_index_options += 1
            if slots_put > 0:
                self.cache.add_index_option(index_put)
                total_index_options += 1
        
        self._send_progress('stage3_complete', total=total_index_options, total_options=len(self.cache.options))
    
    def _interpolate_option(self, option: OptionData):
        """Интерполирует пропуски для одного обычного опциона от первого до последнего появления"""
        
        # Получаем все слоты, где есть данные, и сортируем по времени
        all_slots = sorted(option.get_all_slots(), key=lambda x: (x[0], x[1], x[2]))
        
        if len(all_slots) < 2:
            return
        
        # Создаем словарь существующих слотов с реальными данными
        real_slots = {slot: option.time_series[slot] for slot in all_slots}
        
        # ВАЛИДАЦИЯ: проверяем оригинальные Bid/Ask на адекватность
        validated_count = 0
        for slot, data in real_slots.items():
            if data.get('is_original', False):  # Только оригинальные данные
                mark = data.get('markPrice', 0)
                bid = data.get('bid1Price', 0)
                ask = data.get('ask1Price', 0)
                
                # Пропускаем, если markPrice = 0
                if mark == 0:
                    continue
                
                # Проверяем Bid
                if bid == 0:
                    # Bid = 0 - некорректно при mark > 0
                    # Рассчитываем Bid как mark - 10%
                    bid_calc = mark * 0.9  # -10%
                    # Округляем вниз до кратного 5
                    bid_calc = np.floor(bid_calc / 5) * 5
                    bid_calc = max(0, bid_calc)
                    
                    if abs(bid_calc - mark) / mark <= 0.1:  # Проверяем, что в пределах 10%
                        data['bid1Price'] = bid_calc
                        validated_count += 1
                        # Убираем print - используем очередь
                        self._send_progress('debug', msg=f"Bid скорректирован: {slot} {bid:.2f} -> {bid_calc:.2f}")
                
                # Проверяем Ask
                if ask == 0:
                    # Ask = 0 - некорректно при mark > 0
                    # Рассчитываем Ask как mark + 10%
                    ask_calc = mark * 1.1  # +10%
                    # Округляем вверх до кратного 5
                    ask_calc = np.ceil(ask_calc / 5) * 5
                    ask_calc = max(0, ask_calc)
                    
                    if abs(ask_calc - mark) / mark <= 0.1:  # Проверяем, что в пределах 10%
                        data['ask1Price'] = ask_calc
                        validated_count += 1
                        self._send_progress('debug', msg=f"Ask скорректирован: {slot} {ask:.2f} -> {ask_calc:.2f}")
                
                # Проверяем соотношение Ask >= Bid после корректировки
                if data.get('ask1Price', 0) < data.get('bid1Price', 0):
                    self._send_progress('debug', msg=f"Ask < Bid после корректировки: {slot}")
                    # Устанавливаем Ask = Bid (с округлением вверх)
                    data['ask1Price'] = np.ceil(data['bid1Price'] / 5) * 5
                    validated_count += 1
        
        if validated_count > 0:
            self._send_progress('debug', msg=f"Опцион {option.symbol}: скорректировано {validated_count} некорректных значений")
        
        # Определяем первый и последний момент времени
        first_dt = slot_to_datetime(*all_slots[0])
        last_dt = slot_to_datetime(*all_slots[-1])
        
        # Генерируем ВСЕ возможные слоты от первого до последнего
        current_dt = first_dt
        all_possible_slots = []
        slot_to_dt = {}
        while current_dt <= last_dt:
            slot = datetime_to_slot(current_dt)
            all_possible_slots.append(slot)
            slot_to_dt[slot] = current_dt
            current_dt += timedelta(minutes=10)
        
        # Проходим по всем реальным слотам и интерполируем пропуски между ними
        interpolated_count = 0
        
        for i in range(len(all_slots) - 1):
            left_slot = all_slots[i]
            right_slot = all_slots[i + 1]
            
            left_data = real_slots[left_slot]
            right_data = real_slots[right_slot]
            
            left_dt = slot_to_datetime(*left_slot)
            right_dt = slot_to_datetime(*right_slot)
            
            # Находим все слоты между left и right
            current_dt = left_dt + timedelta(minutes=10)
            while current_dt < right_dt:
                current_slot = datetime_to_slot(current_dt)
                
                # Пропускаем, если слот уже есть
                if current_slot in real_slots:
                    current_dt += timedelta(minutes=10)
                    continue
                
                # Вычисляем ratio для этого слота
                total_seconds = (right_dt - left_dt).total_seconds()
                current_seconds = (current_dt - left_dt).total_seconds()
                ratio = current_seconds / total_seconds if total_seconds > 0 else 0
                
                # Интерполируем данные
                new_data = self._interpolate_data(left_data, right_data, ratio)
                
                # Добавляем метаданные
                new_data['timestamp'] = current_dt
                new_data['snapshot_date'] = current_slot[0]
                new_data['snapshot_hour'] = current_slot[1]
                new_data['snapshot_minute_slot'] = current_slot[2]
                new_data['snapshot_weekday'] = current_slot[0].weekday()
                new_data['is_original'] = False
                
                # Пересчитываем временные поля до экспирации
                expiry_dt = datetime.combine(option.expiry_date, datetime_time(8, 0))
                time_to_expiry = expiry_dt - current_dt
                new_data['days_to_expiry'] = time_to_expiry.days
                new_data['hours_to_expiry'] = time_to_expiry.seconds // 3600
                new_data['dte'] = max(0, time_to_expiry.days)
                
                # Сохраняем
                option.time_series[current_slot] = new_data
                option.slot_index.add(current_slot)
                real_slots[current_slot] = new_data
                interpolated_count += 1
                
                # Отправляем прогресс с диагностикой (убираем print)
                if interpolated_count % 10 == 0 and interpolated_count > 0:
                    self._send_progress('interpolating', count=interpolated_count)
                
                current_dt += timedelta(minutes=10)
        
        if interpolated_count > 0:
            self._send_progress('option_interpolated', 
                            symbol=option.symbol, 
                            count=interpolated_count)

    def _interpolate_data(self, left: Dict, right: Dict, ratio: float) -> Dict:
        """
        Интерполирует данные между двумя слотами с сохранением точности
        Использует одни и те же опорные точки для всех полей
        """
        
        # Поля, которые нужно интерполировать линейно
        interpolate_fields = [
            'indexPrice', 'markPrice', 'delta', 'gamma', 'vega', 'theta',
            'markIv', 'bid1Iv', 'ask1Iv'
        ]
        
        # Поля, которые нужно копировать (не интерполировать)
        copy_fields = [
            'symbol', 'base_asset', 'quote_asset', 'option_type', 'strike',
            'expiry_date', 'expiry_weekday'
        ]
        
        new_data = {}
        
        # Защита от None для всех полей
        for field in interpolate_fields:
            if field in left and left[field] is None:
                left[field] = 0.0
            if field in right and right[field] is None:
                right[field] = 0.0
        
        # 1. Линейная интерполяция всех обычных полей
        for field in interpolate_fields:
            if field in left and field in right:
                left_val = left[field]
                right_val = right[field]
                
                # Линейная интерполяция
                val = left_val + (right_val - left_val) * ratio
                
                if field in PRECISION_MAP:
                    val = round(val, PRECISION_MAP[field])
                
                new_data[field] = val
        
        # 2. Интерполяция Bid цены
        if 'bid1Price' in left and 'bid1Price' in right:
            left_bid = left['bid1Price']
            right_bid = right['bid1Price']
            
            # Проверяем, есть ли у нас markPrice для этого слота
            mark_price = new_data.get('markPrice', 0)
            
            # Определяем Bid цену
            if left_bid > 0 and right_bid > 0:
                # Оба значения валидны - линейная интерполяция
                bid_val = left_bid + (right_bid - left_bid) * ratio
                # Округляем вниз до 5
                bid_val = round_down_to_5(bid_val)
            else:
                # Хотя бы одно значение невалидно - используем markPrice
                # Округляем markPrice вниз до ближайшего кратного 5
                bid_val = np.floor(mark_price / 5) * 5
                # Не может быть отрицательным
                bid_val = max(0, bid_val)
            
            new_data['bid1Price'] = bid_val
        
        # 3. Интерполяция Ask цены
        if 'ask1Price' in left and 'ask1Price' in right:
            left_ask = left['ask1Price']
            right_ask = right['ask1Price']
            
            # Проверяем, есть ли у нас markPrice для этого слота
            mark_price = new_data.get('markPrice', 0)
            
            # Определяем Ask цену
            if left_ask > 0 and right_ask > 0:
                # Оба значения валидны - линейная интерполяция
                ask_val = left_ask + (right_ask - left_ask) * ratio
                # Округляем вверх до 5
                ask_val = round_up_to_5(ask_val)
            else:
                # Хотя бы одно значение невалидно - используем markPrice
                # Округляем markPrice вверх до ближайшего кратного 5
                ask_val = np.ceil(mark_price / 5) * 5
            
            ask_val = max(0, ask_val)
            new_data['ask1Price'] = ask_val
        
        # 4. Гарантируем, что Ask >= Bid
        if 'bid1Price' in new_data and 'ask1Price' in new_data:
            if new_data['ask1Price'] < new_data['bid1Price']:
                new_data['ask1Price'] = round_up_to_5(new_data['bid1Price'])
        
        # 5. Копирование остальных полей
        for field in copy_fields:
            if field in left:
                new_data[field] = left[field]
            elif field in right:
                new_data[field] = right[field]
        
        return new_data

    def _group_by_expiry(self):
        """Группирует опционы по базовому активу и дате экспирации"""
        groups = {}
        for symbol, option in self.cache.options.items():
            key = (option.base_asset, option.expiry_date)
            if key not in groups:
                groups[key] = []
            groups[key].append(option)
        return groups.items()

class CacheManager:
    """Менеджер для работы с кэшированными данными (адаптирован для нового ядра)"""
    
    def __init__(self, cache_dir=None):
        if cache_dir is None:
            appname = "StrangleCharts"
            if os.name == 'nt':
                self.cache_dir = os.path.join(
                    os.environ.get('LOCALAPPDATA', os.path.expanduser("~")), 
                    appname, "cache"
                )
            else:
                self.cache_dir = os.path.join(os.path.expanduser("~"), ".cache", appname)
        else:
            self.cache_dir = os.path.expanduser(cache_dir)
        
        os.makedirs(self.cache_dir, exist_ok=True)
        print(f"Кэш-директория: {self.cache_dir}")
    
    def save_cache(self, cache: OptimizedCache) -> str:
        """Сохраняет кэш с автоматическим именем на основе токена"""
        # Определяем базовый актив
        base_assets = cache.metadata.get('base_assets', [])
        if base_assets:
            base_asset = base_assets[0]
        else:
            base_asset = 'UNKNOWN'
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        # Просто: токен_дата_время
        cache_filename = f"{base_asset}_{timestamp}"
        
        cache_file = os.path.join(self.cache_dir, f"{cache_filename}.pkl")
        cache.save(cache_file)
        
        # Сохраняем метаданные
        meta_file = os.path.join(self.cache_dir, f"{cache_filename}_meta.pkl")
        with open(meta_file, 'wb') as f:
            pickle.dump(cache.metadata, f)
        
        print(f"✅ Кэш сохранён: {cache_file}")
        return cache_filename

    def load_cache(self, cache_id: str) -> OptimizedCache:
        """Загружает кэш по ID"""
        # Проверяем, есть ли файл с таким именем
        cache_file = os.path.join(self.cache_dir, f"{cache_id}.pkl")
        
        if not os.path.exists(cache_file):
            # Пробуем найти файл, начинающийся с cache_id
            for file in os.listdir(self.cache_dir):
                if file.endswith('.pkl') and not file.endswith('_meta.pkl'):
                    if file.startswith(cache_id) or cache_id in file:
                        cache_file = os.path.join(self.cache_dir, file)
                        break
            else:
                raise FileNotFoundError(f"Кэш {cache_id} не найден")
        
        return OptimizedCache.load(cache_file)
    
    def delete_cache(self, cache_id: str):
        """Удаляет кэш по ID"""
        # Удаляем основной файл
        cache_file = os.path.join(self.cache_dir, f"{cache_id}.pkl")
        if os.path.exists(cache_file):
            os.remove(cache_file)
        
        # Удаляем файл метаданных
        meta_file = os.path.join(self.cache_dir, f"{cache_id}_meta.pkl")
        if os.path.exists(meta_file):
            os.remove(meta_file)
        
        # Также пробуем удалить файлы, которые начинаются с этого ID
        for file in os.listdir(self.cache_dir):
            if file.startswith(cache_id) and (file.endswith('.pkl') or file.endswith('_meta.pkl')):
                file_path = os.path.join(self.cache_dir, file)
                if os.path.exists(file_path):
                    os.remove(file_path)

    def get_available_caches(self) -> List[Dict]:
        """Получает список доступных кэшей"""
        caches = []
        
        if not os.path.exists(self.cache_dir):
            return caches
        
        # Ищем все .pkl файлы (кроме meta файлов)
        for file in os.listdir(self.cache_dir):
            if file.endswith('.pkl') and not file.endswith('_meta.pkl'):
                file_path = os.path.join(self.cache_dir, file)
                meta_path = file_path.replace('.pkl', '_meta.pkl')
                
                try:
                    # Пытаемся загрузить метаданные
                    if os.path.exists(meta_path):
                        with open(meta_path, 'rb') as f:
                            meta = pickle.load(f)
                    else:
                        meta = {}
                    
                    # Извлекаем информацию из имени файла
                    # Формат: TOKEN_YYYYMMDD_HHMMSS.pkl
                    filename = file.replace('.pkl', '')
                    parts = filename.split('_')
                    
                    # Определяем базовый актив (первая часть)
                    base_asset = parts[0] if parts else 'UNKNOWN'
                    
                    # Извлекаем дату и время из имени
                    date_str = ""
                    time_str = ""
                    if len(parts) >= 3:
                        date_str = parts[1] if len(parts) > 1 else ""
                        time_str = parts[2] if len(parts) > 2 else ""
                    
                    created = None
                    if 'created' in meta:
                        created_value = meta['created']
                        if isinstance(created_value, str):
                            try:
                                created = datetime.fromisoformat(created_value)
                            except:
                                created = datetime.fromtimestamp(os.path.getmtime(file_path))
                        elif isinstance(created_value, datetime):
                            created = created_value
                        else:
                            created = datetime.fromtimestamp(os.path.getmtime(file_path))
                    else:
                        created = datetime.fromtimestamp(os.path.getmtime(file_path))
                    
                    size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    
                    # Формируем отображаемый ID
                    display_id = f"{base_asset}"
                    if date_str:
                        display_id += f"_{date_str}"
                    if time_str:
                        display_id += f"_{time_str}"
                    
                    caches.append({
                        'id': filename,
                        'display_id': display_id,
                        'base_asset': base_asset,
                        'date_str': date_str,
                        'time_str': time_str,
                        'file_path': file_path,
                        'meta_path': meta_path,
                        'meta': meta,
                        'created': created,
                        'num_options': meta.get('num_options', 0),
                        'num_index_options': meta.get('num_index_options', 0),
                        'num_snapshots': meta.get('num_snapshots', 0),
                        'num_expiries': meta.get('num_expiries', 0),
                        'base_assets': meta.get('base_assets', [base_asset]),
                        'size_mb': size_mb
                    })
                    
                except Exception as e:
                    print(f"  ❌ Ошибка чтения {file}: {e}")
                    continue
        
        # Сортируем по дате создания (новые сверху)
        caches.sort(key=lambda x: x['created'], reverse=True)
        
        return caches

# ============================================================================
# КЛАССЫ ДЛЯ АНАЛИЗА (НОВОЕ ЯДРО)
# ============================================================================

class TimeSlotSearcher:
    def __init__(self, cache: OptimizedCache, max_time_diff: int = 20):
        self.cache = cache
        self.max_deviation_minutes = max_time_diff

    def find_closest_slot(self, target_date: date, target_hour: int,
                         target_minute_slot: int) -> Optional[Tuple[date, int, int]]:
        # Прямая проверка
        if self.cache.has_slot(target_date, target_hour, target_minute_slot):
            return (target_date, target_hour, target_minute_slot)

        target_dt = slot_to_datetime(target_date, target_hour, target_minute_slot)

        # Поиск вперёд и назад с шагом 10 минут в пределах max_deviation_minutes
        for step in range(10, self.max_deviation_minutes + 1, 10):
            # Вперёд
            dt_plus = target_dt + timedelta(minutes=step)
            d_plus, h_plus, m_plus = datetime_to_slot(dt_plus)
            if self.cache.has_slot(d_plus, h_plus, m_plus):
                return (d_plus, h_plus, m_plus)

            # Назад
            dt_minus = target_dt - timedelta(minutes=step)
            d_minus, h_minus, m_minus = datetime_to_slot(dt_minus)
            if self.cache.has_slot(d_minus, h_minus, m_minus):
                return (d_minus, h_minus, m_minus)

        return None


class StrangleFinder:
    """Поиск стренглов в кэше"""
    
    def __init__(self, cache: OptimizedCache):
        self.cache = cache
    
    def find_strangles(self, buy_slot: Tuple[date, int, int], dte: int,
                    target_delta: Optional[float] = None, 
                    delta_tolerance: float = 0.05,
                    bidask_mode: bool = False) -> List[Dict]:
        buy_date, buy_hour, buy_minute_slot = buy_slot
        
        options = self.cache.get_options_at_slot(buy_date, buy_hour, buy_minute_slot, dte)
        
        if not options:
            return []
        
        options = [opt for opt in options if abs(opt.get('delta', 0)) >= 0.001]

        # Дополнительная фильтрация для режима Bid/Ask
        if bidask_mode:
            # Проверяем наличие и корректность Bid/Ask цен
            options = [opt for opt in options if opt.get('ask1Price', 0) > 0] 

        if not options:
            return []
        
        calls = [opt for opt in options if opt['option_type'] == 'call']
        puts = [opt for opt in options if opt['option_type'] == 'put']
        
        if not calls or not puts:
            return []
        
        index_price = options[0]['indexPrice']
        puts = [p for p in puts if p['strike'] < index_price]
        
        strangles = []
        
        if target_delta is not None:
            target_put_delta = -target_delta
            
            for call in calls:
                if abs(call['delta'] - target_delta) > delta_tolerance:
                    continue
                
                for put in puts:
                    if put['strike'] >= call['strike']:
                        continue
                    
                    if abs(put['delta'] - target_put_delta) > delta_tolerance:
                        continue
                    
                    if abs(call['delta'] + put['delta']) <= delta_tolerance:
                        strangles.append({
                            'call': call,
                            'put': put,
                            'index_price': index_price,
                            'buy_slot': buy_slot
                        })
        else:
            for call in calls:
                for put in puts:
                    if put['strike'] >= call['strike']:
                        continue
                    
                    if abs(call['delta'] + put['delta']) <= delta_tolerance:
                        strangles.append({
                            'call': call,
                            'put': put,
                            'index_price': index_price,
                            'buy_slot': buy_slot
                        })
                        
                        if len(strangles) >= 100:
                            return strangles
        
        return strangles


class StrangleTracker:
    """Отслеживание эволюции стренгла"""
    
    def __init__(self, cache: OptimizedCache, bidask_mode: bool = False, 
                 fee_mode: bool = False, neutral_param: str = 'delta',
                 fee_rate: float = 0.07):
        self.cache = cache
        self.bidask_mode = bidask_mode
        self.fee_mode = fee_mode
        self.neutral_param = neutral_param
        self.fee_rate = fee_rate
    
    def track(self, strangle: Dict, sell_slot: Tuple[date, int, int], 
            cancel_check=None, bidask_mode: bool = False) -> Optional[pd.DataFrame]:
        """
        Отслеживает эволюцию стренгла от покупки до продажи.
        """
        if cancel_check is None:
            cancel_check = lambda: False
        
        call = strangle['call']
        put = strangle['put']
        buy_slot = strangle['buy_slot']
        buy_date, buy_hour, buy_minute_slot = buy_slot

        call_option = self.cache.options[call['symbol']]
        put_option = self.cache.options[put['symbol']]

        sell_date, sell_hour, sell_minute_slot = sell_slot
        sell_dt = slot_to_datetime(sell_date, sell_hour, sell_minute_slot)
        buy_dt = slot_to_datetime(buy_date, buy_hour, buy_minute_slot)

        # Проверка, что продажа после покупки
        if sell_dt <= buy_dt:
            return None

        expiry_dt = datetime.combine(call['expiry_date'], datetime_time(8, 0))
        if sell_dt > expiry_dt:
            return None

        # Проверяем наличие данных в момент покупки (обязательно)
        call_buy_data = call_option.get_at_slot(buy_date, buy_hour, buy_minute_slot)
        put_buy_data = put_option.get_at_slot(buy_date, buy_hour, buy_minute_slot)
        
        if not call_buy_data or not put_buy_data:
            if bidask_mode:
                print(f"    ⚠️ Нет данных в момент покупки")
            return None
        
        # Для режима Bid/Ask проверяем Ask при покупке
        if bidask_mode:
            if call_buy_data.get('ask1Price', 0) <= 0 or put_buy_data.get('ask1Price', 0) <= 0:
                print(f"    ⚠️ Нулевые Ask при покупке")
                return None

        # Собираем все слоты от покупки до продажи
        slots = []
        current_dt = slot_to_datetime(buy_date, buy_hour, buy_minute_slot)
        while current_dt <= sell_dt:
            slots.append(datetime_to_slot(current_dt))
            current_dt += timedelta(minutes=10)
        
        # Собираем данные для всех слотов
        timeline = []
        last_valid_call = call_buy_data
        last_valid_put = put_buy_data
        missing_data_count = 0
        
        for slot in slots:
            if cancel_check():
                return None
            slot_date, slot_hour, slot_minute = slot
            
            # Пытаемся получить данные для этого слота
            call_data = call_option.get_at_slot(slot_date, slot_hour, slot_minute)
            put_data = put_option.get_at_slot(slot_date, slot_hour, slot_minute)
            
            # Если данных нет, используем последние валидные
            if not call_data:
                call_data = last_valid_call
                missing_data_count += 1
            else:
                last_valid_call = call_data
                
            if not put_data:
                put_data = last_valid_put
                missing_data_count += 1
            else:
                last_valid_put = put_data
            
            # Получаем индексные данные
            index_option = self.cache.get_index_option(
                call['base_asset'], call['expiry_date'], 'call'
            )
            index_data = None
            if index_option:
                index_data = index_option.get_at_slot(slot_date, slot_hour, slot_minute)
            
            # ДИАГНОСТИКА: логируем подозрительные значения
            if bidask_mode and (call_data.get('bid1Price', 0) == 0 or put_data.get('bid1Price', 0) == 0):
                print(f"      ⚠️ Нулевой Bid: {slot_date} {slot_hour}:{slot_minute*10}")
                print(f"        call_bid={call_data.get('bid1Price', 0)}, put_bid={put_data.get('bid1Price', 0)}")
                print(f"        call_mark={call_data.get('markPrice', 0)}, put_mark={put_data.get('markPrice', 0)}")
            
            timeline.append({
                'timestamp': datetime.combine(slot_date, 
                                            datetime_time(slot_hour, slot_minute * 10)),
                'call_data': call_data,
                'put_data': put_data,
                'index_data': index_data
            })
        
        if len(timeline) < 2:
            return None
        
        # Диагностика
        if missing_data_count > 0 and bidask_mode:
            print(f"    📊 Использовано последних значений для {missing_data_count} слотов")
        
        return self._calculate_position(strangle, timeline, bidask_mode)

    def _calculate_position(self, strangle: Dict, timeline: List[Dict], bidask_mode: bool = False) -> pd.DataFrame:
        call = strangle['call']
        put = strangle['put']
        
        # Дополнительная проверка дельт при покупке
        if abs(call['delta']) < 0.001 or abs(put['delta']) < 0.001:
            return None
        
        # Проверка цен при покупке для режима Bid/Ask
        if bidask_mode:
            if call.get('ask1Price', 0) <= 0 or put.get('ask1Price', 0) <= 0:
                return None
        
        # Расчет нейтральных количеств с защитой от деления на ноль
        if self.neutral_param == 'delta':
            if abs(put['delta']) < 0.0001:
                return None
            base_put_qty = -call['delta'] / put['delta']
            base_call_qty = 1.0
        elif self.neutral_param == 'gamma':
            put_gamma = put.get('gamma', 0)
            if abs(put_gamma) < 0.0001:
                return None
            base_put_qty = call.get('gamma', 0) / put_gamma
            base_call_qty = 1.0
        elif self.neutral_param == 'vega':
            put_vega = put.get('vega', 0)
            if abs(put_vega) < 0.0001:
                return None
            base_put_qty = call.get('vega', 0) / put_vega
            base_call_qty = 1.0
        elif self.neutral_param == 'theta':
            put_theta = put.get('theta', 0)
            if abs(put_theta) < 0.0001:
                return None
            base_put_qty = call.get('theta', 0) / put_theta
            base_call_qty = 1.0
        else:  # iv
            put_iv = put.get('markIv', 0)
            if put_iv <= 0:
                return None
            base_put_qty = call.get('markIv', 0) / put_iv
            base_call_qty = 1.0
        
        # Цены при покупке
        if bidask_mode:
            call_price_buy = round_up_to_5(call['ask1Price'])
            put_price_buy = round_up_to_5(put['ask1Price'])
        else:
            call_price_buy = call['markPrice']
            put_price_buy = put['markPrice']
        
        # Проверка, что цены покупки положительные
        if call_price_buy <= 0 or put_price_buy <= 0:
            return None
        
        base_cost = base_call_qty * call_price_buy + base_put_qty * put_price_buy
        
        if base_cost <= 0:
            return None
        
        # Комиссия
        if self.fee_mode:
            target_position_cost = 100.0 / (1.0 + self.fee_rate)
            commission_at_buy = 100.0 - target_position_cost
        else:
            target_position_cost = 100.0
            commission_at_buy = 0.0
        
        scale_factor = target_position_cost / base_cost
        call_qty = base_call_qty * scale_factor
        put_qty = base_put_qty * scale_factor
        
        # Строим временной ряд
        rows = []
        buy_time = timeline[0]['timestamp']
        
        for point in timeline:
            timestamp = point['timestamp']
            call_data = point['call_data']
            put_data = point['put_data']
            index_data = point['index_data']
            
            if bidask_mode:
                # Для режима Bid/Ask используем Bid цены (могут быть 0)
                call_price = call_data.get('bid1Price', 0)
                put_price = put_data.get('bid1Price', 0)
                
                # Дополнительная защита: если Bid = 0, но есть markPrice, можно использовать его?
                # Но по логике Bid/Ask мы должны использовать именно Bid для продажи
            else:
                call_price = call_data.get('markPrice', 0)
                put_price = put_data.get('markPrice', 0)
            
            # Защита от отрицательных цен
            call_price = max(0, call_price)
            put_price = max(0, put_price)
            
            call_value = call_qty * call_price
            put_value = put_qty * put_price
            total_before_fee = call_value + put_value
            
            if self.fee_mode:
                commission_at_sell = total_before_fee * self.fee_rate
                total_after_fee = total_before_fee - commission_at_sell
            else:
                commission_at_sell = 0.0
                total_after_fee = total_before_fee
            
            minutes_from_buy = int((timestamp - buy_time).total_seconds() / 60)
            
            rows.append({
                'timestamp': timestamp,
                'minutes_from_buy': minutes_from_buy,
                'call_price': call_price,
                'put_price': put_price,
                'call_mark': call_data['markPrice'],
                'put_mark': put_data['markPrice'],
                'call_bid': call_data['bid1Price'],
                'put_bid': put_data['bid1Price'],
                'call_ask': call_data['ask1Price'],
                'put_ask': put_data['ask1Price'],
                'call_delta': call_data['delta'],
                'put_delta': put_data['delta'],
                'call_gamma': call_data.get('gamma', 0),
                'put_gamma': put_data.get('gamma', 0),
                'call_vega': call_data.get('vega', 0),
                'put_vega': put_data.get('vega', 0),
                'call_theta': call_data.get('theta', 0),
                'put_theta': put_data.get('theta', 0),
                'call_iv': call_data.get('markIv', 0),
                'put_iv': put_data.get('markIv', 0),
                'call_value': call_value,
                'put_value': put_value,
                'position_value': total_after_fee,
                'pnl': total_after_fee - 100.0,
                'commission_at_buy': commission_at_buy,
                'commission_at_sell': commission_at_sell,
                'total_commission': commission_at_buy + commission_at_sell,
                'atm_iv': index_data['iv'] if index_data else 0,
                'index_price': call_data['indexPrice'],
                'is_original': call_data.get('is_original', True)
            })
        
        result_df = pd.DataFrame(rows)
        
        # Метаданные
        result_df['call_symbol'] = call['symbol']
        result_df['put_symbol'] = put['symbol']
        result_df['call_strike'] = call['strike']
        result_df['put_strike'] = put['strike']
        result_df['call_qty'] = call_qty
        result_df['put_qty'] = put_qty
        result_df['call_price_buy'] = call_price_buy
        result_df['put_price_buy'] = put_price_buy
        result_df['buy_cost'] = 100.0
        result_df['expiry_date'] = call['expiry_date']
        result_df['buy_date'] = buy_time.date()
        result_df['buy_weekday'] = buy_time.weekday()
        
        return result_df


class StrangleAnalyzer:
    """Анализатор дельта-нейтральных стренглов (новое ядро)"""
    
    def __init__(self, cache: OptimizedCache):
        self.cache = cache
        self.results = None
        self.summary = None
        self.analysis_cache = {}
        
        self.buy_time = None
        self.sell_time = None
        self.dte = None
        self.nights = None
        self.target_delta = None
        self.delta_tolerance = None
        self.weekdays = None
        self.bidask_mode = False
        self.fee_mode = False
        self.neutral_param = 'delta'
        self.max_time_diff = 20
        self.daily_stats = None
    
    def set_analysis_params(self, buy_time: str, sell_time: str, dte: int, nights: int,
                        target_delta: Optional[float], delta_tolerance: float,
                        weekdays: List[int]):
        self.buy_time = buy_time
        self.sell_time = sell_time
        self.dte = dte
        self.nights = nights  # <-- обязательно
        self.target_delta = target_delta
        self.delta_tolerance = delta_tolerance
        self.weekdays = weekdays

    def analyze(self, cancel_check=None) -> pd.DataFrame:
        """
        Выполняет анализ стренглов с возможностью проверки отмены.
        cancel_check: функция, возвращающая True если нужно прервать анализ
        """
        if not self.buy_time:
            raise ValueError("Не установлено время покупки")
        
        # Функция проверки отмены по умолчанию
        if cancel_check is None:
            cancel_check = lambda: False
        
        # Парсим время покупки
        buy_hour, buy_minute = map(int, self.buy_time.split(':'))
        buy_minute_slot = buy_minute // 10
        
        # Парсим время продажи
        sell_hour, sell_minute = map(int, self.sell_time.split(':'))
        sell_minute_slot = sell_minute // 10
        
        slot_searcher = TimeSlotSearcher(self.cache, self.max_time_diff)
        snapshot_dates = self.cache.get_all_snapshot_dates()
        
        all_results = []
        strangle_id = 0
        total_slots_found = 0
        total_strangles_found = 0
        
        for snap_date in snapshot_dates:
            # Проверка отмены на каждом шаге
            if cancel_check():
                print("⏹️ Анализ прерван пользователем")
                break
            # Проверяем день недели
            if snap_date.weekday() not in self.weekdays:
                continue
            
            # Ищем слот покупки
            buy_slot = slot_searcher.find_closest_slot(snap_date, buy_hour, buy_minute_slot)
            if not buy_slot:
                continue
            
            total_slots_found += 1
            
            # Получаем опционы для этого слота
            options = self.cache.get_options_at_slot(buy_slot[0], buy_slot[1], buy_slot[2], self.dte)
            
            if not options:
                continue
            
            # Ищем стренглы с учётом режима Bid/Ask
            finder = StrangleFinder(self.cache)
            strangles = finder.find_strangles(
                buy_slot, self.dte, self.target_delta, self.delta_tolerance,
                bidask_mode=self.bidask_mode  # Передаём параметр
            )
            
            total_strangles_found += len(strangles)
            
            if not strangles:
                continue
            
            # Определяем дату продажи = дата покупки + nights
            sell_date = snap_date + timedelta(days=self.nights)
            
            # Проверяем, что продажа происходит после покупки
            buy_dt = slot_to_datetime(snap_date, buy_hour, buy_minute_slot)
            sell_dt_candidate = datetime.combine(sell_date, datetime_time(sell_hour, sell_minute))
            
            if sell_dt_candidate <= buy_dt:
                # Нельзя продать раньше, чем купить
                continue
            
            # Ищем слот продажи
            sell_slot = slot_searcher.find_closest_slot(sell_date, sell_hour, sell_minute_slot)
            if not sell_slot:
                continue
            
            # Отслеживаем каждый стренгл
            tracker = StrangleTracker(
                self.cache, self.bidask_mode, self.fee_mode, self.neutral_param
            )
            
            tracked_count = 0
            for strangle in strangles:
                if cancel_check():
                    break                
                strangle_id += 1
                # Передаём параметр bidask_mode в метод track
                evolution = tracker.track(strangle, sell_slot, cancel_check, self.bidask_mode)
                
                if evolution is not None and not evolution.empty:
                    evolution['strangle_id'] = strangle_id
                    all_results.append(evolution)
                    tracked_count += 1
        
        if not all_results:
            self.results = pd.DataFrame()
            return self.results
        
        self.results = pd.concat(all_results, ignore_index=True)
        self.summary = self._create_summary()
        
        return self.results

    def _create_summary(self) -> pd.DataFrame:
        unique_ids = self.results['strangle_id'].unique()
        summary = []
        
        weekday_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        
        for strangle_id in unique_ids:
            data = self.results[self.results['strangle_id'] == strangle_id]
            first = data.iloc[0]
            last = data.iloc[-1]
            
            summary.append({
                'strangle_id': strangle_id,
                'date': first['buy_date'],
                'weekday': first['buy_weekday'],
                'weekday_name': weekday_names[first['buy_weekday']],
                'expiry_date': first['expiry_date'],
                'call_symbol': first['call_symbol'],
                'put_symbol': first['put_symbol'],
                'call_strike': first['call_strike'],
                'put_strike': first['put_strike'],
                'call_qty': first['call_qty'],
                'put_qty': first['put_qty'],
                'call_price_buy': first['call_price'],
                'put_price_buy': first['put_price'],
                'call_delta_buy': first['call_delta'],
                'put_delta_buy': first['put_delta'],
                'call_gamma': first['call_gamma'],
                'put_gamma': first['put_gamma'],
                'call_vega': first['call_vega'],
                'put_vega': first['put_vega'],
                'call_theta': first['call_theta'],
                'put_theta': first['put_theta'],
                'call_iv_buy': first['call_iv'],
                'put_iv_buy': first['put_iv'],
                'index_price_buy': first['index_price'],
                'atm_iv_buy': first['atm_iv'],
                
                # НОВЫЕ ПОЛЯ (значения на момент продажи)
                'call_price_sell': last['call_price'],
                'put_price_sell': last['put_price'],
                'call_delta_sell': last['call_delta'],
                'put_delta_sell': last['put_delta'],
                'call_iv_sell': last['call_iv'],
                'put_iv_sell': last['put_iv'],
                'index_price_sell': last['index_price'],
                'atm_iv_sell': last['atm_iv'],
                
                'final_value': last['position_value'],
                'final_pnl': last['pnl'],
                'commission_total': last['total_commission']
            })
        
        df = pd.DataFrame(summary)
        
        # Добавляем агрегированные данные по дням
        self.daily_stats = df.groupby('date').agg({
            'final_pnl': ['mean', 'std', 'count', 'sum'],
            'strangle_id': 'count'
        }).round(2)
        self.daily_stats.columns = ['pnl_mean', 'pnl_std', 'pnl_count', 'pnl_sum', 'strangle_count']
        
        # Сохраняем в атрибут для использования в графиках
        self.daily_stats = self.daily_stats.reset_index()
        
        return df.sort_values('date').reset_index(drop=True)
    
    def get_summary(self) -> pd.DataFrame:
        return self.summary if self.summary is not None else pd.DataFrame()


# ============================================================================
# ГРАФИЧЕСКИЙ ИНТЕРФЕЙС (СТАРЫЙ ИЗ strangle22cache.py)
# ============================================================================

class SortableTreeview(ttk.Treeview):
    """Treeview с возможностью сортировки по колонкам"""
    
    def __init__(self, parent, columns, **kwargs):
        super().__init__(parent, columns=columns, **kwargs)
        self.columns = columns
        self.sort_dir = {col: False for col in columns}
        
        for col in columns:
            self.heading(col, text=col, command=lambda c=col: self.sort_by(c))
    
    def sort_by(self, col):
        ascending = self.sort_dir.get(col, True)
        self.sort_dir[col] = not ascending
        
        items = [(self.set(item, col), item) for item in self.get_children('')]
        
        try:
            items = [(float(val.replace('%', '').replace('✅', '').replace('❌', '').strip()), item) 
                    for val, item in items if val.strip()]
        except:
            pass
        
        items.sort(reverse=not ascending, key=lambda x: x[0])
        
        for index, (_, item) in enumerate(items):
            self.move(item, '', index)


class CacheManagerDialog:
    """Диалог для управления кэшами (адаптирован для нового ядра)"""
    
    def __init__(self, parent, cache_manager: CacheManager, main_gui):
        self.parent = parent
        self.cache_manager = cache_manager
        self.main_gui = main_gui
        self.root = parent
        self.selected_caches = set()
        self.cache_items = {}
        self.sort_dir = {}
        
        self._create_dialog()
    
    def _create_dialog(self):
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Менеджер кэша")
        self.dialog.geometry("1200x700")
        self.dialog.minsize(1000, 600)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        
        style = ttk.Style(self.dialog)
        style.theme_use('clam')
        self.dialog.configure(bg='#f0f0f0')
        
        main_container = ttk.Frame(self.dialog, padding="10")
        main_container.pack(fill=tk.BOTH, expand=True)
        
        header_frame = ttk.Frame(main_container)
        header_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(header_frame, text="Менеджер кэша", 
                font=('Arial', 14, 'bold')).pack(side=tk.LEFT)
        
        ttk.Label(header_frame, 
                text=f"📁 {self.cache_manager.cache_dir}",
                font=('Arial', 8), foreground='gray').pack(side=tk.RIGHT)
        
        operations_frame = ttk.Frame(main_container)
        operations_frame.pack(fill=tk.X, pady=5)
        
        left_ops = ttk.Frame(operations_frame)
        left_ops.pack(side=tk.LEFT)
        
        self.load_btn = ttk.Button(left_ops, text="📥 Загрузить выбранные", 
                                   command=self.load_selected, width=20)
        self.load_btn.pack(side=tk.LEFT, padx=2)
        
        self.delete_btn = ttk.Button(left_ops, text="🗑 Удалить выбранные", 
                                     command=self.delete_selected, width=20)
        self.delete_btn.pack(side=tk.LEFT, padx=2)
        
        right_ops = ttk.Frame(operations_frame)
        right_ops.pack(side=tk.RIGHT)
        
        ttk.Button(right_ops, text="💾 Бэкап выбранных", 
                  command=self.backup_selected, width=18).pack(side=tk.LEFT, padx=2)
        ttk.Button(right_ops, text="📀 Бэкап всей базы", 
                  command=self.backup_all, width=18).pack(side=tk.LEFT, padx=2)
        ttk.Button(right_ops, text="🔄 Восстановить из бэкапа", 
                  command=self.restore_from_backup, width=22).pack(side=tk.LEFT, padx=2)
        
        ttk.Separator(main_container, orient='horizontal').pack(fill=tk.X, pady=5)
        
        selection_frame = ttk.Frame(main_container)
        selection_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(selection_frame, text="Выделение:", 
                 font=('Arial', 9, 'bold')).pack(side=tk.LEFT, padx=(0, 10))
        
        ttk.Button(selection_frame, text="✓ Выбрать все", 
                  command=self.select_all, width=15).pack(side=tk.LEFT, padx=2)
        ttk.Button(selection_frame, text="✗ Снять все", 
                  command=self.deselect_all, width=15).pack(side=tk.LEFT, padx=2)
        ttk.Button(selection_frame, text="🔄 Инвертировать", 
                  command=self.invert_selection, width=15).pack(side=tk.LEFT, padx=2)
        
        self.selection_count_var = tk.StringVar(value="Выбрано: 0")
        ttk.Label(selection_frame, textvariable=self.selection_count_var,
                 font=('Arial', 9, 'bold')).pack(side=tk.RIGHT, padx=10)
        
        self._create_treeview(main_container)
        
        bottom_frame = ttk.Frame(main_container)
        bottom_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(bottom_frame, text="Закрыть", 
                  command=self.dialog.destroy, width=15).pack(side=tk.RIGHT, padx=5)
        
        self.load_caches()
        self.update_button_states()
    
    def _create_treeview(self, parent):
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        columns = ('ID', 'Дата создания', 'Опционов', 'Индексных', 
                  'Снэпшотов', 'Экспираций', 'Актив', 'Размер (MB)')
        
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings', 
                                 height=15, selectmode='extended')
        
        for col in columns:
            self.sort_dir[col] = False
            self.tree.heading(col, text=col, command=lambda c=col: self.sort_by(c))
        
        self.tree.heading('ID', text='ID')
        self.tree.heading('Дата создания', text='Дата создания')
        self.tree.heading('Опционов', text='Опционов')
        self.tree.heading('Индексных', text='Индексных')
        self.tree.heading('Снэпшотов', text='Снэпшотов')
        self.tree.heading('Экспираций', text='Экспираций')
        self.tree.heading('Актив', text='Актив')
        self.tree.heading('Размер (MB)', text='Размер (MB)')
        
        self.tree.column('ID', width=250, minwidth=200, anchor='w')
        self.tree.column('Дата создания', width=150, minwidth=120, anchor='center')
        self.tree.column('Опционов', width=80, minwidth=60, anchor='center')
        self.tree.column('Индексных', width=80, minwidth=60, anchor='center')
        self.tree.column('Снэпшотов', width=80, minwidth=60, anchor='center')
        self.tree.column('Экспираций', width=80, minwidth=60, anchor='center')
        self.tree.column('Актив', width=100, minwidth=80, anchor='center')
        self.tree.column('Размер (MB)', width=90, minwidth=70, anchor='center')
        
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        self.tree.bind('<<TreeviewSelect>>', self.on_selection_change)
    
    def sort_by(self, col):
        self.sort_dir[col] = not self.sort_dir[col]
        ascending = self.sort_dir[col]
        
        items = [(self.tree.set(item, col), item) for item in self.tree.get_children('')]
        
        try:
            if col in ['Опционов', 'Индексных', 'Снэпшотов', 'Экспираций', 'Размер (MB)']:
                items = [(float(val.replace(',', '')), item) for val, item in items]
            elif col == 'Дата создания':
                items = [(datetime.strptime(val, '%Y-%m-%d %H:%M').timestamp(), item) 
                        for val, item in items]
        except:
            pass
        
        items.sort(reverse=not ascending, key=lambda x: x[0])
        
        for index, (_, item) in enumerate(items):
            self.tree.move(item, '', index)
        
        for c in self.tree['columns']:
            if c == col:
                arrow = ' ↑' if ascending else ' ↓'
                self.tree.heading(c, text=c + arrow)
            else:
                self.tree.heading(c, text=c)
    
    def load_caches(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        self.cache_items.clear()
        self.selected_caches.clear()
        
        caches = self.cache_manager.get_available_caches()
        
        for cache in caches:
            # Используем display_id вместо полного ID
            display_id = cache.get('display_id', cache['id'])
            
            if len(display_id) > 40:
                display_id = display_id[:37] + '...'
            
            created_str = "Неизвестно"
            if cache['created']:
                try:
                    created_str = cache['created'].strftime('%Y-%m-%d %H:%M')
                except:
                    created_str = "Ошибка"
            
            # Формируем строку активов
            base_assets = cache.get('base_assets', [cache.get('base_asset', 'UNKNOWN')])
            assets_str = ', '.join(base_assets[:3])
            if len(base_assets) > 3:
                assets_str += f"... (+{len(base_assets)-3})"
            
            item_id = self.tree.insert('', tk.END, values=(
                display_id,
                created_str,
                cache['num_options'],
                cache['num_index_options'],
                cache['num_snapshots'],
                cache['num_expiries'],
                assets_str,
                f"{cache['size_mb']:.1f}"
            ), tags=(cache['id'],))
            
            self.cache_items[item_id] = cache['id']
        
        self.update_selection_count()
        
        for col in self.tree['columns']:
            self.tree.heading(col, text=col)


    def on_selection_change(self, event):
        selected_items = self.tree.selection()
        self.selected_caches.clear()
        for item_id in selected_items:
            cache_id = self.cache_items.get(item_id)
            if cache_id:
                self.selected_caches.add(cache_id)
        
        self.update_selection_count()
        self.update_button_states()
    
    def update_button_states(self):
        count = len(self.selected_caches)
        
        if count == 0:
            self.load_btn.config(state='disabled')
            self.delete_btn.config(state='disabled')
        elif count == 1:
            self.load_btn.config(state='normal')
            self.delete_btn.config(state='normal')
        else:
            self.load_btn.config(state='normal')
            self.delete_btn.config(state='normal')
    
    def update_selection_count(self):
        count = len(self.selected_caches)
        self.selection_count_var.set(f"Выбрано: {count}")
    
    def select_all(self):
        self.tree.selection_set(self.tree.get_children())
    
    def deselect_all(self):
        self.tree.selection_set([])
    
    def invert_selection(self):
        all_items = set(self.tree.get_children())
        selected_items = set(self.tree.selection())
        new_selection = list(all_items - selected_items)
        self.tree.selection_set(new_selection)
    
    def load_selected(self):
        if not self.selected_caches:
            messagebox.showwarning("Предупреждение", "Выберите хотя бы один кэш")
            return
        
        if len(self.selected_caches) == 1:
            cache_id = list(self.selected_caches)[0]
            try:
                self._update_status(f"Загрузка кэша {cache_id[:20]}...")
                
                cache = self.cache_manager.load_cache(cache_id)
                self.main_gui.set_cache(cache)
                
                self.dialog.destroy()
                messagebox.showinfo("Успех", "Кэш загружен")
                
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить кэш:\n{str(e)}")
        else:
            msg = f"Выбрано {len(self.selected_caches)} кэшей.\n\nДля анализа можно загрузить только один кэш."
            messagebox.showinfo("Множественный выбор", msg)
    
    def delete_selected(self):
        if not self.selected_caches:
            return
        
        msg = f"Вы действительно хотите удалить {len(self.selected_caches)} кэш(ей)?\n\nЭто действие нельзя отменить!"
        
        if not messagebox.askyesno("Подтверждение удаления", msg, icon='warning'):
            return
        
        deleted = 0
        errors = []
        
        for cache_id in list(self.selected_caches):
            try:
                self.cache_manager.delete_cache(cache_id)
                deleted += 1
            except Exception as e:
                errors.append(f"{cache_id[:20]}...: {str(e)}")
        
        self.load_caches()
        
        if errors:
            error_text = "\n".join(errors[:5])
            if len(errors) > 5:
                error_text += f"\n... и еще {len(errors)-5} ошибок"
            messagebox.showwarning("Удаление с ошибками", 
                                 f"Удалено: {deleted}\nОшибки:\n{error_text}")
        else:
            messagebox.showinfo("Удаление", f"Успешно удалено {deleted} кэшей")
    
    def backup_selected(self):
        if not self.selected_caches:
            messagebox.showwarning("Предупреждение", "Выберите кэши для бэкапа")
            return
        
        default_name = f"strangle_backup_selected_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        
        backup_file = filedialog.asksaveasfilename(
            title="Сохранить бэкап выбранных кэшей",
            initialfile=default_name,
            defaultextension=".zip",
            filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")]
        )
        
        if not backup_file:
            return
        
        try:
            self._update_status("Создание бэкапа выбранных кэшей...")
            
            with tempfile.TemporaryDirectory() as temp_dir:
                for cache_id in self.selected_caches:
                    data_file = os.path.join(self.cache_manager.cache_dir, f"{cache_id}.pkl")
                    meta_file = os.path.join(self.cache_manager.cache_dir, f"{cache_id}_meta.pkl")
                    
                    if os.path.exists(data_file):
                        shutil.copy2(data_file, temp_dir)
                    if os.path.exists(meta_file):
                        shutil.copy2(meta_file, temp_dir)
                
                with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(temp_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            zipf.write(file_path, file)
            
            self._update_status(f"Бэкап создан: {os.path.basename(backup_file)}")
            messagebox.showinfo("Успех", f"Бэкап {len(self.selected_caches)} кэшей сохранен")
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать бэкап:\n{str(e)}")
    
    def backup_all(self):
        default_name = f"strangle_backup_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        
        backup_file = filedialog.asksaveasfilename(
            title="Сохранить бэкап всех кэшей",
            initialfile=default_name,
            defaultextension=".zip",
            filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")]
        )
        
        if not backup_file:
            return
        
        try:
            self._update_status("Создание бэкапа всех кэшей...")
            
            with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in os.listdir(self.cache_manager.cache_dir):
                    file_path = os.path.join(self.cache_manager.cache_dir, file)
                    if os.path.isfile(file_path) and file.endswith('.pkl'):
                        zipf.write(file_path, file)
            
            self._update_status(f"Бэкап создан: {os.path.basename(backup_file)}")
            messagebox.showinfo("Успех", "Бэкап всех кэшей сохранен")
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать бэкап:\n{str(e)}")
    
    def restore_from_backup(self):
        backup_file = filedialog.askopenfilename(
            title="Выберите файл бэкапа для восстановления",
            filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")]
        )
        
        if not backup_file:
            return
        
        # Упрощенная версия - просто распаковываем в папку кэша
        try:
            self._update_status("Восстановление из бэкапа...")
            
            with zipfile.ZipFile(backup_file, 'r') as zip_ref:
                zip_ref.extractall(self.cache_manager.cache_dir)
            
            self.load_caches()
            self._update_status("Кэш восстановлен из бэкапа")
            messagebox.showinfo("Успех", "Кэш успешно восстановлен")
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось восстановить:\n{str(e)}")
    
    def _update_status(self, message):
        if hasattr(self.main_gui, 'status_var'):
            self.main_gui.status_var.set(message)
            self.main_gui.left_status_var.set(message)
            if self.root and self.root.winfo_exists():
                self.root.update_idletasks()


class StrangleAnalyzerGUI:
    """Главное окно программы (старое из strangle22cache.py, адаптированное)"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Анализатор стренглов BTC опционов")
        self.root.state('zoomed')
        
        self.cache = None
        self.analyzer = None
        self.results = None
        self.summary = None
        self.data_files = []
        self.cache_manager = CacheManager()
        
        self.current_page = 0
        self.total_pages = 3
        self.page_figures = {}

        # Режим отображения в таблице: 'bs' (покупка/продажа) или 'sb' (продажа/покупка)
        self.table_display_mode = tk.StringVar(value='bs')
        # Параметры для отображения информации
        self.show_info_mode = tk.BooleanVar(value=True)  

        # Параметры по умолчанию
        self.buy_time = "13:00"
        self.sell_time = "17:00"
        self.max_time_diff = 20
        self.dte = 0
        self.nights = 0
        self.target_delta = 0.1
        self.tolerance = 0.05
        self.weekdays = [0, 1, 2, 3, 4, 5, 6]
        self.neutral_param = "delta"
        self.bidask_mode = False
        self.fee_mode = False
        
        style = ttk.Style()
        style.theme_use('clam')
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Создание пользовательского интерфейса (полностью из strangle22cache.py)"""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.BOTH, expand=True)
        
        left_frame = ttk.Frame(top_frame, width=450)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)
        
        right_frame = ttk.Frame(top_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # Левая панель: настройки
        title_label = ttk.Label(left_frame, text="Настройки анализа", font=('Arial', 16, 'bold'))
        title_label.pack(pady=10)
        
        # Файлы данных
        files_frame = ttk.LabelFrame(left_frame, text="Файлы данных", padding="5")
        files_frame.pack(fill=tk.X, pady=5)
        
        files_header = ttk.Frame(files_frame)
        files_header.pack(fill=tk.X)
        
        self.files_expanded = tk.BooleanVar(value=True)
        
        def toggle_files():
            if self.files_expanded.get():
                files_content.pack_forget()
                self.files_expanded.set(False)
                toggle_btn.config(text="▶")
            else:
                files_content.pack(fill=tk.BOTH, expand=True, pady=5)
                self.files_expanded.set(True)
                toggle_btn.config(text="▼")
        
        toggle_btn = ttk.Button(files_header, text="▼", width=3, command=toggle_files)
        toggle_btn.pack(side=tk.RIGHT, padx=2)
        
        ttk.Label(files_header, text="Управление файлами", font=('Arial', 9, 'bold')).pack(side=tk.LEFT)
        
        files_content = ttk.Frame(files_frame)
        
        self.files_count_var = tk.StringVar(value="Файлов не выбрано")
        ttk.Label(files_content, textvariable=self.files_count_var, font=('Arial', 10)).pack(pady=2)
        
        btn_frame_files = ttk.Frame(files_content)
        btn_frame_files.pack(fill=tk.X, pady=2)
        
        ttk.Button(btn_frame_files, text="Выбрать файлы", command=self.select_files).pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(btn_frame_files, text="Удалить", command=self.remove_selected_file).pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(btn_frame_files, text="Очистить", command=self.clear_files).pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(btn_frame_files, text="📦 Подготовить кэш", command=self.prepare_cache).pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        
        listbox_frame = ttk.Frame(files_content)
        listbox_frame.pack(fill=tk.BOTH, expand=True, pady=2)
        
        scrollbar = ttk.Scrollbar(listbox_frame, orient="vertical")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.files_listbox = tk.Listbox(listbox_frame, height=4, font=('Arial', 9),
                                        yscrollcommand=scrollbar.set)
        self.files_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar.config(command=self.files_listbox.yview)
        
        files_content.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Параметры анализа
        params_frame = ttk.LabelFrame(left_frame, text="Параметры", padding="10")
        params_frame.pack(fill=tk.X, pady=5)
        
        # Время покупки
        time_frame = ttk.Frame(params_frame)
        time_frame.pack(fill=tk.X, pady=2)
        
        ttk.Label(time_frame, text="Время покупки:", width=20).pack(side=tk.LEFT)
        
        buy_right_container = ttk.Frame(time_frame)
        buy_right_container.pack(side=tk.RIGHT)
        
        self.buy_minute_var = tk.StringVar(value="00")
        buy_minute_spinbox = ttk.Spinbox(buy_right_container, from_=0, to=59, textvariable=self.buy_minute_var,
                                        width=5, wrap=True, increment=10, command=self._update_buy_time)
        buy_minute_spinbox.pack(side=tk.RIGHT, padx=(2, 0))
        ttk.Label(buy_right_container, text="мин").pack(side=tk.RIGHT)
        
        self.buy_hour_var = tk.StringVar(value="13")
        buy_hour_spinbox = ttk.Spinbox(buy_right_container, from_=0, to=23, textvariable=self.buy_hour_var, 
                                    width=5, wrap=True, command=self._update_buy_time)
        buy_hour_spinbox.pack(side=tk.RIGHT, padx=(2, 0))
        ttk.Label(buy_right_container, text="ч").pack(side=tk.RIGHT)
        
        self.buy_time_var = tk.StringVar(value="13:00")
        
        # Время продажи (всегда активно)
        sell_frame = ttk.Frame(params_frame)
        sell_frame.pack(fill=tk.X, pady=2)

        ttk.Label(sell_frame, text="Время продажи:", width=20).pack(side=tk.LEFT)

        time_container = ttk.Frame(sell_frame)
        time_container.pack(side=tk.RIGHT)

        self.sell_minute_var = tk.StringVar(value="00")
        sell_minute_spinbox = ttk.Spinbox(time_container, from_=0, to=59, textvariable=self.sell_minute_var,
                                        width=5, wrap=True, increment=10, command=self._update_sell_time)
        sell_minute_spinbox.pack(side=tk.RIGHT, padx=(0, 2))
        ttk.Label(time_container, text="мин").pack(side=tk.RIGHT)

        self.sell_hour_var = tk.StringVar(value="17")
        sell_hour_spinbox = ttk.Spinbox(time_container, from_=0, to=23, textvariable=self.sell_hour_var,
                                    width=5, wrap=True, command=self._update_sell_time)
        sell_hour_spinbox.pack(side=tk.RIGHT, padx=(0, 2))
        ttk.Label(time_container, text="ч").pack(side=tk.RIGHT)

        self.sell_time_var = tk.StringVar(value="17:00")
        self._update_sell_time()

        
        # Макс. отклонение
        diff_frame = ttk.Frame(params_frame)
        diff_frame.pack(fill=tk.X, pady=2)
        ttk.Label(diff_frame, text="Макс. отклонение:", width=20).pack(side=tk.LEFT)
        self.max_time_diff_var = tk.StringVar(value="20")
        ttk.Entry(diff_frame, textvariable=self.max_time_diff_var, width=10).pack(side=tk.RIGHT)
        ttk.Label(diff_frame, text="мин").pack(side=tk.RIGHT, padx=2)
        
        # Время экспирации
        expiry_frame = ttk.Frame(params_frame)
        expiry_frame.pack(fill=tk.X, pady=2)
        ttk.Label(expiry_frame, text="Время экспирации:", width=20).pack(side=tk.LEFT)
        self.expiry_time_var = tk.StringVar(value="08:00")
        ttk.Entry(expiry_frame, textvariable=self.expiry_time_var, width=10).pack(side=tk.RIGHT)
        
        # DTE
        dte_frame = ttk.Frame(params_frame)
        dte_frame.pack(fill=tk.X, pady=2)
        ttk.Label(dte_frame, text="DTE:", width=20).pack(side=tk.LEFT)
        self.dte_var = tk.StringVar(value="0")
        dte_combo = ttk.Combobox(dte_frame, textvariable=self.dte_var,
                                values=["0", "1", "2", "3", "4", "5", "6", "7"],
                                width=8, state="readonly")
        dte_combo.pack(side=tk.RIGHT)
        dte_combo.bind('<<ComboboxSelected>>', lambda e: self._update_info_panel())
        
        # Nights (отдельное поле)
        nights_frame = ttk.Frame(params_frame)
        nights_frame.pack(fill=tk.X, pady=2)
        ttk.Label(nights_frame, text="Переносы через ночь:", width=20).pack(side=tk.LEFT)
        self.nights_var = tk.StringVar(value="0")
        nights_combo = ttk.Combobox(nights_frame, textvariable=self.nights_var,
                                    values=["0", "1", "2", "3", "4", "5", "6", "7"],
                                    width=8, state="readonly")
        nights_combo.pack(side=tk.RIGHT)
        nights_combo.bind('<<ComboboxSelected>>', lambda e: self._update_info_panel())
        
        # Нейтрализация
        neutral_param_frame = ttk.Frame(params_frame)
        neutral_param_frame.pack(fill=tk.X, pady=2)
        ttk.Label(neutral_param_frame, text="Нейтрализзация по:", width=20).pack(side=tk.LEFT)
        self.neutral_param_var = tk.StringVar(value="delta")
        neutral_param_combo = ttk.Combobox(neutral_param_frame, textvariable=self.neutral_param_var,
                                    values=["delta", "gamma", "vega", "theta", "iv"],
                                    width=8, state="readonly")
        neutral_param_combo.pack(side=tk.RIGHT)
        neutral_param_combo.bind('<<ComboboxSelected>>', lambda e: self._update_info_panel())
        
        # Дельта
        delta_frame = ttk.Frame(params_frame)
        delta_frame.pack(fill=tk.X, pady=2)
        ttk.Label(delta_frame, text="Целевая дельта:", width=20).pack(side=tk.LEFT)
        self.delta_var = tk.StringVar(value="0.1")
        ttk.Entry(delta_frame, textvariable=self.delta_var, width=8).pack(side=tk.RIGHT)
        self.delta_enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(delta_frame, text="", variable=self.delta_enabled_var,
                    command=self._toggle_delta).pack(side=tk.RIGHT)
        self.delta_enabled_var.trace_add('write', lambda *args: self._update_info_panel())
        
        # Допуск
        tol_frame = ttk.Frame(params_frame)
        tol_frame.pack(fill=tk.X, pady=2)
        ttk.Label(tol_frame, text="Допуск по дельте:", width=20).pack(side=tk.LEFT)
        self.tolerance_var = tk.StringVar(value="0.05")
        ttk.Entry(tol_frame, textvariable=self.tolerance_var, width=8).pack(side=tk.RIGHT)
        
        ttk.Separator(params_frame, orient='horizontal').pack(fill=tk.X, pady=5)
        
        # Режимы
        modes_frame = ttk.Frame(params_frame)
        modes_frame.pack(fill=tk.X, pady=2)
        self.bidask_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(modes_frame, text="Режим Bid/Ask", 
                    variable=self.bidask_mode_var).pack(side=tk.LEFT, padx=5)
        self.bidask_mode_var.trace_add('write', lambda *args: self._update_info_panel())
        self.fee_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(modes_frame, text="Комиссия 7%", 
                    variable=self.fee_mode_var).pack(side=tk.LEFT, padx=5)
        self.fee_mode_var.trace_add('write', lambda *args: self._update_info_panel())
        
        # Дни недели
        weekdays_frame = ttk.LabelFrame(left_frame, text="Дни недели", padding="10")
        weekdays_frame.pack(fill=tk.X, pady=5)
        
        self.weekday_vars = {}
        days = [("Пн", 0), ("Вт", 1), ("Ср", 2), ("Чт", 3), ("Пт", 4), ("Сб", 5), ("Вс", 6)]
        
        days_row = ttk.Frame(weekdays_frame)
        days_row.pack()
        for i, (name, idx) in enumerate(days):
            var = tk.BooleanVar(value=True)
            self.weekday_vars[idx] = var
            cb = ttk.Checkbutton(days_row, text=name, variable=var)
            cb.pack(side=tk.LEFT, padx=5)
            var.trace_add('write', lambda *args: self._update_info_panel())
        
        # Кнопки управления
        control_frame = ttk.Frame(left_frame)
        control_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(control_frame, text="Запустить анализ", command=self.run_analysis, style='Accent.TButton').pack(fill=tk.X, pady=2)
        ttk.Button(control_frame, text="Очистить результаты", command=self.clear_results).pack(fill=tk.X, pady=2)
        
        cache_operations_frame = ttk.Frame(control_frame)
        cache_operations_frame.pack(fill=tk.X, pady=2)
        
        ttk.Button(cache_operations_frame, text="📋 Менеджер кэша", 
                command=self.open_cache_manager).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        
        # Информационная панель с параметрами
        info_frame = ttk.LabelFrame(left_frame, text="📋 Информация о параметрах", padding="10")
        info_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Текстовое поле с информацией
        info_text_frame = ttk.Frame(info_frame)
        info_text_frame.pack(fill=tk.BOTH, expand=True)

        self.info_text_widget = tk.Text(info_text_frame, wrap=tk.WORD, font=('Courier', 9), 
                                        height=18, relief=tk.SUNKEN, borderwidth=1)
        self.info_text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Скроллбар для текста
        info_scrollbar = ttk.Scrollbar(info_text_frame, orient="vertical", 
                                        command=self.info_text_widget.yview)
        info_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.info_text_widget.config(yscrollcommand=info_scrollbar.set)

        # Делаем поле только для чтения
        self.info_text_widget.config(state='disabled')

        # Кнопка обновления информации
        update_info_btn = ttk.Button(info_frame, text="🔄 Обновить информацию", 
                                    command=self._update_info_panel)
        update_info_btn.pack(pady=5)

        # Текущий статус (оставляем внизу)
        status_frame = ttk.LabelFrame(left_frame, text="Текущий статус", padding="10")
        status_frame.pack(fill=tk.X, pady=5, side=tk.BOTTOM)
        
        self.left_status_var = tk.StringVar(value="Готов к работе")
        ttk.Label(status_frame, textvariable=self.left_status_var, wraplength=400, 
                font=('Arial', 9, 'bold')).pack()
        
        self.left_cache_var = tk.StringVar(value="📁 Кэш не загружен")
        ttk.Label(status_frame, textvariable=self.left_cache_var, 
                font=('Arial', 8)).pack(pady=2)
        
        # Правая панель с вкладками
        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Вкладка с графиками
        plots_frame = ttk.Frame(self.notebook)
        self.notebook.add(plots_frame, text="Графики")
        
        self.button_frame = ttk.Frame(plots_frame)
        self.button_frame.pack(fill=tk.X, pady=5)
        
        nav_frame = ttk.Frame(self.button_frame)
        nav_frame.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(nav_frame, text="◀", command=self.prev_page, width=3).pack(side=tk.LEFT)
        self.page_label = ttk.Label(nav_frame, text="Страница 1/3", font=('Arial', 10))
        self.page_label.pack(side=tk.LEFT, padx=10)
        ttk.Button(nav_frame, text="▶", command=self.next_page, width=3).pack(side=tk.LEFT)
        
        self._add_fullscreen_buttons()
        
        graph_frame = ttk.Frame(plots_frame)
        graph_frame.pack(fill=tk.BOTH, expand=True)
        
        # Фиксированный размер для Full HD
        self.fig = Figure(figsize=(14, 8), dpi=100)  # Оптимально для 1920x1080
        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        toolbar_frame = ttk.Frame(graph_frame)
        toolbar_frame.pack(fill=tk.X)
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.update()
        
        # Вкладка с таблицей результатов
        table_frame = ttk.Frame(self.notebook)
        self.notebook.add(table_frame, text="Таблица результатов")

        # Добавляем панель управления таблицей (используем pack)
        table_control_frame = ttk.Frame(table_frame)
        table_control_frame.pack(fill=tk.X, pady=5)

        ttk.Label(table_control_frame, text="Режим отображения:", font=('Arial', 10)).pack(side=tk.LEFT, padx=5)

        bs_radio = ttk.Radiobutton(
            table_control_frame, 
            text="Покупка / Продажа (b/s)", 
            variable=self.table_display_mode, 
            value='bs',
            command=self.update_table
        )
        bs_radio.pack(side=tk.LEFT, padx=5)

        sb_radio = ttk.Radiobutton(
            table_control_frame, 
            text="Продажа / Покупка (s/b)", 
            variable=self.table_display_mode, 
            value='sb',
            command=self.update_table
        )
        sb_radio.pack(side=tk.LEFT, padx=5)


        # Создаем фрейм для таблицы и скроллбаров (используем grid внутри этого фрейма)
        table_container = ttk.Frame(table_frame)
        table_container.pack(fill=tk.BOTH, expand=True, pady=5)

        columns = ('ID', 'Дата', 'День', 'Index', 'Кол (стр)', 'Пут (стр)', 
                'Кол-во колов', 'Кол-во путов', 'Цена кола (b/s)', 
                'Цена пута (b/s)', 'Дельта кола (b/s)', 'Дельта пута (b/s)', 
                'IV кола (b/s) (%)', 'IV пута (b/s) (%)', 'ATM IV (b/s) (%)',
                'P&L ($)')

        self.tree = SortableTreeview(table_container, columns=columns, show='headings', height=25)

        column_widths = [50, 90, 50, 70, 70, 70, 90, 90, 120, 120, 120, 120, 100, 100, 100, 80]
        for col, width in zip(columns, column_widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor='center')

        # Скроллбары
        vsb = ttk.Scrollbar(table_container, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # Размещаем элементы с grid внутри table_container
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')

        # Настраиваем веса для grid
        table_container.grid_rowconfigure(0, weight=1)
        table_container.grid_columnconfigure(0, weight=1)
        
        # Вкладка со статистикой
        stats_frame = ttk.Frame(self.notebook)
        self.notebook.add(stats_frame, text="Статистика")
        
        stats_table_frame = ttk.Frame(stats_frame)
        stats_table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        style = ttk.Style()
        style.configure("Stats.Treeview", 
                        font=('Courier', 10),
                        rowheight=25,
                        borderwidth=1,
                        relief='solid')
        style.configure("Stats.Treeview.Heading", 
                        font=('Courier', 10, 'bold'),
                        borderwidth=1,
                        relief='solid')
        style.map('Stats.Treeview', background=[('selected', '#347083')])
        
        self.stats_tree = ttk.Treeview(stats_table_frame, 
                                    columns=('col1', 'col2'), 
                                    show='tree headings', 
                                    height=25,
                                    style="Stats.Treeview")
        
        self.stats_tree.heading('#0', text='Показатель')
        self.stats_tree.heading('col1', text='По стренглам')
        self.stats_tree.heading('col2', text='По дням')
        
        self.stats_tree.column('#0', anchor='w', stretch=False)
        self.stats_tree.column('col1', anchor='e', stretch=False)
        self.stats_tree.column('col2', anchor='e', stretch=False)
        
        self.stats_tree.tag_configure('header', font=('Courier', 10, 'bold'), background="#C0C0C0")
        self.stats_tree.tag_configure('block_header', font=('Courier', 10,), background="#e0e0e0")
        self.stats_tree.tag_configure('separator', font=('Courier', 10), background='#f0f0f0')
        self.stats_tree.tag_configure('normal', font=('Courier', 10))
        
        stats_scroll = ttk.Scrollbar(stats_table_frame, orient="vertical", command=self.stats_tree.yview)
        self.stats_tree.configure(yscrollcommand=stats_scroll.set)
        
        self.stats_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        stats_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Статус бар внизу
        status_bar = ttk.Frame(main_frame, relief=tk.SUNKEN, padding=(5, 2))
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.status_var = tk.StringVar(value="Готов к работе. Выберите файлы с данными.")
        status_label = ttk.Label(status_bar, textvariable=self.status_var, font=('Arial', 9))
        status_label.pack(side=tk.LEFT)
        
        self.cache_indicator_var = tk.StringVar(value="📁 Кэш не загружен")
        cache_label = ttk.Label(status_bar, textvariable=self.cache_indicator_var, font=('Arial', 9))
        cache_label.pack(side=tk.RIGHT, padx=10)

        self.buy_hour_var.trace_add('write', lambda *args: self._update_info_panel())
        self.buy_minute_var.trace_add('write', lambda *args: self._update_info_panel())
        self.sell_hour_var.trace_add('write', lambda *args: self._update_info_panel())
        self.sell_minute_var.trace_add('write', lambda *args: self._update_info_panel())
        self.dte_var.trace_add('write', lambda *args: self._update_info_panel())
        self.nights_var.trace_add('write', lambda *args: self._update_info_panel())
        self.neutral_param_var.trace_add('write', lambda *args: self._update_info_panel())
        self.delta_var.trace_add('write', lambda *args: self._update_info_panel())
        self.tolerance_var.trace_add('write', lambda *args: self._update_info_panel())
        self.max_time_diff_var.trace_add('write', lambda *args: self._update_info_panel())
        self.bidask_mode_var.trace_add('write', lambda *args: self._update_info_panel())
        self.fee_mode_var.trace_add('write', lambda *args: self._update_info_panel())
        self.delta_enabled_var.trace_add('write', lambda *args: self._update_info_panel())
        self.expiry_time_var.trace_add('write', lambda *args: self._update_info_panel())
    
    def _generate_info_text(self) -> str:
        """Генерирует текстовое описание выбранных параметров для верификации"""
        lines = []
        lines.append("=" * 60)
        lines.append("📊 ПАРАМЕТРЫ АНАЛИЗА ДЛЯ ВЕРИФИКАЦИИ")
        lines.append("=" * 60)
        
        # Получаем параметры
        buy_time = self.buy_time_var.get()
        buy_hour = int(self.buy_hour_var.get())
        buy_minute = int(self.buy_minute_var.get())
        
        sell_time = self.sell_time_var.get()
        sell_hour = int(self.sell_hour_var.get())
        sell_minute = int(self.sell_minute_var.get())
        
        nights = int(self.nights_var.get())
        dte = int(self.dte_var.get())
        expiry_time = self.expiry_time_var.get()
        expiry_hour, expiry_minute = map(int, expiry_time.split(':'))
        
        selected_weekdays = sorted(self.get_weekdays())  # Сортируем для порядка
        
        weekday_names = {0: 'Понедельник', 1: 'Вторник', 2: 'Среда', 
                        3: 'Четверг', 4: 'Пятница', 5: 'Суббота', 6: 'Воскресенье'}
        weekday_short = {0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'}
        
        # ========================================================================
        # 1. ПОКУПКА
        # ========================================================================
        days_list = [weekday_names[d] for d in selected_weekdays]
        days_short = [weekday_short[d] for d in selected_weekdays]
        
        lines.append(f"\n🟢 ПОКУПКА:")
        lines.append(f"   • Время: {buy_time}")
        lines.append(f"   • Дни недели: {', '.join(days_list)} ({', '.join(days_short)})")
        
        # ========================================================================
        # 2. ПРОДАЖА (с соответствием дням покупки)
        # ========================================================================
        lines.append(f"\n🔴 ПРОДАЖА:")
        lines.append(f"   • Время: {sell_time}")
        lines.append(f"   • Переносов через ночь (Nights): {nights}")
        lines.append(f"   • Соответствие дней:")
        
        for buy_wd in selected_weekdays:
            sell_wd = (buy_wd + nights) % 7
            lines.append(f"      - {weekday_names[buy_wd]} → {weekday_names[sell_wd]} ({weekday_short[sell_wd]})")
        
        # ========================================================================
        # 3. ЭКСПИРАЦИЯ - РАСЧЕТ ДНЕЙ НЕДЕЛИ
        # ========================================================================
        def get_expiry_weekday(buy_weekday: int, buy_hour: int, buy_minute: int, 
                            dte: int, expiry_hour: int, expiry_minute: int) -> int:
            """Возвращает день недели экспирации"""
            buy_minutes = buy_hour * 60 + buy_minute
            expiry_minutes = expiry_hour * 60 + expiry_minute
            
            if buy_minutes <= expiry_minutes:
                if dte == 0:
                    days_offset = 0
                else:
                    days_offset = dte
            else:
                if dte == 0:
                    days_offset = 1
                else:
                    days_offset = dte + 1
            
            return (buy_weekday + days_offset) % 7
        
        lines.append(f"\n⏰ ЭКСПИРАЦИЯ:")
        lines.append(f"   • Время экспирации: {expiry_time} UTC")
        lines.append(f"   • DTE (полных дней до экспирации): {dte}")
        lines.append(f"   • Соответствие дней:")
        
        expiry_map = {}
        for buy_wd in selected_weekdays:
            expiry_wd = get_expiry_weekday(buy_wd, buy_hour, buy_minute, 
                                            dte, expiry_hour, expiry_minute)
            expiry_map[buy_wd] = expiry_wd
            lines.append(f"      - {weekday_names[buy_wd]} → {weekday_names[expiry_wd]} ({weekday_short[expiry_wd]})")
        
        # ========================================================================
        # 4. ВРЕМЯ ДО ЭКСПИРАЦИИ
        # ========================================================================
        def calculate_time_to_expiry(hour: int, minute: int, dte: int, 
                                    expiry_hour: int, expiry_minute: int) -> dict:
            current_minutes = hour * 60 + minute
            expiry_minutes = expiry_hour * 60 + expiry_minute
            
            if current_minutes <= expiry_minutes:
                base_minutes = expiry_minutes - current_minutes
            else:
                base_minutes = (24 * 60 - current_minutes) + expiry_minutes
            
            total_minutes = base_minutes + dte * 24 * 60
            
            total_hours = total_minutes / 60
            days = int(total_minutes // (24 * 60))
            hours = int((total_minutes % (24 * 60)) // 60)
            minutes = int(total_minutes % 60)
            
            return {
                'total_hours': total_hours,
                'total_minutes': total_minutes,
                'days': days,
                'hours': hours,
                'minutes': minutes,
                'dte_actual': int(total_hours // 24)
            }
        
        # Расчет от покупки
        buy_time_info = calculate_time_to_expiry(buy_hour, buy_minute, dte, expiry_hour, expiry_minute)
        
        lines.append(f"\n⏱️ ВРЕМЯ ДО ЭКСПИРАЦИИ:")
        lines.append(f"\n   📍 ОТ ПОКУПКИ ({buy_time}):")
        lines.append(f"      • {buy_time_info['days']} дн {buy_time_info['hours']} ч {buy_time_info['minutes']} мин")
        lines.append(f"      • Всего: {buy_time_info['total_hours']:.1f} часов")
        
        if buy_time_info['dte_actual'] != dte:
            lines.append(f"      • ⚠️ ВНИМАНИЕ: Фактический DTE = {buy_time_info['dte_actual']}, указан DTE = {dte}")
        
        # Расчет от продажи
        buy_minutes_total = buy_hour * 60 + buy_minute
        sell_minutes_total = sell_hour * 60 + sell_minute
        
        if sell_minutes_total > buy_minutes_total:
            minutes_to_sell = (nights * 24 * 60) + (sell_minutes_total - buy_minutes_total)
        else:
            minutes_to_sell = (nights * 24 * 60) + (24 * 60 - buy_minutes_total + sell_minutes_total)
        
        minutes_to_expiry_from_sell = max(0, buy_time_info['total_minutes'] - minutes_to_sell)
        
        sell_days = int(minutes_to_expiry_from_sell // (24 * 60))
        sell_hours = int((minutes_to_expiry_from_sell % (24 * 60)) // 60)
        sell_minutes = int(minutes_to_expiry_from_sell % 60)
        sell_total_hours = minutes_to_expiry_from_sell / 60
        sell_dte_actual = int(sell_total_hours // 24)
        
        lines.append(f"\n   📍 ОТ ПРОДАЖИ ({sell_time}):")
        if minutes_to_expiry_from_sell > 0:
            lines.append(f"      • {sell_days} дн {sell_hours} ч {sell_minutes} мин")
            lines.append(f"      • Всего: {sell_total_hours:.1f} часов")
            lines.append(f"      • DTE на момент продажи: {sell_dte_actual}")
        else:
            lines.append(f"      • ❌ Экспирация уже наступила")
        
        # Время удержания позиции
        hold_days = int(minutes_to_sell // (24 * 60))
        hold_hours = int((minutes_to_sell % (24 * 60)) // 60)
        hold_minutes = int(minutes_to_sell % 60)
        hold_total_hours = minutes_to_sell / 60
        
        lines.append(f"\n   📍 УДЕРЖАНИЕ ПОЗИЦИИ:")
        lines.append(f"      • {hold_days} дн {hold_hours} ч {hold_minutes} мин")
        lines.append(f"      • Всего: {hold_total_hours:.1f} часов")
        
        # ========================================================================
        # 5. СВОДНАЯ ТАБЛИЦА СООТВЕТСТВИЯ
        # ========================================================================
        lines.append(f"\n📋 СВОДНАЯ ТАБЛИЦА СООТВЕТСТВИЯ:")
        lines.append(f"   {'День покупки':<18} → {'День продажи':<18} → {'День экспирации':<18}")
        lines.append(f"   {'-' * 18}   {'-' * 18}   {'-' * 18}")
        
        for buy_wd in selected_weekdays:
            sell_wd = (buy_wd + nights) % 7
            expiry_wd = expiry_map[buy_wd]
            lines.append(f"   {weekday_names[buy_wd]:<18} → {weekday_names[sell_wd]:<18} → {weekday_names[expiry_wd]:<18}")
        
        # ========================================================================
        # 6. ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ
        # ========================================================================
        lines.append(f"\n📈 ДОПОЛНИТЕЛЬНО:")
        
        if self.delta_enabled_var.get():
            target_delta = self.delta_var.get()
            tolerance = self.tolerance_var.get()
            lines.append(f"   • Целевая дельта: {target_delta} ± {tolerance}")
        else:
            lines.append(f"   • Целевая дельта: все дельты")
        
        neutral_param = self.neutral_param_var.get()
        neutral_names = {'delta': 'дельта', 'gamma': 'гамма', 'vega': 'вега', 'theta': 'тета', 'iv': 'IV'}
        lines.append(f"   • Нейтрализация по: {neutral_names.get(neutral_param, neutral_param)}")
        
        if self.bidask_mode_var.get():
            lines.append(f"   • Режим: Bid/Ask (покупка по Ask, продажа по Bid)")
        else:
            lines.append(f"   • Режим: Mark Price")
        
        if self.fee_mode_var.get():
            lines.append(f"   • Комиссия: 7% (включена)")
        else:
            lines.append(f"   • Комиссия: выключена")
        
        max_time_diff = self.max_time_diff_var.get()
        lines.append(f"   • Макс. отклонение по времени: ±{max_time_diff} мин")
        
        lines.append("\n" + "=" * 60)
        lines.append("✅ Если параметры верны, запускайте анализ")
        lines.append("=" * 60)
        
        return "\n".join(lines)

    def _update_info_panel(self):
        """Обновляет текстовую информацию в левой панели"""
        try:
            if hasattr(self, 'info_text_widget') and self.info_text_widget.winfo_exists():
                info_text = self._generate_info_text()
                self.info_text_widget.config(state='normal')
                self.info_text_widget.delete(1.0, tk.END)
                self.info_text_widget.insert(1.0, info_text)
                self.info_text_widget.config(state='disabled')
        except Exception as e:
            # Игнорируем ошибки при инициализации
            pass

    def _toggle_delta(self):
        if self.delta_enabled_var.get():
            self.delta_var.set("0.1")
        else:
            self.delta_var.set("")
        self._update_info_panel()

    def _update_buy_time(self):
        try:
            hour = int(self.buy_hour_var.get())
            minute = int(self.buy_minute_var.get())
            hour = max(0, min(23, hour))
            minute = max(0, min(59, minute))
            # Округляем минуты вниз до 10
            minute = (minute // 10) * 10
            self.buy_hour_var.set(f"{hour:02d}")
            self.buy_minute_var.set(f"{minute:02d}")
            self.buy_time_var.set(f"{hour:02d}:{minute:02d}")
            self._update_info_panel()  # Уже есть, но убедитесь что вызов есть
        except ValueError:
            pass

    def _update_sell_time(self):
        try:
            hour = int(self.sell_hour_var.get())
            minute = int(self.sell_minute_var.get())
            hour = max(0, min(23, hour))
            minute = max(0, min(59, minute))
            # Округляем минуты вниз до 10
            minute = (minute // 10) * 10
            self.sell_hour_var.set(f"{hour:02d}")
            self.sell_minute_var.set(f"{minute:02d}")
            self.sell_time_var.set(f"{hour:02d}:{minute:02d}")
            self._update_info_panel()  # Уже есть, но убедитесь что вызов есть
        except ValueError:
            pass  

    def _add_fullscreen_buttons(self):
        fullscreen_frame = ttk.Frame(self.button_frame)
        fullscreen_frame.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(fullscreen_frame, text="Полноэкранные графики:", font=('Arial', 10)).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(fullscreen_frame, text="1", 
                command=lambda: self._show_fullscreen_plot(0, "Динамика стоимости"), width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(fullscreen_frame, text="2", 
                command=lambda: self._show_fullscreen_plot(1, "Динамика средней стоимости"), width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(fullscreen_frame, text="3", 
                command=lambda: self._show_fullscreen_plot(2, "Дни недели (стренглы)"), width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(fullscreen_frame, text="4", 
                command=lambda: self._show_fullscreen_plot(3, "Дни недели (по дням)"), width=3).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(fullscreen_frame, text="Δ", 
                command=lambda: self._show_fullscreen_plot(4, "P&L от дельты"), width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(fullscreen_frame, text="Γ", 
                command=lambda: self._show_fullscreen_plot(5, "P&L от гаммы"), width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(fullscreen_frame, text="ν", 
                command=lambda: self._show_fullscreen_plot(6, "P&L от веги"), width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(fullscreen_frame, text="θ", 
                command=lambda: self._show_fullscreen_plot(7, "P&L от теты"), width=3).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(fullscreen_frame, text="IV", 
                command=lambda: self._show_fullscreen_plot(8, "P&L от IV "), width=4).pack(side=tk.LEFT, padx=2)
        ttk.Button(fullscreen_frame, text="index IV", 
                command=lambda: self._show_fullscreen_plot(9, "Динамика ATM IV"), width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(fullscreen_frame, text="atm IV", 
                command=lambda: self._show_fullscreen_plot(10, "P&L от ATM IV"), width=6).pack(side=tk.LEFT, padx=2)
        ttk.Button(fullscreen_frame, text="all IV", 
                command=lambda: self._show_fullscreen_plot(11, "Сравнение IV"), width=6).pack(side=tk.LEFT, padx=2)
        
        export_frame = ttk.Frame(self.button_frame)
        export_frame.pack(side=tk.RIGHT, padx=5)
        
        ttk.Label(export_frame, text="Экспорт:", font=('Arial', 10)).pack(side=tk.LEFT, padx=5)
        
        self.copy_graph_var = tk.StringVar()
        self.copy_combo = ttk.Combobox(export_frame, textvariable=self.copy_graph_var,
                                    values=["Динамика стоимости", "Динамика средней стоимости", "Дни недели (по стренглам)", 
                                            "Дни недели (по дням)", "P&L от дельты", "P&L от гаммы", "P&L от веги", "P&L от теты",
                                            "P&L от IV", "Динамика ATM IV", "P&L от ATM IV", 
                                            "Сравнение IV"],
                                    state="readonly", width=20)
        self.copy_combo.pack(side=tk.LEFT, padx=5)
        self.copy_combo.set("Выберите график")
        
        ttk.Button(export_frame, text="📋 Копировать", 
                command=self.copy_graph_to_clipboard, width=16).pack(side=tk.LEFT, padx=2)
        ttk.Button(export_frame, text="📄 PDF", 
                command=self.export_to_pdf, width=8).pack(side=tk.LEFT, padx=2)
    
    def _show_fullscreen_plot(self, plot_index: int, title: str):
        if self.results is None or self.results.empty or self.summary is None:
            return
        
        fullscreen_window = tk.Toplevel(self.root)
        fullscreen_window.title(f"Полноэкранный режим - {title}")
        fullscreen_window.state('zoomed')
        
        fig = Figure(figsize=(16, 9), dpi=100)
        self._render_fullscreen_plot(fig, plot_index, title)
        
        canvas = FigureCanvasTkAgg(fig, master=fullscreen_window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        toolbar_frame = ttk.Frame(fullscreen_window)
        toolbar_frame.pack(fill=tk.X)
        toolbar = NavigationToolbar2Tk(canvas, toolbar_frame)
        toolbar.update()
        
        ttk.Button(fullscreen_window, text="Закрыть", command=fullscreen_window.destroy).pack(pady=5)
    
    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._update_current_page()
            self.page_label.config(text=f"Страница {self.current_page + 1}/{self.total_pages}")

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._update_current_page()
            self.page_label.config(text=f"Страница {self.current_page + 1}/{self.total_pages}")

    def _update_current_page(self):
        """Обновляет отображение текущей страницы"""
        self.fig.clear()
        
        if self.summary is None or self.summary.empty:
            # Если нет результатов, показываем сообщение
            ax = self.fig.add_subplot(111)
            ax.text(0.5, 0.5, 'Нет данных для отображения\nЗапустите анализ', 
                    ha='center', va='center', transform=ax.transAxes,
                    fontsize=16, fontweight='bold')
            ax.set_title("Нет данных", fontsize=14, fontweight='bold')
            self.fig.suptitle("Анализатор стренглов", fontsize=14, fontweight='bold', y=0.98)
        else:
            # Отображаем соответствующую страницу
            if self.current_page == 0:
                self._create_page_1(self.fig)
            elif self.current_page == 1:
                self._create_page_2(self.fig)
            elif self.current_page == 2:
                self._create_page_3(self.fig)
        
        self.canvas.draw_idle()

    def prepare_cache(self):
        """Подготавливает новые кэши из файлов - отдельный для каждого токена"""
        if not self.data_files:
            messagebox.showerror("Ошибка", "Сначала выберите файлы с данными")
            return
        
        expiry_time = self.expiry_time_var.get().strip()
        if not expiry_time:
            messagebox.showerror("Ошибка", "Укажите время экспирации")
            return
        
        # Создаем окно прогресса
        progress_window = tk.Toplevel(self.root)
        progress_window.title("Подготовка кэшей")
        progress_window.geometry("600x650")
        progress_window.transient(self.root)
        progress_window.resizable(False, False)
        progress_window.grab_set()
        
        # Заголовок
        ttk.Label(progress_window, text="Подготовка кэшей из файлов", 
                font=('Arial', 14, 'bold')).pack(pady=10)
        
        # Основной фрейм
        main_frame = ttk.Frame(progress_window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Информация о файлах
        files_info = f"Файлов для обработки: {len(self.data_files)}"
        ttk.Label(main_frame, text=files_info, font=('Arial', 10)).pack(pady=5)
        
        # ===== ЭТАП 1: Загрузка файлов =====
        stage1_frame = ttk.LabelFrame(main_frame, text="Этап 1: Загрузка файлов", padding="5")
        stage1_frame.pack(fill=tk.X, pady=5)
        
        stage1_status = ttk.Label(stage1_frame, text="⏳ Ожидание...", font=('Arial', 9))
        stage1_status.pack(anchor='w')
        
        stage1_progress = ttk.Progressbar(stage1_frame, mode='indeterminate', length=500)
        stage1_progress.pack(fill=tk.X, pady=5)
        
        # ===== ЭТАП 2: Создание объектов опционов =====
        stage2_frame = ttk.LabelFrame(main_frame, text="Этап 2: Создание объектов опционов", padding="5")
        stage2_frame.pack(fill=tk.X, pady=5)
        
        stage2_status = ttk.Label(stage2_frame, text="⏳ Ожидание...", font=('Arial', 9))
        stage2_status.pack(anchor='w')
        
        stage2_progress = ttk.Progressbar(stage2_frame, mode='indeterminate', length=500)
        stage2_progress.pack(fill=tk.X, pady=5)
        
        stage2_label = ttk.Label(stage2_frame, text="0/0 опционов", font=('Arial', 8))
        stage2_label.pack()
        
        # ===== ЭТАП 3: Создание индексных опционов =====
        stage3_frame = ttk.LabelFrame(main_frame, text="Этап 3: Создание индексных опционов", padding="5")
        stage3_frame.pack(fill=tk.X, pady=5)
        
        stage3_status = ttk.Label(stage3_frame, text="⏳ Ожидание...", font=('Arial', 9))
        stage3_status.pack(anchor='w')
        
        stage3_progress = ttk.Progressbar(stage3_frame, mode='indeterminate', length=500)
        stage3_progress.pack(fill=tk.X, pady=5)
        
        # ===== ЭТАП 4: Интерполяция =====
        stage4_frame = ttk.LabelFrame(main_frame, text="Этап 4: Интерполяция данных", padding="5")
        stage4_frame.pack(fill=tk.X, pady=5)
        
        stage4_status = ttk.Label(stage4_frame, text="⏳ Ожидание...", font=('Arial', 9))
        stage4_status.pack(anchor='w')
        
        stage4_progress = ttk.Progressbar(stage4_frame, mode='determinate', length=500)
        stage4_progress.pack(fill=tk.X, pady=5)
        
        stage4_label = ttk.Label(stage4_frame, text="0/0 опционов", font=('Arial', 8))
        stage4_label.pack()
        
        # ===== ЭТАП 5: Сохранение =====
        stage5_frame = ttk.LabelFrame(main_frame, text="Этап 5: Сохранение в кэш", padding="5")
        stage5_frame.pack(fill=tk.X, pady=5)
        
        stage5_status = ttk.Label(stage5_frame, text="⏳ Ожидание...", font=('Arial', 9))
        stage5_status.pack(anchor='w')
        
        stage5_progress = ttk.Progressbar(stage5_frame, mode='indeterminate', length=500)
        stage5_progress.pack(fill=tk.X, pady=5)
        
        # Кнопки
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        cancel_button = ttk.Button(button_frame, text="Отмена", 
                                command=lambda: self._cancel_cache_building(progress_window))
        cancel_button.pack(side=tk.RIGHT, padx=5)
        
        close_button = ttk.Button(button_frame, text="Готово", 
                                command=progress_window.destroy, state='disabled')
        close_button.pack(side=tk.RIGHT, padx=5)
        
        # Флаг для отслеживания отмены
        self.cache_building_cancelled = False
        
        # Используем queue для передачи сообщений
        update_queue = queue.Queue()
        
        def process_updates():
            """Обрабатывает сообщения из очереди и обновляет GUI"""
            try:
                while True:
                    data = update_queue.get_nowait()
                    stage = data.get('stage')
                    
                    if stage == 'stage1_start':
                        stage1_progress.start(10)
                        stage1_status.config(text="🔄 Загрузка файлов...")
                    
                    elif stage == 'stage1_complete':
                        stage1_progress.stop()
                        stage1_progress['value'] = 100
                        stage1_status.config(text="✅ Загрузка завершена")
                        
                        stage2_progress.start(10)
                        stage2_status.config(text="🔄 Создание опционов...")
                    
                    elif stage == 'stage2_progress':
                        stage2_progress.stop()
                        stage2_progress.config(mode='determinate')
                        stage2_progress['value'] = data['current']
                        stage2_progress['maximum'] = data['total']
                        stage2_label.config(text=f"{data['current']}/{data['total']} опционов")
                        stage2_status.config(text=f"🔄 Создание опционов... ({data['current']}/{data['total']})")
                    
                    elif stage == 'stage2_complete':
                        stage2_progress.stop()
                        stage2_progress['value'] = data['total']
                        stage2_status.config(text=f"✅ Создано {data['total']} опционов")
                        
                        stage3_progress.start(10)
                        stage3_status.config(text="🔄 Создание индексных опционов...")
                    
                    elif stage == 'stage3_complete':
                        stage3_progress.stop()
                        stage3_progress['value'] = 100
                        stage3_status.config(text=f"✅ Создано {data['total']} индексных опционов")
                        
                        stage4_progress['value'] = 0
                        stage4_progress['maximum'] = data['total_options']
                        stage4_status.config(text="🔄 Интерполяция...")
                        stage4_label.config(text=f"0/{data['total_options']} опционов")
                    
                    elif stage == 'stage4_progress':
                        stage4_progress['value'] = data['current']
                        stage4_label.config(text=f"{data['current']}/{data['total']} опционов")
                        if data.get('interpolated'):
                            stage4_status.config(text=f"🔄 Интерполяция... +{data['interpolated']} слотов")
                        else:
                            stage4_status.config(text=f"🔄 Интерполяция... ({data['current']}/{data['total']})")
                    
                    elif stage == 'stage4_complete':
                        stage4_progress.stop()
                        stage4_progress['value'] = data['total']
                        stage4_status.config(text=f"✅ Интерполяция завершена (+{data['interpolated']} слотов)")
                        
                        stage5_progress.start(10)
                        stage5_status.config(text="🔄 Сохранение в кэш...")
                    
                    elif stage == 'stage5_complete':
                        stage5_progress.stop()
                        stage5_progress['value'] = 100
                        
                        # Поддерживаем как один cache_id, так и список cache_ids
                        if 'cache_ids' in data:
                            # Несколько кэшей
                            count = data.get('count', len(data['cache_ids']))
                            stage5_status.config(text=f"✅ Сохранено {count} кэшей")
                            # Убираем stage5_label - его нет
                        elif 'cache_id' in data:
                            # Один кэш (для обратной совместимости)
                            stage5_status.config(text=f"✅ Кэш сохранён: {data['cache_id'][:30]}...")
                        else:
                            stage5_status.config(text="✅ Кэши сохранены")
                        
                        # Активируем кнопку "Готово"
                        close_button.config(state='normal')
                        cancel_button.config(state='disabled')
                        
                        # Обновляем информацию в главном окне
                        self.root.after(0, lambda: self.status_var.set("✅ Кэши готовы к использованию"))
                    
                    elif stage == 'asset_start':
                        # Отображаем прогресс по активам
                        current = data.get('current', 0)
                        total = data.get('total', 0)
                        asset = data.get('asset', '')
                        stage2_status.config(text=f"🔄 Обработка актива {current}/{total}: {asset}")
                        stage2_label.config(text=f"Актив: {asset}")
                        stage2_progress['value'] = 0
                    
                    elif stage == 'asset_complete':
                        asset = data.get('asset', '')
                        stage2_status.config(text=f"✅ Актив {asset} обработан")
                    
                    elif stage == 'error':
                        stage1_progress.stop()
                        stage2_progress.stop()
                        stage3_progress.stop()
                        stage4_progress.stop()
                        stage5_progress.stop()
                        
                        stage5_status.config(text=f"❌ Ошибка: {data['error']}")
                        cancel_button.config(state='normal')
                        close_button.config(state='normal')
                        
                        messagebox.showerror("Ошибка", f"Не удалось создать кэш:\n{data['error']}")
                    
                    progress_window.update()
                    
            except queue.Empty:
                pass
            finally:
                if progress_window.winfo_exists():
                    progress_window.after(100, process_updates)

        # Запускаем обработчик очереди
        progress_window.after(100, process_updates)
        
        # Функция для выполнения в отдельном потоке
        def build_cache_thread():
            try:
                builder = CacheBuilder()
                builder.set_queue(update_queue)
                
                # Получаем список кэшей (по одному на токен)
                caches = builder.build_from_files(self.data_files, expiry_time)
                
                # Сохраняем каждый кэш
                cache_ids = []
                for cache in caches:
                    cache_id = self.cache_manager.save_cache(cache)
                    cache_ids.append(cache_id)
                    print(f"Сохранён кэш: {cache_id}")
                
                update_queue.put({
                    'stage': 'stage5_complete',
                    'cache_ids': cache_ids,
                    'count': len(cache_ids)
                })
                
                # Загружаем первый кэш (или показываем выбор)
                if caches:
                    self.root.after(0, lambda: self.set_cache(caches[0]))
                    if len(caches) > 1:
                        self.root.after(0, lambda: messagebox.showinfo(
                            "Кэши созданы", 
                            f"Создано {len(caches)} кэшей для разных токенов.\n"
                            f"Загружен кэш для {caches[0].metadata.get('main_asset', 'UNKNOWN')}.\n"
                            f"Другие кэши доступны в менеджере кэша."
                        ))
                
            except Exception as e:
                import traceback
                traceback.print_exc()
                update_queue.put({'stage': 'error', 'error': str(e)})

        # Запускаем поток
        thread = threading.Thread(target=build_cache_thread)
        thread.daemon = True
        thread.start()


    def _cancel_cache_building(self, progress_window):
        """Отменяет построение кэша"""
        self.cache_building_cancelled = True
        progress_window.destroy()
        self.status_var.set("❌ Построение кэша отменено")

    def open_cache_manager(self):
        CacheManagerDialog(self.root, self.cache_manager, self)
    
    def _load_page_from_cache(self):
        """Загружает страницу из кэша (теперь вызывает _update_current_page)"""
        self._update_current_page()
    
    def _copy_axes(self, source_ax, target_ax):
        for line in source_ax.get_lines():
            target_ax.plot(line.get_xdata(), line.get_ydata(), 
                          color=line.get_color(), linestyle=line.get_linestyle(),
                          linewidth=line.get_linewidth(), alpha=line.get_alpha(),
                          label=line.get_label())
        
        for patch in source_ax.patches:
            if hasattr(patch, 'get_width') and hasattr(patch, 'get_height'):
                target_ax.add_patch(plt.Rectangle(
                    (patch.get_x(), patch.get_y()),
                    patch.get_width(), patch.get_height(),
                    facecolor=patch.get_facecolor(), edgecolor=patch.get_edgecolor(),
                    alpha=patch.get_alpha(), linewidth=patch.get_linewidth()
                ))
        
        for collection in source_ax.collections:
            if hasattr(collection, 'get_offsets') and len(collection.get_offsets()) > 0:
                offsets = collection.get_offsets()
                sizes = collection.get_sizes()
                if len(sizes) == 1:
                    target_ax.scatter(offsets[:, 0], offsets[:, 1],
                                     c=collection.get_facecolor(),
                                     edgecolors=collection.get_edgecolor(),
                                     s=sizes[0], alpha=collection.get_alpha(),
                                     label=collection.get_label())
        
        target_ax.set_title(source_ax.get_title(), fontweight='bold')
        target_ax.set_xlabel(source_ax.get_xlabel())
        target_ax.set_ylabel(source_ax.get_ylabel())
        target_ax.set_xlim(source_ax.get_xlim())
        target_ax.set_ylim(source_ax.get_ylim())
        target_ax.grid(source_ax.get_visible())
        
        if source_ax.get_legend():
            legend = source_ax.get_legend()
            target_ax.legend(loc=legend._loc, fontsize=8)
    
    def select_files(self):
        files = filedialog.askopenfilenames(
            title="Выберите CSV файлы с данными опционов",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if files:
            for file in files:
                if file not in self.data_files:
                    self.data_files.append(file)
                    self.files_listbox.insert(tk.END, os.path.basename(file))
            
            self.files_count_var.set(f"Выбрано файлов: {len(self.data_files)}")
            self.status_var.set(f"Загружено {len(self.data_files)} файлов")
    
    def remove_selected_file(self):
        selection = self.files_listbox.curselection()
        if selection:
            index = selection[0]
            self.files_listbox.delete(index)
            del self.data_files[index]
            self.files_count_var.set(f"Выбрано файлов: {len(self.data_files)}")
            self.status_var.set(f"Файл удален. Осталось: {len(self.data_files)}")
    
    def clear_files(self):
        self.data_files = []
        self.files_listbox.delete(0, tk.END)
        self.files_count_var.set("Файлов не выбрано")
        self.status_var.set("Список файлов очищен")
    
    def get_weekdays(self) -> List[int]:
        days = [day for day, var in self.weekday_vars.items() if var.get()]
        # Не вызываем здесь _update_info_panel, чтобы избежать рекурсии
        return days
    
    def set_cache(self, cache: OptimizedCache):
        self.cache = cache
        self.analyzer = None
        self.results = None
        self.summary = None
        
        # Определяем основной актив для отображения
        base_assets = cache.metadata.get('base_assets', [])
        main_asset = cache.metadata.get('main_asset', 'UNKNOWN')
        
        if base_assets:
            assets_str = ', '.join(base_assets)
            cache_info = f"📁 {main_asset}: {cache.metadata.get('num_options', 0)} опционов"
            if len(base_assets) > 1:
                cache_info += f" (всего активов: {assets_str})"
        else:
            cache_info = f"📁 Кэш: {cache.metadata.get('num_options', 0)} опционов"
        
        self.cache_indicator_var.set(cache_info)
        self.left_cache_var.set(cache_info)
        self.status_var.set(f"Кэш загружен: {main_asset}")
        self.left_status_var.set(f"✅ Кэш загружен: {main_asset}")
        
        # Сбрасываем на первую страницу
        self.current_page = 0
        self.page_label.config(text=f"Страница 1/{self.total_pages}")
        self.fig.clear()
        self._show_no_data_message()
        self.canvas.draw()
        self._update_info_panel()

    def _show_no_data_message(self):
        """Показывает сообщение об отсутствии данных"""
        ax = self.fig.add_subplot(111)
        ax.text(0.5, 0.5, 'Нет данных для отображения\nЗапустите анализ', 
                ha='center', va='center', transform=ax.transAxes,
                fontsize=16, fontweight='bold')
        ax.set_title("Нет данных", fontsize=14, fontweight='bold')
        self.fig.suptitle("Анализатор стренглов", fontsize=14, fontweight='bold', y=0.98)
    
    def run_analysis(self, silent=False):
        if self.cache is None:
            if not silent:
                messagebox.showerror("Ошибка", "Сначала загрузите кэш")
            return
        
        # Очищаем предыдущие результаты
        self.results = None
        self.summary = None
        self.page_figures = {}
        self.fig.clear()
        self.canvas.draw()

        # Сбрасываем на первую страницу
        self.current_page = 0
        self.page_label.config(text=f"Страница 1/{self.total_pages}")
        
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)
        
        try:
            # Получаем параметры
            self._update_buy_time()
            buy_time = self.buy_time_var.get().strip()
            if not buy_time:
                if not silent:
                    messagebox.showerror("Ошибка", "Укажите время покупки")
                return
            
            sell_time = self.sell_time_var.get().strip()
            if not sell_time:
                if not silent:
                    messagebox.showerror("Ошибка", "Укажите время продажи")
                return
            
            try:
                max_time_diff = int(self.max_time_diff_var.get())
                if max_time_diff <= 0:
                    if not silent:
                        messagebox.showerror("Ошибка", "Макс. отклонение должно быть положительным")
                    return
            except ValueError:
                if not silent:
                    messagebox.showerror("Ошибка", "Некорректное значение макс. отклонения")
                return
            
            try:
                dte = int(self.dte_var.get())
            except ValueError:
                if not silent:
                    messagebox.showerror("Ошибка", "Некорректное значение DTE")
                return
            
            try:
                nights = int(self.nights_var.get())
                if nights < 0:
                    if not silent:
                        messagebox.showerror("Ошибка", "Nights не может быть отрицательным")
                    return
            except ValueError:
                if not silent:
                    messagebox.showerror("Ошибка", "Некорректное значение Nights")
                return
            
            target_delta = None
            if self.delta_enabled_var.get():
                try:
                    target_delta = float(self.delta_var.get())
                    if target_delta <= 0 or target_delta >= 1:
                        if not silent:
                            messagebox.showerror("Ошибка", "Дельта должна быть между 0 и 1")
                        return
                except ValueError:
                    if not silent:
                        messagebox.showerror("Ошибка", "Некорректное значение дельты")
                    return
            
            try:
                tolerance = float(self.tolerance_var.get())
                if tolerance <= 0:
                    if not silent:
                        messagebox.showerror("Ошибка", "Допуск должен быть положительным числом")
                    return
            except ValueError:
                if not silent:
                    messagebox.showerror("Ошибка", "Некорректное значение допуска")
                return
            
            weekdays = self.get_weekdays()
            if not weekdays:
                if not silent:
                    messagebox.showerror("Ошибка", "Выберите хотя бы один день недели")
                return
            
            bidask_mode = self.bidask_mode_var.get()
            fee_mode = self.fee_mode_var.get()
            neutral_param = self.neutral_param_var.get()
            
            # СОЗДАЕМ НОВЫЙ АНАЛИЗАТОР КАЖДЫЙ РАЗ
            self.analyzer = StrangleAnalyzer(self.cache)
            self.analyzer.bidask_mode = bidask_mode
            self.analyzer.fee_mode = fee_mode
            self.analyzer.neutral_param = neutral_param
            self.analyzer.max_time_diff = max_time_diff
            
            self.analyzer.set_analysis_params(
                buy_time=buy_time,
                sell_time=sell_time,
                dte=dte,
                nights=nights,
                target_delta=target_delta,
                delta_tolerance=tolerance,
                weekdays=weekdays
            )
            
            self.status_var.set("Поиск стренглов...")
            self.left_status_var.set("Поиск стренглов...")
            self.root.update()
            
            # Запускаем анализ
            results = self.analyzer.analyze()
            
            if results is None or results.empty:
                msg = (f"Стренглы не найдены для параметров:\n"
                    f"• DTE={dte}\n• Nights={nights}\n"
                    f"• Время покупки={buy_time}\n"
                    f"• Время продажи={sell_time if sell_time else 'экспирация'}\n"
                    f"• Дельта={target_delta if target_delta else 'все'} ±{tolerance}")
                
                if not silent:
                    messagebox.showwarning("Стренглы не найдены", msg)
                
                self.status_var.set("❌ Стренглы не найдены")
                self.left_status_var.set("❌ Стренглы не найдены")
                return
            
            self.results = results
            self.summary = self.analyzer.get_summary()
            
            self.status_var.set("Генерация графиков...")
            self.root.update()
            
            self._generate_all_pages()
            
            if not silent:
                self.current_page = 0
                self._update_current_page()
                self.update_table()
                self.update_stats_table()
                
                profit_count = (self.summary['final_pnl'] > 0).sum() if self.summary is not None else 0
                profit_pct = (profit_count / len(self.summary)) * 100 if self.summary is not None else 0
                avg_pnl = self.summary['final_pnl'].mean() if self.summary is not None else 0
                
                status_msg = (f"✅ Анализ завершен! Найдено стренглов: {len(self.summary) if self.summary is not None else 0} | "
                            f"Прибыльных: {profit_count} ({profit_pct:.1f}%) | "
                            f"Средний P&L: ${avg_pnl:.2f}")
                self.status_var.set(status_msg)
                self.left_status_var.set(status_msg)

            self._update_info_panel()
            
        except Exception as e:
            if not silent:
                messagebox.showerror("Ошибка", f"Ошибка при выполнении анализа:\n{str(e)}")
                self.status_var.set(f"❌ Ошибка: {str(e)[:50]}...")
                self.left_status_var.set(f"❌ Ошибка: {str(e)[:50]}...")
            else:
                raise

    def _generate_all_pages(self):
        # Очищаем старые figure
        for fig in self.page_figures.values():
            plt.close(fig)
        self.page_figures = {}
        
        # Создаем страницы как обычно
        fig1 = Figure(figsize=(16, 9), dpi=100)
        self._create_page_1(fig1)
        self.page_figures[0] = fig1
        
        fig2 = Figure(figsize=(16, 9), dpi=100)
        self._create_page_2(fig2)
        self.page_figures[1] = fig2
        
        fig3 = Figure(figsize=(16, 9), dpi=100)
        self._create_page_3(fig3)
        self.page_figures[2] = fig3
    
    def _create_page_1(self, fig):
        """Первая страница"""
        fig.clear()
        
        fig.subplots_adjust(left=0.06, right=0.98, top=0.92, bottom=0.06, hspace=0.25, wspace=0.2)
        
        ax1 = fig.add_subplot(2, 2, 1)
        ax2 = fig.add_subplot(2, 2, 2)
        ax3 = fig.add_subplot(2, 2, 3)
        ax4 = fig.add_subplot(2, 2, 4)
        
        if self.summary is None or self.summary.empty:
            for ax in [ax1, ax2, ax3, ax4]:
                ax.text(0.5, 0.5, 'Нет данных для отображения', 
                    ha='center', va='center', transform=ax.transAxes,
                    fontsize=12, fontweight='bold')
                ax.set_title("Нет данных", fontsize=11, fontweight='bold')
        else:
            self._render_single_plot(ax1, 'dynamic', "Динамика стоимости")
            self._render_single_plot(ax2, 'dynamic_average', "Динамика средней стоимости")
            self._render_single_plot(ax3, 'weekday_strangles', "Дни недели (по стренглам)")
            self._render_single_plot(ax4, 'weekday_unique', "Дни недели (по дням)")
        
        delta_str = f", Δ{self.delta_var.get()}±{self.tolerance_var.get()}" if self.delta_enabled_var.get() else ", все дельты"
        
        neutral_param_text = "дельта"
        if self.analyzer is not None and hasattr(self.analyzer, 'neutral_param'):
            neutral_param_names = {'delta': 'Δ', 'gamma': 'Γ', 'vega': 'ν', 'theta': 'θ', 'iv': 'IV'}
            neutral_param_text = neutral_param_names.get(self.analyzer.neutral_param, self.analyzer.neutral_param)
        
        title = f"Анализ стренглов (DTE={self.dte_var.get()}, покупка={self.buy_time_var.get()}, продажа={self.sell_time_var.get()}{delta_str}, нейтрализация по {neutral_param_text})"
        
        fig.suptitle(title, fontsize=12, fontweight='bold', y=0.98)

    def _create_page_2(self, fig):
        """Вторая страница - греки"""
        fig.clear()
        
        fig.subplots_adjust(left=0.06, right=0.98, top=0.92, bottom=0.06, hspace=0.25, wspace=0.2)
        
        ax1 = fig.add_subplot(2, 2, 1)
        ax2 = fig.add_subplot(2, 2, 2)
        ax3 = fig.add_subplot(2, 2, 3)
        ax4 = fig.add_subplot(2, 2, 4)
        
        # Проверяем, есть ли результаты
        if self.summary is None or self.summary.empty:
            for ax in [ax1, ax2, ax3, ax4]:
                ax.text(0.5, 0.5, 'Нет данных для отображения', 
                    ha='center', va='center', transform=ax.transAxes,
                    fontsize=12, fontweight='bold')
                ax.set_title("Нет данных", fontsize=11, fontweight='bold')
        else:
            self._render_single_plot(ax1, 'delta', "Зависимость P&L от дельты")
            self._render_single_plot(ax2, 'gamma', "Зависимость P&L от гаммы")
            self._render_single_plot(ax3, 'vega', "Зависимость P&L от веги")
            self._render_single_plot(ax4, 'theta', "Зависимость P&L от теты")
        
        # Формируем заголовок с проверкой на None
        delta_str = f", Δ{self.delta_var.get()}±{self.tolerance_var.get()}" if self.delta_enabled_var.get() else ", все дельты"
        
        neutral_param_text = "дельта"  # значение по умолчанию
        if self.analyzer is not None and hasattr(self.analyzer, 'neutral_param'):
            neutral_param_names = {'delta': 'Δ', 'gamma': 'Γ', 'vega': 'ν', 'theta': 'θ', 'iv': 'IV'}
            neutral_param_text = neutral_param_names.get(self.analyzer.neutral_param, self.analyzer.neutral_param)
        
        title = f"Анализ стренглов (DTE={self.dte_var.get()}, покупка={self.buy_time_var.get()}, продажа={self.sell_time_var.get()}{delta_str}, нейтрализация по {neutral_param_text}) - Греки"
        
        fig.suptitle(title, fontsize=12, fontweight='bold', y=0.98)
        self.canvas.draw_idle()

    def _create_page_3(self, fig):
        """Третья страница - волатильность"""
        fig.clear()
        
        fig.subplots_adjust(left=0.06, right=0.98, top=0.92, bottom=0.06, hspace=0.25, wspace=0.2)
        
        ax1 = fig.add_subplot(2, 2, 1)
        ax2 = fig.add_subplot(2, 2, 2)
        ax3 = fig.add_subplot(2, 2, 3)
        ax4 = fig.add_subplot(2, 2, 4)
        
        if self.summary is None or self.summary.empty:
            for ax in [ax1, ax2, ax3, ax4]:
                ax.text(0.5, 0.5, 'Нет данных для отображения', 
                    ha='center', va='center', transform=ax.transAxes,
                    fontsize=12, fontweight='bold')
                ax.set_title("Нет данных", fontsize=11, fontweight='bold')
        else:
            self._render_single_plot(ax1, 'iv_options', "Зависимость P&L от IV")
            self._render_single_plot(ax2, 'atm_iv_dynamic', "Динамика центральной волатильности")
            self._render_single_plot(ax3, 'atm_iv', "Зависимость P&L от ATM волатильности")
            self._render_single_plot(ax4, 'iv_comparison', "Сравнение IV и ATM IV")
        
        delta_str = f", Δ{self.delta_var.get()}±{self.tolerance_var.get()}" if self.delta_enabled_var.get() else ", все дельты"
        
        neutral_param_text = "дельта"
        if self.analyzer is not None and hasattr(self.analyzer, 'neutral_param'):
            neutral_param_names = {'delta': 'Δ', 'gamma': 'Γ', 'vega': 'ν', 'theta': 'θ', 'iv': 'IV'}
            neutral_param_text = neutral_param_names.get(self.analyzer.neutral_param, self.analyzer.neutral_param)
        
        title = f"Анализ волатильности (DTE={self.dte_var.get()}, покупка={self.buy_time_var.get()}, продажа={self.sell_time_var.get()}{delta_str}, нейтрализация по {neutral_param_text})"
        
        fig.suptitle(title, fontsize=12, fontweight='bold', y=0.98)
        self.canvas.draw_idle()

    def _render_single_plot(self, ax, plot_type: str, title: str = ""):
        """Отрисовка одного графика с фиксированными размерами для Full HD"""
        
        if self.summary is None or self.summary.empty or self.results is None:
            ax.text(0.5, 0.5, 'Нет данных', ha='center', va='center', transform=ax.transAxes,
                    fontsize=12, fontweight='bold')
            ax.set_title(title, fontsize=11, fontweight='bold')
            return
        
        # Фиксированные размеры шрифтов для Full HD
        title_fontsize = 11
        label_fontsize = 10
        tick_fontsize = 9
        legend_fontsize = 8
        annotation_fontsize = 9
        
        ax.title.set_fontsize(title_fontsize)
        ax.xaxis.label.set_fontsize(label_fontsize)
        ax.yaxis.label.set_fontsize(label_fontsize)
        ax.tick_params(labelsize=tick_fontsize)
        
        if plot_type == 'dynamic':
            for strangle_id in self.results['strangle_id'].unique():
                data = self.results[self.results['strangle_id'] == strangle_id].copy()
                data = data.sort_values('minutes_from_buy')
                data = data[data['minutes_from_buy'] >= 0]
                if not data.empty:
                    ax.plot(data['minutes_from_buy'], data['position_value'], 
                        alpha=0.8, linewidth=1)
            
            ax.axhline(y=100, color='black', linestyle='--', alpha=0.5, linewidth=1.5)
            
            all_minutes = self.results['minutes_from_buy']
            all_minutes = all_minutes[all_minutes >= 0]
            if not all_minutes.empty:
                max_minutes = all_minutes.max()
                ax.set_xlim(left=0, right=max_minutes * 1.05)
                
                # Устанавливаем шаг сетки кратно 10 минутам
                if max_minutes <= 100:
                    step = 10
                elif max_minutes <= 200:
                    step = 20
                elif max_minutes <= 500:
                    step = 50
                elif max_minutes <= 1000:
                    step = 100
                elif max_minutes <= 2000:
                    step = 200 
                elif max_minutes <= 4000:
                    step = 500 
                else:
                    step = 1000
                    
                # Округляем step вверх до ближайшего кратного 10
                step = ((step + 9) // 10) * 10
                
                xticks = np.arange(0, max_minutes + step, step)
                ax.set_xticks(xticks)
                # Просто числа, без преобразования в часы:минуты
                ax.set_xticklabels([f'{int(x)}' for x in xticks])
            
            ax.set_xlabel('Минуты от покупки')
            ax.set_ylabel('Стоимость позиции ($)')
            ax.grid(True, alpha=0.3, linestyle=':', which='both')
            ax.grid(True, alpha=0.5, linestyle='-', which='major')
            
            ax.text(0.02, 0.98, f'n = {len(self.summary)}', 
                    transform=ax.transAxes, ha='left', va='top', 
                    fontsize=annotation_fontsize, fontweight='bold',
                    bbox=dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='gray', pad=0.5))

        elif plot_type == 'dynamic_average':
            if self.results is not None and not self.results.empty:
                # Группируем по дате покупки и минутам, усредняем position_value
                dynamic_avg = self.results.groupby(['buy_date', 'minutes_from_buy'])['position_value'].mean().reset_index()
                
                # Получаем уникальные даты
                unique_dates = dynamic_avg['buy_date'].unique()
                
                # Используем цветовую карту для разных дней
                colors = plt.cm.tab20(np.linspace(0, 1, len(unique_dates)))
                
                # Для каждой даты рисуем усреднённую линию
                for i, date in enumerate(unique_dates):
                    date_data = dynamic_avg[dynamic_avg['buy_date'] == date].copy()
                    date_data = date_data.sort_values('minutes_from_buy')
                    date_data = date_data[date_data['minutes_from_buy'] >= 0]
                    
                    if not date_data.empty:
                        # Форматируем дату для легенды
                        if hasattr(date, 'strftime'):
                            label = date.strftime('%d.%m')
                        else:
                            label = str(date)[5:10]
                        
                        ax.plot(date_data['minutes_from_buy'], date_data['position_value'], 
                            color=colors[i], linewidth=1.5, alpha=0.8, )
                
                # Линия начальной стоимости
                ax.axhline(y=100, color='black', linestyle='--', alpha=0.5, linewidth=1.5, label='Начало (100$)')
                
                # Настраиваем шкалу минут
                all_minutes = dynamic_avg['minutes_from_buy']
                all_minutes = all_minutes[all_minutes >= 0]
                if not all_minutes.empty:
                    max_minutes = all_minutes.max()
                    ax.set_xlim(left=0, right=max_minutes * 1.05)
                    
                    # Устанавливаем шаг сетки кратно 10 минутам
                    if max_minutes <= 100:
                        step = 10
                    elif max_minutes <= 200:
                        step = 20
                    elif max_minutes <= 500:
                        step = 50
                    elif max_minutes <= 1000:
                        step = 100
                    elif max_minutes <= 2000:
                        step = 200 
                    elif max_minutes <= 4000:
                        step = 500 
                    else:
                        step = 1000
                        
                    step = ((step + 9) // 10) * 10
                    
                    xticks = np.arange(0, max_minutes + step, step)
                    ax.set_xticks(xticks)
                    ax.set_xticklabels([f'{int(x)}' for x in xticks])
                
                ax.set_xlabel('Минуты от покупки')
                ax.set_ylabel('Средняя стоимость позиции ($)')
                ax.grid(True, alpha=0.3, linestyle=':', which='both')
                ax.grid(True, alpha=0.5, linestyle='-', which='major')
                
                # Добавляем легенду и статистику
                if len(unique_dates) <= 15:
                    ax.legend(loc='upper right', fontsize=8, ncol=2 if len(unique_dates) > 8 else 1)
                
                # Статистика
                total_days = len(unique_dates)
                total_strangles = len(self.summary)
                
                ax.text(0.02, 0.98, f'Дней: {total_days}\nСтренглов: {total_strangles}', 
                        transform=ax.transAxes, ha='left', va='top', 
                        fontsize=9, fontweight='bold',
                        bbox=dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='gray', pad=0.5))
            else:
                ax.text(0.5, 0.5, 'Нет данных', ha='center', va='center', transform=ax.transAxes,
                        fontsize=12, fontweight='bold')

        elif plot_type == 'weekday_strangles':
            # Группировка по дням недели
            weekday_stats = self.summary.groupby('weekday_name')['final_pnl'].agg(['mean', 'std', 'count'])
            weekday_stats = weekday_stats.reset_index()
            
            # Сортировка дней
            day_order = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
            day_to_order = {day: i for i, day in enumerate(day_order)}
            weekday_stats['order'] = weekday_stats['weekday_name'].map(day_to_order)
            weekday_stats = weekday_stats.sort_values('order')
            
            if not weekday_stats.empty:
                x_pos = range(len(weekday_stats))
                means = weekday_stats['mean'].values
                stds = weekday_stats['std'].values
                counts = weekday_stats['count'].values
                
                ax.bar(x_pos, means, alpha=0.7, color='lightgreen', edgecolor='black', linewidth=1)
                
                for i, (mean, std, count) in enumerate(zip(means, stds, counts)):
                    lower = mean - std
                    upper = mean + std
                    ax.plot([i, i], [lower, upper], color='black', linewidth=1.5)
                    ax.plot([i-0.1, i+0.1], [lower, lower], color='black', linewidth=1.5)
                    ax.plot([i-0.1, i+0.1], [upper, upper], color='black', linewidth=1.5)
                    ax.text(i, upper + 2, f'n={int(count)}', 
                        ha='center', va='bottom', fontsize=annotation_fontsize-1)
                
                ax.axhline(y=0, color='red', linestyle='--', alpha=0.7, linewidth=2)
                ax.set_xticks(x_pos)
                ax.set_xticklabels(weekday_stats['weekday_name'].values)
                ax.set_xlabel('День недели')
                ax.set_ylabel('Средний P&L ($)')
                ax.grid(True, alpha=0.3, axis='y', linestyle=':')
        
        elif plot_type == 'weekday_unique':
            # Группировка по дням недели для уникальных дней
            if self.summary is not None and not self.summary.empty:
                # Получаем уникальные дни с их P&L
                unique_days = self.summary.groupby(['date', 'weekday_name'])['final_pnl'].mean().reset_index()
                
                # Группируем по дням недели
                weekday_stats = unique_days.groupby('weekday_name')['final_pnl'].agg(['mean', 'std', 'count'])
                weekday_stats = weekday_stats.reset_index()
                
                # Сортировка дней
                day_order = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
                day_to_order = {day: i for i, day in enumerate(day_order)}
                weekday_stats['order'] = weekday_stats['weekday_name'].map(day_to_order)
                weekday_stats = weekday_stats.sort_values('order')
                
                if not weekday_stats.empty:
                    x_pos = range(len(weekday_stats))
                    means = weekday_stats['mean'].values
                    stds = weekday_stats['std'].values
                    counts = weekday_stats['count'].values
                    
                    ax.bar(x_pos, means, alpha=0.7, color='lightblue', edgecolor='black', linewidth=1)
                    
                    for i, (mean, std, count) in enumerate(zip(means, stds, counts)):
                        lower = mean - std
                        upper = mean + std
                        ax.plot([i, i], [lower, upper], color='black', linewidth=1.5)
                        ax.plot([i-0.1, i+0.1], [lower, lower], color='black', linewidth=1.5)
                        ax.plot([i-0.1, i+0.1], [upper, upper], color='black', linewidth=1.5)
                        # Добавляем количество дней над каждым столбцом
                        ax.text(i, upper + 2, f'n={int(count)}', 
                            ha='center', va='bottom', fontsize=8)
                    
                    # Добавляем общее количество уникальных дней в углу
                    total_unique_days = len(unique_days['date'].unique())
                    ax.text(0.02, 0.98, f'Всего дней: {total_unique_days}', 
                        transform=ax.transAxes, ha='left', va='top', 
                        fontsize=9, fontweight='bold',
                        bbox=dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='gray', pad=0.5))
                    
                    ax.axhline(y=0, color='red', linestyle='--', alpha=0.7, linewidth=2)
                    ax.set_xticks(x_pos)
                    ax.set_xticklabels(weekday_stats['weekday_name'].values)
                    ax.set_xlabel('День недели')
                    ax.set_ylabel('Средний P&L ($)')
                    ax.grid(True, alpha=0.3, axis='y', linestyle=':')
            else:
                ax.text(0.5, 0.5, 'Нет данных', ha='center', va='center', transform=ax.transAxes,
                        fontsize=12, fontweight='bold')

        elif plot_type in ['delta', 'gamma', 'vega', 'theta', 'iv_options', 'atm_iv', 'iv_comparison']:
            if plot_type == 'delta':
                ax.scatter(self.summary['call_delta_buy'], self.summary['final_pnl'], 
                        alpha=0.6, c='blue', edgecolors='black', s=30, label='Call')
                ax.scatter(self.summary['put_delta_buy'], self.summary['final_pnl'], 
                        alpha=0.6, c='red', edgecolors='black', s=30, label='Put')
                ax.set_xlabel('Дельта при покупке')
            elif plot_type == 'gamma':
                ax.scatter(self.summary['call_gamma'], self.summary['final_pnl'], 
                        alpha=0.6, c='blue', edgecolors='black', s=30, label='Call')
                ax.scatter(self.summary['put_gamma'], self.summary['final_pnl'], 
                        alpha=0.6, c='red', edgecolors='black', s=30, label='Put')
                ax.set_xlabel('Гамма при покупке')
            elif plot_type == 'vega':
                ax.scatter(self.summary['call_vega'], self.summary['final_pnl'], 
                        alpha=0.6, c='blue', edgecolors='black', s=30, label='Call')
                ax.scatter(self.summary['put_vega'], self.summary['final_pnl'], 
                        alpha=0.6, c='red', edgecolors='black', s=30, label='Put')
                ax.set_xlabel('Вега при покупке')
            elif plot_type == 'theta':
                ax.scatter(self.summary['call_theta'], self.summary['final_pnl'], 
                        alpha=0.6, c='blue', edgecolors='black', s=30, label='Call')
                ax.scatter(self.summary['put_theta'], self.summary['final_pnl'], 
                        alpha=0.6, c='red', edgecolors='black', s=30, label='Put')
                ax.set_xlabel('Тета при покупке')
            elif plot_type == 'iv_options':
                ax.scatter(self.summary['call_iv_buy'] * 100, self.summary['final_pnl'], 
                        alpha=0.6, c='blue', edgecolors='black', s=30, label='Call IV')
                ax.scatter(self.summary['put_iv_buy'] * 100, self.summary['final_pnl'], 
                        alpha=0.6, c='red', edgecolors='black', s=30, label='Put IV')
                ax.set_xlabel('IV при покупке (%)')
            elif plot_type == 'atm_iv':
                if 'atm_iv_buy' in self.summary.columns:
                    ax.scatter(self.summary['atm_iv_buy'] * 100, self.summary['final_pnl'], 
                            alpha=0.7, c='green', edgecolors='black', s=45, label='ATM IV')
                    ax.set_xlabel('ATM IV при покупке (%)')
            elif plot_type == 'iv_comparison':
                # Показываем все точки с прозрачностью
                ax.scatter(self.summary['call_iv_buy'] * 100, self.summary['final_pnl'], 
                        alpha=0.3, c='lightblue', edgecolors='gray', s=20, label='Call IV', zorder=2)
                ax.scatter(self.summary['put_iv_buy'] * 100, self.summary['final_pnl'], 
                        alpha=0.3, c='lightcoral', edgecolors='gray', s=20, label='Put IV', zorder=2)
                
                # Добавляем средние значения для наглядности
                if 'atm_iv_buy' in self.summary.columns:
                    # Группируем по уникальным значениям ATM IV для уменьшения количества точек
                    atm_iv_unique = self.summary.groupby('atm_iv_buy')['final_pnl'].mean().reset_index()
                    ax.scatter(atm_iv_unique['atm_iv_buy'] * 100, atm_iv_unique['final_pnl'], 
                            alpha=0.9, c='green', edgecolors='black', s=60, label='ATM IV (среднее)', zorder=4)
                
                ax.set_xlabel('IV при покупке (%)')
                ax.axhline(y=0, color='black', linestyle='--', alpha=0.7, linewidth=1.5)
                ax.set_ylabel('P&L ($)')
                ax.grid(True, alpha=0.3, linestyle=':')
                ax.legend(fontsize=8, loc='best')
                
                ax.text(0.02, 0.98, f'n = {len(self.summary)}', 
                        transform=ax.transAxes, ha='left', va='top', 
                        fontsize=9, fontweight='bold',
                        bbox=dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='gray', pad=0.5))
            
            ax.axhline(y=0, color='black', linestyle='--', alpha=0.7, linewidth=1.5)
            ax.set_ylabel('P&L ($)')
            ax.grid(True, alpha=0.3, linestyle=':')
            ax.legend(fontsize=legend_fontsize, loc='best')
            
            ax.text(0.02, 0.98, f'n = {len(self.summary)}', 
                    transform=ax.transAxes, ha='left', va='top', 
                    fontsize=annotation_fontsize, fontweight='bold',
                    bbox=dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='gray', pad=0.5))

        elif plot_type == 'atm_iv_dynamic':
            if self.results is not None and not self.results.empty:
                if self.results['atm_iv'].max() == 0:
                    ax.text(0.5, 0.5, 'Все значения ATM IV равны 0', 
                        ha='center', va='center', transform=ax.transAxes,
                        fontsize=12, fontweight='bold', color='red')
                    ax.set_title(title, fontsize=title_fontsize, fontweight='bold')
                    return
                
                plot_data = self.results[['minutes_from_buy', 'expiry_date', 'atm_iv']].copy()
                plot_data = plot_data[plot_data['minutes_from_buy'] >= 0]
                
                # Группируем по минутам и экспирациям
                plot_data = plot_data.groupby(['expiry_date', 'minutes_from_buy'])['atm_iv'].mean().reset_index()
                plot_data = plot_data.sort_values(['expiry_date', 'minutes_from_buy'])
                
                expiry_dates = plot_data['expiry_date'].unique()
                colors = plt.cm.tab20(np.linspace(0, 1, len(expiry_dates)))
                
                lines_drawn = 0
                
                for i, expiry in enumerate(expiry_dates):
                    expiry_data = plot_data[plot_data['expiry_date'] == expiry].copy()
                    
                    if expiry_data['atm_iv'].max() == 0:
                        continue
                    
                    if isinstance(expiry, pd.Timestamp):
                        label = expiry.strftime('%d.%m')
                    else:
                        label = str(expiry)[5:10] if len(str(expiry)) > 5 else str(expiry)
                    
                    ax.plot(expiry_data['minutes_from_buy'], expiry_data['atm_iv'] * 100,
                        color=colors[i], linewidth=2, alpha=0.8,
                        marker='o', markersize=4, label=label)
                    
                    lines_drawn += 1
                
                ax.axvline(x=0, color='black', linestyle='--', alpha=0.5, linewidth=1.5)
                
                # Настраиваем шкалу минут
                if not plot_data.empty:
                    max_minutes = plot_data['minutes_from_buy'].max()
                    
                    # Устанавливаем шаг сетки кратно 10 минутам
                    if max_minutes <= 100:
                        step = 10
                    elif max_minutes <= 200:
                        step = 20
                    elif max_minutes <= 500:
                        step = 50
                    elif max_minutes <= 1000:
                        step = 100
                    elif max_minutes <= 2000:
                        step = 200 
                    elif max_minutes <= 4000:
                        step = 500 
                    else:
                        step = 1000

                    step = ((step + 9) // 10) * 10
                    
                    xticks = np.arange(0, max_minutes + step, step)
                    ax.set_xticks(xticks)
                    ax.set_xticklabels([f'{int(x)}' for x in xticks])
                
                ax.set_xlabel('Минуты от покупки')
                ax.set_ylabel('ATM IV (%)')
                ax.grid(True, alpha=0.3, linestyle=':', which='both')
                ax.grid(True, alpha=0.5, linestyle='-', which='major')
                
                if lines_drawn > 0:
                    if lines_drawn <= 10:
                        ax.legend(loc='upper right', fontsize=8, ncol=2 if lines_drawn > 5 else 1)
                    
                    ax.text(0.02, 0.98, f'Экспираций: {lines_drawn}\nВсего стренглов: {len(self.summary)}', 
                        transform=ax.transAxes, ha='left', va='top', 
                        fontsize=9, fontweight='bold',
                        bbox=dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='gray', pad=0.5))
                else:
                    ax.text(0.5, 0.5, 'Нет данных с ненулевым ATM IV', 
                        ha='center', va='center', transform=ax.transAxes)
            else:
                ax.text(0.5, 0.5, 'Нет результатов', 
                    ha='center', va='center', transform=ax.transAxes)

        ax.set_title(title, fontsize=title_fontsize, fontweight='bold') 

    def _render_fullscreen_plot(self, fig: Figure, plot_index: int, title: str):
        ax = fig.add_subplot(111)
        
        plot_type_map = {
            0: 'dynamic',
            1: 'dynamic_average',
            2: 'weekday_strangles',
            3: 'weekday_unique',
            4: 'delta',
            5: 'gamma',
            6: 'vega',
            7: 'theta',
            8: 'iv_options',
            9: 'atm_iv_dynamic',
            10: 'atm_iv',
            11: 'iv_comparison'
        }
        
        plot_type = plot_type_map.get(plot_index, 'dynamic')
        self._render_single_plot(ax, plot_type, title)
        fig.tight_layout()
    
    def update_table(self):
        if self.summary is None or self.summary.empty:
            return

        # Очищаем таблицу
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Обновляем заголовки колонок в зависимости от режима
        if self.table_display_mode.get() == 'bs':
            # Режим покупка/продажа
            self.tree.heading('Цена кола (b/s)', text='Цена кола (b/s)')
            self.tree.heading('Цена пута (b/s)', text='Цена пута (b/s)')
            self.tree.heading('Дельта кола (b/s)', text='Дельта кола (b/s)')
            self.tree.heading('Дельта пута (b/s)', text='Дельта пута (b/s)')
            self.tree.heading('IV кола (b/s) (%)', text='IV кола (b/s) (%)')
            self.tree.heading('IV пута (b/s) (%)', text='IV пута (b/s) (%)')
            self.tree.heading('ATM IV (b/s) (%)', text='ATM IV (b/s) (%)')
        else:
            # Режим продажа/покупка
            self.tree.heading('Цена кола (b/s)', text='Цена кола (s/b)')
            self.tree.heading('Цена пута (b/s)', text='Цена пута (s/b)')
            self.tree.heading('Дельта кола (b/s)', text='Дельта кола (s/b)')
            self.tree.heading('Дельта пута (b/s)', text='Дельта пута (s/b)')
            self.tree.heading('IV кола (b/s) (%)', text='IV кола (s/b) (%)')
            self.tree.heading('IV пута (b/s) (%)', text='IV пута (s/b) (%)')
            self.tree.heading('ATM IV (b/s) (%)', text='ATM IV (s/b) (%)')

        for idx, row in self.summary.iterrows():
            date_str = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])

            # Формируем значения в зависимости от режима
            if self.table_display_mode.get() == 'bs':
                # Режим покупка/продажа (b/s)
                index_display = f"{row.get('index_price_buy', 0):.0f} / {row.get('index_price_sell', 0):.0f}"
                call_price_display = f"{row['call_price_buy']:.2f} / {row.get('call_price_sell', 0):.2f}"
                put_price_display = f"{row['put_price_buy']:.2f} / {row.get('put_price_sell', 0):.2f}"
                call_delta_display = f"{row['call_delta_buy']:.3f} / {row.get('call_delta_sell', 0):.3f}"
                put_delta_display = f"{row['put_delta_buy']:.3f} / {row.get('put_delta_sell', 0):.3f}"
                call_iv_display = f"{row['call_iv_buy']*100:.1f} / {row.get('call_iv_sell', 0)*100:.1f}"
                put_iv_display = f"{row['put_iv_buy']*100:.1f} / {row.get('put_iv_sell', 0)*100:.1f}"
                atm_iv_display = f"{row['atm_iv_buy']*100:.1f} / {row.get('atm_iv_sell', 0)*100:.1f}"
            else:
                # Режим продажа/покупка (s/b)
                index_display = f"{row.get('index_price_sell', 0):.0f} / {row.get('index_price_buy', 0):.0f}"
                call_price_display = f"{row.get('call_price_sell', 0):.2f} / {row['call_price_buy']:.2f}"
                put_price_display = f"{row.get('put_price_sell', 0):.2f} / {row['put_price_buy']:.2f}"
                call_delta_display = f"{row.get('call_delta_sell', 0):.3f} / {row['call_delta_buy']:.3f}"
                put_delta_display = f"{row.get('put_delta_sell', 0):.3f} / {row['put_delta_buy']:.3f}"
                call_iv_display = f"{row.get('call_iv_sell', 0)*100:.1f} / {row['call_iv_buy']*100:.1f}"
                put_iv_display = f"{row.get('put_iv_sell', 0)*100:.1f} / {row['put_iv_buy']*100:.1f}"
                atm_iv_display = f"{row.get('atm_iv_sell', 0)*100:.1f} / {row['atm_iv_buy']*100:.1f}"

            values = (
                f"{idx}",
                date_str,
                row['weekday_name'],
                index_display,
                f"{row['call_strike']:.0f}",
                f"{row['put_strike']:.0f}",
                f"{row['call_qty']:.4f}",
                f"{row['put_qty']:.4f}",
                call_price_display,
                put_price_display,
                call_delta_display,
                put_delta_display,
                call_iv_display,
                put_iv_display,
                atm_iv_display,
                f"{row['final_pnl']:.2f}"
            )
            self.tree.insert('', tk.END, values=values)

    def update_stats_table(self):
        if self.summary is None or self.summary.empty:
            return
        
        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)
        
        # Получаем информацию об активе из кэша
        if self.cache:
            base_assets = self.cache.metadata.get('base_assets', [])
            main_asset = self.cache.metadata.get('main_asset', 'UNKNOWN')
            custom_name = self.cache.metadata.get('custom_name', '')
            
            asset_info = f"Актив: {main_asset}"
            if len(base_assets) > 1:
                asset_info += f" (также: {', '.join([a for a in base_assets if a != main_asset][:3])})"
            if custom_name:
                asset_info += f" | Имя: {custom_name}"
        else:
            asset_info = "Актив: не определён"
        
        # Добавляем заголовок с информацией об активе
        self.stats_tree.insert('', tk.END, text=asset_info, values=('', ''), tags=('header',))
        
        final_values = self.summary['final_value'].values
        pnl = self.summary['final_pnl'].values
        
        unique_dates = self.summary['date'].nunique()
        daily_avg_value = self.summary.groupby('date')['final_value'].mean()
        daily_avg_pnl = daily_avg_value - 100
        
        def fmt(val):
            return f"{val:>8.2f}"
        
        neutral_param_names = {
            'delta': 'дельта', 'gamma': 'гамма', 'vega': 'вега', 
            'theta': 'тета', 'iv': 'IV'
        }
        
        sell_text = self.sell_time_var.get()
        delta_text = self.delta_var.get() if self.delta_enabled_var.get() else 'все дельты'
        days_text = ', '.join([name for name, idx in zip(['Пн','Вт','Ср','Чт','Пт','Сб','Вс'], range(7)) 
                            if self.weekday_vars[idx].get()])
        
        # ПАРАМЕТРЫ АНАЛИЗА
        self.stats_tree.insert('', tk.END, text='ПАРАМЕТРЫ АНАЛИЗА', values=('', ''), tags=('header',))
        
        params = [
            ('  Время покупки', self.buy_time_var.get(), ''),
            ('  Время продажи', sell_text, ''),
            ('  Макс. отклонение', f'±{self.max_time_diff_var.get()} мин', ''),
            ('  Время экспирации', f'{self.expiry_time_var.get()} UTC', ''),
            ('  DTE', self.dte_var.get(), ''),
            ('  Переносы через ночь', self.nights_var.get(), ''),
            ('  Целевая дельта', delta_text, ''),
            ('  Допуск', self.tolerance_var.get(), ''),
            ('  Нейтрализация', neutral_param_names.get(self.analyzer.neutral_param, self.analyzer.neutral_param), ''),
            ('  Дни недели', days_text, '')
        ]
        
        for label, val1, val2 in params:
            self.stats_tree.insert('', tk.END, text=label, values=(val1, val2), tags=('normal',))
        
        # ОБЩАЯ СТАТИСТИКА
        self.stats_tree.insert('', tk.END, text='ОБЩАЯ СТАТИСТИКА', values=('', ''), tags=('header',))
        
        self.stats_tree.insert('', tk.END, text='  Количество', 
                            values=(f'{len(self.summary):,} стр', f'{unique_dates:,} дн'), 
                            tags=('normal',))
        
        self.stats_tree.insert('', tk.END, text='  СТОИМОСТЬ ПОЗИЦИИ ($)', values=('', ''), tags=('block_header',))
        
        value_stats = [
            ('    Среднее', np.mean(final_values), np.mean(daily_avg_value)),
            ('    Медиана', np.median(final_values), np.median(daily_avg_value)),
            ('    Минимум', np.min(final_values), np.min(daily_avg_value)),
            ('    Максимум', np.max(final_values), np.max(daily_avg_value)),
            ('    Стд.откл.', np.std(final_values), np.std(daily_avg_value)),
        ]
        
        for label, val1, val2 in value_stats:
            self.stats_tree.insert('', tk.END, text=label, 
                                values=(f'${fmt(val1)}', f'${fmt(val2)}'), 
                                tags=('normal',))
        
        self.stats_tree.insert('', tk.END, text='  P&L ($)', values=('', ''), tags=('block_header',))
        
        pnl_stats = [
            ('    Средний', np.mean(pnl), np.mean(daily_avg_pnl)),
            ('    Медианный', np.median(pnl), np.median(daily_avg_pnl)),
            ('    Минимальный', np.min(pnl), np.min(daily_avg_pnl)),
            ('    Максимальный', np.max(pnl), np.max(daily_avg_pnl)),
        ]
        
        for label, val1, val2 in pnl_stats:
            self.stats_tree.insert('', tk.END, text=label, 
                                values=(f'${fmt(val1)}', f'${fmt(val2)}'), 
                                tags=('normal',))
        
        # ПРИБЫЛЬНЫЕ/УБЫТОЧНЫЕ
        profit_strangles = pnl[pnl > 0]
        profit_days = daily_avg_pnl[daily_avg_pnl > 0]
        loss_strangles = pnl[pnl <= 0]
        loss_days = daily_avg_pnl[daily_avg_pnl <= 0]
        
        self.stats_tree.insert('', tk.END, text='  ПРИБЫЛЬНЫЕ', values=('', ''), tags=('block_header',))
        
        self.stats_tree.insert('', tk.END, text='    Количество', 
                            values=(f'{len(profit_strangles):,}', f'{len(profit_days):,}'), 
                            tags=('normal',))
        
        profit_str_pct = len(profit_strangles)/len(pnl)*100 if len(pnl) > 0 else 0
        profit_day_pct = len(profit_days)/len(daily_avg_pnl)*100 if len(daily_avg_pnl) > 0 else 0
        
        self.stats_tree.insert('', tk.END, text='    Доля', 
                            values=(f'{profit_str_pct:.1f}%', f'{profit_day_pct:.1f}%'), 
                            tags=('normal',))
        
        if len(profit_strangles) > 0:
            mean_profit_str = np.mean(profit_strangles)
            mean_profit_day = np.mean(profit_days) if len(profit_days) > 0 else 0
            self.stats_tree.insert('', tk.END, text='    Средняя прибыль', 
                                values=(f'${fmt(mean_profit_str)}', f'${fmt(mean_profit_day)}'), 
                                tags=('normal',))
        
        self.stats_tree.insert('', tk.END, text='  УБЫТОЧНЫЕ', values=('', ''), tags=('block_header',))
        
        self.stats_tree.insert('', tk.END, text='    Количество', 
                            values=(f'{len(loss_strangles):,}', f'{len(loss_days):,}'), 
                            tags=('normal',))
        
        loss_str_pct = len(loss_strangles)/len(pnl)*100 if len(pnl) > 0 else 0
        loss_day_pct = len(loss_days)/len(daily_avg_pnl)*100 if len(daily_avg_pnl) > 0 else 0
        
        self.stats_tree.insert('', tk.END, text='    Доля', 
                            values=(f'{loss_str_pct:.1f}%', f'{loss_day_pct:.1f}%'), 
                            tags=('normal',))
        
        if len(loss_strangles) > 0:
            mean_loss_str = np.mean(loss_strangles)
            mean_loss_day = np.mean(loss_days) if len(loss_days) > 0 else 0
            self.stats_tree.insert('', tk.END, text='    Средний убыток', 
                                values=(f'${fmt(mean_loss_str)}', f'${fmt(mean_loss_day)}'), 
                                tags=('normal',))
        
        # РАСПРЕДЕЛЕНИЕ ПО ДНЯМ НЕДЕЛИ
        self.stats_tree.insert('', tk.END, text='РАСПРЕДЕЛЕНИЕ ПО ДНЯМ НЕДЕЛИ', values=('', ''), tags=('header',))
        
        day_order = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        for day in day_order:
            day_data = self.summary[self.summary['weekday_name'] == day]
            if not day_data.empty:
                str_count = len(day_data)
                str_mean = day_data['final_pnl'].mean()
                str_std = day_data['final_pnl'].std()
                
                day_vals = day_data.groupby('date')['final_pnl'].mean()
                day_count = len(day_vals)
                day_mean = day_vals.mean()
                day_std = day_vals.std() if len(day_vals) > 1 else 0
                
                self.stats_tree.insert('', tk.END, 
                                    text=f'  {day}', 
                                    values=(f'{str_count:2} стр, {str_mean:6.2f}±{str_std:5.2f}',
                                            f'{day_count:2} дн, {day_mean:6.2f}±{day_std:5.2f}'),
                                    tags=('normal',))
            else:
                self.stats_tree.insert('', tk.END, 
                                    text=f'  {day}', 
                                    values=(' 0 стр,   0.00±0.00', ' 0 дн,   0.00±0.00'),
                                    tags=('normal',))
        
        # ИТОГОВАЯ СТАТИСТИКА ПО ДНЯМ
        self.stats_tree.insert('', tk.END, text='ИТОГОВАЯ СТАТИСТИКА ПО ДНЯМ', values=('', ''), tags=('header',))
        
        self.stats_tree.insert('', tk.END, text='  Всего уникальных дней', 
                            values=(f'{unique_dates}', ''), 
                            tags=('normal',))
        
        self.stats_tree.insert('', tk.END, text='  Средняя стоимость за день', 
                            values=(f'${np.mean(daily_avg_value):.2f}', ''), 
                            tags=('normal',))
        
        self.stats_tree.insert('', tk.END, text='  Средний P&L за день', 
                            values=(f'${np.mean(daily_avg_pnl):.2f}', ''), 
                            tags=('normal',))
        
        self.stats_tree.insert('', tk.END, text='  Медианный P&L за день', 
                            values=(f'${np.median(daily_avg_pnl):.2f}', ''), 
                            tags=('normal',))
        
        self.stats_tree.insert('', tk.END, text='  Лучший день', 
                            values=(f'${np.max(daily_avg_pnl):.2f}', ''), 
                            tags=('normal',))
        
        self.stats_tree.insert('', tk.END, text='  Худший день', 
                            values=(f'${np.min(daily_avg_pnl):.2f}', ''), 
                            tags=('normal',))
        
        profit_days_count = (daily_avg_pnl > 0).sum()
        profit_days_pct = (daily_avg_pnl > 0).mean() * 100 if len(daily_avg_pnl) > 0 else 0
        self.stats_tree.insert('', tk.END, text='  Дней с P&L > 0', 
                            values=(f'{profit_days_count} ({profit_days_pct:.1f}%)', ''), 
                            tags=('normal',))
        
        loss_days_count = (daily_avg_pnl <= 0).sum()
        loss_days_pct = (daily_avg_pnl <= 0).mean() * 100 if len(daily_avg_pnl) > 0 else 0
        self.stats_tree.insert('', tk.END, text='  Дней с P&L ≤ 0', 
                            values=(f'{loss_days_count} ({loss_days_pct:.1f}%)', ''), 
                            tags=('normal',))
        
        self.stats_tree.update_idletasks()
        
        self.stats_tree.column('#0', width=350)
        self.stats_tree.column('col1', width=250)
        self.stats_tree.column('col2', width=250)

    def clear_results(self):
        self.results = None
        self.summary = None
        self.page_figures = {}
        self.fig.clear()
        self.canvas.draw()
        
        # Сбрасываем на первую страницу
        self.current_page = 0
        self.page_label.config(text=f"Страница 1/{self.total_pages}")
        
        # Сбрасываем режим отображения таблицы на значение по умолчанию
        self.table_display_mode.set('bs')
        
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)
        
        self.status_var.set("Результаты очищены. Анализатор сохранён.")
        self.left_status_var.set("Результаты очищены")
    
    def export_to_pdf(self):
        if self.summary is None or self.summary.empty:
            messagebox.showwarning("Предупреждение", "Нет результатов для экспорта")
            return
        
        # Генерируем имя файла
        base_filename = self._generate_filename('pdf')
        
        filename = filedialog.asksaveasfilename(
            title="Сохранить отчет в PDF",
            initialfile=base_filename,
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
        )
        
        if not filename:
            return
        
        try:
            self.status_var.set("Создание PDF отчета...")
            self.left_status_var.set("Создание PDF отчета...")
            self.root.update()
            
            with PdfPages(filename) as pdf:
                fullscreen_graphs = [
                    (0, "Динамика стоимости"),
                    (1, "Динамика средней стоимости"),
                    (2, "Дни недели (по стренглам)"),
                    (3, "Дни недели (по дням)"),
                    (4, "P&L от дельты"),
                    (5, "P&L от гаммы"),
                    (6, "P&L от веги"),
                    (7, "P&L от теты"),
                    (8, "P&L от IV"),
                    (9, "Динамика ATM IV"),
                    (10, "P&L от ATM IV"),
                    (11, "Сравнение IV")
                ]
                
                for idx, title in fullscreen_graphs:
                    self.status_var.set(f"Добавление графика {idx+1}/12: {title}")
                    self.root.update()
                    
                    fig = Figure(figsize=(11.69, 8.27), dpi=150)
                    self._render_fullscreen_plot(fig, idx, title)
                    pdf.savefig(fig)
                    plt.close(fig)
            
            self.status_var.set(f"PDF сохранен: {os.path.basename(filename)}")
            self.left_status_var.set(f"✅ PDF сохранен")
            messagebox.showinfo("Успех", f"Отчет сохранен в:\n{filename}")
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить PDF:\n{str(e)}")
            self.status_var.set("Ошибка при сохранении") 

    def _get_base_asset(self) -> str:
        """Получает базовый актив из кэша"""
        if self.cache:
            base_assets = self.cache.metadata.get('base_assets', [])
            if base_assets:
                return base_assets[0]
        return 'BTC'

    def _get_weekday_code(self, selected_days: List[int]) -> str:
        """Преобразует список выбранных дней в код для имени файла"""
        if len(selected_days) == 7:
            return "WDall"
        
        day_map = {0: 'MON', 1: 'TUE', 2: 'WED', 3: 'THU', 4: 'FRI', 5: 'SAT', 6: 'SUN'}
        codes = [day_map[d] for d in sorted(selected_days)]
        return f"WD{''.join(codes)}"

    def _generate_filename(self, extension: str) -> str:
        """Генерирует имя файла для экспорта"""
        # Получаем текущие параметры из GUI
        base_asset = self._get_base_asset()
        
        # Ночи (nights)
        nights = int(self.nights_var.get())
        
        # DTE
        dte = int(self.dte_var.get())
        
        # Время (всегда есть время продажи)
        buy_hour = self.buy_hour_var.get()
        sell_hour = self.sell_hour_var.get()
        time_code = f"{buy_hour}-{sell_hour}"
        
        # Нейтрализация
        neutral_map = {
            'delta': 'delta',
            'gamma': 'gamma',
            'vega': 'vega',
            'theta': 'theta',
            'iv': 'iv'
        }
        neutral_code = neutral_map.get(self.neutral_param_var.get(), 'delta')
        
        # Дельта
        if self.delta_enabled_var.get():
            delta_val = float(self.delta_var.get())
            tol_val = float(self.tolerance_var.get())
            # Преобразуем в целые числа (0.10 -> 10, 0.05 -> 05)
            delta_int = int(delta_val * 100)
            tol_int = int(tol_val * 100)
            delta_code = f"{delta_int:02d}-{tol_int:02d}"
        else:
            delta_code = "all"
        
        # Режимы
        fee_mode = self.fee_mode_var.get()
        bidask_mode = self.bidask_mode_var.get()
        
        # Дни недели
        selected_days = self.get_weekdays()
        weekdays_code = self._get_weekday_code(selected_days)
        
        # Генерируем имя
        filename = self.generate_filename(
            base_asset=base_asset,
            nights=nights,
            dte=dte,
            time_code=time_code,
            neutral_code=neutral_code,
            delta_code=delta_code,
            fee_mode=fee_mode,
            bidask_mode=bidask_mode,
            weekdays_code=weekdays_code
        )
        
        # Меняем расширение
        return filename.replace('.pdf', f'.{extension}')

    def generate_filename(self, base_asset, nights, dte, time_code, neutral_code, 
                        delta_code, fee_mode, bidask_mode, weekdays_code):
        """
        Генерирует имя файла
        """
        date = datetime.now().strftime('%Y-%m-%d')
        
        # Комиссия и Bid/Ask
        fee_str = "F" if fee_mode else "NF"
        bidask_str = "BA" if bidask_mode else "NBA"
        
        # Собираем имя
        parts = [
            date,
            base_asset,
            f"NG{nights}",
            f"DTE{dte}",
            f"TM{time_code}",
            f"NZ{neutral_code}",
            f"DL{delta_code}",
            fee_str,
            bidask_str,
            weekdays_code
        ]
        
        return '_'.join(parts) + '.pdf'

    def copy_graph_to_clipboard(self):
        if self.summary is None or self.summary.empty:
            messagebox.showwarning("Предупреждение", "Нет результатов для копирования")
            return
        
        selected = self.copy_graph_var.get()
        if selected == "Выберите график" or not selected:
            messagebox.showwarning("Предупреждение", "Выберите график из списка")
            return
        
        graph_map = {
            "Динамика стоимости": 0,
            "Динамика средней стоимости": 1,
            "Дни недели (по стренглам)": 2,
            "Дни недели (по дням)": 3,
            "P&L от дельты": 4,
            "P&L от гаммы": 5,
            "P&L от веги": 6,
            "P&L от теты": 7,
            "P&L от IV": 8,
            "Динамика ATM IV": 9,
            "P&L от ATM IV": 10,
            "Сравнение IV": 11
        }
        
        plot_index = graph_map.get(selected, 0)
        
        try:
            self.status_var.set(f"Копирование графика: {selected}...")
            self.left_status_var.set(f"Копирование графика: {selected}...")
            self.root.update()
            
            fig = Figure(figsize=(16, 9), dpi=150)
            self._render_fullscreen_plot(fig, plot_index, selected)
            
            temp_file = os.path.join(tempfile.gettempdir(), f"temp_graph_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
            fig.savefig(temp_file, dpi=150, bbox_inches='tight', format='png')
            plt.close(fig)
            
            if os.name == 'nt':
                image = Image.open(temp_file)
                output = io.BytesIO()
                image.convert("RGB").save(output, format="BMP")
                data = output.getvalue()[14:]
                
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                win32clipboard.CloseClipboard()
                
                self.status_var.set(f"✅ График скопирован: {selected}")
                self.left_status_var.set("✅ График скопирован")
                messagebox.showinfo("Успех", f"График '{selected}' скопирован в буфер обмена")
            
            os.remove(temp_file)
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать график:\n{str(e)}")
            self.status_var.set("Ошибка при копировании")

def main():
    root = tk.Tk()
    app = StrangleAnalyzerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()