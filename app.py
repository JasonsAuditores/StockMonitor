#Github_flaskversion
from flask import Flask, request
import time
import telebot
import finnhub
from datetime import datetime
import pytz
from concurrent.futures import ThreadPoolExecutor
import logging
import os

# 从环境变量获取API密钥
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY')
TG_API_KEY = os.getenv('TG_API_KEY')
chat_id = "5157836313"

# 检查密钥是否存在
if FINNHUB_API_KEY is None or TG_API_KEY is None:
    raise ValueError("Please ensure that FINNHUB_API_KEY&TG_API_KEY have been set")

finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)
bot = telebot.TeleBot(TG_API_KEY)

# 创建一个线程池执行器，全局只有这一个
executor = ThreadPoolExecutor(max_workers=20)  # 可以根据您的需求调整最大工作线程数

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
MAX_RETRIES = 5 #max try times: 5

# 存储股票目标价格和监控状态
stocks = {}
# 可能需要一个变量来存储哪只股票需要价格调整，以及是上涨还是下跌
pending_adjustment = {}

class Stock:
    def __init__(self, symbol, target_rise, target_fall):
        self.symbol = symbol
        self.target_rise = target_rise
        self.target_fall = target_fall
        self.monitoring = True

    def get_current_price(self):
        # 从FinnHub获取实时股票数据
        res = finnhub_client.quote(self.symbol)
        if 'c' in res:
            return res['c']
        raise Exception("Could not get the price from Finnhub")

    def check_price(self):
        """
        检查股票价格是否达到任何目标，并返回相应的状态。
        """
        current_price = self.get_current_price()
        if current_price >= self.target_rise:
            return 'rise'
        elif current_price <= self.target_fall:
            return 'fall'
        return None  # 如果价格在目标范围内，则返回None

# 响应 /start 和 /help 命令
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    help_text = """
Welcome to use MilesBot for your fortune discovery：

To set a stock price alert, use:
/setprice SYMBOL RISE_PRICE FALL_PRICE

Change 'SYMBOL' to the stock code, then 'RISE_PRICE' and 'FALL_PRICE' as your expected price limit.

Example:
/setprice TSLA 650 600

To remove a stock price alert, use:
/removeprice SYMBOL

Example:
/removeprice TSLA
"""
    bot.reply_to(message, help_text)
    
def send_telegram_message(chat_id, msg, expect_reply=False):
    for attempt in range(MAX_RETRIES):
        try:
            if expect_reply:
                msg_sent = bot.send_message(chat_id, msg, reply_markup=telebot.types.ForceReply(selective=False))
                bot.register_next_step_handler(msg_sent, receive_user_reply)
            else:
                bot.send_message(chat_id, msg)
            break  # 如果成功，则跳出循环
        except Exception as e:
            logging.error(f"Attempt {attempt + 1} failed to send telegram message. Error: {e}")
            if attempt < MAX_RETRIES - 1:  # 最后一次尝试后不要休眠
                time.sleep(2)  # 在重试之前等待2秒可能会防止过多的请求
    else:
        logging.error("All attempts failed to send telegram message.")
           
def monitor_stock(stock):
    logger.info(f"Monitoring {stock.symbol}...")  # 记录信息级别的日志
    retries = 0
    while stock.monitoring:
        try:
            status = stock.check_price()
            tz = pytz.timezone('Asia/Shanghai')  # 指定时区
            current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
            chat_id = "5157836313"

            if status == 'rise':
                notification = f"{stock.symbol} has reached or exceeded the target rise price: ${stock.target_rise} (Current time: {current_time}). Would you like to adjust the target rise price? Please reply with the new price."
                logger.info(notification)  # 将通知记录到日志中
                send_telegram_message(chat_id, notification, expect_reply=True)
                stock.monitoring = False  # 停止监控
                pending_adjustment[chat_id] = {'symbol': stock.symbol, 'target_type': 'rise'}  # 存储待处理信息

            elif status == 'fall':
                notification = f"{stock.symbol} has reached or fallen below the target fall price: ${stock.target_fall} (Current time: {current_time}). Would you like to adjust the target fall price? Please reply with the new price."
                logger.info(notification)  # 将通知记录到日志中
                send_telegram_message(chat_id, notification, expect_reply=True)
                stock.monitoring = False  # 停止监控
                pending_adjustment[chat_id] = {'symbol': stock.symbol, 'target_type': 'fall'}  # 存储待处理信息

            # 如果操作成功，重置重试计数器
            retries = 0

        except Exception as e:
            retries += 1  # 增加重试计数器
            logger.error(f"Error while monitoring stock {stock.symbol}: {e}", exc_info=True)

            if retries < MAX_RETRIES:
                # 如果没有达到最大重试次数，等待一段时间再重试
                time.sleep(10)  # 等待时间可以根据实际情况调整
                continue
            else:
                # 达到最大重试次数，通知用户并结束监控
                error_message = f"Continuous errors occurred while monitoring {stock.symbol}. Monitoring is being stopped."
                logger.error(error_message)  # 这里记录错误日志
                send_telegram_message(chat_id, error_message)  # 注意这里没有 expect_reply 参数，因为是普通通知
                stock.monitoring = False  # 停止监控
                # 这里，你还可以选择记录此错误，或采取其他错误处理措施。
                
        time.sleep(30)  # 正常的睡眠时间用于下一次检查

