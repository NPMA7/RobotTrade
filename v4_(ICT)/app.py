import MetaTrader5 as mt5
import pandas as pd
import time
import numpy as np
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from threading import Thread

app = Flask(__name__)

# Variabel global untuk status robot
robot_active = False
robot_lot = 0.01  # Default lot size
current_signal = None
signal_thread = None

# Tambahkan variabel global untuk SL/TP default
robot_sl_pips = 200  # Default 200 points
robot_tp_pips = 200  # Default 200 points
AUTO_BE_POINTS = 100   # Pindahkan SL ke entry setelah 100 points

# Tambahkan daftar pair forex yang akan dipantau
FOREX_PAIRS = [
    # "EURUSDm","GBPUSD","USDJPYm", "GBPJPYm", "XAUUSDm", "BTCUSDm", "AUDUSDm", "USDCHFm", "USDCADm", "NZDUSDm"
    "XAUUSDm"
]

# Tambahkan variabel global untuk timeframe
TIMEFRAME_ENTRY = mt5.TIMEFRAME_M5 # Menggunakan timeframe M5
TIMEFRAME_CONFIRMATION = mt5.TIMEFRAME_M15 # Menggunakan timeframe M15
TIMEFRAME = TIMEFRAME_ENTRY  # Menambahkan definisi TIMEFRAME

# Tambahkan variabel global
max_positions_per_pair = 1  # Default value

# Variabel global untuk status inisialisasi MT5
mt5_initialized = False

def initialize_mt5():
    global mt5_initialized
    if not mt5_initialized:
        if not mt5.initialize():
            print("Inisialisasi MT5 gagal!")
            return False
        mt5_initialized = True
    return True

def login_account(login, password, server):
    if not initialize_mt5():
        return False
    if not mt5.login(login, password, server):
        print("Login gagal!")
        return False
    return True

def calculate_max_lot(symbol):
    account_info = mt5.account_info()
    margin_free = account_info.margin_free
    
    symbol_info = mt5.symbol_info(symbol)
    margin_per_lot = symbol_info.margin_initial
    # Gunakan 90% dari free margin yang tersedia
    max_lot = (margin_free * 0.9) / margin_per_lot
    
    # Pembulatan ke bawah dengan 2 desimal
    return round(max_lot, 2)

