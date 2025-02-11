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
robot_sl_pips = 100  # Default 100 points
robot_tp_pips = 100  # Default 100 points
AUTO_BE_POINTS = 20   # Pindahkan SL ke entry setelah 20 points

# Tambahkan daftar pair forex yang akan dipantau
FOREX_PAIRS = [
    "EURUSDm", "GBPUSDm", "USDJPYm", "AUDUSDm", "USDCADm", 
    "NZDUSDm", "EURJPYm", "GBPJPYm", "XAUUSDm", "USDCHFm",
    "AUDJPYm", "AUDNZDm", "CADJPYm", "CHFJPYm", "EURCADm",
    "EURNOKm", "EURSEKm", "GBPCADm", "GBPNZDm", "NZDCADm"
]

# Tambahkan variabel global untuk timeframe
TIMEFRAME = mt5.TIMEFRAME_M5  # Menggunakan timeframe M5

# Tambahkan variabel global
max_positions_per_pair = 2  # Default value


def initialize_mt5():
    if not mt5.initialize():
        print("Inisialisasi MT5 gagal!")
        return False
    return True

def login_account(login, password, server):
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
        # Mengambil data historis dengan timeframe yang ditentukan
        rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 100)
        df = pd.DataFrame(rates)
        
        # Strategi scalping menggunakan MA cepat
        df['MA5'] = df['close'].rolling(window=5).mean()
        df['MA10'] = df['close'].rolling(window=10).mean()
        
        # RSI untuk konfirmasi
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # Stochastic Oscillator
        df['L14'] = df['low'].rolling(window=14).min()
        df['H14'] = df['high'].rolling(window=14).max()
        df['%K'] = (df['close'] - df['L14']) / (df['H14'] - df['L14']) * 100
        df['%D'] = df['%K'].rolling(window=3).mean()
        
        # Hitung ATR untuk mengukur volatilitas
        df['ATR'] = df['high'].rolling(window=14).max() - df['low'].rolling(window=14).min()
        
        # Tambahkan konfirmasi lain, misalnya, MA 50
        df['MA50'] = df['close'].rolling(window=50).mean()
        
        # Hitung Bollinger Bands
        df['Middle_Band'] = df['close'].rolling(window=20).mean()
        df['Upper_Band'] = df['Middle_Band'] + (df['close'].rolling(window=20).std() * 2)
        df['Lower_Band'] = df['Middle_Band'] - (df['close'].rolling(window=20).std() * 2)
        
        # Hitung level Fibonacci
        high_price = df['high'].max()
        low_price = df['low'].min()
        diff = high_price - low_price
        level_23_6 = high_price - diff * 0.236
        level_38_2 = high_price - diff * 0.382
        level_61_8 = high_price - diff * 0.618
        
        # Generate sinyal dengan kondisi yang lebih ketat
        if (df['MA5'].iloc[-1] > df['MA10'].iloc[-1] and 
            df['RSI'].iloc[-1] < 70 and 
            df['%K'].iloc[-1] > df['%D'].iloc[-1] and  # Konfirmasi Stochastic
            df['close'].iloc[-1] > df['close'].iloc[-2] and
            df['close'].iloc[-1] > df['MA50'].iloc[-1] and
            df['close'].iloc[-1] < df['Upper_Band'].iloc[-1] and
            df['ATR'].iloc[-1] > 0.0001 and  # Pastikan ATR cukup untuk momentum
            (df['close'].iloc[-1] > level_38_2 and df['close'].iloc[-1] < level_23_6)):  # Konfirmasi Fibonacci
            return "BUY"
        elif (df['MA5'].iloc[-1] < df['MA10'].iloc[-1] and 
              df['RSI'].iloc[-1] > 30 and 
              df['%K'].iloc[-1] < df['%D'].iloc[-1] and  # Konfirmasi Stochastic
              df['close'].iloc[-1] < df['close'].iloc[-2] and
              df['close'].iloc[-1] < df['MA50'].iloc[-1] and
              df['close'].iloc[-1] > df['Lower_Band'].iloc[-1] and
              df['ATR'].iloc[-1] > 0.0001 and  # Pastikan ATR cukup untuk momentum
              (df['close'].iloc[-1] < level_61_8 and df['close'].iloc[-1] > level_38_2)):  # Konfirmasi Fibonacci
            return "SELL"
        return None

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
            if profit_points >= AUTO_BE_POINTS and pos.sl != pos.price_open:
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
        if new_lot <= 0:
            return jsonify({'error': 'Lot harus lebih besar dari 0'}), 400
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
        sl_pips = float(data.get('sl', 100))
        tp_pips = float(data.get('tp', 100))
        
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
        max_pos = int(data.get('max_positions', 2))
        
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
    
    # Buat instance robot dengan multiple symbols
    robot = TradingRobot(
        symbols=["EURUSDm", "GBPJPYm", "AUDUSDm", "USDJPYm", "XAUUSDm", "BTCUSDm", "USDCADm", "GBPUSDm", "NZDUSDm", "EURJPYm", "CHFJPYm"],
        lot_size=0.01,  # Ukuran lot yang ditetapkan menjadi 0.02
        stop_loss=50,   # SL 50 pips
        take_profit=50   # TP 50 pips
    )
    
    print("Robot trading dimulai...")
    print(f"Trading pairs: {', '.join(robot.symbols)}")
    print(f"Lot size: {robot.lot_size}")
    print(f"Stop Loss: {robot.stop_loss} pips, Take Profit: {robot.take_profit} pips")
    
    while True:
        try:
            account_info = mt5.account_info()  # Memperoleh informasi akun terbaru
            margin_free = account_info.margin_free
            margin_required = account_info.margin_initial
            
            # Cek apakah margin tersisa kurang dari 50%
            if margin_free < 0.5 * margin_required:
                print("Margin tersisa kurang dari 50%. Menghentikan trading.")
                break  # Hentikan loop jika margin kurang dari 50%
            
            # Cek apakah saldo habis
            if account_info.balance <= 0:
                print("Saldo habis. Menghentikan trading.")
                break  # Hentikan loop jika saldo habis
            
            for symbol in robot.symbols:
                # Hitung total entry untuk setiap simbol
                total_entries = len(mt5.positions_get(symbol=symbol)) if mt5.positions_get(symbol=symbol) is not None else 0

                # Hentikan looping jika total entry sudah 10
                if total_entries >= 2:
                    print(f"Total entry untuk {symbol} sudah mencapai 2. Menghentikan trading.")
                    continue  # Lanjut ke simbol berikutnya

                # Cek posisi terbuka untuk simbol
                positions = mt5.positions_get(symbol=symbol)
                if positions is not None:  # Pastikan positions tidak None
                    for pos in positions:
                        # Hitung profit dalam pips
                        profit_pips = (mt5.symbol_info_tick(symbol).bid - pos.price_open) / \
                                      mt5.symbol_info(symbol).point if pos.type == 0 else \
                                      (pos.price_open - mt5.symbol_info_tick(symbol).ask) / \
                                      mt5.symbol_info(symbol).point
                        
                        # Jika profit sudah mencapai 5 pips, pindahkan SL ke entry
                        if profit_pips >= 5:
                            result = robot.modify_sl_to_entry(symbol, pos)
                            if result.retcode == mt5.TRADE_RETCODE_DONE:
                                print(f"SL dipindahkan ke entry untuk posisi {symbol}")
                            else:
                                print(f"Gagal memindahkan SL: {result.comment}")

                # Cek sinyal untuk simbol
                if total_entries < 2:  # Hanya lakukan entry jika total entry kurang dari 10
                    signal = robot.get_signal(symbol)
                    if signal:
                        print(f"Sinyal ditemukan untuk {symbol}: {signal}")
                        # Eksekusi trade jika sinyal ditemukan
                        result = robot.execute_trade(symbol, signal)
                        if result and result.retcode != mt5.TRADE_RETCODE_DONE:
                            print(f"Error saat eksekusi trade untuk {symbol}: {result.comment}")
                    else:
                        print(f"Tidak ada sinyal trading untuk {symbol} saat ini")

                # Tampilkan informasi posisi aktif
                active_positions = mt5.positions_get(symbol=symbol)
                if active_positions:
                    print(f"Posisi aktif saat ini untuk {symbol}:")
                    for pos in active_positions:
                        print(f"Symbol: {pos.symbol}, Type: {'BUY' if pos.type == 0 else 'SELL'}, Volume: {pos.volume}, Price Open: {pos.price_open}")

            # Update informasi akun
            account_info = mt5.account_info()
            print(f"\nBalance saat ini: Rp{account_info.balance:,.2f}")  # Menampilkan saldo
            print(f"Equity saat ini: Rp{account_info.equity:,.2f}")  # Menampilkan ekuitas
            
            # Tunggu 5 detik sebelum check sinyal berikutnya
            time.sleep(5)
            
        except Exception as e:
            print(f"Error: {e}")
            continue

if __name__ == "__main__":
    # app.run(host='0.0.0.0', port=5000)
      app.run(debug=True)