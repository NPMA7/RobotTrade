import MetaTrader5 as mt5
import pandas as pd
import time
import numpy as np
from datetime import datetime

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
    def __init__(self, symbols, lot_size=0.1, stop_loss=50, take_profit=100):
        self.symbols = symbols
        self.lot_size = lot_size
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        
    def get_signal(self, symbol):
        # Mengambil data historis dengan timeframe M30
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 0, 100)
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
        
        # Tambahkan konfirmasi lain, misalnya, MA 50
        df['MA50'] = df['close'].rolling(window=50).mean()
        
        # Tambahkan Bollinger Bands
        df['Middle_Band'] = df['close'].rolling(window=20).mean()
        df['Upper_Band'] = df['Middle_Band'] + (df['close'].rolling(window=20).std() * 2)
        df['Lower_Band'] = df['Middle_Band'] - (df['close'].rolling(window=20).std() * 2)
        
        # Generate sinyal dengan kondisi yang lebih ketat
        if (df['MA5'].iloc[-1] > df['MA10'].iloc[-1] and 
            df['RSI'].iloc[-1] < 70 and 
            df['close'].iloc[-1] > df['close'].iloc[-2] and
            df['close'].iloc[-1] > df['MA50'].iloc[-1] and
            df['close'].iloc[-1] > df['Upper_Band'].iloc[-1]):  # Konfirmasi tambahan
            return "BUY"
        elif (df['MA5'].iloc[-1] < df['MA10'].iloc[-1] and 
              df['RSI'].iloc[-1] > 30 and 
              df['close'].iloc[-1] < df['close'].iloc[-2] and
              df['close'].iloc[-1] < df['MA50'].iloc[-1] and
              df['close'].iloc[-1] < df['Lower_Band'].iloc[-1]):  # Konfirmasi tambahan
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
        tp = price + (self.take_profit * symbol_info.point) if signal == "BUY" else price - (self.take_profit * symbol_info.point)

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

def main():
    # Inisialisasi MT5
    if not initialize_mt5():
        print("Gagal menginisialisasi MT5. Pastikan MT5 sudah terinstall!")
        return
    
    # Login ke akun demo Exness
    if not login_account(79615758, "@Pasha9371", "Exness-MT5Trial8"):
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
        symbols=["EURUSD", "GBPJPY", "AUDUSD", "USDJPY", "XAUUSD", "BTCUSD", "USDCAD", "NZDUSD", "CHFJPY"],
        lot_size=0.02,
        stop_loss=50,
        take_profit=100
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

                # Hentikan looping jika total entry sudah 5
                if total_entries >= 5:
                    print(f"Total entry untuk {symbol} sudah mencapai 5. Menghentikan trading.")
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
                        if profit_pips >= 10:
                            result = robot.modify_sl_to_entry(symbol, pos)
                            if result.retcode == mt5.TRADE_RETCODE_DONE:
                                print(f"SL dipindahkan ke entry untuk posisi {symbol}")
                            else:
                                print(f"Gagal memindahkan SL: {result.comment}")

                # Cek sinyal untuk simbol
                if total_entries < 5:  # Hanya lakukan entry jika total entry kurang dari 5
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
    main()