class TradingRobot:
    def __init__(self, symbols, lot_size=0.01, stop_loss=5, take_profit=5):
        self.symbols = symbols  # Sekarang menerima list simbol
        self.lot_size = lot_size
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        
    def get_signal(self, symbol):
        # Mengambil data historis dengan timeframe M5 untuk konfirmasi entry
        rates_m5 = mt5.copy_rates_from_pos(symbol, TIMEFRAME_ENTRY, 0, 100)
        df_m5 = pd.DataFrame(rates_m5)

        # Mengambil data historis dengan timeframe M15 untuk memeriksa MSS
        rates_m15 = mt5.copy_rates_from_pos(symbol, TIMEFRAME_CONFIRMATION, 0, 100)
        df_m15 = pd.DataFrame(rates_m15)

        # Menentukan kondisi untuk Buy dan Sell
        current_price_m5 = df_m5['close'].iloc[-1]
        previous_low_m15 = df_m15['low'].iloc[-2]
        previous_high_m15 = df_m15['high'].iloc[-2]

        # Asian Session Buy-Setup
        if current_price_m5 < previous_low_m15:
            # Tunggu untuk penolakan harga atau MSS
            if self.check_market_structure_shift(df_m15):
                return "BUY"

        # Asian Session Sell-Setup
        if current_price_m5 > previous_high_m15:
            # Tunggu untuk penolakan harga atau MSS
            if self.check_market_structure_shift(df_m15):
                return "SELL"

        return None

    def check_market_structure_shift(self, df):
        # Logika untuk memeriksa apakah ada pergeseran struktur pasar
        # Misalnya, jika harga menolak dari level tertentu
        # Implementasikan logika sesuai dengan strategi ICT
        # Contoh sederhana:
        if df['close'].iloc[-1] < df['low'].iloc[-2] or df['close'].iloc[-1] > df['high'].iloc[-2]:
            return True
        return False

    def check_margin(self, symbol):
        account_info = mt5.account_info()
        margin_free = account_info.margin_free
        
        symbol_info = mt5.symbol_info(symbol)
        margin_required = symbol_info.margin_initial * self.lot_size
        
        if margin_free * 0.9 >= margin_required:
            return True
        return False

    def execute_trade(self, symbol, signal):
        if not self.check_margin(symbol):
            print(f"Margin tidak mencukupi untuk {symbol}!")
            return None
            
        symbol_info = mt5.symbol_info(symbol)
        
        price = symbol_info.ask if signal == "BUY" else symbol_info.bid
        
        # Mengatur stop loss dan take profit dengan jarak yang cukup
        sl = price - (self.stop_loss * symbol_info.point) if signal == "BUY" else price + (self.stop_loss * symbol_info.point)
        tp = price + (self.stop_loss * symbol_info.point) if signal == "BUY" else price - (self.stop_loss * symbol_info.point)

        # Cek jarak minimum untuk SL dan TP
        min_distance = 10 * symbol_info.point  # Misalnya, jarak minimum 10 pips
        if abs(price - sl) < min_distance or abs(price - tp) < min_distance:
            print(f"Invalid stops for {symbol}: SL = {sl}, TP = {tp}, Price = {price}")
            return None  # Hentikan eksekusi jika SL/TP tidak valid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": self.lot_size,
            "type": mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl,
            "tp": tp,
            "magic": 234000,
            "comment": f"python-scalp-{signal.lower()}-{symbol}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        return result

    def modify_sl_to_entry(self, symbol, position):
        modify_request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": position.ticket,
            "sl": position.price_open,  # Set SL ke harga entry
            "tp": position.tp  # Pertahankan TP yang sama
        }
        return mt5.order_send(modify_request)