def receive_user_reply(message):
    chat_id = message.chat.id  # The chat from which the reply was received
    try:
        new_price = float(message.text)  # Try to convert the reply to a float
        
        if chat_id in pending_adjustment:
            symbol = pending_adjustment[chat_id]['symbol']
            target_type = pending_adjustment[chat_id]['target_type']  # 'rise' or 'fall'
            
            # Retrieve the Stock instance and update the price
            stock = stocks[symbol]  # Retrieve the Stock object from the dictionary
            if target_type == 'rise':
                stock.target_rise = new_price  # Update the attribute of the Stock instance directly
            else:
                stock.target_fall = new_price

            # Important: Reactivate monitoring
            if not stock.monitoring:  # If it's currently not monitoring
                stock.monitoring = True  # Set the monitoring flag to True
                # Restart the monitoring thread
                monitor_thread = threading.Thread(target=monitor_stock, args=(stock,))
                monitor_thread.start()

            bot.send_message(chat_id, f"The {('rise' if target_type == 'rise' else 'fall')} target price for {symbol} has been updated to: ${new_price}")
            del pending_adjustment[chat_id]  # Clear the pending item
        else:
            bot.send_message(chat_id, "No stock found that requires a price adjustment.")

    except ValueError:
        # If the conversion fails, send an error message
        bot.send_message(chat_id, "Please enter a valid price.")
    except Exception as e:
        bot.send_message(chat_id, str(e))        
@bot.message_handler(commands=['setprice'])
def handle_setprice(message):
    try:
        # Parse the command, the format is like: /setprice TSLA 650 600
        parts = message.text.split()
        if len(parts) != 4:
            bot.reply_to(message, "Incorrect command format. The correct format is: /setprice SYMBOL RISE_PRICE FALL_PRICE")
            return

        symbol, target_rise_str, target_fall_str = parts[1], parts[2], parts[3]

        # Validate if the prices are valid numbers
        try:
            target_rise = float(target_rise_str)
            target_fall = float(target_fall_str)
        except ValueError:
            bot.reply_to(message, "Prices need to be numbers. Please re-enter valid rise or fall prices.")
            return

        # Check if the prices are positive numbers
        if target_rise <= 0 or target_fall <= 0:
            bot.reply_to(message, "Prices must be positive numbers. Please reset the rise or fall prices.")
            return

        # Validate the stock symbol
        try:
            test_stock = Stock(symbol, 0, 0)  # Temporary object for testing, price information isn't important here
            test_stock.get_current_price()  # Try to get the current price to validate the stock symbol
        except Exception as e:
            # If there is an exception, it might be because the stock symbol is invalid
            bot.reply_to(message, f"Invalid stock symbol or there was a problem fetching the stock price. Error details: {str(e)}")
            return

        # If this stock is already being monitored, just update the price information
        if symbol in stocks:
            stock = stocks[symbol]
            stock.target_rise = target_rise
            stock.target_fall = target_fall
        else:
            stock = Stock(symbol, target_rise, target_fall)
            stocks[symbol] = stock
            # Execute the monitoring task using a thread pool
            executor.submit(monitor_stock, stock)

        bot.reply_to(message, f"Started monitoring {stock.symbol}. Rise target: ${stock.target_rise}, Fall target: ${stock.target_fall}")

    except Exception as e:
        # Log the exception
        logging.error(f"Error setting price: {e}", exc_info=True)
        # Send an error message to the user
        bot.reply_to(message, "An error occurred while processing your request. Please try again later.")
        
@bot.message_handler(commands=['removeprice'])
def handle_removeprice(message):
    try:
        # Parsing the command, expecting something like: /removeprice TSLA
        parts = message.text.split()
        if len(parts) != 2:
            bot.reply_to(message, "Invalid command format. Correct format: /removeprice SYMBOL")
            return

        symbol = parts[1]

        # Check if the stock is currently being monitored
        if symbol in stocks:
            stock = stocks[symbol]
            stock.monitoring = False  # This will cause the monitoring thread to exit
            del stocks[symbol]  # Remove this stock from the dictionary
            
            bot.reply_to(message, f"Stopped monitoring {stock.symbol}.")
        else:
            bot.reply_to(message, f"No active monitoring found for {symbol}.")

    except Exception as e:
        # Log the exception
        logging.error(f"Error removing stock: {e}", exc_info=True)
        
        # Send an error message to the user
        bot.reply_to(message, "An error occurred while processing your request. Please try again later.")        

app = Flask(__name__)

# 设置webhook路由
@app.route('/bot_webhook/', methods=['POST'])
def get_message():
    json_str = request.stream.read().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return '', 200

if __name__ == '__main__':
    # 设置Webhook，以便在你的Flask应用中接收消息
    bot.remove_webhook()
    bot.set_webhook(url="https://milesyop.azurewebsites.net/bot_webhook/")  # 设置你的外部URL
    port = int(os.environ.get('PORT', 80))
    app.run(host="0.0.0.0", port=port)
