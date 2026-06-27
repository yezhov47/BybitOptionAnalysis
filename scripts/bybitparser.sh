#!/bin/bash

# ==============================================
# BYBIT BTC OPTIONS PARSER - ДАТЫ С ВЕДУЩИМИ НУЛЯМИ
# ==============================================

# Парсим аргументы командной строки
COIN="BTC"  # Значение по умолчанию
ONCE_MODE=false

# Обрабатываем аргументы
while [[ $# -gt 0 ]]; do
    case $1 in
        --once)
            ONCE_MODE=true
            shift
            ;;
        --token)
            COIN="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [--once] [--token TOKEN]"
            echo "  --once     Run once and exit"
            echo "  --token    Specify token (default: BTC)"
            echo "  --help     Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage"
            exit 1
            ;;
    esac
done

OUTPUT_DIR="$HOME/bybit_options_data"
INTERVAL=600
API_URL="https://api.bybit.com/v5/market/tickers"

mkdir -p "$OUTPUT_DIR"
echo "📁 Output directory: $OUTPUT_DIR"
echo "🪙 Token: $COIN"

# Функция для преобразования даты из формата 9MAR26 в 2026-03-09
convert_date_format() {
    local exp_date=$1
    
    if [[ $exp_date =~ ^([0-9]{1,2})([A-Z]{3})([0-9]{2})$ ]]; then
        local day=${BASH_REMATCH[1]}
        local month_str=${BASH_REMATCH[2]}
        local year_short=${BASH_REMATCH[3]}
        
        # Конвертируем месяц в число
        case $month_str in
            JAN) month=1 ;;
            FEB) month=2 ;;
            MAR) month=3 ;;
            APR) month=4 ;;
            MAY) month=5 ;;
            JUN) month=6 ;;
            JUL) month=7 ;;
            AUG) month=8 ;;
            SEP) month=9 ;;
            OCT) month=10 ;;
            NOV) month=11 ;;
            DEC) month=12 ;;
            *) echo "UNKNOWN"; return ;;
        esac
        
        # Форматируем день с ведущим нулем (всегда 2 цифры)
        day=$(printf "%02d" $((10#$day)))
        
        # Форматируем месяц с ведущим нулем (всегда 2 цифры)
        month=$(printf "%02d" $month)
        
        # Полный год
        year=$((2000 + 10#$year_short))
        
        echo "$year-$month-$day"
    else
        echo "UNKNOWN"
    fi
}

fetch_data() {
    echo "[$(date '+%H:%M:%S')] 🚀 Fetching $COIN options..."
    
    # ВРЕМЯ СНЭПШОТА - ОДИН РАЗ
    local snapshot_time=$(date -u +"%Y-%m-%dT%H:%M:%S.%6N+00:00")
    
    # Запрос к API - используем переменную COIN
    local response=$(curl --connect-timeout 10 --max-time 20 -s "$API_URL?category=option&baseCoin=$COIN" \
        -H "Accept: application/json" 2>/dev/null)
    
    if [[ -z "$response" ]]; then
        echo "[ERROR] Empty response for $COIN"
        return 1
    fi
    
    # Проверка JSON
    if ! echo "$response" | jq empty 2>/dev/null; then
        echo "[ERROR] Invalid JSON response for $COIN"
        return 1
    fi
    
    # Обрабатываем через jq - используем переменную COIN в capture
    echo "$response" | jq -r --arg time "$snapshot_time" --arg coin "$COIN" '
        .result.list[] | 
        select(.bid1Price != "0" or .ask1Price != "0") |
        . as $item |
        # Извлекаем expiry из symbol - динамический префикс
        ($item.symbol | capture("(?<prefix>[A-Z]+)-(?<exp>[0-9]{1,2}[A-Z]{3}[0-9]{2})-(?<strike>[0-9]+)-[CP]")) as $parts |
        # Формируем строку с разделителем |: expiry|полная_строка_csv
        "\($parts.exp)|\($time),\($item.symbol),\($parts.exp),\($parts.strike),\($item.markPrice // ""),\($item.indexPrice // ""),\($item.bid1Price // "0"),\($item.ask1Price // "0"),\($item.delta // ""),\($item.gamma // ""),\($item.vega // ""),\($item.theta // ""),\($item.bid1Iv // ""),\($item.ask1Iv // ""),\($item.markIv // "")"
    ' 2>/dev/null | while IFS='|' read -r exp_date csv_line; do
        
        # Преобразуем дату в формат YYYY-MM-DD (с ведущими нулями)
        local formatted_date=$(convert_date_format "$exp_date")
        
        # Пропускаем если дата не распознана
        [[ "$formatted_date" == "UNKNOWN" ]] && continue
        
        # Формируем имя файла - используем переменную COIN
        local filename="$OUTPUT_DIR/${COIN}_${formatted_date}.csv"
        
        # Записываем в файл
        if [[ ! -f "$filename" ]]; then
            echo "fetch_time_utc,symbol,expiry,strike,markPrice,indexPrice,bid1Price,ask1Price,delta,gamma,vega,theta,bid1Iv,ask1Iv,markIv" > "$filename"
        fi
        echo "$csv_line" >> "$filename"
    done
    
    # Считаем количество файлов, обновленных за последнюю минуту
    local total_files=$(find "$OUTPUT_DIR" -name "${COIN}_*.csv" -newermt "1 minute ago" | wc -l)
    echo "[$(date '+%H:%M:%S')] ✅ Updated $total_files files for $COIN"
}

# Проверка соединения
if ! curl --connect-timeout 5 -s "https://api.bybit.com/v5/market/time" | jq -e '.retCode == 0' >/dev/null 2>&1; then
    echo "❌ Cannot connect to Bybit API"
    exit 1
fi

# Запуск
if [[ "$ONCE_MODE" == true ]]; then
    time fetch_data
    exit 0
fi

echo "=========================================="
echo "BYBIT OPTIONS PARSER"
echo "✅ Token: $COIN"
echo "✅ Формат файлов: ${COIN}_2026-03-09.csv"
echo "✅ День и месяц всегда с ведущими нулями"
echo "Interval: ${INTERVAL}s ($((INTERVAL/60)) min)"
echo "Output: $OUTPUT_DIR"
echo "=========================================="
echo "Press Ctrl+C to stop"
echo "──────────────────────────────────────────"

while true; do
    fetch_data
    echo "⏳ Waiting ${INTERVAL}s..."
    echo "──────────────────────────────────────────"
    sleep $INTERVAL
done