def check_trading_signal():
    global robot_active, current_signal
    while robot_active:
        if not mt5.initialize():
            current_signal = {"status": "error", "message": "Gagal koneksi MT5"}
            continue

        try:
            for symbol in FOREX_PAIRS:
                # Cek jumlah posisi terbuka untuk simbol ini
                positions = mt5.positions_get(symbol=symbol)
                total_positions = len(positions) if positions is not None else 0
                
                # Skip jika sudah mencapai batas maksimal posisi
                if total_positions >= max_positions_per_pair:
                    continue
                
                rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 100)
                if rates is None:
                    continue
                    
                df = pd.DataFrame(rates)
                
                # Hitung indikator
                df['MA5'] = df['close'].rolling(window=5).mean()
                df['MA10'] = df['close'].rolling(window=10).mean()
                df['MA50'] = df['close'].rolling(window=50).mean()
                
                # RSI
                delta = df['close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                df['RSI'] = 100 - (100 / (1 + rs))
                
                # Stochastic
                df['L14'] = df['low'].rolling(window=14).min()
                df['H14'] = df['high'].rolling(window=14).max()
                df['%K'] = (df['close'] - df['L14']) / (df['H14'] - df['L14']) * 100
                df['%D'] = df['%K'].rolling(window=3).mean()
                
                # Generate sinyal
                if (df['MA5'].iloc[-1] > df['MA10'].iloc[-1] and 
                    df['RSI'].iloc[-1] < 70 and 
                    df['%K'].iloc[-1] > df['%D'].iloc[-1] and
                    df['close'].iloc[-1] > df['MA50'].iloc[-1]):
                    
                    current_signal = {
                        "status": "signal",
                        "type": "BUY",
                        "symbol": symbol,
                        "price": mt5.symbol_info_tick(symbol).ask,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    execute_trade(current_signal)
                    
                elif (df['MA5'].iloc[-1] < df['MA10'].iloc[-1] and 
                      df['RSI'].iloc[-1] > 30 and 
                      df['%K'].iloc[-1] < df['%D'].iloc[-1] and
                      df['close'].iloc[-1] < df['MA50'].iloc[-1]):
                    
                    current_signal = {
                        "status": "signal",
                        "type": "SELL",
                        "symbol": symbol,
                        "price": mt5.symbol_info_tick(symbol).bid,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    execute_trade(current_signal)
                else:
                    current_signal = {
                        "status": "no_signal",
                        "message": f"Mencari sinyal trading... ({datetime.now().strftime('%H:%M:%S')})",
                        "checked_pairs": FOREX_PAIRS
                    }
                
        except Exception as e:
            current_signal = {
                "status": "error",
                "message": f"Error: {str(e)}"
            }
        
        time.sleep(5)  # Cek setiap 5 detik

def execute_trade(signal):
    try:
        if not mt5.initialize():
            return {"status": "error", "message": "Gagal koneksi MT5"}

        # Dapatkan nilai SL/TP terbaru dalam point
        sl_points = float(robot_sl_pips)  # Nilai dalam point
        tp_points = float(robot_tp_pips)  # Nilai dalam point
        
        # Cek apakah simbol adalah XAUUSDm atau BTCUSDm
        if signal['symbol'] == "XAUUSDm":
            sl_points *= 10  # Kalikan SL dengan 10
            tp_points *= 10  # Kalikan TP dengan 10
        elif signal['symbol'] == "BTCUSDm":
            sl_points *= 100  # Kalikan SL dengan 100
            tp_points *= 100  # Kalikan TP dengan 100

        symbol = signal['symbol']
        lot = robot_lot
        point = mt5.symbol_info(symbol).point
        digits = mt5.symbol_info(symbol).digits

        # Hitung SL dan TP berdasarkan jenis order
        if signal['type'] == 'BUY':
            order_type = mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(symbol).ask
            sl = round(price - (sl_points * point), digits)  # SL untuk BUY
            tp = round(price + (tp_points * point), digits)  # TP untuk BUY
        else:  # SELL
            order_type = mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(symbol).bid
            sl = round(price + (sl_points * point), digits)  # SL untuk SELL
            tp = round(price - (tp_points * point), digits)  # TP untuk SELL

        # Print untuk debugging
        print(f"Trade request for {symbol}:")
        print(f"Point size: {point}")
        print(f"SL points: {sl_points}")
        print(f"TP points: {tp_points}")
        print(f"Price: {price}")
        print(f"SL price: {sl}")
        print(f"TP price: {tp}")

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": 234000,
            "comment": f"python_{signal['type']}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {
                "status": "error",
                "message": f"Error executing trade: {result.comment}",
                "details": {
                    "sl_points": sl_points,
                    "tp_points": tp_points,
                    "calculated_sl": sl,
                    "calculated_tp": tp,
                    "point_size": point
                }
            }
        
        return {
            "status": "success",
            "message": f"Trade executed: {signal['type']} {symbol} at {price}",
            "details": {
                "ticket": result.order,
                "sl_points": sl_points,
                "tp_points": tp_points,
                "calculated_sl": sl,
                "calculated_tp": tp,
                "point_size": point
            }
        }

    except Exception as e:
        return {"status": "error", "message": f"Error: {str(e)}"}

@app.route('/')
def index():
    return render_template('index.html')

def get_current_session():
    now = datetime.now()
    current_hour = now.hour

    if 0 <= current_hour < 9:
        return "Asian Session"
    elif 9 <= current_hour < 17:
        return "London Session"
    else:
        return "New York Session"
@app.route('/get_current_session')
def get_current_session_route():
    session = get_current_session()
    return jsonify({'current_session': session})


@app.route('/get_trading_info')
def get_trading_info():
    if not mt5.initialize():
        return jsonify({'error': 'Gagal menginisialisasi MT5'})
    
    account_info = mt5.account_info()
    positions = mt5.positions_get()
    active_positions = []
    positions_per_pair = {}
    
    if positions:
        for pos in positions:
            point = mt5.symbol_info(pos.symbol).point
            current_price = mt5.symbol_info_tick(pos.symbol).bid if pos.type == 0 else mt5.symbol_info_tick(pos.symbol).ask
            
            # Hitung profit dalam points
            profit_points = (current_price - pos.price_open) / point if pos.type == 0 else (pos.price_open - current_price) / point
            
            # Update positions count per pair
            if pos.symbol not in positions_per_pair:
                positions_per_pair[pos.symbol] = 1
            else:
                positions_per_pair[pos.symbol] += 1
            
            # Cek apakah perlu auto BE
            if pos.symbol == "XAUUSDm":
                auto_be_points = 1000  # 100 points dikali 10
            elif pos.symbol == "BTCUSDm":
                auto_be_points = 10000  # 100 points dikali 100
            else:
                auto_be_points = AUTO_BE_POINTS  # Default
            
            if profit_points >= auto_be_points and pos.sl != pos.price_open:
                # Pindahkan SL ke entry point
                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": pos.symbol,
                    "position": pos.ticket,
                    "sl": pos.price_open,
                    "tp": pos.tp
                }
                result = mt5.order_send(request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"Auto BE executed for ticket {pos.ticket} at {profit_points} points profit")
            
            active_positions.append({
                'ticket': str(pos.ticket),
                'symbol': pos.symbol,
                'type': 'BUY' if pos.type == 0 else 'SELL',
                'volume': pos.volume,
                'price_open': pos.price_open,
                'price_current': current_price,
                'profit': pos.profit,
                'profit_points': round(profit_points, 1),
                'sl': pos.sl,
                'tp': pos.tp,
                'show_be_button': profit_points >= 5 and profit_points < AUTO_BE_POINTS and pos.sl != pos.price_open,
                'auto_be_executed': profit_points >= AUTO_BE_POINTS and pos.sl == pos.price_open
            })
    
    data = {
        'balance': account_info.balance,
        'equity': account_info.equity,
        'margin_free': account_info.margin_free,
        'positions': active_positions,
        'positions_per_pair': positions_per_pair,
        'max_positions_per_pair': max_positions_per_pair
    }
    
    return jsonify(data)

@app.route('/toggle_robot', methods=['POST'])
def toggle_robot():
    global robot_active, signal_thread
    robot_active = not robot_active
    
    if robot_active:
        signal_thread = Thread(target=check_trading_signal)
        signal_thread.daemon = True
        signal_thread.start()
    
    return jsonify({
        'status': 'active' if robot_active else 'inactive',
        'message': 'Robot Trading ' + ('aktif' if robot_active else 'nonaktif')
    })

@app.route('/set_lot', methods=['POST'])
def set_lot():
    global robot_lot
    try:
        new_lot = float(request.json.get('lot'))
        if new_lot < 0.01 or new_lot > 1:
            return jsonify({'error': 'Lot harus antara 0.01 dan 1'}), 400
        robot_lot = new_lot
        return jsonify({
            'success': True,
            'message': f'Lot size diubah menjadi {new_lot}',
            'lot': new_lot
        })
    except ValueError:
        return jsonify({'error': 'Nilai lot tidak valid'}), 400

@app.route('/get_robot_status')
def get_robot_status():
    return jsonify({
        'active': robot_active,
        'lot': robot_lot
    })

@app.route('/get_signal_status')
def get_signal_status():
    return jsonify({
        'signal': current_signal if current_signal else {
            "status": "no_signal",
            "message": "Belum ada sinyal trading"
        }
    })

@app.route('/get_total_profit')
def get_total_profit():
    if not mt5.initialize():
        return jsonify({'error': 'Gagal koneksi MT5'})
    
    positions = mt5.positions_get()
    total_profit = 0
    if positions:
        total_profit = sum(pos.profit for pos in positions)
    
    return jsonify({
        'total_profit': total_profit,
        'position_count': len(positions) if positions else 0
    })

@app.route('/close_position', methods=['POST'])
def close_position():
    try:
        if not request.is_json:
            return jsonify({'error': 'Invalid JSON request'}), 400
            
        position_data = request.json
        symbol = position_data.get('symbol')
        
        try:
            ticket = int(position_data.get('ticket', 0))
            if ticket == 0:
                return jsonify({'error': 'Invalid ticket number'})
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid ticket format'})
            
        if not mt5.initialize():
            return jsonify({'error': 'Gagal koneksi MT5'})
            
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return jsonify({'error': f'Posisi {ticket} tidak ditemukan'})
            
        position = positions[0]
        
        # Menentukan tipe order penutupan
        close_type = mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(symbol).bid if position.type == 0 else mt5.symbol_info_tick(symbol).ask
        
        request_data = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": position.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": f"close_{ticket}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request_data)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return jsonify({
                'error': f'Error menutup posisi {ticket}: {result.comment}'
            })
        
        return jsonify({
            'success': True,
            'message': f'Posisi {ticket} ({symbol}) berhasil ditutup'
        })
    
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'})

@app.route('/close_all_positions', methods=['POST'])
def close_all_positions():
    if not mt5.initialize():
        return jsonify({'error': 'Gagal koneksi MT5'})
    
    positions = mt5.positions_get()
    if not positions:
        return jsonify({'message': 'Tidak ada posisi yang terbuka'})
    
    results = []
    for position in positions:
        close_type = mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(position.symbol).bid if position.type == 0 else mt5.symbol_info_tick(position.symbol).ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": close_type,
            "position": position.ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": "python close all",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        results.append({
            'ticket': position.ticket,
            'success': result.retcode == mt5.TRADE_RETCODE_DONE,
            'message': result.comment
        })
    
    return jsonify({
        'success': True,
        'message': f'Menutup {len(results)} posisi',
        'details': results
    })

@app.route('/set_sl_tp', methods=['POST'])
def set_sl_tp():
    global robot_sl_pips, robot_tp_pips
    try:
        data = request.json
        sl_pips = float(data.get('sl', 200))
        tp_pips = float(data.get('tp', 200))
        
        if sl_pips < 0 or tp_pips < 0:
            return jsonify({'error': 'SL dan TP harus lebih besar dari 0'})
        
        # Update nilai global
        robot_sl_pips = sl_pips
        robot_tp_pips = tp_pips
        
        # Log untuk debugging
        print(f"SL/TP updated - SL: {robot_sl_pips} pips, TP: {robot_tp_pips} pips")
        
        return jsonify({
            'success': True,
            'message': f'SL diatur ke {sl_pips} pips, TP diatur ke {tp_pips} pips',
            'sl': sl_pips,
            'tp': tp_pips
        })
    except ValueError as e:
        return jsonify({'error': f'Nilai tidak valid: {str(e)}'})
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'})

@app.route('/get_current_sl_tp')
def get_current_sl_tp():
    return jsonify({
        'sl': robot_sl_pips,
        'tp': robot_tp_pips
    })

@app.route('/modify_position', methods=['POST'])
def modify_position():
    try:
        if not request.is_json:
            return jsonify({'error': 'Invalid JSON request'}), 400
            
        if not mt5.initialize():
            return jsonify({'error': 'Gagal koneksi MT5'})
            
        data = request.json
        ticket = int(data.get('ticket', 0))
        sl = float(data.get('sl', 0)) if data.get('sl') is not None else None
        tp = float(data.get('tp', 0)) if data.get('tp') is not None else None
        
        if ticket == 0:
            return jsonify({'error': 'Invalid ticket number'})
        
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return jsonify({'error': 'Posisi tidak ditemukan'})
            
        position = position[0]
        
        # Buat request dengan hanya mengubah nilai yang diberikan
        request_data = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": ticket,
            "sl": sl if sl is not None else position.sl,
            "tp": tp if tp is not None else position.tp
        }
        
        result = mt5.order_send(request_data)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return jsonify({'error': f'Error modifikasi posisi: {result.comment}'})
        
        # Hitung profit dalam pips
        point = mt5.symbol_info(position.symbol).point
        multiplier = 100 if 'JPY' in position.symbol else 10000
        current_price = mt5.symbol_info_tick(position.symbol).bid if position.type == 0 else mt5.symbol_info_tick(position.symbol).ask
        profit_pips = (current_price - position.price_open) * multiplier * (1/point) if position.type == 0 else (position.price_open - current_price) * multiplier * (1/point)
        
        return jsonify({
            'success': True,
            'message': f'SL/TP berhasil diubah untuk posisi {ticket}',
            'profit_pips': round(profit_pips, 1),
            'can_move_sl': profit_pips >= 10
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'})

@app.route('/move_sl_to_entry', methods=['POST'])
def move_sl_to_entry():
    try:
        if not request.is_json:
            return jsonify({'error': 'Invalid JSON request'}), 400
            
        if not mt5.initialize():
            return jsonify({'error': 'Gagal koneksi MT5'})
            
        data = request.json
        ticket = int(data.get('ticket', 0))
        
        if ticket == 0:
            return jsonify({'error': 'Invalid ticket number'})
        
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return jsonify({'error': 'Posisi tidak ditemukan'})
            
        position = position[0]
        
        request_data = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": ticket,
            "sl": position.price_open,
            "tp": position.tp  # Pertahankan TP yang ada
        }
        
        result = mt5.order_send(request_data)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return jsonify({'error': f'Error memindahkan SL: {result.comment}'})
        
        return jsonify({
            'success': True,
            'message': f'SL berhasil dipindahkan ke entry point untuk posisi {ticket}'
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'})

@app.route('/set_max_positions', methods=['POST'])
def set_max_positions():
    global max_positions_per_pair
    try:
        data = request.json
        max_pos = int(data.get('max_positions', 1))
        
        if max_pos < 1:
            return jsonify({'error': 'Jumlah posisi minimal adalah 1'})
        if max_pos > 10:  # Batasi maksimal 10 posisi per pair
            return jsonify({'error': 'Jumlah posisi maksimal adalah 10'})
            
        max_positions_per_pair = max_pos
        return jsonify({
            'success': True,
            'message': f'Maksimal posisi per pair diatur ke {max_pos}',
            'max_positions': max_pos
        })
    except ValueError:
        return jsonify({'error': 'Nilai tidak valid'})

@app.route('/get_max_positions')
def get_max_positions():
    return jsonify({
        'max_positions': max_positions_per_pair
    })

@app.route('/get_forex_pairs')
def get_forex_pairs():
    return jsonify({
        'forex_pairs': FOREX_PAIRS,
        'timeframe': TIMEFRAME
    })

@app.route('/get_sl_tp', methods=['GET'])
def get_sl_tp():
    return jsonify({
        'sl': robot_sl_pips,
        'tp': robot_tp_pips
    })

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    login = data.get('login')
    password = data.get('password')
    server = data.get('server')

    if login_account(login, password, server):
        return jsonify({'status': 'success', 'message': 'Login berhasil!'})
    else:
        return jsonify({'status': 'error', 'message': 'Login gagal!'}), 400



def main():
    # Inisialisasi MT5
    if not initialize_mt5():
        print("Gagal menginisialisasi MT5. Pastikan MT5 sudah terinstall!")
        return
    
    # Login ke akun demo Exness
    if not login_account(239546435, "@Pasha9371", "Exness-MT5Trial6"):
        print("Gagal login ke akun demo. Periksa kredensial!")
        return
    
    print("Login berhasil!")
    account_info = mt5.account_info()
    if account_info is None:
        print("Gagal mendapatkan informasi akun!")
        return
    
    # Tampilkan informasi akun dalam IDR
    print(f"Balance: Rp{account_info.balance:,.2f}")
    print(f"Equity: Rp{account_info.equity:,.2f}")
    print(f"Free Margin: Rp{account_info.margin_free:,.2f}")
    
    # Tambahkan logika untuk menampilkan informasi lebih lanjut jika diperlukan

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
    #   app.run(debug=True)